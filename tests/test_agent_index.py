"""Logical handoffs consume records, never another agent's conversation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from cloud.agent_index import CAPABILITIES, MAX_HISTORY, build_index
from cloud.records import content_hash, make_record, validate_record

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def record(kind, payload, identity, status="observed"):
    return make_record(kind, payload, identity, status)


def contact(identity="contact:one", **updates):
    payload = {"contact_id": identity, "name": "Test Recruiter", "company_id": "company:acme",
               "source": "verified_manual_observation", "state": "connected", "identity_verified": True,
               "history_complete": True, "live_state_verified": True, "verified_at": NOW.isoformat(),
               "revision": 1, **updates}
    return record("contacts", payload, identity + ":r" + str(payload["revision"]))


def event(outcome, identity, *, kind="conversation_events", **updates):
    payload = {"contact_id": "contact:one", "thread_id": "thread:one", "actor": "Naren",
               "source": "verified_manual_observation", "live_state_verified": True,
               "occurred_at": NOW.isoformat(), "confirmation_evidence": {
                   "event_id": identity, "verified": True, "outcome": outcome,
                   "confirmed_at": NOW.isoformat(), "evidence_url": "https://evidence.example.test/" + identity}, **updates}
    return record(kind, payload, identity)


def app(status="applied_verified", revision=1, identity="application:acme:req1", **updates):
    payload = {"application_id": identity, "company": "Acme", "company_id": "company:acme",
               "job_id": "job:acme:req1", "requisition_id": "req1", "revision": revision,
               "url": "https://jobs.lever.co/acme/req1", "observed_at": NOW.isoformat(),
               "actor": "Codex", "source": "cloud_application_lane", **updates}
    return record("applications", payload, identity + ":r" + str(revision), status)


def receipt():
    return {"status": "applied_verified", "receipt_id": "receipt-1", "http_status": 200,
            "evidence_url": "https://hire.lever.co/search?applicationId=receipt-1", "confirmed_at": NOW.isoformat()}


def job(**updates):
    return record("jobs", {"job_id": "job:acme:req1", "company": "Acme", "requisition_id": "req1",
                           "url": "https://jobs.lever.co/acme/req1?utm_source=feed", **updates}, "job-record:one")


def indexed(rows, *, now=NOW, previous=None, complete=True):
    return build_index(rows, now, previous, source_complete=complete)


def manifest(rows):
    return next(row for row in rows if row["kind"] == "index_manifests")


def projection(rows, identity):
    mapping = manifest(rows)["payload"]["projections"]
    return next(row["payload"] for row in rows if row["id"] == mapping[identity])


def tasks(rows, identity=None):
    return [row["payload"] for row in rows if row["kind"] == "agent_tasks" and (identity is None or row["payload"]["entity_id"] == identity)]


class AgentIndexTests(unittest.TestCase):
    def test_queued_current_task_is_hidden_without_verifying_business_outcome(self):
        source = job()
        initial = indexed([source])
        projection_before = projection(initial, "job:acme:req1")
        task_before = tasks(initial, "job:acme:req1")[0]
        projection_hash = content_hash(projection_before)
        action_id = "agent-intake:fixture"
        intake = record("agent_proposals", {"actor": "fixture-agent", "action": {"entity_id": "job:acme:req1", "type": "research"},
            "expected_projection_hash": projection_hash}, action_id, "proposed")
        reservation = record("agent_action_reservations", {"action_id": action_id, "intake_hash": intake["hash"],
            "semantic": {"kind": "agent_proposals", "entity_id": "job:acme:req1", "type": "research", "projection_hash": projection_hash}}, "reservation:fixture", "queued")
        queue = record("agent_execution_queue", {"action_id": action_id, "intake_hash": intake["hash"], "actor": "fixture-agent",
            "entity_id": "job:acme:req1", "action_type": "research", "expected_projection_hash": projection_hash,
            "external_effect": False}, "action-queue:" + action_id, "queued")
        ack = record("agent_action_results", {"action_id": action_id, "status": "queued", "actor": "fixture-agent",
            "external_effect": False}, "action-result:" + action_id, "queued")
        sources = [source, intake, reservation, queue, ack]
        queued = indexed(sources, now=NOW + timedelta(seconds=1), previous=manifest(initial))
        self.assertEqual(projection(queued, "job:acme:req1"), projection_before)
        current_task = tasks(queued, "job:acme:req1")[0]
        self.assertEqual(current_task["task_id"], task_before["task_id"])
        self.assertEqual(current_task["status"], "queued")
        self.assertEqual(current_task["queued_action_ids"], [action_id])
        summary = manifest(queued)["payload"]["campaign_brief"]
        self.assertEqual(summary["ready_tasks"], 0)
        self.assertEqual(summary["queued_tasks"], 1)
        self.assertEqual(summary["blocked_tasks"], 0)
        self.assertNotEqual(manifest(queued)["payload"]["source_cursor"], manifest(initial)["payload"]["source_cursor"])
        self.assertTrue(any(row["payload"].get("change") == "task_updated" for row in queued if row["kind"] == "index_changes"))
        repeated = indexed(sources, now=NOW + timedelta(seconds=2), previous=manifest(queued))
        self.assertEqual(manifest(repeated)["payload"]["changes"], [])
        self.assertEqual(manifest(repeated)["payload"]["source_cursor"], manifest(queued)["payload"]["source_cursor"])
        fresh_source = job(observed_at=(NOW + timedelta(seconds=2)).isoformat(), source="fresh_official_job")
        fresh = indexed([fresh_source, intake, reservation, queue, ack], now=NOW + timedelta(seconds=2), previous=manifest(queued))
        self.assertEqual(tasks(fresh, "job:acme:req1")[0]["status"], "open")

    def test_partial_or_unknown_dispatch_reservations_block_current_task(self):
        source = job()
        initial = indexed([source])
        digest = content_hash(projection(initial, "job:acme:req1"))
        intake = record("agent_proposals", {"actor": "fixture-agent", "action": {"entity_id": "job:acme:req1", "type": "research"},
            "expected_projection_hash": digest}, "agent-intake:partial", "proposed")
        reservation = record("agent_action_reservations", {"action_id": intake["id"], "intake_hash": intake["hash"],
            "semantic": {"kind": "agent_proposals", "entity_id": "job:acme:req1", "type": "research", "projection_hash": digest}}, "reservation:partial", "queued")
        queue = record("agent_execution_queue", {"action_id": intake["id"], "entity_id": "job:acme:req1", "action_type": "research",
            "expected_projection_hash": digest}, "action-queue:" + intake["id"], "queued")
        unknown = record("agent_action_reservations", {"semantic": {"entity_id": "job:acme:req1", "type": "research"}}, "reservation:unknown", "queued")
        for partial in ([intake], [reservation], [queue], [intake, reservation], [intake, reservation, queue], [unknown]):
            current = indexed([source, *partial], now=NOW + timedelta(seconds=1), previous=manifest(initial))
            task = tasks(current, "job:acme:req1")[0]
            self.assertEqual(task["status"], "blocked")
            self.assertIn("dispatch_intake_requires_reconciliation", task["blocking_conditions"])
            self.assertEqual(manifest(current)["payload"]["campaign_brief"]["ready_tasks"], 0)
            self.assertEqual(projection(current, "job:acme:req1")["state"], projection(initial, "job:acme:req1")["state"])

    def test_default_snapshot_is_incomplete_and_all_tasks_are_blocked(self):
        result = build_index([job()], NOW)
        self.assertFalse(manifest(result)["payload"]["complete"])
        self.assertTrue(all(item["status"] == "blocked" for item in tasks(result)))
        self.assertEqual(manifest(result)["payload"]["campaign_brief"]["ready_tasks"], 0)

    def test_snapshot_envelope_controls_completeness(self):
        for source in ({"hash": "snapshot", "complete": False, "conflicts": []},
                       {"hash": "snapshot", "complete": True, "conflicts": ["failed_readback"]}):
            result = build_index([contact()], NOW, source_snapshot_hash=source, source_complete=True)
            self.assertFalse(manifest(result)["payload"]["complete"])
            self.assertEqual(tasks(result)[0]["status"], "blocked")

    def test_pending_confirmation_suppresses_invitation_and_accepted_message(self):
        result = indexed([contact(state="uncontacted"), event("invitation_pending_confirmed", "pending-proof")])
        value = projection(result, "contact:one")
        self.assertEqual(value["state"], "pending")
        self.assertTrue(value["suppression"]["retry_blocked"])
        self.assertEqual(tasks(result, "contact:one")[0]["action"], "verify_invitation_status")
        self.assertFalse(any(item["type"] in {"linkedin_message", "linkedin_invitation"} for item in tasks(result)))

    def test_verified_receipt_suppresses_job_alias_and_selects_hiring_research(self):
        result = indexed([job(), app(receipt=receipt())])
        self.assertEqual(projection(result, "application:acme:req1")["state"], "applied_verified")
        self.assertEqual(projection(result, "job:acme:req1")["application_state"], "applied_verified")
        self.assertEqual(tasks(result, "application:acme:req1")[0]["action"], "research_hiring_team")
        self.assertFalse(tasks(result, "job:acme:req1"))
        self.assertFalse(any(item["type"] == "submit_application" for item in tasks(result)))

    def test_human_receipt_is_supported_without_claiming_a_browser_click(self):
        confirmation = {"schema": "human-application-confirmation/v1", "application_id": "application:acme:req1",
                        "user_confirmed": True, "actor": "Naren", "user_statement": "Employer acknowledged receipt.",
                        "source_record_hash": "a" * 64, "employer_message_id": "employer-receipt-1",
                        "evidence_url": "https://mail.google.com/mail/u/0/#inbox/receipt", "employer_acknowledged_at": NOW.isoformat()}
        result = indexed([app(status="applied", confirmation_evidence=confirmation)])
        self.assertEqual(projection(result, "application:acme:req1")["state"], "applied_verified")
        confirmation["user_confirmed"] = False
        result = indexed([app(status="applied", confirmation_evidence=confirmation)])
        self.assertEqual(projection(result, "application:acme:req1")["state"], "submission_unknown")

    def test_receipt_label_or_sloppy_evidence_is_uncertainty(self):
        for evidence in (None, "clicked submit", {"status": "applied_verified", "receipt_id": "r"},
                         {**receipt(), "confirmed_at": (NOW + timedelta(days=1)).isoformat()}):
            result = indexed([app(receipt=evidence)])
            value = projection(result, "application:acme:req1")
            self.assertEqual(value["state"], "submission_unknown")
            self.assertTrue(value["suppression"]["retry_blocked"])
            self.assertEqual(tasks(result)[0]["type"], "reconcile")

    def test_application_revisions_fold_and_old_receipt_remains_suppression(self):
        result = indexed([app(status="prepared", revision=2), app(receipt=receipt())])
        self.assertEqual(projection(result, "application:acme:req1")["state"], "applied_verified")
        self.assertEqual(projection(result, "application:acme:req1")["source_record_id"], "application:acme:req1:r2")

    def test_uncertain_submission_and_delivery_require_reconciliation(self):
        result = indexed([app(status="submitting", attempt={"started_at": NOW.isoformat()}), contact(delivery_unknown=True)])
        self.assertTrue(all(item["type"] == "reconcile" and item["status"] == "reconciliation" for item in tasks(result)))
        self.assertTrue(all(item["allowed_capabilities"] == ["research_propose"] for item in tasks(result)))

    def test_confirmed_recruiter_reply_prioritizes_human_response(self):
        result = indexed([contact(), event("recruiter_reply_confirmed", "reply")])
        self.assertEqual(projection(result, "contact:one")["state"], "reply_received")
        task = tasks(result, "contact:one")[0]
        self.assertEqual((task["action"], task["priority"], task["executor"]), ("prepare_human_reply", 100, "human"))
        self.assertEqual(task["allowed_capabilities"], ["research_propose"])

    def test_reply_missing_history_prioritizes_verification_without_outbound_draft(self):
        result = indexed([contact(history_complete=False), event("recruiter_reply_confirmed", "reply")])
        task = tasks(result, "contact:one")[0]
        self.assertEqual((task["action"], task["priority"]), ("verify_identity_and_history", 100))
        self.assertFalse(projection(result, "contact:one")["history_complete"])
        self.assertEqual(task["blocking_conditions"], [])

    def test_export_accepted_state_never_becomes_live(self):
        result = indexed([contact(source="linkedin_member_export", historical_only=True),
                          event("invitation_accepted_confirmed", "historical", historical_only=True, live_state_verified=False)])
        self.assertEqual(projection(result, "contact:one")["state"], "historical_observation")
        self.assertEqual(projection(result, "contact:one")["freshness"], "historical")
        self.assertEqual(tasks(result, "contact:one")[0]["action"], "verify_identity_and_history")

    def test_unknown_source_and_stale_history_cannot_create_conversation_draft(self):
        for updates in ({"source": None}, {"verified_at": (NOW - timedelta(hours=25)).isoformat()},
                        {"verified_at": (NOW + timedelta(hours=1)).isoformat()}, {"live_state_verified": False}):
            result = indexed([contact(**updates)])
            self.assertEqual(tasks(result, "contact:one")[0]["action"], "verify_identity_and_history")

    def test_planned_and_assigned_records_never_become_verified_action(self):
        result = indexed([contact(state="uncontacted", identity_verified=False, history_complete=False),
                          record("conversation_observations", {"contact_id": "contact:one", "action": "new_invitation", "actor": "Muse", "draft": "Test"}, "planned", "assigned"),
                          record("tasks", {"task_id": "old-task", "contact_id": "contact:one", "owner": "Muse"}, "old-task", "assigned")])
        value = projection(result, "contact:one")
        self.assertIsNone(value["last_verified_action"])
        self.assertNotEqual(value["state"], "pending")
        self.assertEqual(projection(result, "old-task")["state"], "assigned")

    def test_referral_offer_and_submission_are_distinct(self):
        for outcome, expected in (("referral_offered_confirmed", "review_referral_offer"),
                                  ("referral_submitted_confirmed", "review_referral_status")):
            row = event(outcome, outcome, kind="referrals", referral_id="referral:one")
            result = indexed([contact(), row])
            self.assertEqual(tasks(result, "referral:one")[0]["action"], expected)
            self.assertEqual(projection(result, "contact:one")["state"], "connected")

    def test_scheduled_interview_prioritizes_prep_for_exact_application(self):
        interview = event("interview_scheduled_confirmed", "interview-record", kind="interviews", interview_id="interview:one",
                          application_id="application:acme:req1", scheduled_at=NOW.isoformat())
        result = indexed([app(receipt=receipt()), interview])
        self.assertEqual(projection(result, "application:acme:req1")["state"], "interview")
        self.assertEqual(projection(result, "application:acme:req1")["next_action"]["action"], "prepare_interview")
        self.assertEqual(tasks(result, "interview:one")[0]["status"], "due")
        self.assertEqual(sum(item["action"] == "prepare_interview" for item in tasks(result)), 1)
        self.assertFalse(any(item["action"] == "research_hiring_team" for item in tasks(result)))

    def test_opt_out_persists_across_reimport_and_agents(self):
        prior = contact(opt_out=True)
        newer = contact(revision=2, opt_out=False, verified_at=(NOW + timedelta(seconds=1)).isoformat())
        result = indexed([prior, newer, event("recruiter_reply_confirmed", "reply")], now=NOW + timedelta(minutes=1))
        self.assertEqual(projection(result, "contact:one")["state"], "suppressed")
        self.assertEqual(projection(result, "thread:one")["state"], "suppressed")
        self.assertFalse(tasks(result, "contact:one"))
        self.assertFalse(tasks(result, "thread:one"))

    def test_company_opt_out_suppresses_related_contact(self):
        company = record("companies", {"company_id": "company:acme", "name": "Acme", "opt_out": True}, "company-record")
        result = indexed([company, contact()])
        self.assertTrue(projection(result, "contact:one")["suppressed"])
        self.assertFalse(tasks(result, "contact:one"))

    def test_source_conflicts_and_partial_imports_fail_closed(self):
        conflicting = contact(name="Different Person")
        for rows in ([contact(), conflicting], [app(), app(identity="application:acme:req1", receipt=receipt(), extra=True)],
                     [contact(), record("import_manifests", {"complete": False}, "partial", "partial")]):
            result = indexed(rows)
            self.assertFalse(manifest(result)["payload"]["complete"])
            self.assertTrue(manifest(result)["payload"]["conflicts"])
            self.assertTrue(all(item["status"] == "blocked" for item in tasks(result)))

    def test_ambiguous_application_revisions_fail_closed(self):
        first = app(status="prepared")
        second = record("applications", first["payload"], "conflicting-revision", "qualified")
        result = indexed([first, second])
        self.assertFalse(manifest(result)["payload"]["complete"])
        self.assertTrue(any("revision" in item["reason"] for item in manifest(result)["payload"]["conflicts"]))

    def test_provider_exception_does_not_create_submission_task(self):
        result = indexed([app(status="submission_eligible", packet={"qa": {"verified": True}, "resume_sha256": "a" * 64, "answers_hash": "b" * 64})])
        value = projection(result, "application:acme:req1")
        self.assertIn("permitted_submission_provider_unverified", value["blocking_conditions"])
        self.assertEqual(tasks(result)[0]["action"], "resolve_submission_exception")
        self.assertEqual(tasks(result)[0]["status"], "blocked")

    def test_employer_only_legacy_record_blocks_new_requisition_until_reconciled(self):
        legacy = app(status="prepared", requisition_id=None, employer_history_unresolved=True)
        result = indexed([legacy, job(requisition_id="different-req", url="https://jobs.lever.co/acme/different-req")])
        value = projection(result, "job:acme:req1")
        self.assertEqual(value["state"], "needs_reconciliation")
        self.assertIn("ambiguous_application_identity", value["blocking_conditions"])
        self.assertEqual(tasks(result, "job:acme:req1")[0]["type"], "reconcile")

    def test_malformed_confirmation_outcome_never_becomes_a_verified_reply(self):
        row = event("recruiter_reply_confirmed", "bad-event")
        payload = deepcopy(row["payload"])
        payload["confirmation_evidence"]["outcome"] = ["recruiter_reply_confirmed"]
        result = indexed([contact(), record(row["kind"], payload, row["id"])])
        self.assertNotEqual(projection(result, "contact:one")["state"], "reply_received")

    def test_same_receipt_claimed_by_two_applications_blocks_all_ready_work(self):
        result = indexed([app(receipt=receipt()), app(identity="application:acme:req2", requisition_id="req2", receipt=receipt())])
        self.assertFalse(manifest(result)["payload"]["complete"])
        self.assertTrue(any(item["reason"] == "verified_evidence_identity_conflict" for item in manifest(result)["payload"]["conflicts"]))
        self.assertTrue(all(item["status"] == "blocked" for item in tasks(result)))

    def test_validated_action_result_map_does_not_infer_external_completion(self):
        valid = record("agent_action_results", {"action_id": "proposal:one", "status": "completed", "evidence_refs": ["contact:one:r1"],
                                               "external_effect": False, "actor": "central_dispatcher"}, "action-result:proposal:one", "completed")
        invalid = record("agent_action_results", {"action_id": "proposal:two", "status": "completed", "external_effect": True}, "action-result:proposal:two", "completed")
        result = indexed([contact(state="uncontacted", history_complete=False), valid, invalid])
        self.assertEqual(manifest(result)["payload"]["action_results"], {"proposal:one": valid["id"]})
        self.assertIsNone(projection(result, "contact:one")["last_verified_action"])
        self.assertNotEqual(projection(result, "contact:one")["state"], "message_sent")

    def test_verified_routine_eligibility_requires_no_per_application_approval(self):
        eligible = app(status="submission_eligible",
                       job={"open": True, "schema_verified": True, "employer_identity_verified": True, "requisition_id": "req1", "observed_at": NOW.isoformat()},
                       qualification={"qualified": True}, screening_evidence={"sponsorship_source": "https://evidence.test/sponsorship"},
                       permission_evidence={"permitted": True, "scope": "application_submission", "evidence_url": "https://acme.test/permission",
                                            "verified_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat()},
                       packet={"qa": {"verified": True}, **{key: "a" * 64 for key in ("resume_sha256", "source_sha256", "ground_truth_sha256", "answers_hash")}})
        result = indexed([eligible])
        task = tasks(result)[0]
        self.assertEqual((task["type"], task["executor"], task["status"]), ("submit_application", "central_dispatcher", "open"))
        self.assertEqual(task["allowed_capabilities"], ["execute_permitted_application"])
        self.assertNotIn("approved", json.dumps(result))

    def test_semantic_tasks_and_projection_ids_survive_repeated_runs(self):
        source = [contact(), app(receipt=receipt())]
        first = indexed(source)
        second = indexed(source + source, now=NOW + timedelta(minutes=10), previous=manifest(first))
        self.assertEqual(manifest(first)["payload"]["projections"], manifest(second)["payload"]["projections"])
        self.assertEqual(manifest(first)["payload"]["tasks"], manifest(second)["payload"]["tasks"])
        self.assertEqual(manifest(first)["payload"]["source_cursor"], manifest(second)["payload"]["source_cursor"])
        self.assertEqual(manifest(second)["payload"]["changes"], [])
        self.assertTrue(all(validate_record(row) == row for row in first + second))

    def test_index_is_pure_and_never_changes_source_packet_or_evidence(self):
        source = [contact(), app(receipt=receipt())]
        before = deepcopy(source)
        indexed(source)
        self.assertEqual(source, before)

    def test_incremental_changes_have_unique_cursors_and_bounded_history(self):
        source = [contact()]
        source += [event("message_sent_confirmed", "sent-" + str(index), occurred_at=(NOW - timedelta(minutes=index)).isoformat()) for index in range(30)]
        first = indexed(source)
        value = projection(first, "contact:one")
        self.assertEqual(len(value["history_ids"]), MAX_HISTORY)
        self.assertTrue(value["history_truncated"])
        changes = [row["payload"] for row in first if row["kind"] == "index_changes"]
        self.assertEqual(len({item["cursor"] for item in changes}), len(changes))
        self.assertEqual(changes[-1]["cursor"], manifest(first)["payload"]["source_cursor"])
        def private_keys(value):
            if isinstance(value, dict):
                return set(value) & {"text", "body", "message", "answers", "receipt"} or any(private_keys(item) for item in value.values())
            return any(private_keys(item) for item in value) if isinstance(value, list) else False
        self.assertFalse(private_keys(manifest(first)))

    def test_daily_brief_and_task_window_are_bounded(self):
        result = indexed([contact("contact:" + str(index)) for index in range(100)])
        value = manifest(result)["payload"]
        self.assertEqual(len(value["tasks"]), 40)
        self.assertTrue(value["tasks_truncated"])
        self.assertEqual(len(value["campaign_brief"]["priorities"]), 12)
        self.assertLess(len(json.dumps(value["campaign_brief"]).encode("utf-8")), 8000)
        self.assertTrue(all(set(item["allowed_capabilities"]) <= CAPABILITIES for item in tasks(result)))

    def test_three_agents_select_next_work_using_only_authoritative_index(self):
        # Codex records a receipt. Muse starts with only the manifest and record
        # IDs, not Codex's chat. A third agent receives a confirmed reply event.
        first = indexed([job(), app(receipt=receipt()), contact()])
        private_store = {row["id"]: row for row in first}
        second_manifest = private_store[manifest(first)["id"]]["payload"]
        second_context = private_store[second_manifest["projections"]["application:acme:req1"]]["payload"]
        second_task = private_store[second_context["next_action"]["task_id"]]["payload"]
        self.assertEqual((second_context["state"], second_task["action"]), ("applied_verified", "research_hiring_team"))
        third = indexed([job(), app(receipt=receipt()), contact(), event("recruiter_reply_confirmed", "reply")], previous=manifest(first))
        self.assertEqual(tasks(third, "contact:one")[0]["action"], "prepare_human_reply")
        self.assertFalse(any(item["type"] in {"submit_application", "linkedin_invitation"} for item in tasks(third)))

    def test_thread_and_contact_share_one_conversational_next_task(self):
        result = indexed([contact(), event("recruiter_reply_confirmed", "reply")])
        self.assertEqual(projection(result, "contact:one")["next_action"], projection(result, "thread:one")["next_action"])
        self.assertEqual(sum(item["action"] == "prepare_human_reply" for item in tasks(result)), 1)

    def test_real_protocol_handoff_benchmark_is_bounded_and_no_history_scan(self):
        from cloud.agent_protocol import AgentProtocol
        class IndexedStore:
            def __init__(self, rows):
                self.rows = {row["id"]: row for row in rows}
            def get(self, identity):
                return self.rows.get(identity)
            def latest_index_manifest(self):
                return max((row for row in self.rows.values() if row["kind"] == "index_manifests"), key=lambda row: row["payload"]["generated_at"])
            def query(self, **kwargs):
                raise AssertionError("A handoff must not scan historical Notion records")
        source = [job(), app(receipt=receipt()), contact(), event("recruiter_reply_confirmed", "reply")]
        result = indexed(source)
        store = IndexedStore(source + result)
        agent = AgentProtocol(store, registry={"Muse": {"capabilities": ["read_summarize", "research_propose"]}}, principal="Muse", now=NOW)
        brief = agent.get_campaign_brief()
        ready = agent.get_ready_tasks()
        state = agent.get_application_state("application:acme:req1")
        thread = agent.get_entity_context("thread:one", 2)
        self.assertEqual(brief["usage"]["store_calls"], 1)
        self.assertLess(brief["usage"]["estimated_input_tokens"], 2000)
        self.assertLess(ready["usage"]["store_calls"], 5)
        self.assertEqual(state["data"]["entity"]["state"], "applied_verified")
        self.assertLess(thread["usage"]["store_calls"], 6)
        self.assertTrue(any(item["type"] == "review_outreach" for item in ready["data"]["tasks"]))
        self.assertFalse(any(item["type"] == "submit_application" for item in ready["data"]["tasks"]))


if __name__ == "__main__":
    unittest.main()
