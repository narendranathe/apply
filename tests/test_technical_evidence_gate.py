"""Synthetic technical statements cannot become ready tasks without evidence verification."""
import unittest

from cloud.agent_index import build_index
from cloud.agent_protocol import AgentProtocol
from tests.test_agent_protocol import FixtureStore, REGISTRY
from tests.test_technical_agent_index import NOW, admitted, technical


class TechnicalEvidenceGateTests(unittest.TestCase):
    def test_unverified_observation_and_unverified_source_never_become_ready(self):
        cases = [
            [technical("engineering_observations", "unsupported-observation", evidence_refs=[])],
            admitted([technical("technical_items", "unverified-source")]),
        ]
        for source_rows in cases:
            with self.subTest(source_kind=source_rows[0]["kind"]):
                source = source_rows[0]
                action = technical("technical_next_actions", "review-" + source["kind"],
                                   evidence_refs=[source["id"]])
                rows = build_index(source_rows + [action], NOW, source_complete=True)
                task = next(row for row in rows if row["kind"] == "agent_tasks"
                            and row["payload"]["entity_id"] == action["payload"]["entity_id"])
                self.assertEqual(task["status"], "blocked")
                self.assertIn("technical_evidence_verification_unavailable", task["payload"]["blockers"])
                store = FixtureStore()
                store.records.update({row["id"]: row for row in source_rows + [action] + rows})
                client = AgentProtocol(store, registry=REGISTRY, principal="muse", now=NOW)
                self.assertFalse(client.get_ready_tasks()["data"]["tasks"])
