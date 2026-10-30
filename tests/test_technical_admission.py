"""Synthetic central-writer technical alias admission; no real service calls."""
import copy
import unittest

from cloud.technical_admission import admit_technical_record, AdmissionConflict, AdmissionStorageError, AdmissionRejected
from cloud.technical_records import make_technical_record
from cloud.records import stable_id
from test_technical_records import sample


CONTEXT = {"concurrency_group": "hiring-outreach-single-writer", "reservation_verified": True,
           "orchestrator_verified": True, "previous_writer_reconciled": True, "run_id": "fixture-run"}


class Store:
    def __init__(self):
        self.rows = {}
        self.puts = []

    def index_source_records(self):
        return copy.deepcopy(list(self.rows.values()))

    def get(self, identity):
        return copy.deepcopy(self.rows.get(identity))

    def put(self, record):
        old = self.rows.get(record["id"])
        if old and old != record:
            raise ValueError("conflict")
        self.rows[record["id"]] = copy.deepcopy(record)
        self.puts.append(record["id"])
        return copy.deepcopy(record)


class TechnicalAdmissionTests(unittest.TestCase):
    def test_exact_replay_is_read_only_after_verified_admission(self):
        store = Store()
        source = make_technical_record("technical_items", sample())
        result = admit_technical_record(store, source, writer_context=CONTEXT)
        self.assertEqual(result["status"], "admitted")
        self.assertEqual(store.get(source["id"]), source)
        puts = list(store.puts)
        self.assertEqual(admit_technical_record(store, source, writer_context=CONTEXT), result)
        self.assertEqual(store.puts, puts)

    def test_partial_prior_admission_blocks_an_unrelated_source_after_restart(self):
        store = Store()
        original = store.put
        def interrupted(row):
            if store.puts:
                raise RuntimeError("uncertain write")
            return original(row)
        store.put = interrupted
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, make_technical_record("technical_items", sample()), writer_context=CONTEXT)
        store.put = original
        unrelated = sample()
        unrelated.update(entity_id=stable_id("article", "other"), source_identity=stable_id("source", "other"),
                         source_url="https://another.example/blog")
        before = list(store.puts)
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, make_technical_record("technical_items", unrelated), writer_context=CONTEXT)
        self.assertEqual(store.puts, before)

    def test_conflicting_alias_and_missing_trusted_context_cause_no_writes(self):
        store = Store()
        first = make_technical_record("technical_items", sample())
        admit_technical_record(store, first, writer_context=CONTEXT)
        before = list(store.puts)
        other = sample()
        other["entity_id"] = stable_id("article", "different")
        other["source_identity"] = stable_id("source", "different")
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, make_technical_record("technical_items", other), writer_context=CONTEXT)
        self.assertEqual(store.puts, before)
        for field in ("orchestrator_verified", "reservation_verified", "previous_writer_reconciled"):
            context = dict(CONTEXT)
            context[field] = False
            with self.subTest(field=field), self.assertRaises(AdmissionRejected):
                admit_technical_record(store, first, writer_context=context)
        self.assertEqual(store.puts, before)

    def test_readback_failure_stops_before_any_following_write(self):
        store = Store()
        original = store.get
        def failing_after_put(identity):
            if store.puts:
                raise RuntimeError("readback unavailable")
            return original(identity)
        store.get = failing_after_put
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, make_technical_record("technical_items", sample()), writer_context=CONTEXT)
        self.assertEqual(len(store.puts), 1)

    def test_other_revision_can_reuse_its_same_entity_reservations(self):
        store = Store()
        payload = sample()
        first = make_technical_record("technical_items", payload)
        admit_technical_record(store, first, writer_context=CONTEXT)
        payload["revision"] = 2
        payload["content_hash"] = "b" * 64
        second = make_technical_record("technical_items", payload)
        admit_technical_record(store, second, writer_context=CONTEXT)
        self.assertEqual(store.get(second["id"]), second)

    def test_failures_after_each_committed_append_block_further_admissions(self):
        for fail_after in range(1, 5):
            with self.subTest(fail_after=fail_after):
                store = Store()
                original = store.put
                def ambiguous_after_write(row):
                    result = original(row)
                    if len(store.puts) == fail_after:
                        raise RuntimeError("response lost")
                    return result
                store.put = ambiguous_after_write
                first = make_technical_record("technical_items", sample())
                with self.assertRaises(AdmissionStorageError):
                    admit_technical_record(store, first, writer_context=CONTEXT)
                store.put = original
                before = list(store.puts)
                with self.assertRaises(AdmissionStorageError):
                    admit_technical_record(store, first, writer_context=CONTEXT)
                self.assertEqual(store.puts, before)

    def test_unavailable_snapshot_blocks_before_writes(self):
        store = Store()
        def unavailable():
            raise RuntimeError("partial pagination")
        store.index_source_records = unavailable
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, make_technical_record("technical_items", sample()), writer_context=CONTEXT)
        self.assertEqual(store.puts, [])

    def test_inconsistent_snapshot_and_exact_readback_fail_closed(self):
        store = Store()
        first = make_technical_record("technical_items", sample())
        admit_technical_record(store, first, writer_context=CONTEXT)
        before = list(store.puts)
        store.index_source_records = lambda: []
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, first, writer_context=CONTEXT)
        self.assertEqual(store.puts, before)

    def test_completion_timeout_requires_host_reconciliation_before_replay(self):
        store = Store()
        original = store.put
        def lose_completion_response(row):
            result = original(row)
            if row["kind"] == "technical_admission_completions":
                raise RuntimeError("completion response lost")
            return result
        store.put = lose_completion_response
        record = make_technical_record("technical_items", sample())
        with self.assertRaises(AdmissionStorageError):
            admit_technical_record(store, record, writer_context=CONTEXT)
        self.assertEqual(len(store.puts), 5)
        store.put = original
        before = list(store.puts)
        unresolved = dict(CONTEXT, previous_writer_reconciled=False, run_id="restart-unresolved")
        with self.assertRaises(AdmissionRejected):
            admit_technical_record(store, record, writer_context=unresolved)
        self.assertEqual(store.puts, before)
        # The fixture asserts the host independently reconciled the previous run;
        # this function cannot establish that fact or rule out invisible writes.
        reconciled = dict(CONTEXT, previous_writer_reconciled=True, run_id="restart-reconciled")
        self.assertEqual(admit_technical_record(store, record, writer_context=reconciled)["status"], "admitted")
        self.assertEqual(store.puts, before)


if __name__ == "__main__":
    unittest.main()
