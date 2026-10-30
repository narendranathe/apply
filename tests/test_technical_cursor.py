"""Synthetic source-cursor safety; no live Notion or provider access."""
import unittest

from cloud.records import stable_id
from cloud.technical_records import make_technical_record
from cloud.technical_admission import AdmissionStorageError, admit_technical_record
from tests.test_technical_admission import CONTEXT, Store
from tests.test_technical_records import sample


def fixture_records():
    source = make_technical_record("technical_sources", {**sample(),
        "entity_id": stable_id("source_entity", "feed"), "source_identity": stable_id("source", "feed")})
    item = make_technical_record("technical_items", {**sample(),
        "technical_source_id": source["payload"]["entity_id"]})
    cursor = make_technical_record("source_cursors", {**sample(),
        "entity_id": stable_id("source_cursor", source["payload"]["entity_id"]), "source_identity": stable_id("source", "feed"),
        "technical_source_id": source["payload"]["entity_id"],
        "evidence_refs": [source["id"], item["id"]], "cursor_value": "fixture-next"})
    return source, item, cursor


class TechnicalCursorTests(unittest.TestCase):
    def test_out_of_order_cursor_and_competing_identity_never_advance(self):
        from cloud.technical_cursor import commit_cursor, CursorBlocked
        store = Store()
        source, item, cursor = fixture_records()
        admit_technical_record(store, source, writer_context=CONTEXT)
        admit_technical_record(store, item, writer_context=CONTEXT)
        late = make_technical_record("source_cursors", {**cursor["payload"], "revision": 3})
        with self.assertRaises(CursorBlocked):
            commit_cursor(store, late, writer_context=CONTEXT)
        self.assertIsNone(store.get(late["id"]))
        commit_cursor(store, cursor, writer_context=CONTEXT)
        competing = make_technical_record("source_cursors", {**cursor["payload"],
            "entity_id": stable_id("cursor", "another")})
        with self.assertRaises(CursorBlocked):
            commit_cursor(store, competing, writer_context=CONTEXT)
        self.assertIsNone(store.get(competing["id"]))
        second = make_technical_record("source_cursors", {**cursor["payload"], "revision": 2,
            "previous_cursor_hash": cursor["hash"], "cursor_value": "fixture-next-2"})
        commit_cursor(store, second, writer_context=CONTEXT)
        with self.assertRaises(CursorBlocked):
            commit_cursor(store, cursor, writer_context=CONTEXT)

    def test_cursor_advances_only_after_exact_admitted_item_readback(self):
        from cloud.technical_cursor import commit_cursor, CursorBlocked
        store = Store()
        source, item, cursor = fixture_records()
        with self.assertRaises(CursorBlocked):
            commit_cursor(store, cursor, writer_context=CONTEXT)
        self.assertEqual(store.puts, [])
        admit_technical_record(store, source, writer_context=CONTEXT)
        admit_technical_record(store, item, writer_context=CONTEXT)
        commit_cursor(store, cursor, writer_context=CONTEXT)
        self.assertEqual(store.get(cursor["id"]), cursor)
        count = len(store.puts)
        commit_cursor(store, cursor, writer_context=CONTEXT)
        self.assertEqual(len(store.puts), count)

    def test_read_failure_fences_run_instead_of_becoming_a_normal_block(self):
        from cloud.technical_cursor import commit_cursor
        store = Store()
        source, item, cursor = fixture_records()
        admit_technical_record(store, source, writer_context=CONTEXT)
        admit_technical_record(store, item, writer_context=CONTEXT)
        store.get = lambda _: (_ for _ in ()).throw(RuntimeError("read unavailable"))
        with self.assertRaises(AdmissionStorageError):
            commit_cursor(store, cursor, writer_context=CONTEXT)

    def test_poisoned_predecessor_fences_all_cursor_advancement(self):
        from cloud.technical_cursor import commit_cursor
        for corruption in ("source_identity", "source_link"):
            with self.subTest(corruption=corruption):
                store = Store()
                source, item, cursor = fixture_records()
                admit_technical_record(store, source, writer_context=CONTEXT)
                admit_technical_record(store, item, writer_context=CONTEXT)
                payload = dict(cursor["payload"])
                if corruption == "source_identity":
                    payload["source_identity"] = stable_id("source", "different")
                else:
                    del payload["technical_source_id"]
                poisoned = make_technical_record("source_cursors", payload)
                store.rows[poisoned["id"]] = poisoned  # adversarial pre-existing record
                second = make_technical_record("source_cursors", {**cursor["payload"], "revision": 2,
                    "previous_cursor_hash": poisoned["hash"], "cursor_value": "next-2"})
                before = list(store.puts)
                with self.assertRaises(AdmissionStorageError):
                    commit_cursor(store, second, writer_context=CONTEXT)
                self.assertEqual(store.puts, before)

    def test_cursor_cannot_advance_using_an_unrelated_source_item(self):
        from cloud.technical_cursor import commit_cursor, CursorBlocked
        store = Store()
        source = make_technical_record("technical_sources", {**sample(),
            "entity_id": stable_id("source_entity", "feed"),
            "source_identity": stable_id("source", "feed")})
        item = make_technical_record("technical_items", {**sample(),
            "technical_source_id": stable_id("source_entity", "different")})
        admit_technical_record(store, source, writer_context=CONTEXT)
        admit_technical_record(store, item, writer_context=CONTEXT)
        cursor = make_technical_record("source_cursors", {**sample(),
            "entity_id": stable_id("cursor", "feed"), "technical_source_id": source["payload"]["entity_id"],
            "evidence_refs": [source["id"], item["id"]]})
        with self.assertRaises(CursorBlocked):
            commit_cursor(store, cursor, writer_context=CONTEXT)
