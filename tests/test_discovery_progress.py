"""Discovery advances using durable observed coverage, with synthetic official reads."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from cloud.discovery import run_discovery
from cloud.records import make_record
from cloud.shadow import ShadowStore
from cloud.submission import Response


class OfficialReads:
    def __init__(self): self.calls = []

    def request(self, method, url, *, headers=None, body=None):
        if method != 'GET': raise AssertionError('discovery must never submit')
        self.calls.append((method, url))
        posting = int(url.split('/jobs/', 1)[1].split('?', 1)[0])
        return Response(200, {'id': posting, 'company_name': 'Acme', 'requisition_id': f'req-{posting}',
            'title': 'AI Platform Engineer' if posting % 2 == 0 else 'Data Engineer',
            'content': '<h3>Requirements</h3><li>SQL</li>', 'location': {'name': 'Dallas, TX'},
            'absolute_url': f'https://job-boards.greenhouse.io/acme/jobs/{posting}'})


class DiscoveryProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        self.config = {'applications': {'max_candidates': 5}, 'outreach': {'research_ai_share': 0.6}}
        self.feed = {'exported_at': self.now.isoformat(), 'jobs': [self.lead(posting) for posting in range(100, 112)]}
        self.store = ShadowStore()

    def lead(self, posting):
        return {'company': 'Acme', 'title': 'AI Platform Engineer' if posting % 2 == 0 else 'Data Engineer',
            'requisition_id': f'req-{posting}', 'url': f'https://job-boards.greenhouse.io/acme/jobs/{posting}',
            'is_active': True, 'posted_at': self.now.isoformat(), 'relevance_score': 0.6}

    def run_day(self, offset=0):
        now = self.now + timedelta(days=offset)
        feed = {**self.feed, 'exported_at': now.isoformat()}
        transport = OfficialReads()
        result = run_discovery(self.store, self.config, now, feed, transport)
        self.assertLessEqual(len(transport.calls), 5)
        self.assertTrue(all(method == 'GET' for method, _ in transport.calls))
        return result, [int(url.split('/jobs/', 1)[1].split('?', 1)[0]) for _, url in transport.calls]

    def cover(self, posting, *, application=False, status='submission_blocked'):
        payload = {'company': 'Acme', 'requisition_id': f'req-{posting}',
            'url': f'https://job-boards.greenhouse.io/acme/jobs/{posting}'}
        self.store.put(make_record('applications' if application else 'jobs', payload,
            'synthetic-covered-' + str(posting), status))

    def test_job_only_observations_advance_second_run_without_application_mutation(self):
        first, initial = self.run_day()
        original = deepcopy(self.store.records)
        second, following = self.run_day(1)
        self.assertEqual(initial, [100, 102, 104, 101, 103])
        self.assertEqual(following, [106, 108, 110, 105, 107])
        self.assertEqual(first['research_tasks'], second['research_tasks'])
        self.assertEqual(len(self.store.query('jobs')), 10)
        self.assertEqual(self.store.query('applications'), [])
        self.assertTrue(all(self.store.records[identity] == row for identity, row in original.items()))

    def test_application_coverage_moves_fresh_roles_first_without_changing_suppression(self):
        for posting in range(100, 105): self.cover(posting, application=True, status='submission_unknown')
        original = deepcopy(self.store.records)
        result, checked = self.run_day()
        self.assertEqual(checked, [106, 108, 110, 105, 107])
        self.assertEqual(result['duplicates'], 0)  # Selected roles are genuinely unmatched.
        self.assertTrue(all(self.store.records[identity] == row for identity, row in original.items()))

    def test_fill_covered_capacity_respects_original_overall_ai_target(self):
        # Only one fresh AI role; fill with two covered AI and two covered data.
        for posting in range(101, 112): self.cover(posting)
        _, checked = self.run_day()
        self.assertEqual(checked[0], 100)
        self.assertEqual(len(checked), 5)
        self.assertEqual(sum(posting % 2 == 0 for posting in checked), 3)

    def test_covered_pool_is_rechecked_when_no_unmatched_roles_remain(self):
        for posting in range(100, 112): self.cover(posting)
        _, checked = self.run_day()
        self.assertEqual(checked, [100, 102, 104, 101, 103])

    def test_distinct_requisition_not_hidden_by_same_employer_job_history(self):
        self.cover(999)
        _, checked = self.run_day()
        self.assertEqual(checked, [100, 102, 104, 101, 103])

    def test_empty_feed_has_no_provider_calls_or_writes(self):
        self.feed['jobs'] = []
        result, checked = self.run_day()
        self.assertEqual(checked, [])
        self.assertEqual(result['research_tasks'], 0)
        self.assertEqual(self.store.records, {})


if __name__ == '__main__': unittest.main()
