"""Technical intake cannot trust an unproven previous-writer boundary."""
import unittest

from cloud.agent_dispatcher import DispatcherRejected, dispatch_intake
from cloud.agent_protocol import AgentProtocol
from tests.test_agent_protocol import NOW, REGISTRY, action_for
from tests.test_agent_dispatcher import AUTH, WRITER
from tests.test_agent_worker import job_store
from tests.test_technical_worker_intake import candidate


class TechnicalIntakeGuardTests(unittest.TestCase):
    def test_technical_intake_without_reconciliation_proof_stops_before_append(self):
        store = job_store()
        agent = AgentProtocol(store, REGISTRY, "codex", now=NOW)
        action = action_for(agent, "admit_technical_source", "job:fixture", "technical-guard")
        action["candidate"] = candidate()
        envelope = agent.propose_action(action, ["source:job"])["data"]["envelope"]
        before = store.writes
        with self.assertRaisesRegex(DispatcherRejected, "technical_previous_writer_reconciliation_required"):
            dispatch_intake(store, envelope, github_actor="fixture-github-codex", trusted_registry=AUTH,
                            writer_context=WRITER, now=NOW)
        self.assertEqual(store.writes, before)
        self.assertNotIn(envelope["id"], store.records)
