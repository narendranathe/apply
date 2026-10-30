"""Synthetic transport checks for technical-source Notion creation boundary."""
import unittest

from cloud.notion import ConflictError, NotionStore
from cloud.records import make_record, validate_record
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
