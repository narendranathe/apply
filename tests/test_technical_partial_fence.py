"""A partial technical admission must fence unrelated work and index publication."""
import unittest
from datetime import timedelta

from cloud.agent_worker import WorkerStorageError
from cloud.technical_admission import _records
from cloud.technical_records import make_technical_record
from tests.test_agent_worker import NOW, WRITER, OfficialTransport, admit, job_store, publish, worker
from tests.test_technical_worker_intake import candidate


class PartialTechnicalFenceTests(unittest.TestCase):
    def test_partial_technical_chain_makes_manifest_incomplete(self):
        intent, aliases, completion = _records(candidate())
        for row in (intent, *aliases, completion):
            with self.subTest(kind=row["kind"]):
                store = job_store()
                store.records[row["id"]] = row
                manifest = publish(store, NOW + timedelta(seconds=1))["payload"]
                self.assertFalse(manifest["complete"])
                self.assertTrue(any(c.get("reason") == "technical_admission_incomplete" for c in manifest["conflicts"]))
                self.assertEqual(manifest["campaign_brief"]["ready_tasks"], 0)

    def test_partial_technical_chain_blocks_unrelated_worker_before_append(self):
        intent, aliases, completion = _records(candidate())
        for row in (intent, *aliases, completion):
            with self.subTest(kind=row["kind"]):
                store = job_store()
                admit(store)
                store.records[row["id"]] = row
                publish(store, NOW + timedelta(seconds=1))
                before = store.writes
                with self.assertRaises(WorkerStorageError):
                    worker(store, context={**WRITER, "previous_writer_reconciled": True},
                           now=NOW + timedelta(seconds=2), transport=OfficialTransport())
                self.assertEqual(store.writes, before)

    def test_unavailable_full_snapshot_fences_before_append(self):
        store = job_store()
        admit(store)
        publish(store, NOW + timedelta(seconds=1))
        before = store.writes
        def unavailable():
            raise RuntimeError("synthetic partial pagination")
        store.index_source_records = unavailable
        with self.assertRaises(WorkerStorageError):
            worker(store, context={**WRITER, "previous_writer_reconciled": True},
                   now=NOW + timedelta(seconds=2), transport=OfficialTransport())
        self.assertEqual(store.writes, before)

    def test_written_source_without_completion_still_fences_unrelated_work(self):
        store = job_store()
        admit(store)
        source = candidate()
        intent, aliases, _ = _records(source)
        for row in (intent, *aliases, source):
            store.records[row["id"]] = row
        manifest = publish(store, NOW + timedelta(seconds=1))["payload"]
        self.assertFalse(manifest["complete"])
        before = store.writes
        with self.assertRaises(WorkerStorageError):
            worker(store, context={**WRITER, "previous_writer_reconciled": True},
                   now=NOW + timedelta(seconds=2), transport=OfficialTransport())
        self.assertEqual(store.writes, before)

    def test_naked_technical_source_and_item_fence_index_and_worker(self):
        for kind in ("technical_sources", "technical_items"):
            with self.subTest(kind=kind):
                store = job_store()
                admit(store)
                row = make_technical_record(kind, candidate()["payload"])
                store.records[row["id"]] = row
                manifest = publish(store, NOW + timedelta(seconds=1))["payload"]
                self.assertFalse(manifest["complete"])
                self.assertIn("technical_admission_incomplete", {item["reason"] for item in manifest["conflicts"]})
                before = store.writes
                with self.assertRaises(WorkerStorageError):
                    worker(store, context={**WRITER, "previous_writer_reconciled": True},
                           now=NOW + timedelta(seconds=2), transport=OfficialTransport())
                self.assertEqual(store.writes, before)


if __name__ == "__main__":
    unittest.main()
