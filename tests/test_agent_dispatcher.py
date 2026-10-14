"""Serialized intake fixtures: no live writer, provider or private data."""
import copy
from datetime import timedelta, timezone
import unittest

from cloud.agent_dispatcher import (CONCURRENCY_GROUP, MAX_INTAKE_BYTES, DispatcherConflict,
    DispatcherRejected, DispatcherStorageError, dispatch_intake)
from cloud.agent_protocol import AgentProtocol
from cloud.records import make_record
from tests.test_agent_protocol import NOW, REGISTRY, action_for, fixture, protocol

WRITER = {"concurrency_group": CONCURRENCY_GROUP, "run_id": "fixture-run", "reservation_verified": True}
AUTH = {"fixture-github-codex": {"principal": "codex", **REGISTRY["codex"]},
        "fixture-github-muse": {"principal": "muse", **REGISTRY["muse"]},
        "fixture-github-future": {"principal": "future", **REGISTRY["future"]}}


def writer_store(state="submission_eligible"):
    store, manifest = fixture(state)
    store.fail_on_write = None
    store.persist_before_failure = False
    store.hide_readback = False
    def put(record):
        store.writes += 1
        if store.writes == store.fail_on_write:
            if store.persist_before_failure:
                store.records[record["id"]] = copy.deepcopy(record)
            raise TimeoutError("SYNTHETIC PRIVATE STORAGE ERROR")
        old = store.records.get(record["id"])
        if old and old != record:
            raise RuntimeError("must not overwrite")
        if not store.hide_readback:
            store.records[record["id"]] = copy.deepcopy(record)
        return record
    store.put = put
    return store, manifest


def proposal(store, agent="codex", kind="submit_application", key="fixture-dispatch"):
    client = protocol(store, agent)
    action = action_for(client, kind, key=key)
    return client.propose_action(action, ["evidence:application"])["data"]["envelope"]


def dispatch(store, envelope, *, actor="fixture-github-codex", registry=None, context=None, now=NOW):
    return dispatch_intake(store, envelope, github_actor=actor,
        trusted_registry=AUTH if registry is None else registry,
        writer_context=WRITER if context is None else context, now=now)


