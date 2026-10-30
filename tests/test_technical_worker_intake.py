"""Synthetic end-to-end admission through the existing authenticated agent worker."""
import unittest
import copy
from datetime import timedelta
from unittest.mock import patch

from cloud.agent_worker import WorkerBlocked, WorkerStorageError, _admit_technical_source
from cloud.agent_dispatcher import DispatcherConflict
from cloud.agent_protocol import ACTION_CAPABILITIES

from cloud.agent_protocol import AgentProtocol
from cloud.records import stable_id
from cloud.technical_records import make_technical_record
from tests.test_agent_protocol import NOW, REGISTRY
from tests.test_agent_worker import admit, job_store, publish, worker, WRITER
from tests.test_agent_dispatcher import AUTH
from tests.test_technical_records import sample


def setup_store():
    store = job_store()
    store.index_source_records = lambda: [row for row in store.records.values()
        if row["kind"] not in {"entity_projections", "index_manifests", "index_changes"}
        and not (row["kind"] == "agent_tasks" and row["payload"].get("schema") == "agent-index/v1")]
    return store


def candidate():
    p = sample()
    p.update(actor="codex", status="proposed", freshness="unknown", author=None, author_verified=False,
             evidence_refs=["source:job"], observed_at=NOW.isoformat(),
             first_seen_at=NOW.isoformat(), source_published_at=None,
             entity_id=stable_id("technical_item", "source-fixture"),
             source_identity=stable_id("source", "source-fixture"))
    return make_technical_record("technical_items", p)


class TechnicalWorkerIntakeTests(unittest.TestCase):
    def test_distinct_candidates_do_not_collide_and_duplicate_does(self):
        store = setup_store()
        first = candidate()
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="first")
        with self.assertRaises(DispatcherConflict):
            admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="duplicate")
        second_agent = make_technical_record("technical_items", {**first["payload"], "actor": "muse"})
        with self.assertRaises(DispatcherConflict):
            admit(store, "admit_technical_source", agent="muse", evidence=["source:job"],
                  candidate=second_agent, key="other-agent")
        payload = {**first["payload"], "entity_id": stable_id("technical_item", "different"),
                   "source_identity": stable_id("source", "different"),
                   "source_url": "https://example.org/engineering/different"}
        second = make_technical_record("technical_items", payload)
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=second, key="second")

    def test_authenticated_worker_admits_source_and_preserves_result_chain(self):
        store = setup_store()
        intended = candidate()
        intake = admit(store, "admit_technical_source", evidence=["source:job"], candidate=intended)
        publish(store, NOW + timedelta(seconds=1))
        context = {**WRITER, "previous_writer_reconciled": True}
        summary = worker(store, context=context, now=NOW + timedelta(seconds=2))
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["external_effects"], 0)
        self.assertEqual(store.get(intended["id"]), intended)
        manifest = publish(store, NOW + timedelta(seconds=3))
        client = AgentProtocol(store, REGISTRY, "muse", now=NOW + timedelta(seconds=3))
        result = client.get_action_result(intake["id"])["data"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["outcome"], "technical_source_admitted")
        self.assertTrue(result["verified"])
        self.assertTrue(manifest["payload"]["complete"])
        before = store.writes
        self.assertEqual(worker(store, context=context, now=NOW + timedelta(seconds=4))["already_finished"], 1)
        self.assertEqual(store.writes, before)

    def test_missing_previous_writer_fence_stops_before_any_append(self):
        store = setup_store()
        with patch.dict(ACTION_CAPABILITIES, {"admit_technical_source": "research_propose"}):
            admit(store, "admit_technical_source", evidence=["source:job"], candidate=candidate())
            publish(store, NOW + timedelta(seconds=1))
            before = store.writes
            with self.assertRaises(WorkerStorageError):
                worker(store, context=WRITER, now=NOW + timedelta(seconds=2))
            self.assertEqual(store.writes, before)

    def test_revoked_research_grant_cannot_create_source(self):
        store = setup_store()
        intended = candidate()
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=intended)
        publish(store, NOW + timedelta(seconds=1))
        registry = copy.deepcopy(AUTH)
        registry["fixture-github-codex"]["capabilities"] = ["read_summarize"]
        result = worker(store, registry=registry, context={**WRITER, "previous_writer_reconciled": True},
                        now=NOW + timedelta(seconds=2))
        self.assertEqual(result["blocked"], 1)
        self.assertIsNone(store.get(intended["id"]))

    def test_uncertain_admission_append_stops_worker_without_blind_retry(self):
        store = setup_store()
        intended = candidate()
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=intended)
        publish(store, NOW + timedelta(seconds=1))
        store.fail_on_write = store.writes + 2  # execution succeeds; intent is ambiguous
        store.persist_before_failure = True
        context = {**WRITER, "previous_writer_reconciled": True}
        with self.assertRaises(WorkerStorageError):
            worker(store, context=context, now=NOW + timedelta(seconds=2))
        before = store.writes
        store.fail_on_write = None
        with self.assertRaises(WorkerStorageError):
            worker(store, context=context, now=NOW + timedelta(seconds=3))
        self.assertEqual(store.writes, before)
        self.assertIsNone(store.get(intended["id"]))

    def test_unrelated_evidence_cannot_promote_claimed_source_as_verified_observation(self):
        valid = candidate()
        claimed = {**valid["payload"], "status": "observed", "freshness": "fresh",
                   "author": "Claimed Author", "author_verified": True}
        intended = make_technical_record("technical_items", claimed)
        intake = {"payload": {"actor": "codex", "action": {"candidate": intended},
                              "evidence_hashes": {"source:job": "f" * 64}}}
        with self.assertRaises(WorkerBlocked):
            _admit_technical_source(setup_store(), intake, {**WRITER, "previous_writer_reconciled": True})


if __name__ == "__main__":
    unittest.main()
