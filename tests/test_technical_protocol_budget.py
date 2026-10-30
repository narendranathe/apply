"""Wire-size regression using synthetic metadata and no provider access."""
import unittest
from datetime import datetime, timezone

from cloud.agent_protocol import AgentProtocol, ProtocolError
from cloud.records import canonical_json, make_record

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


class ManifestStore:
    def __init__(self, padding):
        self.record = make_record('index_manifests', {
            'generated_at': NOW.isoformat(), 'complete': True, 'conflicts': [],
            'campaign_brief': {'items': ['x' * padding] * 4},
        }, record_id='index:synthetic')

    def latest_index_manifest(self):
        return self.record


class TechnicalProtocolBudgetTests(unittest.TestCase):
    def test_metadata_wire_response_never_exceeds_configured_ceiling(self):
        # Exercise values around the boundary: a response may be rejected, but
        # every returned JSON envelope must fit, including usage and schema.
        for padding in range(100, 251, 5):
            with self.subTest(padding=padding):
                agent = AgentProtocol(ManifestStore(padding), now=NOW, max_context_bytes=1000)
                try:
                    result = agent.get_campaign_brief()
                except ProtocolError as exc:
                    self.assertEqual(exc.code, 'context_budget_exceeded_select_smaller_scope')
                else:
                    self.assertLessEqual(len(canonical_json(result).encode('utf-8')), 1000)


if __name__ == '__main__':
    unittest.main()