class AgentDispatcherTests(unittest.TestCase):
    def test_three_fresh_agents_see_queue_ack_and_published_cursor_without_completion(self):
        from cloud.agent_index import build_index
        store, _ = writer_store()
        store.records.clear()
        source = store.add("jobs", {"job_id": "job:handoff", "company": "Fixture Company", "requisition_id": "R-HANDOFF",
            "url": "https://careers.fixture.test/jobs/R-HANDOFF", "source": "official_job_read",
            "observed_at": NOW.isoformat()}, "source:handoff-job")
        initial = build_index([source], NOW, source_complete=True)
        store.records.update({row["id"]: row for row in initial})
        original_manifest = next(row for row in initial if row["kind"] == "index_manifests")
        codex = AgentProtocol(store, REGISTRY, "codex", now=NOW)
        task = codex.get_ready_tasks(limit=1)["data"]["tasks"][0]
        context = codex.get_entity_context(task["entity_id"], 2)["data"]
        action = {"type": task["type"], "entity_id": task["entity_id"], "task_id": task["task_id"],
            "idempotency_key": "three-agent-handoff", "expected_manifest_id": context["manifest_id"],
            "expected_projection_hash": context["projection_hash"]}
        envelope = codex.propose_action(action, task["evidence_refs"])["data"]["envelope"]
        ack = dispatch(store, envelope)
        self.assertEqual(ack["status"], "queued")
        published = build_index(list(store.records.values()), NOW + timedelta(seconds=1), original_manifest, source_complete=True)
        store.records.update({row["id"]: row for row in published})
        current_manifest = next(row for row in published if row["kind"] == "index_manifests")
        muse = AgentProtocol(store, REGISTRY, "muse", now=NOW + timedelta(seconds=1))
        result = muse.get_action_result(envelope["id"])["data"]
        self.assertEqual(result["status"], "queued")
        self.assertNotEqual(result.get("verified"), True)
        self.assertEqual(muse.get_ready_tasks()["data"]["tasks"], [])
        self.assertEqual(muse.get_entity_context("job:handoff", 1)["data"]["entity"]["state"], context["entity"]["state"])
        future = AgentProtocol(store, REGISTRY, "future", now=NOW + timedelta(seconds=2))
        brief = future.get_campaign_brief()
        self.assertEqual(brief["data"]["manifest_id"], current_manifest["id"])
        self.assertEqual(brief["data"]["cursor"], current_manifest["payload"]["source_cursor"])
        self.assertNotEqual(brief["data"]["cursor"], original_manifest["payload"]["source_cursor"])
        changes = future.get_recent_changes(original_manifest["payload"]["source_cursor"])["data"]["changes"]
        self.assertTrue(any(change.get("change") == "task_updated" for change in changes))
        self.assertEqual(future.get_ready_tasks()["data"]["tasks"], [])
        self.assertEqual(future.get_action_result(envelope["id"])["data"]["status"], "queued")
        self.assertEqual(store.writes, 4)
        self.assertFalse(any(row["status"] in {"applied_verified", "message_sent_confirmed"} for row in store.records.values()))

    def test_queue_ack_exact_readback_and_complete_replay(self):
        store, _ = writer_store()
        envelope = proposal(store)
        result = dispatch(store, envelope)
        self.assertEqual(result["id"], "action-result:" + envelope["id"])
        self.assertEqual(result["kind"], "agent_action_results")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["payload"], {"action_id": envelope["id"], "status": "queued",
            "evidence_refs": ["evidence:application"], "external_effect": False, "actor": "codex"})
        queue = store.records["action-queue:" + envelope["id"]]
        self.assertEqual(queue["kind"], "agent_execution_queue")
        self.assertFalse(queue["payload"]["external_effect"])
        self.assertEqual(store.writes, 4)
        self.assertEqual(dispatch(store, envelope, now=NOW + timedelta(days=2)), result)
        self.assertEqual(store.writes, 4)
        client = protocol(store)
        self.assertEqual(client.get_action_result(envelope["id"])["data"]["status"], "queued")

    def test_current_revocation_stops_even_complete_replay(self):
        store, _ = writer_store()
        envelope = proposal(store)
        dispatch(store, envelope)
        revoked = copy.deepcopy(AUTH)
        revoked["fixture-github-codex"]["capabilities"] = ["read_summarize"]
        with self.assertRaisesRegex(DispatcherRejected, "current_trusted_capability_denied"):
            dispatch(store, envelope, registry=revoked)
        revoked["fixture-github-codex"]["enabled"] = False
        with self.assertRaisesRegex(DispatcherRejected, "github_actor_not_authorized"):
            dispatch(store, envelope, registry=revoked)
        self.assertEqual(store.writes, 4)

    def test_actor_capability_and_single_writer_claims_are_host_only(self):
        store, _ = writer_store()
        envelope = proposal(store)
        with self.assertRaisesRegex(DispatcherRejected, "authenticated_actor_principal_mismatch"):
            dispatch(store, envelope, actor="fixture-github-muse")
        with self.assertRaisesRegex(DispatcherRejected, "github_actor_not_authorized"):
            dispatch(store, envelope, actor="caller-invented-agent")
        for context in ({}, {**WRITER, "concurrency_group": "independent-writer"}, {**WRITER, "reservation_verified": False}, {**WRITER, "run_id": ""}):
            with self.assertRaisesRegex(DispatcherRejected, "trusted_single_writer_reservation_required"):
                dispatch(store, envelope, context=context)
        altered = copy.deepcopy(envelope["payload"])
        altered["action"]["capabilities"] = ["execute_permitted_application"]
        spoof = make_record(envelope["kind"], altered, envelope["id"], envelope["status"])
        restricted = copy.deepcopy(AUTH)
        restricted["fixture-github-codex"]["capabilities"] = ["read_summarize"]
        with self.assertRaisesRegex(DispatcherRejected, "current_trusted_capability_denied"):
            dispatch(store, spoof, registry=restricted)
        self.assertEqual(store.writes, 0)

    def test_noncanonical_spoof_and_linkedin_effects_are_rejected(self):
        store, _ = writer_store()
        envelope = proposal(store)
        altered = copy.deepcopy(envelope["payload"])
        altered["required_capability"] = "read_summarize"
        spoof = make_record(envelope["kind"], altered, envelope["id"], envelope["status"])
        with self.assertRaisesRegex(DispatcherRejected, "intake_canonical_revalidation_mismatch"):
            dispatch(store, spoof)
        for kind in ("linkedin_message", "linkedin_invitation"):
            altered["action"]["type"] = kind
            unsafe = make_record(envelope["kind"], altered, envelope["id"], envelope["status"])
            with self.assertRaisesRegex(DispatcherRejected, "unsupported_intake_action"):
                dispatch(store, unsafe)
        self.assertEqual(store.writes, 0)

    def test_stale_snapshot_partial_source_and_missing_index_hold(self):
        store, manifest = writer_store()
        envelope = proposal(store)
        newer = copy.deepcopy(manifest["payload"])
        newer["generated_at"] = (NOW + timedelta(seconds=1)).isoformat()
        store.add("index_manifests", newer, "index:newer")
        with self.assertRaisesRegex(DispatcherRejected, "intake_revalidation_stale_manifest"):
            dispatch(store, envelope, now=NOW + timedelta(seconds=1))
        self.assertEqual(store.writes, 0)
        for update in ({"complete": False}, {"conflicts": ["fixture-conflict"]}, {"generated_at": (NOW - timedelta(days=2)).isoformat()}):
            store, manifest = writer_store()
            envelope = proposal(store)
            payload = {**manifest["payload"], **update}
            store.records[manifest["id"]] = make_record("index_manifests", payload, manifest["id"])
            with self.assertRaisesRegex(DispatcherRejected, "intake_revalidation_index_not_eligible"):
                dispatch(store, envelope)
            self.assertEqual(store.writes, 0)
        store, manifest = writer_store()
        envelope = proposal(store)
        del store.records[manifest["id"]]
        with self.assertRaisesRegex(DispatcherStorageError, "authoritative_revalidation_unavailable"):
            dispatch(store, envelope)
        self.assertEqual(store.writes, 0)

    def test_conflicting_same_identity_never_overwrites(self):
        store, _ = writer_store()
        envelope = proposal(store)
        dispatch(store, envelope)
        changed = copy.deepcopy(envelope["payload"])
        changed["action"]["reason"] = "conflicting current intent"
        conflicting = make_record(envelope["kind"], changed, envelope["id"], envelope["status"])
        with self.assertRaisesRegex(DispatcherConflict, "immutable_intake_identity_conflict"):
            dispatch(store, conflicting)
        self.assertEqual(store.writes, 4)
        self.assertEqual(store.records[envelope["id"]], envelope)

    def test_semantic_duplicate_across_agents_and_new_keys_blocked(self):
        store, _ = writer_store()
        first = proposal(store)
        dispatch(store, first)
        future_entry = {"principal": "future", "capabilities": ["read_summarize", "execute_permitted_application"]}
        client = AgentProtocol(store, {"future": future_entry}, "future", now=NOW)
        action = action_for(client, "submit_application", key="different-agent-and-key")
        second = client.propose_action(action, ["evidence:application"])["data"]["envelope"]
        auth = {**AUTH, "fixture-github-future": future_entry}
        with self.assertRaisesRegex(DispatcherConflict, "semantic_action_already_reserved"):
            dispatch(store, second, actor="fixture-github-future", registry=auth)
        self.assertEqual(store.writes, 4)
        self.assertNotIn(second["id"], store.records)

    def test_structured_observation_dedup_without_effect(self):
        store, _ = writer_store()
        client = protocol(store, "future")
        observed = action_for(client, "conversation_observed", "contact:recruiter", "confirmed-reply")
        observed.update(occurred_at=NOW.isoformat(), outcome="recruiter_request")
        first = client.report_observed_outcome(observed, ["evidence:reply"])["data"]["envelope"]
        result = dispatch(store, first, actor="fixture-github-future")
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["payload"]["external_effect"])
        second = client.report_observed_outcome({**observed,
            'occurred_at': NOW.astimezone(timezone(timedelta(hours=-5))).isoformat(),
            "idempotency_key": "new-key-same-observation"}, ["evidence:reply"])["data"]["envelope"]
        with self.assertRaisesRegex(DispatcherConflict, "semantic_action_already_reserved"):
            dispatch(store, second, actor="fixture-github-future")
        self.assertEqual(store.writes, 4)

    def test_partial_reservation_failed_write_and_readback_require_reconciliation(self):
        for fail_at in (1, 2, 3, 4):
            store, _ = writer_store()
            envelope = proposal(store)
            store.fail_on_write, store.persist_before_failure = fail_at, True
            with self.assertRaisesRegex(DispatcherStorageError, "storage_write_unconfirmed_reconcile"):
                dispatch(store, envelope)
            self.assertEqual(store.writes, fail_at)
            store.fail_on_write = None
            if fail_at < 4:
                with self.assertRaisesRegex(DispatcherStorageError, "partial_intake_requires_reconciliation"):
                    dispatch(store, envelope)
            else:
                # A complete exact acknowledgement is distinguishable from a
                # partial/unknown queue, without performing another write.
                self.assertEqual(dispatch(store, envelope)["status"], "queued")
            self.assertEqual(store.writes, fail_at)
        store, _ = writer_store()
        envelope = proposal(store)
        store.hide_readback = True
        with self.assertRaisesRegex(DispatcherStorageError, "storage_readback_unconfirmed_reconcile"):
            dispatch(store, envelope)
        self.assertEqual(store.writes, 1)

    def test_invalid_size_hash_contract_and_storage_error_sanitized(self):
        store, _ = writer_store()
        envelope = proposal(store)
        giant = copy.deepcopy(envelope)
        giant["payload"]["private_body"] = "x" * MAX_INTAKE_BYTES
        with self.assertRaisesRegex(DispatcherRejected, "intake_size_limit_exceeded"):
            dispatch(store, giant)
        invalid = {**envelope, "hash": "incorrect"}
        with self.assertRaisesRegex(DispatcherRejected, "invalid_intake_envelope"):
            dispatch(store, invalid)
        altered = copy.deepcopy(envelope["payload"])
        altered["external_effect"] = True
        with self.assertRaisesRegex(DispatcherRejected, "invalid_intake_contract"):
            dispatch(store, make_record(envelope["kind"], altered, envelope["id"], "proposed"))
        store.fail = True
        with self.assertRaisesRegex(DispatcherStorageError, "storage_read_unavailable_or_conflicted") as error:
            dispatch(store, envelope)
        self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(store.writes, 0)


if __name__ == "__main__":
    unittest.main()
