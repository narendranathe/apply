"""No Notion cursor advances until a verified complete feed cohort exists."""
import unittest

from cloud.notion import ConflictError, NotionStore
from cloud.records import stable_id
from cloud.technical_admission import admit_technical_record
from cloud.technical_records import make_technical_record
from cloud.technical_cursor import CursorBlocked, commit_cursor
from tests.test_notion import FakeNotion
from tests.test_technical_storage_integration import technical


class CursorCohortGateTests(unittest.TestCase):
    def test_known_closed_gate_is_not_reported_as_an_ambiguous_write(self):
        api = FakeNotion()
        store = NotionStore("fixture", transport=api, sleeper=lambda _: None, request_interval=0)
        context = {"concurrency_group": "hiring-outreach-single-writer", "reservation_verified": True,
                   "orchestrator_verified": True, "previous_writer_reconciled": True, "run_id": "fixture"}
        source = technical("technical_sources")
        admit_technical_record(store, source, writer_context=context)
        cursor = make_technical_record("source_cursors", {**technical("source_cursors")["payload"],
            "entity_id": stable_id("source_cursor", source["payload"]["entity_id"]),
            "source_identity": source["payload"]["source_identity"],
            "technical_source_id": source["payload"]["entity_id"],
            "evidence_refs": [source["id"]], "cursor_value": "unverified-next"})
        before = len(api.writes())
        with self.assertRaisesRegex(CursorBlocked, "cursor_cohort_verification_unavailable"):
            commit_cursor(store, cursor, writer_context=context)
        self.assertEqual(len(api.writes()), before)

    def test_structurally_valid_source_only_cursor_cannot_advance_notion(self):
        api = FakeNotion()
        store = NotionStore("fixture", transport=api, sleeper=lambda _: None, request_interval=0)
        context = {"concurrency_group": "hiring-outreach-single-writer", "reservation_verified": True,
                   "orchestrator_verified": True, "previous_writer_reconciled": True, "run_id": "fixture"}
        source = technical("technical_sources")
        admit_technical_record(store, source, writer_context=context)
        cursor = make_technical_record("source_cursors", {**technical("source_cursors")["payload"],
            "entity_id": stable_id("source_cursor", source["payload"]["entity_id"]),
            "source_identity": source["payload"]["source_identity"],
            "technical_source_id": source["payload"]["entity_id"],
            "evidence_refs": [source["id"]], "cursor_value": "unverified-next"})
        before = len(api.writes())
        with self.assertRaises(ConflictError):
            store.put(cursor)
        self.assertEqual(len(api.writes()), before)

    def test_partial_item_list_cannot_substitute_for_verified_complete_cohort(self):
        api = FakeNotion()
        store = NotionStore("fixture", transport=api, sleeper=lambda _: None, request_interval=0)
        context = {"concurrency_group": "hiring-outreach-single-writer", "reservation_verified": True,
                   "orchestrator_verified": True, "previous_writer_reconciled": True, "run_id": "fixture"}
        source = technical("technical_sources")
        item = make_technical_record("technical_items", {**technical("technical_items")["payload"],
            "technical_source_id": source["payload"]["entity_id"]})
        admit_technical_record(store, source, writer_context=context)
        admit_technical_record(store, item, writer_context=context)
        cursor = make_technical_record("source_cursors", {**technical("source_cursors")["payload"],
            "entity_id": stable_id("source_cursor", source["payload"]["entity_id"]),
            "source_identity": source["payload"]["source_identity"],
            "technical_source_id": source["payload"]["entity_id"],
            "evidence_refs": [source["id"], item["id"]], "cursor_value": "unverified-next"})
        before = len(api.writes())
        with self.assertRaises(ConflictError):
            store.put(cursor)
        self.assertEqual(len(api.writes()), before)
