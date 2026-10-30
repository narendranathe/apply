"""Synthetic transport checks for technical-source Notion creation boundary."""
import unittest

from cloud.notion import ConflictError, NotionStore
from cloud.records import make_record, validate_record
from cloud.technical_admission import _records
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


if __name__ == "__main__":
    unittest.main()
