"""Offline integration of technical envelopes with the existing Notion adapter.

FakeNotion implements the transport boundary; these tests make no live calls
and do not establish current Notion permissions or hosted operation.
"""
import copy
import unittest

from cloud.notion import AmbiguousWriteError, ConflictError, NotionStore
from cloud.records import make_record, stable_id, validate_record
from cloud.technical_records import TECHNICAL_KINDS, make_technical_record
from cloud.technical_admission import _records
from tests.test_notion import FakeNotion


def technical(kind):
    return make_technical_record(kind, {
        "entity_id": stable_id("technical_entity", kind),
        "revision": 1,
        "source_url": "https://example.org/engineering/post",
        "source_identity": stable_id("source", "fixture"),
        "author": None,
        "author_verified": False,
        "first_seen_at": "2026-10-29T12:00:00+00:00",
        "source_published_at": None,
        "observed_at": "2026-10-30T12:00:00+00:00",
        "content_hash": "a" * 64,
        "freshness": "fresh",
        "confidentiality": "private",
        "evidence_refs": [],
        "status": "observed",
        "actor": "agent:fixture",
    })


class TechnicalStorageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeNotion()
        self.store = NotionStore("fixture-token", transport=self.api,
                                 sleeper=lambda _: None, request_interval=0)

    def reserve_source(self, record):
        intent, aliases, _ = _records(record)
        for row in (intent, *aliases):
            self.store.put(row)

    def test_all_technical_kinds_round_trip_without_rewriting_history(self):
        history = make_record("outreach_events", {
            "message": "historic fixture", "occurred_at": "2026-10-01T12:00:00+00:00"
        }, "historical:fixture", "delivery_unknown")
        self.store.put(history)
        original_page = copy.deepcopy(self.api.pages[0])
        original_blocks = copy.deepcopy(self.api.blocks[original_page["id"]])

        for kind in sorted(TECHNICAL_KINDS):
            with self.subTest(kind=kind):
                record = technical(kind)
                if kind == "source_cursors":
                    # Cursor writes stay unavailable until complete feed-cohort
                    # verification exists, even for a valid generic envelope.
                    with self.assertRaises(ConflictError):
                        self.store.put(record)
                    continue
                if kind in {"technical_sources", "technical_items"}:
                    self.reserve_source(record)
                self.assertEqual(validate_record(self.store.put(record)), record)
                self.assertEqual(validate_record(self.store.get(record["id"])), record)
                self.assertEqual(validate_record(self.store.put(record)), record)

        self.assertEqual(len(self.api.writes()), 1 + len(TECHNICAL_KINDS) + 6 - 1)
        self.assertEqual(validate_record(self.store.get(history["id"])), history)
        self.assertEqual(self.api.pages[0], original_page)
        self.assertEqual(self.api.blocks[original_page["id"]], original_blocks)

    def test_changed_payload_at_same_immutable_revision_conflicts(self):
        record = technical("technical_items")
        self.reserve_source(record)
        self.store.put(record)
        changed = make_technical_record("technical_items", {
            **record["payload"], "content_hash": "b" * 64,
        })
        self.assertEqual(changed["id"], record["id"])
        with self.assertRaises(ConflictError):
            self.store.put(changed)
        self.assertEqual(len(self.api.writes()), 4)
        self.assertEqual(validate_record(self.store.get(record["id"])), record)

    def test_invalid_technical_envelope_never_reaches_transport(self):
        malformed = make_record("technical_items", {"entity_id": "fixture:incomplete"},
                                record_id="technical_items:malformed")
        with self.assertRaises(ValueError):
            self.store.put(malformed)
        self.assertEqual(self.api.writes(), [])

    def test_uncertain_create_is_not_blindly_retried_when_readback_absent(self):
        record = technical("technical_items")
        self.reserve_source(record)
        self.api.write_then_timeout = True
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(record)
        self.assertEqual(len(self.api.writes()), 4)
        # Query invisibility cannot prove that the timed-out create failed.
        self.api.hide_created = True
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(record)
        self.assertEqual(len(self.api.writes()), 4)


if __name__ == "__main__":
    unittest.main()
