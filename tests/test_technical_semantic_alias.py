"""Synthetic cross-agent semantic alias admission regression."""
import unittest
from datetime import timedelta

from cloud.agent_dispatcher import DispatcherConflict, DispatcherStorageError
from cloud.agent_protocol import AgentProtocol
from cloud.records import stable_id
from cloud.technical_records import make_technical_record
from tests.test_agent_dispatcher import WRITER, dispatch
from tests.test_agent_protocol import NOW, REGISTRY, action_for
from tests.test_technical_worker_intake import admit, candidate, setup_store
from tests.test_agent_worker import publish, worker
from cloud.agent_worker import WorkerStorageError
from cloud.technical_admission import AdmissionConflict, admit_technical_record


class TechnicalSemanticAliasTests(unittest.TestCase):
    def test_already_admitted_source_aliases_reject_conflicting_intake_before_append(self):
        for collision in ("url", "source_identity"):
            with self.subTest(collision=collision):
                store = setup_store()
                first = candidate()
                context = {**WRITER, "orchestrator_verified": True, "previous_writer_reconciled": True}
                admit_technical_record(store, first, writer_context=context)
                publish(store, NOW + timedelta(seconds=1))
                payload = {**first["payload"], "entity_id": stable_id("technical_item", "other")}
                if collision == "url":
                    payload["source_identity"] = stable_id("source", "other")
                else:
                    payload["source_url"] = "https://example.org/engineering/other"
                other = make_technical_record("technical_items", payload)
                before = store.writes
                with self.assertRaises(DispatcherConflict):
                    admit(store, "admit_technical_source", evidence=["source:job"], candidate=other,
                          key="other", now=NOW + timedelta(seconds=2))
                self.assertEqual(store.writes, before)

    def test_previously_admitted_entity_can_enqueue_a_new_revision(self):
        store = setup_store()
        first = candidate()
        context = {**WRITER, "orchestrator_verified": True, "previous_writer_reconciled": True}
        admit_technical_record(store, first, writer_context=context)
        publish(store, NOW + timedelta(seconds=1))
        revised = make_technical_record("technical_items", {**first["payload"], "revision": 2,
            "content_hash": "b" * 64})
        envelope = admit(store, "admit_technical_source", evidence=["source:job"], candidate=revised,
                         key="next", now=NOW + timedelta(seconds=2))
        self.assertEqual(store.records[envelope["id"]], envelope)

    def test_previously_admitted_same_revision_with_changed_content_is_rejected(self):
        store = setup_store()
        first = candidate()
        context = {**WRITER, "orchestrator_verified": True, "previous_writer_reconciled": True}
        admit_technical_record(store, first, writer_context=context)
        publish(store, NOW + timedelta(seconds=1))
        conflicting = make_technical_record("technical_items", {**first["payload"], "content_hash": "b" * 64})
        before = store.writes
        with self.assertRaises(DispatcherConflict):
            admit(store, "admit_technical_source", evidence=["source:job"], candidate=conflicting,
                  key="changed", now=NOW + timedelta(seconds=2))
        self.assertEqual(store.writes, before)

    def test_exact_original_intake_replays_after_source_admission_without_writes(self):
        store = setup_store()
        intake = admit(store, "admit_technical_source", evidence=["source:job"], candidate=candidate(), key="first")
        publish(store, NOW + timedelta(seconds=1))
        context = {**WRITER, "previous_writer_reconciled": True, "orchestrator_verified": True}
        worker(store, context=context, now=NOW + timedelta(seconds=2))
        before = store.writes
        acknowledgement = dispatch(store, intake, context=context, now=NOW + timedelta(seconds=3))
        self.assertEqual(acknowledgement["status"], "queued")
        self.assertEqual(store.writes, before)

    def test_distinct_entity_aliases_to_one_canonical_source_admit_only_one_intent(self):
        store = setup_store()
        first = candidate()
        second = make_technical_record("technical_items", {**first["payload"],
            "actor": "muse", "entity_id": stable_id("technical_item", "different-agent"),
            "source_identity": stable_id("source", "different-agent")})
        self.assertEqual(first["payload"]["source_url"], second["payload"]["source_url"])
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="first")
        before = store.writes
        with self.assertRaises(DispatcherConflict):
            admit(store, "admit_technical_source", agent="muse", evidence=["source:job"],
                  candidate=second, key="second")
        self.assertEqual(store.writes, before)

    def test_same_source_identity_at_different_urls_cannot_duplicate_across_agents(self):
        store = setup_store()
        first = candidate()
        second = make_technical_record("technical_items", {**first["payload"],
            "actor": "muse", "entity_id": stable_id("technical_item", "different"),
            "source_url": "https://example.org/different-location"})
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="first")
        before = store.writes
        with self.assertRaises(DispatcherConflict):
            admit(store, "admit_technical_source", agent="muse", evidence=["source:job"],
                  candidate=second, key="second")
        self.assertEqual(store.writes, before)

    def test_partial_technical_intent_globally_fences_unrelated_intake(self):
        store = setup_store()
        store.fail_on_write = store.writes + 2  # intake written; first reservation uncertain
        with self.assertRaises(DispatcherStorageError):
            admit(store, "admit_technical_source", evidence=["source:job"], candidate=candidate(), key="first")
        store.fail_on_write = None
        before = store.writes
        with self.assertRaises(DispatcherStorageError):
            admit(store, "research", evidence=["source:job"], key="other")
        self.assertEqual(store.writes, before)

    def test_same_bound_entity_can_enqueue_a_new_revision(self):
        store = setup_store()
        first = candidate()
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="first")
        revised = make_technical_record("technical_items", {**first["payload"],
            "revision": 2, "content_hash": "b" * 64})
        result = admit(store, "admit_technical_source", evidence=["source:job"], candidate=revised, key="next")
        self.assertEqual(store.records[result["id"]], result)

    def test_every_partial_alias_chain_fences_other_intakes_after_restart(self):
        for fail_after in range(1, 8):
            for persists in (False, True):
                with self.subTest(fail_after=fail_after, persists=persists):
                    store = setup_store()
                    store.fail_on_write = store.writes + fail_after
                    store.persist_before_failure = persists
                    with self.assertRaises(DispatcherStorageError):
                        admit(store, "admit_technical_source", evidence=["source:job"], candidate=candidate(), key="first")
                    store.fail_on_write = None
                    before = store.writes
                    # If the first attempt failed without persisting its first
                    # append, no record is visible; the host must supply its
                    # separate prior-writer fence before any retry.
                    if fail_after == 1 and not persists:
                        continue
                    with self.assertRaises(DispatcherStorageError):
                        admit(store, "research", evidence=["source:job"], key="other")
                    self.assertEqual(store.writes, before)

    def test_other_lane_requires_host_reconciliation_when_technical_intake_exists(self):
        store = setup_store()
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=candidate(), key="first")
        agent = AgentProtocol(store, REGISTRY, "codex", now=NOW)
        action = action_for(agent, "research", "job:fixture", "other")
        envelope = agent.propose_action(action, ["source:job"])["data"]["envelope"]
        before = store.writes
        with self.assertRaises(DispatcherStorageError):
            dispatch(store, envelope, context=WRITER)
        self.assertEqual(store.writes, before)

    def test_missing_alias_after_completion_fences_index_and_worker_replay(self):
        store = setup_store()
        intake = admit(store, "admit_technical_source", evidence=["source:job"], candidate=candidate(), key="first")
        publish(store, NOW + timedelta(seconds=1))
        context = {**WRITER, "previous_writer_reconciled": True, "orchestrator_verified": True}
        worker(store, context=context, now=NOW + timedelta(seconds=2))
        alias = next(row for row in store.records.values() if row["kind"] == "technical_intake_alias_intents")
        del store.records[alias["id"]]
        manifest = publish(store, NOW + timedelta(seconds=3))
        self.assertFalse(manifest["payload"]["complete"])
        before = store.writes
        with self.assertRaises(WorkerStorageError):
            worker(store, context=context, now=NOW + timedelta(seconds=4))
        self.assertEqual(store.writes, before)
        client = AgentProtocol(store, REGISTRY, "muse", now=NOW + timedelta(seconds=4))
        self.assertFalse(client.get_action_result(intake["id"])["data"].get("verified", False))

    def test_direct_source_admission_respects_existing_intake_alias_binding(self):
        store = setup_store()
        first = candidate()
        admit(store, "admit_technical_source", evidence=["source:job"], candidate=first, key="first")
        second = make_technical_record("technical_items", {**first["payload"],
            "entity_id": stable_id("technical_item", "other"), "source_identity": stable_id("source", "other")})
        before = store.writes
        with self.assertRaises(AdmissionConflict):
            admit_technical_record(store, second, writer_context={**WRITER,
                "orchestrator_verified": True, "previous_writer_reconciled": True})
        self.assertEqual(store.writes, before)
