"""Synthetic transport checks for technical-source Notion creation boundary."""
import unittest
from unittest.mock import patch

from cloud.notion import ConflictError, NotionStore
from cloud.records import make_record, stable_id, validate_record
from cloud.technical_records import make_technical_record
from cloud.technical_admission import _records, admit_technical_record
from tests.test_notion import FakeNotion
from tests.test_technical_storage_integration import technical


class TechnicalNotionGateTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeNotion()
        self.store = NotionStore("fixture-token", transport=self.api, sleeper=lambda _: None, request_interval=0)

    def test_source_creation_requires_exact_readback_of_intent_and_all_aliases(self):
        item = technical("technical_items")
        intent, aliases, _ = _records(item)
        with self.assertRaises(ConflictError):
            self.store.put(item)
        self.assertEqual(len(self.api.writes()), 0)
        self.store.put(intent)
        self.store.put(aliases[0])
        with self.assertRaises(ConflictError):
            self.store.put(item)
        self.assertEqual(len(self.api.writes()), 2)
        self.store.put(aliases[1])
        self.assertEqual(validate_record(self.store.put(item)), item)
        self.assertEqual(len(self.api.writes()), 4)

    def test_generic_unrelated_records_remain_writable(self):
        history = make_record("outreach_events", {"actor": "fixture"}, "fixture:history")
        self.assertEqual(validate_record(self.store.put(history)), history)
        self.assertEqual(len(self.api.writes()), 1)

    def test_cursor_cannot_be_created_without_admitted_source_evidence(self):
        cursor = technical("source_cursors")
        with self.assertRaises(ConflictError):
            self.store.put(cursor)
        self.assertEqual(self.api.writes(), [])

    def test_old_cursor_replay_is_not_treated_as_current_head(self):
        context = {"concurrency_group": "hiring-outreach-single-writer", "reservation_verified": True,
                   "orchestrator_verified": True, "previous_writer_reconciled": True, "run_id": "fixture"}
        source = technical("technical_sources")
        admit_technical_record(self.store, source, writer_context=context)
        base = technical("source_cursors")["payload"]
        first = make_technical_record("source_cursors", {**base,
            "entity_id": stable_id("source_cursor", source["payload"]["entity_id"]),
            "source_identity": source["payload"]["source_identity"],
            "technical_source_id": source["payload"]["entity_id"],
            "evidence_refs": [source["id"]], "cursor_value": "first"})
        second = make_technical_record("source_cursors", {**first["payload"], "revision": 2,
            "previous_cursor_hash": first["hash"], "cursor_value": "second"})
        # Simulate historical rows written by a future independently verified
        # cohort path; the actual creation guard remains closed in production.
        with patch("cloud.notion._require_verified_cursor_cohort"):
            self.store.put(first)
            self.store.put(second)
        count = len(self.api.writes())
        with self.assertRaises(ConflictError):
            self.store.put(first)
        self.assertEqual(validate_record(self.store.put(second)), second)
        self.assertEqual(len(self.api.writes()), count)

    def test_serialized_admission_uses_real_notion_adapter_and_exact_replay(self):
        source = technical("technical_items")
        context = {"concurrency_group": "hiring-outreach-single-writer", "reservation_verified": True,
                   "orchestrator_verified": True, "previous_writer_reconciled": True, "run_id": "fixture"}
        result = admit_technical_record(self.store, source, writer_context=context)
        self.assertEqual(result["status"], "admitted")
        self.assertEqual(validate_record(self.store.get(source["id"])), source)
        writes = len(self.api.writes())
        self.assertEqual(admit_technical_record(self.store, source, writer_context=context), result)
        self.assertEqual(len(self.api.writes()), writes)


if __name__ == "__main__":
    unittest.main()
