"""TI-02/TI-05: technical state participates in the shared handoff index."""
import unittest
from datetime import datetime, timezone

from cloud.agent_index import build_index
from cloud.records import make_record, stable_id
from cloud.technical_records import make_technical_record

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def technical(kind="technical_items", entity_id="technical-item:example", **overrides):
    payload = {
        "schema": "apply-technical-record/v1", "entity_id": stable_id("technical_entity", entity_id), "revision": 1,
        "source_url": "https://example.com/engineering/item", "source_identity": stable_id("source", "example/item"),
        "author": None, "author_verified": False, "first_seen_at": NOW.isoformat(),
        "source_published_at": None, "observed_at": NOW.isoformat(), "content_hash": "a" * 64,
        "freshness": "fresh", "confidentiality": "private", "evidence_refs": [],
        "status": "observed", "actor": "agent:fixture",
    }
    payload.update(overrides)
    if "technical_item_id" in payload:
        payload["technical_item_id"] = stable_id("technical_entity", payload["technical_item_id"])
    return make_technical_record(kind, payload)


class TechnicalIndexTests(unittest.TestCase):
    def test_ti02_technical_item_is_indexed_for_agent_handoff(self):
        source = technical()
        rows = build_index([source], NOW, source_complete=True)
        manifest = next(row["payload"] for row in rows if row["kind"] == "index_manifests")
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections")
        self.assertTrue(manifest["complete"])
        self.assertIn(source["payload"]["entity_id"], manifest["projections"])
        self.assertEqual(projection["entity_type"], "technical_item")
        self.assertEqual(projection["source_record_id"], source["id"])
        self.assertNotIn("content", projection)

    def test_ti02_all_technical_families_share_index_and_latest_revision(self):
        kinds = ("technical_sources", "technical_items", "engineering_observations", "technical_signals",
                 "technical_hypotheses", "engineering_opportunities", "contact_topic_relevances",
                 "engagement_intents", "engagement_outcomes", "content_drafts", "content_revisions",
                 "publisher_intents", "publisher_receipts", "source_cursors", "technical_next_actions")
        sources = [technical(kind, entity_id="entity:" + kind) for kind in kinds]
        sources.append(technical("technical_items", entity_id="entity:technical_items", revision=2))
        rows = build_index(sources, NOW, source_complete=True)
        manifest = next(row["payload"] for row in rows if row["kind"] == "index_manifests")
        projections = {row["payload"]["entity_id"]: row["payload"] for row in rows if row["kind"] == "entity_projections"}
        self.assertTrue(manifest["complete"])
        self.assertEqual(set(projections), {stable_id("technical_entity", "entity:" + kind) for kind in kinds})
        self.assertEqual(projections[stable_id("technical_entity", "entity:technical_items")]["source_record_id"], sources[-1]["id"])

    def test_ti05_malformed_technical_provenance_blocks_the_index(self):
        good = technical()
        bad_payload = {**good["payload"], "author": "Unverified Person"}
        bad = make_record(good["kind"], bad_payload, good["id"], good["status"])
        rows = build_index([bad], NOW, source_complete=True)
        manifest = next(row["payload"] for row in rows if row["kind"] == "index_manifests")
        self.assertFalse(manifest["complete"])
        self.assertIn("invalid_source_record", {item["reason"] for item in manifest["conflicts"]})

    def test_ti02_dossier_links_technical_sources_without_original_content(self):
        item = technical()
        opportunity = technical("engineering_opportunities", "opportunity:one",
                                technical_item_id="technical-item:example",
                                evidence_refs=[item["id"]], original_text="private fixture body")
        rows = build_index([item, opportunity], NOW, source_complete=True)
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections"
                          and row["payload"]["entity_id"] == opportunity["payload"]["entity_id"])
        self.assertEqual(projection["related_ids"]["technical_item_ids"], [item["payload"]["entity_id"]])
        self.assertIn(item["id"], projection["evidence_refs"])
        self.assertNotIn("private fixture body", str(rows))

    def test_ti05_technical_task_respects_contact_history_and_opt_out(self):
        item = technical()
        action = technical("technical_next_actions", "next:one", contact_id="contact:one",
                           technical_item_id="technical-item:example", evidence_refs=[item["id"]])
        contact = make_record("contacts", {"contact_id": "contact:one", "identity_verified": False}, "contact-row")
        rows = build_index([item, action, contact], NOW, source_complete=True)
        task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks"
                    and row["payload"]["entity_id"] == action["payload"]["entity_id"])
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["executor"], "human")
        self.assertNotEqual(task["type"], "research")
        opted_out = make_record("contacts", {"contact_id": "contact:one", "opt_out": True}, "contact-row")
        rows = build_index([item, action, opted_out], NOW, source_complete=True)
        self.assertFalse(any(row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == action["payload"]["entity_id"] for row in rows))
        company = make_record("companies", {"company_id": "company:one", "opt_out": True}, "company-row")
        contact = make_record("contacts", {"contact_id": "contact:one", "company_id": "company:one"}, "contact-row")
        rows = build_index([item, action, contact, company], NOW, source_complete=True)
        self.assertFalse(any(row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == action["payload"]["entity_id"] for row in rows))

    def test_ti05_private_extra_fields_do_not_enter_index_metadata(self):
        action = technical("technical_next_actions", "next:private", source="PRIVATE SOURCE TEXT",
                           company_id="PRIVATE RELATION TEXT", owner="PRIVATE OWNER TEXT",
                           state="PRIVATE STATE TEXT", evidence_refs=["PRIVATE_REFERENCE_TEXT"])
        rows = build_index([action], NOW, source_complete=True)
        output = str(rows)
        for private_text in ("PRIVATE SOURCE TEXT", "PRIVATE RELATION TEXT", "PRIVATE OWNER TEXT",
                             "PRIVATE STATE TEXT", "PRIVATE_REFERENCE_TEXT"):
            self.assertNotIn(private_text, output)

    def test_ti05_duplicate_source_aliases_block_and_claims_are_unverified(self):
        first = technical()
        second = technical(entity_id="technical-item:other")
        rows = build_index([first, second], NOW, source_complete=True)
        manifest = next(row["payload"] for row in rows if row["kind"] == "index_manifests")
        self.assertFalse(manifest["complete"])

        claimed = technical("technical_next_actions", "next:claim", status="completed",
                            confirmation_evidence={"verified": True, "outcome": "message_sent_confirmed",
                                                   "event_id": "spoof", "evidence_url": "https://example.com/proof",
                                                   "confirmed_at": NOW.isoformat()})
        rows = build_index([claimed], NOW, source_complete=True)
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections")
        self.assertNotEqual(projection["state"], "completed")
        self.assertEqual(projection["confidence"], "unverified")
        self.assertIsNone(projection["last_verified_action"])

    def test_technical_record_cannot_spoof_contact_verification(self):
        claimed = technical(source="live", verified_at=NOW.isoformat(),
                            live_state_verified=True, identity_verified=True, history_complete=True)
        rows = build_index([claimed], NOW, source_complete=True)
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections")
        self.assertEqual(projection["confidence"], "unverified")
        self.assertIsNone(projection["verified_at"])

    def test_explicit_nonfresh_technical_evidence_blocks_review(self):
        for freshness in ("stale", "unknown", "historical"):
            with self.subTest(freshness=freshness):
                item = technical(freshness=freshness)
                action = technical("technical_next_actions", "next:nonfresh", evidence_refs=[item["id"]])
                rows = build_index([item, action], NOW, source_complete=True)
                projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections"
                                  and row["payload"]["entity_id"] == item["payload"]["entity_id"])
                task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks")
                self.assertEqual(projection["freshness"], freshness)
                self.assertEqual(task["status"], "blocked")

    def test_ti05_missing_company_and_partial_snapshot_block_review(self):
        item = technical()
        action = technical("technical_next_actions", "next:missing", company_id="company:missing", evidence_refs=[item["id"]])
        rows = build_index([item, action], NOW, source_complete=True)
        task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == action["payload"]["entity_id"])
        self.assertEqual(task["status"], "blocked")
        self.assertIn("company_context_missing", task["blockers"])
        safe_action = technical("technical_next_actions", "next:safe", evidence_refs=[item["id"]])
        rows = build_index([item, safe_action], NOW, source_complete=False)
        task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks")
        self.assertEqual(task["status"], "blocked")
        self.assertIn("source_snapshot_incomplete", task["blockers"])
