import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from cloud.discovery import choose_mix, run_discovery
from cloud.orchestrator import ShadowStore


class DiscoveryTests(unittest.TestCase):
    def test_mix_is_research_allocation_not_outcome(self):
        leads = [{'title': 'AI Platform ' + str(n)} for n in range(5)] + [{'title': 'Data Engineer ' + str(n)} for n in range(5)]
        chosen = choose_mix(leads, 5)
        self.assertEqual(len(chosen), 5)
        self.assertEqual(sum('AI' in row['title'] for row in chosen), 3)

    def test_stale_or_unknown_feed_stops_discovery(self):
        result = run_discovery(ShadowStore(), {}, datetime(2026, 10, 12, tzinfo=timezone.utc), {'jobs': []})
        self.assertEqual(result['exceptions'], {'feed_stale_or_timestamp_unknown': 1})

    def test_official_evidence_produces_route_research_without_owner_claim(self):
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        store = ShadowStore()
        feed = {'exported_at': now.isoformat(), 'jobs': [{'title': 'Data Engineer', 'company': 'Acme', 'url': 'https://boards.greenhouse.io/acme/jobs/123', 'posted_at': now.isoformat(), 'relevance_score': 0.8}]}
        job = {'open': True, 'company': 'Acme', 'requisition_id': '123', 'title': 'Data Engineer', 'jd': 'Required SQL', 'evidence_url': 'https://boards-api.greenhouse.io/v1/boards/acme/jobs/123'}
        with patch('cloud.discovery.adapter_for') as adapter:
            adapter.return_value.verify.return_value = job
            result = run_discovery(store, {}, now, feed)
        self.assertEqual(result['official_open'], 1)
        plan = store.query(kind='conversation_observations')[0]['payload']
        self.assertIsNone(plan['verified_hiring_owner'])
        self.assertEqual(plan['known_contact_ids'], [])


if __name__ == '__main__':
    unittest.main()
