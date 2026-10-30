"""TI-02/TI-05: technical state participates in the shared handoff index."""
import unittest
from datetime import datetime, timedelta, timezone

from cloud.agent_index import build_index, _completed_action_outputs
from cloud.agent_protocol import AgentProtocol, ProtocolError
from cloud.records import make_record, stable_id
from cloud.technical_records import make_technical_record
from cloud.technical_admission import _records as admission_records
from tests.test_agent_protocol import FixtureStore, fixture, action_for, REGISTRY, NOW as PROTOCOL_NOW

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


def admitted(records):
    """Build complete synthetic admission chains for source/item fixtures."""
    result = {row["id"]: row for row in records}
    for row in records:
        if row["kind"] in {"technical_sources", "technical_items"}:
            intent, aliases, completion = admission_records(row)
            for member in (intent, *aliases, completion):
                if member["id"] in result and result[member["id"]] != member:
                    raise ValueError("conflicting synthetic admission")
                result[member["id"]] = member
    return list(result.values())


class TechnicalIndexTests(unittest.TestCase):
    def test_default_dossier_hides_untrusted_technical_event_fields(self):
        record = technical(actor="PRIVATE AUTHOR NOTES", source="PRIVATE SOURCE TEXT", status="PRIVATE STATUS TEXT")
        rows = build_index(admitted([record]), NOW, source_complete=True)
        store = FixtureStore()
        store.records.update({row["id"]: row for row in [record, *rows]})
        response = AgentProtocol(store, principal="reader", now=NOW).get_entity_context(record["payload"]["entity_id"])
        for secret in ("PRIVATE AUTHOR NOTES", "PRIVATE SOURCE TEXT", "PRIVATE STATUS TEXT"):
            self.assertNotIn(secret, str(response))

    def test_linked_contact_uncertainty_blocks_technical_task(self):
        item = technical()
        action = technical("technical_next_actions", "held", contact_id="contact:one", evidence_refs=[item["id"]])
        for state, uncertain in (("connected", True), ("pending", False)):
            with self.subTest(state=state):
                contact = make_record("contacts", {"contact_id": "contact:one", "source": "live", "state": state,
                    "verified_at": NOW.isoformat(), "live_state_verified": True, "identity_verified": True,
                    "history_complete": True, "delivery_unknown": uncertain}, "contact-row")
                rows = build_index(admitted([item, action, contact]), NOW, source_complete=True)
                task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == action["payload"]["entity_id"])
                self.assertEqual(task["status"], "blocked")
                self.assertIn("contact_action_held", task["blockers"])

    def test_mismatched_relation_type_cannot_enter_dossier(self):
        company = make_record("companies", {"company_id": "company:one"}, "company-row")
        contact = make_record("contacts", {"contact_id": "contact:one"}, "contact-row")
        technology = technical("technical_technologies", "technology:cdc", source_identity=stable_id("source", "cdc"))
        item = technical(contact_id="company:one", company_id=technology["payload"]["entity_id"], technology_id="contact:one")
        rows = build_index(admitted([company, contact, technology, item]), NOW, source_complete=True)
        manifest = next(row["payload"] for row in rows if row["kind"] == "index_manifests")
        self.assertFalse(manifest["complete"])
        for row in rows:
            if row["kind"] == "entity_projections" and row["payload"]["entity_id"] != item["payload"]["entity_id"]:
                self.assertNotIn(item["payload"]["entity_id"], row["payload"]["related_ids"].get("technical_item_ids", []))

    def test_technical_admission_proposal_requires_trusted_actor_and_selected_evidence(self):
        store, _ = fixture()
        client = AgentProtocol(store, REGISTRY, "codex", now=PROTOCOL_NOW)
        action = action_for(client, "admit_technical_source")
        candidate = technical(actor="codex", evidence_refs=["evidence:application"], status="proposed", freshness="unknown")
        action["candidate"] = candidate
        result = client.propose_action(action, ["evidence:application"])
        self.assertEqual(result["data"]["status"], "proposed")
        for index, invalid in enumerate((technical(actor="muse", evidence_refs=["evidence:application"]),
                        technical(actor="codex", evidence_refs=[]),
                        technical(actor="codex", evidence_refs=["evidence:other"]),
                        technical("publisher_intents", actor="codex", evidence_refs=["evidence:application"]),
                        technical(actor="codex", evidence_refs=["evidence:application"], status="verified"),
                        technical(actor="codex", evidence_refs=["evidence:application"], status="observed", freshness="unknown"),
                        technical(actor="codex", evidence_refs=["evidence:application"], status="proposed", freshness="fresh"),
                        technical(actor="codex", evidence_refs=["evidence:application"], status="proposed", freshness="unknown", author="Claimed Person", author_verified=True),
                        technical(actor="codex", evidence_refs=["evidence:application"], status="proposed", freshness="unknown", source_published_at=NOW.isoformat()))):
            with self.subTest(candidate=invalid["kind"], status=invalid["status"]), self.assertRaises(ProtocolError):
                client.propose_action({**action, "candidate": invalid, "idempotency_key": f"invalid:{index}"}, ["evidence:application"])

    def test_technical_admission_result_requires_exact_complete_chain(self):
        from cloud.technical_admission import _records
        candidate = technical(actor="codex", evidence_refs=["evidence:application"], status="proposed", freshness="unknown")
        intent, reservations, completion = _records(candidate)
        outputs = [candidate, intent, *reservations, completion]
        intake = {"id": "fixture-intake", "payload": {"actor": "codex", "action": {
            "type": "admit_technical_source", "candidate": candidate},
            "evidence_hashes": {"evidence:application": "a" * 64}}}
        execution = make_record("agent_action_executions", {}, "execution:fixture")
        result = {"execution_id": execution["id"], "outcome": "technical_source_admitted",
                  "output_hashes": {row["id"]: row["hash"] for row in outputs}}
        sources = {row["id"]: row for row in [execution, *outputs]}
        self.assertTrue(_completed_action_outputs(result, intake, sources))
        for missing in outputs:
            with self.subTest(missing=missing["kind"]):
                shortened = {**result, "output_hashes": {key: value for key, value in result["output_hashes"].items() if key != missing["id"]}}
                self.assertFalse(_completed_action_outputs(shortened, intake, sources))
        altered = {**intake, "payload": {**intake["payload"], "actor": "muse"}}
        self.assertFalse(_completed_action_outputs(result, altered, sources))

    def test_technical_reverse_dossier_and_cursor_handoff(self):
        company = make_record("companies", {"company_id": "company:one"}, "company-row")
        contact = make_record("contacts", {"contact_id": "contact:one", "company_id": "company:one"}, "contact-row")
        tech = technical("technical_technologies", "technology:cdc", source_identity=stable_id("source", "cdc"))
        initial = build_index([company, contact, tech], NOW, source_complete=True)
        previous = next(row for row in initial if row["kind"] == "index_manifests")
        item = technical(company_id="company:one", contact_id="contact:one",
                         technology_id=tech["payload"]["entity_id"], original_text="PRIVATE SOURCE TEXT")
        source_rows = admitted([company, contact, tech, item])
        rows = build_index(source_rows, NOW + timedelta(seconds=1), previous, source_complete=True)
        store = FixtureStore()
        store.records.update({row["id"]: row for row in [*source_rows, *rows]})
        first = AgentProtocol(store, principal="codex", now=NOW + timedelta(seconds=1))
        cursor = previous["payload"]["source_cursor"]
        changes = first.get_recent_changes(cursor)["data"]["changes"]
        changed = {change["entity_id"] for change in changes}
        for entity_id in ("company:one", "contact:one", tech["payload"]["entity_id"]):
            with self.subTest(entity_id=entity_id):
                self.assertIn(entity_id, changed)
                dossier = first.get_entity_context(entity_id)["data"]["entity"]
                self.assertIn(item["payload"]["entity_id"], dossier["related_ids"]["technical_item_ids"])
                self.assertNotIn("PRIVATE SOURCE TEXT", str(dossier))
        self.assertEqual(first.get_entity_context(tech["payload"]["entity_id"])["data"]["entity"]["entity_type"], "technology")
        with self.assertRaisesRegex(ProtocolError, "source_evidence_permission_denied"):
            first.get_entity_context("contact:one", 3, task_id="ungranted", evidence_ids=[item["id"]])
        second = AgentProtocol(store, principal="muse", now=NOW + timedelta(seconds=1))
        self.assertEqual(second.get_entity_context("company:one")["data"]["entity"]["related_ids"],
                         first.get_entity_context("company:one")["data"]["entity"]["related_ids"])
        self.assertEqual(store.writes, 0)

    def test_linked_revision_changes_dossier_cursor_without_old_links(self):
        company = make_record("companies", {"company_id": "company:one"}, "company-row")
        item = technical(company_id="company:one")
        first = build_index(admitted([company, item]), NOW, source_complete=True)
        previous = next(row for row in first if row["kind"] == "index_manifests")
        revised = technical(company_id="company:one", revision=2, content_hash="b" * 64)
        second = build_index(admitted([company, item, revised]), NOW + timedelta(seconds=1), previous, source_complete=True)
        changes = [row["payload"] for row in second if row["kind"] == "index_changes"]
        self.assertIn("company:one", {row["entity_id"] for row in changes})
        moved = technical(company_id="company:other", revision=2, content_hash="b" * 64)
        company2 = make_record("companies", {"company_id": "company:other"}, "company-row-2")
        changed = build_index(admitted([company, company2, item, moved]), NOW + timedelta(seconds=1), previous, source_complete=True)
        old = next(row["payload"] for row in changed if row["kind"] == "entity_projections" and row["payload"]["entity_id"] == "company:one")
        self.assertNotIn(item["payload"]["entity_id"], old["related_ids"].get("technical_item_ids", []))

    def test_reverse_links_are_bounded_and_do_not_change_suppression(self):
        contact = make_record("contacts", {"contact_id": "contact:one", "opt_out": True}, "contact-row")
        items = [technical(entity_id=f"item:{index}", contact_id="contact:one",
                           source_identity=stable_id("source", index),
                           source_url=f"https://example.com/item/{index}") for index in range(25)]
        rows = build_index(admitted([contact, *items]), NOW, source_complete=True)
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections"
                          and row["payload"]["entity_id"] == "contact:one")
        self.assertTrue(projection["suppression"]["suppressed"])
        self.assertEqual(len(projection["related_ids"]["technical_item_ids"]), 20)
        self.assertTrue(projection["technical_links_truncated"])
        self.assertFalse(any(row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == "contact:one" for row in rows))

    def test_ti02_technical_item_is_indexed_for_agent_handoff(self):
        source = technical()
        rows = build_index(admitted([source]), NOW, source_complete=True)
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
        sources = [technical(kind, entity_id="entity:" + kind) for kind in kinds if kind != "source_cursors"]
        source = next(row for row in sources if row["kind"] == "technical_sources")
        cursor_payload = {**technical("source_cursors")["payload"],
            "entity_id": stable_id("source_cursor", source["payload"]["entity_id"]),
            "source_identity": source["payload"]["source_identity"],
            "technical_source_id": source["payload"]["entity_id"],
            "evidence_refs": [source["id"]], "cursor_value": "fixture-cursor",
            "previous_cursor_hash": None}
        sources.append(make_technical_record("source_cursors", cursor_payload))
        sources.append(technical("technical_items", entity_id="entity:technical_items", revision=2))
        rows = build_index(admitted(sources), NOW, source_complete=True)
        manifest = next(row["payload"] for row in rows if row["kind"] == "index_manifests")
        projections = {row["payload"]["entity_id"]: row["payload"] for row in rows if row["kind"] == "entity_projections"}
        self.assertTrue(manifest["complete"])
        self.assertEqual(set(projections), {row["payload"]["entity_id"] for row in sources})
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
        rows = build_index(admitted([item, opportunity]), NOW, source_complete=True)
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
        rows = build_index(admitted([item, action, contact]), NOW, source_complete=True)
        task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks"
                    and row["payload"]["entity_id"] == action["payload"]["entity_id"])
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["executor"], "human")
        self.assertNotEqual(task["type"], "research")
        opted_out = make_record("contacts", {"contact_id": "contact:one", "opt_out": True}, "contact-row")
        rows = build_index(admitted([item, action, opted_out]), NOW, source_complete=True)
        self.assertFalse(any(row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == action["payload"]["entity_id"] for row in rows))
        company = make_record("companies", {"company_id": "company:one", "opt_out": True}, "company-row")
        contact = make_record("contacts", {"contact_id": "contact:one", "company_id": "company:one"}, "contact-row")
        rows = build_index(admitted([item, action, contact, company]), NOW, source_complete=True)
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
        self.assertIn("technical_alias_conflict", {item["reason"] for item in manifest["conflicts"]})

        claimed = technical("technical_next_actions", "next:claim", status="completed",
                            confirmation_evidence={"verified": True, "outcome": "message_sent_confirmed",
                                                   "event_id": "spoof", "evidence_url": "https://example.com/proof",
                                                   "confirmed_at": NOW.isoformat()})
        rows = build_index(admitted([claimed]), NOW, source_complete=True)
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections")
        self.assertNotEqual(projection["state"], "completed")
        self.assertEqual(projection["confidence"], "unverified")
        self.assertIsNone(projection["last_verified_action"])

    def test_technical_record_cannot_spoof_contact_verification(self):
        claimed = technical(source="live", verified_at=NOW.isoformat(),
                            live_state_verified=True, identity_verified=True, history_complete=True)
        rows = build_index(admitted([claimed]), NOW, source_complete=True)
        projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections")
        self.assertEqual(projection["confidence"], "unverified")
        self.assertIsNone(projection["verified_at"])

    def test_explicit_nonfresh_technical_evidence_blocks_review(self):
        for freshness in ("stale", "unknown", "historical"):
            with self.subTest(freshness=freshness):
                item = technical(freshness=freshness)
                action = technical("technical_next_actions", "next:nonfresh", evidence_refs=[item["id"]])
                rows = build_index(admitted([item, action]), NOW, source_complete=True)
                projection = next(row["payload"] for row in rows if row["kind"] == "entity_projections"
                                  and row["payload"]["entity_id"] == item["payload"]["entity_id"])
                task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks")
                self.assertEqual(projection["freshness"], freshness)
                self.assertEqual(task["status"], "blocked")

    def test_ti05_missing_company_and_partial_snapshot_block_review(self):
        item = technical()
        action = technical("technical_next_actions", "next:missing", company_id="company:missing", evidence_refs=[item["id"]])
        rows = build_index(admitted([item, action]), NOW, source_complete=True)
        task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks" and row["payload"]["entity_id"] == action["payload"]["entity_id"])
        self.assertEqual(task["status"], "blocked")
        self.assertIn("company_context_missing", task["blockers"])
        safe_action = technical("technical_next_actions", "next:safe", evidence_refs=[item["id"]])
        rows = build_index(admitted([item, safe_action]), NOW, source_complete=False)
        task = next(row["payload"] for row in rows if row["kind"] == "agent_tasks")
        self.assertEqual(task["status"], "blocked")
        self.assertIn("source_snapshot_incomplete", task["blockers"])
