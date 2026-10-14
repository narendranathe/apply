"""Synthetic agent handoff, privacy and intake regression fixtures only."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from cloud.agent_protocol import AgentProtocol, ProtocolError
from cloud.records import content_hash, make_record
from scripts.agent_gateway import Gateway, load_registry

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
REGISTRY = {
    "codex": {"allow_source_evidence": True, "capabilities": ["read_summarize", "research_propose", "prepare_application", "execute_permitted_application", "read_summarize"]},
    "muse": {"allow_source_evidence": True, "capabilities": ["read_summarize", "research_propose", "read_summarize"]},
    "future": {"capabilities": ["read_summarize", "research_propose", "ingest_verified_conversation"]},
}


class FixtureStore:
    def __init__(self):
        self.records = {}
        self.calls, self.writes, self.fail = [], 0, False

    def add(self, kind, payload, identity, status="observed"):
        record = make_record(kind, copy.deepcopy(payload), record_id=identity, status=status)
        self.records[identity] = record
        return record

    def get(self, identity):
        self.calls.append(("get", identity))
        if self.fail:
            raise RuntimeError("private fixture data must not leak")
        return copy.deepcopy(self.records.get(identity))

    def query(self, kind=None):
        self.calls.append(("query", kind))
        return [copy.deepcopy(r) for r in self.records.values() if kind is None or r["kind"] == kind]

    def latest_index_manifest(self):
        self.calls.append(("latest", "index_manifests"))
        if self.fail:
            raise RuntimeError("private fixture data must not leak")
        manifests = [r for r in self.records.values() if r["kind"] == "index_manifests"]
        return copy.deepcopy(max(manifests, key=lambda r: r["payload"]["generated_at"])) if manifests else None

    def put(self, record):
        self.writes += 1
        raise AssertionError("Agents must not write operational state")


def fixture(state="submission_eligible", *, revision=1):
    store = FixtureStore()
    store.add("application_receipts", {"receipt_id": "fixture-receipt", "body": "SYNTHETIC PRIVATE SOURCE"}, "evidence:application")
    store.add("conversation_events", {"event_id": "reply", "contact_id": "contact:recruiter", "type": "received",
        "status": "verified", "occurred_at": NOW.isoformat(), "body": "SYNTHETIC PRIVATE RECRUITER REPLY",
        "evidence_refs": ["evidence:reply"]}, "event:reply")
    store.add("conversation_sources", {"body": "SYNTHETIC PRIVATE RECRUITER REPLY", "source": "official_member_export"}, "evidence:reply")
    job = store.add("entity_projections", {"entity_id": "job:one", "entity_type": "job", "state": state,
        "application_state": state, "verified_at": NOW.isoformat(), "history_complete": True,
        "source_complete": True, "evidence_refs": ["evidence:application"], "version": revision,
        "next_action": "review_hiring_team_outreach" if state == "applied_verified" else "submit_application"}, "projection:job:v" + str(revision))
    contact = store.add("entity_projections", {"entity_id": "contact:recruiter", "entity_type": "contact", "state": "replied",
        "verified_at": NOW.isoformat(), "history_complete": True, "source_complete": True,
        "evidence_refs": ["evidence:reply"], "recent_event_ids": ["event:reply"], "version": revision,
        "last_verified_action": {"type": "received", "body": "MUST NOT BE DISCLOSED"},
        "next_action": "review_recruiter_reply", "suppression": {"suppressed": False}}, "projection:contact:v" + str(revision))
    task_type = "review_outreach" if state == "applied_verified" else "submit_application"
    task_cap = "research_propose" if state == "applied_verified" else "execute_permitted_application"
    store.add("agent_tasks", {"task_id": "task:application", "entity_id": "job:one", "type": task_type,
        "status": "ready", "priority": 20, "allowed_capabilities": [task_cap], "executor": "human" if state == "applied_verified" else "central_dispatcher",
        "evidence_refs": ["evidence:application"], "expected_projection_hash": job["hash"]}, "task:application", "ready")
    manifest = store.add("index_manifests", {"generated_at": (NOW + timedelta(seconds=revision - 1)).isoformat(),
        "complete": True, "conflicts": [], "source_cursor": "cursor:" + str(revision),
        "projections": {"job:one": job["id"], "contact:recruiter": contact["id"]}, "tasks": ["task:application"],
        "task_map": {"task:application": "task:application"}, "counts": {"jobs": 1, "contacts": 1},
        "campaign_brief": {"highest_priority": ["Review active job"]}, "changes": [
            {"cursor": "cursor:" + str(revision), "entity_id": "job:one", "status": state}],
        "action_results": {}}, "index:v" + str(revision))
    return store, manifest


def protocol(store, agent="codex", **kwargs):
    return AgentProtocol(store, registry=REGISTRY, principal=agent, now=NOW, **kwargs)


def action_for(agent, kind, entity="job:one", key="fixture-application"):
    data = agent.get_entity_context(entity, 1)["data"]
    return {"type": kind, "entity_id": entity, "idempotency_key": key,
            "expected_manifest_id": data["manifest_id"], "expected_projection_hash": data["projection_hash"]}


class AgentProtocolTests(unittest.TestCase):
    def test_three_agents_handoff_without_chat_history(self):
        store, manifest = fixture()
        codex = protocol(store)
        submit = action_for(codex, "submit_application")
        proposed = codex.propose_action(submit, ["evidence:application"])["data"]
        self.assertEqual(proposed["status"], "proposed")
        self.assertEqual(store.writes, 0)
        # Fixture represents central-writer receipt validation and immutable
        # append/publish; the agent never performs this operational write.
        submitted_store, _ = fixture("applied_verified")
        submitted_store.add("action_results", {"action_id": proposed["action_id"], "verified": True,
            "outcome": "applied_verified", "evidence_refs": ["evidence:application"]}, "result:submit", "applied_verified")
        submitted_store.records[manifest["id"]]["payload"]["action_results"] = {proposed["action_id"]: "result:submit"}
        payload = submitted_store.records[manifest["id"]]["payload"]
        submitted_store.records[manifest["id"]]["hash"] = content_hash(payload)
        muse = protocol(submitted_store, "muse")
        brief = muse.get_campaign_brief()
        self.assertLessEqual(brief["usage"]["estimated_input_tokens"], 2000)
        self.assertEqual(muse.get_application_state("job:one")["data"]["entity"]["state"], "applied_verified")
        tasks = muse.get_ready_tasks("muse", ["research_propose"])["data"]["tasks"]
        self.assertEqual(tasks[0]["type"], "review_outreach")
        self.assertEqual(tasks[0]["executor"], "human")
        self.assertEqual(muse.get_action_result(proposed["action_id"])["data"]["status"], "applied_verified")
        with self.assertRaisesRegex(ProtocolError, "application_not_submission_eligible"):
            # Even a new authorized submitter cannot repeat the handled job.
            new_codex = protocol(submitted_store)
            new_codex.propose_action(action_for(new_codex, "submit_application", key="again"), ["evidence:application"])
        third = protocol(submitted_store, "future")
        observation = action_for(third, "conversation_observed", "contact:recruiter", "reply-observation")
        observation.update({"occurred_at": NOW.isoformat(), "outcome": "recruiter_request"})
        self.assertEqual(third.report_observed_outcome(observation, ["evidence:reply"])["data"]["status"], "proposed")
        # A central derived task prioritizes the verified reply above prospecting.
        submitted_store.add("agent_tasks", {"task_id": "task:reply", "entity_id": "contact:recruiter", "type": "review_outreach",
            "reason": "recruiter_request", "priority": 1, "status": "ready", "executor": "human",
            "allowed_capabilities": ["research_propose"], "evidence_refs": ["evidence:reply"]}, "task:reply", "ready")
        payload["tasks"] = ["task:reply", "task:application"]
        submitted_store.records[manifest["id"]]["hash"] = content_hash(payload)
        fresh_third = protocol(submitted_store, "future")
        self.assertEqual(fresh_third.get_ready_tasks()["data"]["tasks"][0]["reason"], "recruiter_request")
        self.assertEqual(submitted_store.writes, 0)

    def test_actual_publisher_payloads_handoff_and_selected_change_page(self):
        from cloud.agent_index import build_index
        store = FixtureStore()
        store.add("jobs", {"job_id": "job:published", "company": "Fixture Company", "requisition_id": "R1",
            "url": "https://careers.fixture.test/jobs/R1", "observed_at": NOW.isoformat()}, "source:job")
        store.add("applications", {"application_id": "application:published", "job_id": "job:published",
            "company": "Fixture Company", "requisition_id": "R1", "revision": 1, "verified_at": NOW.isoformat(),
            "receipt": {"receipt_id": "fixture-receipt", "confirmed_at": NOW.isoformat(), "evidence_url": "https://careers.fixture.test/receipt/R1",
                        "http_status": 200, "status": "applied_verified"}}, "source:application", "applied_verified")
        store.add("contacts", {"contact_id": "contact:published", "source": "user_confirmed", "state": "connected",
            "live_state_verified": True, "identity_verified": True, "history_complete": True,
            "verified_at": NOW.isoformat()}, "source:contact", "connected")
        store.add("conversation_events", {"contact_id": "contact:published", "actor": "manual", "source": "user_confirmed",
            "occurred_at": NOW.isoformat(), "body": "SYNTHETIC PRIVATE CONFIRMED REPLY",
            "confirmation_evidence": {"event_id": "fixture-reply", "verified": True, "confirmed_at": NOW.isoformat(),
                "evidence_url": "https://evidence.fixture.test/reply", "outcome": "recruiter_reply_confirmed"}}, "source:reply", "verified")
        rows = build_index(list(store.records.values()), NOW, source_complete=True)
        store.records.update({row["id"]: row for row in rows})
        agent = protocol(store, "muse")
        self.assertEqual(agent.get_application_state("job:published")["data"]["entity"]["state"], "applied_verified")
        tasks = agent.get_ready_tasks(limit=2)["data"]["tasks"]
        self.assertEqual(tasks[0]["entity_id"], "contact:published")
        self.assertEqual(tasks[0]["executor"], "human")
        self.assertEqual(tasks[0]["type"], "review_outreach")
        self.assertNotIn("SYNTHETIC PRIVATE", json.dumps(tasks))
        context = agent.get_entity_context("contact:published", 2)["data"]
        proposal = {"type": tasks[0]["type"], "entity_id": tasks[0]["entity_id"], "task_id": tasks[0]["task_id"],
            "idempotency_key": "published-human-reply", "expected_manifest_id": context["manifest_id"],
            "expected_projection_hash": context["projection_hash"]}
        self.assertEqual(agent.propose_action(proposal, tasks[0]["evidence_refs"])["data"]["status"], "proposed")
        full = agent.get_entity_context("contact:published", 3, task_id=tasks[0]["task_id"], evidence_ids=tasks[0]["evidence_refs"])
        self.assertIn("SYNTHETIC PRIVATE CONFIRMED REPLY", json.dumps(full))
        changes = agent.get_recent_changes(limit=1)
        self.assertEqual(len(changes["data"]["changes"]), 1)
        self.assertEqual(changes["usage"]["store_calls"], 1)
        self.assertTrue(changes["data"]["has_more"])
        next_page = agent.get_recent_changes(changes["data"]["next_cursor"], limit=1)
        self.assertEqual(len(next_page["data"]["changes"]), 1)
        self.assertEqual(next_page["usage"]["store_calls"], 1)
        self.assertEqual(store.writes, 0)

    def test_default_read_only_and_caller_labels_cannot_grant(self):
        store, _ = fixture()
        unknown = AgentProtocol(store, principal="unregistered", now=NOW)
        unknown.get_campaign_brief()
        self.assertEqual(unknown.get_ready_tasks(capabilities=["execute_permitted_application"])["data"]["tasks"], [])
        with self.assertRaisesRegex(ProtocolError, "agent_identity_mismatch"):
            unknown.get_ready_tasks(agent="codex")
        with self.assertRaisesRegex(ProtocolError, "capability_denied"):
            unknown.propose_action(action_for(unknown, "submit_application"), ["evidence:application"])
        with self.assertRaisesRegex(ProtocolError, "unsupported_action_type"):
            protocol(store, "muse").propose_action(action_for(protocol(store, "muse"), "linkedin_message", "contact:recruiter"), ["evidence:reply"])

    def test_trusted_registry_only_pinned_snapshot_and_actor_mismatch(self):
        store, manifest = fixture()
        agent = AgentProtocol(store, REGISTRY, "codex", now=NOW, manifest_id=manifest["id"])
        action = action_for(agent, "submit_application")
        with self.assertRaisesRegex(ProtocolError, "agent_identity_mismatch"):
            agent.propose_action({**action, "actor": "other-agent"}, ["evidence:application"])
        proposed = agent.propose_action({**action, "capabilities": ["administer_workflow"], "trusted_capabilities": ["administer_workflow"]}, ["evidence:application"])
        envelope_action = proposed["data"]["envelope"]["payload"]["action"]
        self.assertNotIn("capabilities", envelope_action)
        self.assertNotIn("trusted_capabilities", envelope_action)
        changed = copy.deepcopy(manifest["payload"])
        changed["generated_at"] = NOW.isoformat()
        changed["source_cursor"] = "cursor:new"
        # Fixture's newest-manifest selection uses timestamp; advance the live
        # clock as well so this remains healthy, while the pinned read is old.
        changed["generated_at"] = (NOW + timedelta(seconds=1)).isoformat()
        store.add("index_manifests", changed, "index:new")
        pinned = AgentProtocol(store, REGISTRY, "codex", manifest_id=manifest["id"], now=NOW + timedelta(seconds=1))
        self.assertEqual(pinned.get_campaign_brief()["data"]["manifest_id"], manifest["id"])
        with self.assertRaisesRegex(ProtocolError, "stale_manifest"):
            pinned.propose_action(action, ["evidence:application"])
        for kind in ("linkedin_message", "linkedin_invitation"):
            # Even a claimed official capability cannot activate LinkedIn.
            with self.assertRaisesRegex(ProtocolError, "unsupported_action_type"):
                agent.propose_action({**action, "type": kind, "capabilities": ["execute_linkedin_official"]}, ["evidence:application"])

    def test_stale_partial_conflicting_index_blocks_tasks_and_proposals(self):
        for update in ({"generated_at": (NOW - timedelta(days=2)).isoformat()}, {"complete": False}, {"conflicts": ["collision"]}):
            store, manifest = fixture()
            payload = store.records[manifest["id"]]["payload"]
            payload.update(update)
            store.records[manifest["id"]]["hash"] = content_hash(payload)
            agent = protocol(store)
            self.assertEqual(agent.get_ready_tasks()["data"]["tasks"], [])
            with self.assertRaisesRegex(ProtocolError, "index_not_eligible"):
                agent.propose_action(action_for(agent, "submit_application"), ["evidence:application"])

    def test_missing_conflicted_record_and_failed_writer_fail_closed(self):
        store, _ = fixture()
        del store.records["projection:job:v1"]
        with self.assertRaisesRegex(ProtocolError, "entity_projection_missing"):
            protocol(store).get_application_state("job:one")
        store, _ = fixture()
        store.records["projection:job:v1"]["hash"] = "corrupt"
        with self.assertRaisesRegex(ProtocolError, "authoritative_record_unavailable_or_conflicted"):
            protocol(store).get_application_state("job:one")
        store.fail = True
        gateway = Gateway(protocol(store))
        response = gateway.handle({"operation": "get_campaign_brief"})
        self.assertEqual(response["error"]["code"], "authoritative_index_unavailable_or_conflicted")
        self.assertNotIn("private fixture", json.dumps(response))
        store, _ = fixture()
        result = protocol(store).get_action_result("unpersisted-attempt")["data"]
        self.assertEqual(result["status"], "unacknowledged")
        self.assertFalse(result["retry_allowed"])
        agent = protocol(store)
        action = action_for(agent, "submit_application")
        del store.records["evidence:application"]
        with self.assertRaisesRegex(ProtocolError, "indexed_record_missing"):
            agent.propose_action(action, ["evidence:application"])

    def test_suppression_history_unknown_and_stale_source_block(self):
        for change, code in (({"suppressed": True}, "entity_suppressed_or_blocked"),
            ({"history_complete": False}, "complete_contact_history_required"),
            ({"state": "delivery_unknown"}, "entity_suppressed_or_blocked"),
            ({"verified_at": (NOW - timedelta(days=2)).isoformat()}, "entity_source_stale_or_unverified")):
            store, _ = fixture()
            payload = store.records["projection:contact:v1"]["payload"]
            payload.update(change)
            store.records["projection:contact:v1"]["hash"] = content_hash(payload)
            agent = protocol(store, "muse")
            with self.assertRaisesRegex(ProtocolError, code):
                agent.propose_action(action_for(agent, "review_outreach", "contact:recruiter"), ["evidence:reply"])
        for state in ("submission_unknown", "submitting", "applied_verified"):
            store, _ = fixture(state)
            agent = protocol(store)
            with self.assertRaises(ProtocolError):
                agent.propose_action(action_for(agent, "submit_application"), ["evidence:application"])

    def test_idempotent_proposals_and_changed_intent_conflict(self):
        store, _ = fixture()
        agent = protocol(store)
        action = action_for(agent, "submit_application")
        first = agent.propose_action(action, ["evidence:application"])["data"]["envelope"]
        second = agent.propose_action(action, ["evidence:application"])["data"]["envelope"]
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ProtocolError, "idempotency_key_conflict"):
            agent.propose_action({**action, "reason": "changed intent"}, ["evidence:application"])
        self.assertEqual(store.writes, 0)

    def test_context_metadata_privacy_exact_evidence_is_scoped(self):
        store, manifest = fixture()
        agent = protocol(store, "muse")
        response = agent.get_interaction_history("contact:recruiter")
        self.assertNotIn("SYNTHETIC PRIVATE", json.dumps(response))
        self.assertNotIn("MUST NOT", json.dumps(response))
        with self.assertRaisesRegex(ProtocolError, "specific_task_and_evidence_selection_required"):
            agent.get_entity_context("contact:recruiter", 3)
        with self.assertRaisesRegex(ProtocolError, "evidence_task_outside_current_index"):
            agent.get_entity_context("contact:recruiter", 3, task_id="evidence:reply", evidence_ids=["evidence:reply"])
        with self.assertRaisesRegex(ProtocolError, "evidence_task_identity_mismatch"):
            agent.get_entity_context("contact:recruiter", 3, task_id="task:application", evidence_ids=["evidence:reply"])
        task = store.add("agent_tasks", {"task_id": "task:reply", "entity_id": "contact:recruiter", "evidence_refs": ["evidence:reply"]}, "task:reply")
        payload = store.records[manifest["id"]]["payload"]
        payload["task_map"][task["id"]] = task["id"]
        store.records[manifest["id"]]["hash"] = content_hash(payload)
        agent = protocol(store, "muse")
        full = agent.get_entity_context("contact:recruiter", 3, task_id=task["id"], evidence_ids=["evidence:reply"])
        self.assertIn("SYNTHETIC PRIVATE RECRUITER REPLY", json.dumps(full))
        with self.assertRaisesRegex(ProtocolError, "evidence_outside_selected_task"):
            agent.get_entity_context("contact:recruiter", 3, task_id=task["id"], evidence_ids=["evidence:application"])
        with self.assertRaisesRegex(ProtocolError, "source_evidence_permission_denied"):
            protocol(store, "future").get_entity_context("contact:recruiter", 3, task_id=task["id"], evidence_ids=["evidence:reply"])

    def test_incremental_changes_and_bounded_calls_usage(self):
        store, _ = fixture()
        agent = protocol(store)
        first = agent.get_campaign_brief()
        tasks = agent.get_ready_tasks(limit=1)
        state = agent.get_application_state("job:one")
        self.assertEqual(first["usage"]["store_calls"], 1)
        self.assertEqual(tasks["usage"]["store_calls"], 1)
        self.assertEqual(state["usage"]["store_calls"], 1)
        self.assertLess(sum(x["usage"]["context_bytes"] for x in (first, tasks, state)), 8000)
        self.assertIsNone(first["usage"]["actual_input_tokens"])
        agent.record_input_usage({"input_tokens": 777})
        self.assertEqual(agent.get_campaign_brief()["usage"]["actual_input_tokens"], 777)
        self.assertEqual(agent.get_recent_changes("cursor:1")["data"]["changes"], [])
        with self.assertRaisesRegex(ProtocolError, "cursor_expired_refresh_brief"):
            agent.get_recent_changes("unknown-old-cursor")
        self.assertFalse(any(call[0] == "query" for call in store.calls))

    def test_gateway_mcp_output_intake_is_metadata_and_private_staging(self):
        store, _ = fixture()
        agent = protocol(store)
        gateway = Gateway(agent)
        init = gateway.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        self.assertIsNone(gateway.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(len(gateway.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]), 9)
        action = action_for(agent, "submit_application")
        action["body"] = "SYNTHETIC PRIVATE REQUEST"
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "propose_action", "arguments": {"action": action, "evidence": ["evidence:application"]}}}
        result = gateway.handle(request)
        self.assertNotIn("SYNTHETIC PRIVATE", json.dumps(result))
        response = json.loads(result["result"]["content"][0]["text"])
        self.assertFalse(response["data"]["staged"])
        self.assertFalse(response["data"]["acknowledged"])
        with tempfile.TemporaryDirectory() as folder:
            private_gateway = Gateway(protocol(store), outbox=folder)
            private_gateway.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
            private_gateway.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
            result = private_gateway.handle(request)
            self.assertNotIn("SYNTHETIC PRIVATE", json.dumps(result))
            files = list(Path(folder).glob("*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            private_gateway.handle(request)
            self.assertEqual(len(list(Path(folder).glob("*.json"))), 1)
        with self.assertRaisesRegex(ProtocolError, "outbox_requires_private_temporary_directory"):
            Gateway(agent, outbox=str(Path(__file__).parent / "outbox"))

    def test_registry_private_owner_binding_and_no_arbitrary_principal(self):
        self.assertEqual(load_registry(None), ({}, "reader"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "registry.json"
            path.write_text(json.dumps({"schema": "apply-agent-registry/v1", "principal": "muse", "agents": REGISTRY}))
            path.chmod(0o600)
            registry, principal = load_registry(path)
            self.assertEqual(principal, "muse")
            self.assertNotIn("execute_permitted_application", registry[principal]["capabilities"])
            path.chmod(0o644)
            with self.assertRaisesRegex(ProtocolError, "trusted_registry_requires_private_owned_file"):
                load_registry(path)


if __name__ == "__main__":
    unittest.main()
