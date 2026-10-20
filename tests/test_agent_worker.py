"""Synthetic central-worker fixtures; no live Notion, provider, or applicant."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloud.agent_dispatcher import CONCURRENCY_GROUP, DispatcherRejected
from cloud.agent_index import build_index
from cloud.agent_protocol import AgentProtocol
from cloud.agent_worker import WorkerStorageError, run_agent_worker
from cloud.preparation import digest
from cloud.records import make_record, stable_id
from cloud.submission import Response
from tests.test_agent_dispatcher import AUTH, dispatch, writer_store
from tests.test_agent_protocol import NOW, REGISTRY, action_for

WRITER = {"concurrency_group": CONCURRENCY_GROUP, "run_id": "fixture-worker-run",
          "reservation_verified": True, "orchestrator_verified": True}


class OfficialTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append((method, url))
        if method != "GET":
            raise AssertionError("provider submission is forbidden")
        return Response(200, {"id": 100, "company_name": "Acme", "title": "Data Engineer",
            "requisition_id": "REQ-1", "content": "<h3>Requirements</h3><li>SQL and Python</li>",
            "location": {"name": "Dallas, TX"}, "absolute_url": "https://boards.greenhouse.io/acme/jobs/100",
            "questions": [], "pay_input_ranges": [{"currency_type": "USD", "max_cents": 21000000}]})


def publish(store, now=NOW):
    prior = store.latest_index_manifest()
    rows = build_index(list(store.records.values()), now, prior, source_complete=True)
    store.records.update({row["id"]: row for row in rows})
    return next(row for row in rows if row["kind"] == "index_manifests")


def job_store():
    store, _ = writer_store()
    store.records.clear()
    store.add("jobs", {"job_id": "job:fixture", "company": "Acme", "requisition_id": "REQ-1",
        "title": "Data Engineer", "url": "https://boards.greenhouse.io/acme/jobs/100",
        "location": "Dallas, TX", "source": "official_job_read", "observed_at": NOW.isoformat()}, "source:job")
    publish(store)
    return store


def admit(store, kind="research", *, agent="codex", entity="job:fixture", evidence=None, key="worker-fixture", now=NOW, **extra):
    client = AgentProtocol(store, REGISTRY, agent, now=now)
    action = action_for(client, kind, entity, key)
    action.update(extra)
    refs = evidence or client.get_entity_context(entity, 1)["data"]["entity"]["evidence_refs"]
    if kind in {"conversation_observed", "manual_action_observed"}:
        envelope = client.report_observed_outcome(action, refs)["data"]["envelope"]
    else:
        envelope = client.propose_action(action, refs)["data"]["envelope"]
    dispatch(store, envelope, actor="fixture-github-" + agent, now=now)
    return envelope


def worker(store, *, registry=None, context=None, now=NOW, config=None, **kwargs):
    return run_agent_worker(store, config or {}, trusted_registry=AUTH if registry is None else registry,
        writer_context=WRITER if context is None else context, now=now, **kwargs)


class AgentWorkerTests(unittest.TestCase):
    def test_second_agent_gets_finished_research_and_source_provenance(self):
        store = job_store()
        intake = admit(store)
        ack = copy.deepcopy(store.records["action-result:" + intake["id"]])
        publish(store, NOW + timedelta(seconds=1))
        transport = OfficialTransport()

        summary = worker(store, now=NOW + timedelta(seconds=2), transport=transport)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["external_effects"], 0)
        self.assertEqual(store.records[ack["id"]], ack)
        manifest = publish(store, NOW + timedelta(seconds=3))
        muse = AgentProtocol(store, REGISTRY, "muse", now=NOW + timedelta(seconds=3))
        result = muse.get_action_result(intake["id"])["data"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "research_verified")
        self.assertTrue(result["verified"])
        self.assertNotEqual(manifest["payload"]["action_results"][intake["id"]], ack["id"])
        self.assertEqual(muse.get_ready_tasks()["data"]["tasks"], [])
        proof = store.query("agent_research_evidence")[0]["payload"]
        self.assertEqual(proof["source_record_id"], "source:job")
        self.assertEqual(proof["source_record_hash"], store.records["source:job"]["hash"])
        self.assertEqual(proof["official_reads"][0]["url"], transport.calls[0][1])
        self.assertEqual(len(proof["official_reads"][0]["response_sha256"]), 64)
        self.assertTrue(all(method == "GET" for method, _ in transport.calls))
        writes = store.writes
        repeated = worker(store, now=NOW + timedelta(seconds=4), transport=transport)
        self.assertEqual(repeated["already_finished"], 1)
        self.assertEqual(store.writes, writes)
        self.assertEqual(len(transport.calls), 1)

    def test_verified_reply_is_derived_once_and_produces_a_human_reply_task(self):
        store = job_store()
        intake = admit(store)
        worker(store, transport=OfficialTransport())
        publish(store, NOW + timedelta(seconds=1))
        store.add("contacts", {"contact_id": "contact:fixture", "source": "user_confirmed",
            "live_state_verified": True, "identity_verified": True, "history_complete": True,
            "state": "connected", "verified_at": NOW.isoformat()}, "source:contact", "connected")
        source = store.add("conversation_observations", {"contact_id": "contact:fixture", "actor": "manual",
            "source": "user_confirmed", "occurred_at": NOW.isoformat(),
            "confirmation_evidence": {"event_id": "fixture-reply", "verified": True,
                "confirmed_at": NOW.isoformat(), "evidence_url": "https://evidence.fixture.test/reply",
                "outcome": "recruiter_reply_confirmed"}}, "source:reply", "verified")
        publish(store, NOW + timedelta(seconds=2))
        observed = admit(store, "conversation_observed", agent="future", entity="contact:fixture",
            evidence=[source["id"]], key="third-agent-reply", now=NOW + timedelta(seconds=2),
            outcome="recruiter_reply_confirmed", occurred_at=NOW.isoformat())
        summary = worker(store, now=NOW + timedelta(seconds=3), transport=OfficialTransport())
        self.assertEqual(summary["completed"], 1)
        events = store.query("conversation_events")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["source_record_hash"], source["hash"])
        self.assertEqual(events[0]["payload"]["confirmation_evidence"]["event_id"], "fixture-reply")
        publish(store, NOW + timedelta(seconds=4))
        future = AgentProtocol(store, REGISTRY, "future", now=NOW + timedelta(seconds=4))
        self.assertEqual(future.get_action_result(observed["id"])["data"]["status"], "completed")
        task = future.get_ready_tasks()["data"]["tasks"][0]
        self.assertEqual(task["entity_id"], "contact:fixture")
        self.assertEqual(task["type"], "review_outreach")
        self.assertEqual(task["executor"], "human")
        self.assertEqual(future.get_entity_context("contact:fixture", 1)["data"]["entity"]["state"], "reply_received")
        worker(store, now=NOW + timedelta(seconds=5))
        self.assertEqual(len(store.query("conversation_events")), 1)

    def test_unknown_action_is_terminal_blocked_without_provider_effect(self):
        store, _ = writer_store()
        from tests.test_agent_dispatcher import proposal
        intake = proposal(store)
        dispatch(store, intake)
        transport = OfficialTransport()
        result = worker(store, transport=transport)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(transport.calls, [])
        terminal = store.get(stable_id("agent_action_result", intake["id"], 1))
        self.assertEqual(terminal["status"], "blocked")
        self.assertFalse(terminal["payload"]["verified"])

    def test_current_revocation_stale_projection_suppression_and_changed_evidence_block(self):
        for mode in ("revoked", "disabled", "stale", "projection", "suppressed", "evidence"):
            with self.subTest(mode=mode):
                store = job_store()
                intake = admit(store)
                registry = copy.deepcopy(AUTH)
                now = NOW
                if mode == "revoked":
                    registry["fixture-github-codex"]["capabilities"] = ["read_summarize"]
                elif mode == "disabled":
                    registry["fixture-github-codex"]["enabled"] = False
                elif mode == "stale":
                    now += timedelta(days=2)
                elif mode == "projection":
                    store.add("jobs", {**store.records["source:job"]["payload"], "revision": 1,
                        "observed_at": (NOW + timedelta(seconds=1)).isoformat()}, "source:job:new")
                    publish(store, NOW + timedelta(seconds=1))
                    now += timedelta(seconds=1)
                elif mode == "suppressed":
                    manifest = store.latest_index_manifest()
                    projection_id = manifest["payload"]["projections"]["job:fixture"]
                    old = store.records[projection_id]
                    # Synthetic manual conflict: source hash and suppression
                    # differ from admitted intake and must never execute.
                    store.records[projection_id] = make_record(old["kind"], {**old["payload"],
                        "suppression": {"suppressed": True}}, old["id"], old["status"])
                elif mode == "evidence":
                    old = store.records["source:job"]
                    store.records[old["id"]] = make_record(old["kind"], {**old["payload"], "title": "Changed"}, old["id"], old["status"])
                transport = OfficialTransport()
                if mode == "evidence":
                    writes = store.writes
                    with self.assertRaisesRegex(WorkerStorageError, "worker_evidence_hash_requires_reconciliation"):
                        worker(store, registry=registry, now=now, transport=transport)
                    self.assertEqual(store.writes, writes)
                    self.assertEqual(transport.calls, [])
                    continue
                summary = worker(store, registry=registry, now=now, transport=transport)
                self.assertEqual(summary["blocked"], 1)
                self.assertEqual(transport.calls, [])
                self.assertEqual(store.get("action-result:" + intake["id"])["status"], "queued")

    def test_false_asserted_reply_cannot_upgrade_current_business_state(self):
        store, _ = writer_store()
        client = AgentProtocol(store, REGISTRY, "future", now=NOW)
        action = action_for(client, "conversation_observed", "contact:recruiter", "false-reply")
        action.update(outcome="recruiter_reply_confirmed", occurred_at=NOW.isoformat())
        intake = client.report_observed_outcome(action, ["evidence:reply"])["data"]["envelope"]
        dispatch(store, intake, actor="fixture-github-future")
        before = copy.deepcopy(store.records["projection:contact:v1"])
        summary = worker(store)
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["exceptions"], {"matching_structured_observation_required": 1})
        self.assertEqual(store.records[before["id"]], before)
        self.assertEqual(len(store.query("conversation_events")), 1)

    def test_observation_requires_exact_source_kind_entity_outcome_time_and_confirmation(self):
        for mode in ("kind", "entity", "outcome", "time", "unverified", "historical", "untrusted_source"):
            with self.subTest(mode=mode):
                store, _ = writer_store()
                payload = {"contact_id": "contact:recruiter", "source": "user_confirmed",
                    "confirmation_evidence": {"event_id": "strict-reply", "verified": True,
                        "confirmed_at": NOW.isoformat(), "evidence_url": "https://evidence.fixture.test/strict-reply",
                        "outcome": "recruiter_reply_confirmed"}}
                kind = "conversation_sources"
                if mode == "kind":
                    kind = "application_receipts"
                elif mode == "entity":
                    payload["contact_id"] = "contact:other"
                elif mode == "outcome":
                    payload["confirmation_evidence"]["outcome"] = "message_sent_confirmed"
                elif mode == "time":
                    payload["confirmation_evidence"]["confirmed_at"] = (NOW - timedelta(minutes=1)).isoformat()
                elif mode == "unverified":
                    payload["confirmation_evidence"]["verified"] = False
                elif mode == "historical":
                    payload["historical_only"] = True
                elif mode == "untrusted_source":
                    payload["source"] = "caller_claim"
                store.records["evidence:reply"] = make_record(kind, payload, "evidence:reply", "observed")
                intake = admit(store, "conversation_observed", agent="future", entity="contact:recruiter",
                    evidence=["evidence:reply"], key="strict-" + mode,
                    outcome="recruiter_reply_confirmed", occurred_at=NOW.isoformat())
                summary = worker(store)
                self.assertEqual(summary["exceptions"], {"matching_structured_observation_required": 1})
                self.assertEqual(summary["completed"], 0)
                self.assertEqual(store.get(stable_id("agent_action_result", intake["id"], 1))["status"], "blocked")

    def test_result_chain_conflicts_and_missing_outputs_never_resume_or_mask_the_ack(self):
        for mode in ("output_missing", "execution_hash", "outcome", "revision"):
            with self.subTest(mode=mode):
                store = job_store()
                intake = admit(store)
                worker(store, transport=OfficialTransport())
                terminal_id = stable_id("agent_action_result", intake["id"], 1)
                old = store.records[terminal_id]
                if mode == "output_missing":
                    del store.records[next(iter(old["payload"]["output_hashes"]))]
                else:
                    payload = copy.deepcopy(old["payload"])
                    payload[{"execution_hash": "execution_hash", "outcome": "outcome", "revision": "revision"}[mode]] = {
                        "execution_hash": "0" * 64, "outcome": "applied_verified", "revision": 2}[mode]
                    store.records[terminal_id] = make_record(old["kind"], payload, terminal_id, old["status"])
                writes = store.writes
                transport = OfficialTransport()
                with self.assertRaisesRegex(WorkerStorageError, "worker_result_chain_requires_reconciliation"):
                    worker(store, transport=transport)
                self.assertEqual(store.writes, writes)
                self.assertEqual(transport.calls, [])
                index = publish(store, NOW + timedelta(seconds=1))
                self.assertFalse(index["payload"]["complete"])
                self.assertEqual(index["payload"]["action_results"][intake["id"]], "action-result:" + intake["id"])

    def test_queue_budget_and_readback_failure_abort_without_advancing_another_action(self):
        store = job_store()
        store.add("jobs", {**store.records["source:job"]["payload"], "job_id": "job:second",
            "requisition_id": "REQ-2", "url": "https://boards.greenhouse.io/acme/jobs/200"}, "source:second")
        publish(store, NOW + timedelta(seconds=1))
        admit(store, entity="job:fixture", key="budget-first", now=NOW + timedelta(seconds=1))
        admit(store, entity="job:second", key="budget-second", now=NOW + timedelta(seconds=1))
        transport = OfficialTransport()
        result = worker(store, config={"agent_worker": {"max_actions": 1}},
            now=NOW + timedelta(seconds=1), transport=transport)
        self.assertEqual(result["queued_seen"], 1)
        self.assertEqual(result["completed"] + result["blocked"], 1)
        self.assertEqual(len(transport.calls), 1)
        store = job_store()
        admit(store)
        store.hide_readback = True
        writes = store.writes
        transport = OfficialTransport()
        with self.assertRaisesRegex(WorkerStorageError, "storage_readback_unconfirmed_reconcile"):
            worker(store, transport=transport)
        self.assertEqual(store.writes, writes + 1)
        self.assertEqual(transport.calls, [])

    def test_preparation_keeps_full_feed_history_suppression_before_filtering_candidates(self):
        store = job_store()
        intake = admit(store, "prepare_application")
        feed = {"exported_at": NOW.isoformat(), "jobs": [
            {"company": "Acme", "requisition_id": "REQ-1", "title": "Data Engineer",
             "url": "https://boards.greenhouse.io/acme/jobs/100", "is_active": True,
             "posted_at": NOW.isoformat(), "relevance_score": .6},
            {"company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/99",
             "application_status": "applied", "is_active": False, "relevance_score": 0}]}
        transport = OfficialTransport()
        summary = worker(store, feed=feed, transport=transport,
            prepare=lambda *_: self.fail("ambiguous employer history must suppress preparation"))
        self.assertEqual(summary["exceptions"], {"historical_target_submission_requires_reconciliation": 1})
        self.assertEqual(transport.calls, [])
        self.assertEqual(store.query("application_packets"), [])
        self.assertEqual(store.get(stable_id("agent_action_result", intake["id"], 1))["status"], "blocked")

    def test_malformed_or_stale_feed_is_a_terminal_exception_without_a_storage_fence(self):
        for jobs in ([None], {}, [{"company": "Acme", "relevance_score": "high"}],
                     [{"company": "Acme", "aliases": "invented-aliases"}], [{"company": {"name": "Acme"}}]):
            with self.subTest(jobs=jobs):
                store = job_store()
                intake = admit(store, "prepare_application")
                summary = worker(store, feed={"exported_at": NOW.isoformat(), "jobs": jobs})
                self.assertEqual(summary["exceptions"], {"target_feed_unavailable_or_malformed": 1})
                self.assertEqual(worker(store)["already_finished"], 1)
                self.assertEqual(store.get(stable_id("agent_action_result", intake["id"], 1))["status"], "blocked")

    def test_completion_cannot_claim_another_action_or_another_target(self):
        for mode in ("wrong_action", "wrong_research_source"):
            with self.subTest(mode=mode):
                store = job_store()
                intake = admit(store)
                worker(store, transport=OfficialTransport())
                terminal_id = stable_id("agent_action_result", intake["id"], 1)
                old = store.records[terminal_id]
                payload = copy.deepcopy(old["payload"])
                if mode == "wrong_action":
                    payload["outcome"] = "application_prepared"
                    payload["output_hashes"] = {"source:job": store.records["source:job"]["hash"]}
                else:
                    other = store.add("jobs", {**store.records["source:job"]["payload"],
                        "job_id": "job:other", "requisition_id": "REQ-OTHER"}, "source:other")
                    output_id = next(iter(payload["output_hashes"]))
                    research = store.records[output_id]
                    store.records[output_id] = make_record(research["kind"], {**research["payload"],
                        "source_record_id": other["id"], "source_record_hash": other["hash"]}, output_id, research["status"])
                    payload["output_hashes"][output_id] = store.records[output_id]["hash"]
                store.records[terminal_id] = make_record(old["kind"], payload, terminal_id, old["status"])
                with self.assertRaisesRegex(WorkerStorageError, "worker_result_chain_requires_reconciliation"):
                    worker(store)
                self.assertFalse(publish(store, NOW + timedelta(seconds=1))["payload"]["complete"])

    def test_already_recorded_reply_can_reference_a_distinct_matching_admitted_source(self):
        store, _ = writer_store()
        payload = {"contact_id": "contact:recruiter", "source": "user_confirmed",
            "confirmation_evidence": {"event_id": "existing-reply", "verified": True,
                "confirmed_at": NOW.isoformat(), "evidence_url": "https://evidence.fixture.test/existing",
                "outcome": "recruiter_reply_confirmed"}}
        store.records["evidence:reply"] = make_record("conversation_observations", payload, "evidence:reply", "verified")
        old = store.add("conversation_events", payload, "event:already-recorded", "verified")
        intake = admit(store, "conversation_observed", agent="future", entity="contact:recruiter",
            evidence=["evidence:reply"], key="already-recorded", outcome="recruiter_reply_confirmed", occurred_at=NOW.isoformat())
        summary = worker(store)
        self.assertEqual(summary["completed"], 1)
        terminal_id = stable_id("agent_action_result", intake["id"], 1)
        self.assertEqual(store.get(terminal_id)["payload"]["outcome"], "verified_observation_already_recorded")
        self.assertEqual(worker(store)["already_finished"], 1)
        self.assertEqual(store.records[old["id"]], old)

    def test_single_writer_partial_admission_and_partial_execution_stop(self):
        store = job_store()
        intake = admit(store)
        writes = store.writes
        for context in ({}, {**WRITER, "orchestrator_verified": False}, {**WRITER, "concurrency_group": "other"}):
            with self.assertRaisesRegex(DispatcherRejected, "trusted_central_orchestrator_required"):
                worker(store, context=context)
        self.assertEqual(store.writes, writes)
        del store.records["action-result:" + intake["id"]]
        with self.assertRaisesRegex(WorkerStorageError, "worker_admission_requires_reconciliation"):
            worker(store)
        self.assertEqual(store.writes, writes)
        for failure in (1, 2, 3):
            with self.subTest(failure=failure):
                store = job_store()
                intake = admit(store)
                store.fail_on_write, store.persist_before_failure = store.writes + failure, True
                transport = OfficialTransport()
                with self.assertRaises(WorkerStorageError):
                    worker(store, transport=transport)
                store.fail_on_write = None
                writes, reads = store.writes, len(transport.calls)
                if failure < 3:
                    with self.assertRaisesRegex(WorkerStorageError, "partial_worker_execution_requires_reconciliation"):
                        worker(store, transport=transport)
                    manifest = publish(store)
                    self.assertFalse(manifest["payload"]["complete"])
                    self.assertTrue(manifest["payload"]["conflicts"])
                else:
                    self.assertEqual(worker(store, transport=transport)["already_finished"], 1)
                self.assertEqual(store.writes, writes)
                self.assertEqual(len(transport.calls), reads)

    def test_preparation_filters_exact_real_feed_and_never_posts_even_with_permission(self):
        store = job_store()
        intake = admit(store, "prepare_application")
        transport = OfficialTransport()
        feed = {"exported_at": NOW.isoformat(), "jobs": [
            {"company": "Acme", "requisition_id": "REQ-1", "title": "Data Engineer",
             "url": "https://boards.greenhouse.io/acme/jobs/100", "is_active": True,
             "posted_at": NOW.isoformat(), "relevance_score": .6},
            {"company": "Other", "requisition_id": "REQ-2", "title": "Data Engineer",
             "url": "https://boards.greenhouse.io/other/jobs/200", "is_active": True,
             "posted_at": NOW.isoformat(), "relevance_score": .9}]}
        employer = {"ats": "greenhouse", "board_token": "acme", "credential_env": "FIXTURE_ATS_TOKEN",
            "permission": {"permitted": True, "scope": "application_submission",
                "evidence_url": "https://acme.test/permission", "verified_at": (NOW-timedelta(days=1)).isoformat(),
                "expires_at": (NOW+timedelta(days=1)).isoformat()},
            "screening": {"sponsorship_evidence": {"verified": True, "recent_h1b_filings": True,
                "source_url": "https://acme.test/filings", "verified_at": NOW.isoformat(),
                "fiscal_years": [NOW.year-1, NOW.year]}}}
        from tests.test_applications_daily import FrozenDateTime
        FrozenDateTime.current = NOW
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "resume.tex").write_text("synthetic source")
            (path / "resume.pdf").write_bytes(b"%PDF-1.4\nfixture")
            def prepare(job, grade, config):
                self.assertEqual(config["max_submissions_per_day"], 0)
                self.assertEqual(job["requisition_id"], "REQ-1")
                fields = {"name": "Synthetic Applicant"}
                return {"ready": True, "artifact_dir": str(path), "resume_path": str(path / "resume.pdf"),
                    "resume_sha256": digest((path / "resume.pdf").read_bytes()),
                    "source_sha256": digest((path / "resume.tex").read_bytes()),
                    "ground_truth_sha256": digest("fixture-facts"), "fields": fields,
                    "answers_hash": digest(fields), "qa": {"verified": True}, "missing": []}
            with patch("cloud.applications.dt.datetime", FrozenDateTime), patch.dict("os.environ", {"FIXTURE_ATS_TOKEN": "fixture"}):
                summary = worker(store, config={"applications": {"employers": {"acme": employer},
                    "max_submissions_per_day": 100}}, transport=transport, feed=feed, prepare=prepare)
        self.assertEqual(summary["completed"], 1)
        terminal = store.get(stable_id("agent_action_result", intake["id"], 1))
        self.assertEqual(terminal["payload"]["outcome"], "application_prepared")
        self.assertTrue(all(method == "GET" for method, _ in transport.calls))
        self.assertTrue(all("/other/" not in url for _, url in transport.calls))
        self.assertFalse(any(row["status"] in {"submitting", "applied_verified"} for row in store.query("applications")))
        publish(store, NOW + timedelta(seconds=1))
        self.assertEqual(AgentProtocol(store, REGISTRY, "muse", now=NOW + timedelta(seconds=1)).get_action_result(intake["id"])["data"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
