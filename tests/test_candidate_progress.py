"""Bounded daily candidate advancement; synthetic provider GETs only."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from cloud.applications import fold_applications, run_applications
from cloud.records import make_record
from tests import test_applications_daily as daily


class CandidateProgressTests(unittest.TestCase):
    def setUp(self):
        self.fixture = daily.DailyApplicationTests()
        self.fixture.setUp()
        template = self.fixture.feed['jobs'][0]
        self.fixture.feed['jobs'] = [{**template, 'requisition_id': f'req-{posting}',
            'url': f'https://job-boards.greenhouse.io/acme/jobs/{posting}'} for posting in range(100, 112)]

    def seed(self, posting, status, *, attempt=False, company='Acme', requisition=True, url=None, unresolved=False):
        identity = 'synthetic-prior-' + str(posting)
        payload = {'application_id': identity, 'company': company, 'revision': 1,
            'url': url or f'https://job-boards.greenhouse.io/acme/jobs/{posting}'}
        if requisition: payload['requisition_id'] = f'req-{posting}'
        if attempt: payload['attempt'] = {'idempotency_key': 'synthetic-attempt'}
        if unresolved: payload['employer_history_unresolved'] = True
        self.fixture.store.put(make_record('applications', payload, identity, status))

    def reads(self, transport):
        self.assertTrue(all(method == 'GET' for method, _ in transport.calls))
        self.assertLessEqual(len(transport.calls), self.fixture.config['applications']['max_candidates'])
        return [int(url.split('/jobs/', 1)[1].split('?', 1)[0]) for _, url in transport.calls]

    def test_two_runs_advance_beyond_the_same_blocked_prefix(self):
        first, second = daily.DailyATSTransport(), daily.DailyATSTransport()
        self.fixture.run_day(17, first)
        self.fixture.run_day(18, second)
        self.assertEqual(self.reads(first), [100, 101, 102, 103, 104])
        self.assertEqual(self.reads(second), [105, 106, 107, 108, 109])
        self.assertEqual(len(fold_applications(self.fixture.store.query('applications'))), 10)

    def test_bounded_sequence_covers_unseen_roles_then_rechecks_covered_capacity(self):
        original = {}
        observed = []
        for day in (17, 18, 19):
            transport = daily.DailyATSTransport()
            result = self.fixture.run_day(day, transport)
            observed.append(self.reads(transport))
            self.assertEqual(result['submission_attempts'], 0)
            self.assertEqual(result['candidates_seen'], 5)
            self.assertTrue(all(self.fixture.store.records[identity] == row for identity, row in original.items()))
            original = deepcopy(self.fixture.store.records)
        self.assertEqual(observed, [list(range(100, 105)), list(range(105, 110)), [110, 111, 100, 101, 102]])
        self.assertEqual(len(fold_applications(self.fixture.store.query('applications'))), 12)

    def test_qualified_and_prepared_without_attempt_keep_priority_over_new_work(self):
        self.seed(110, 'qualified'); self.seed(111, 'prepared')
        transport = daily.DailyATSTransport()
        self.fixture.run_day(17, transport)
        self.assertEqual(self.reads(transport), [110, 111, 100, 101, 102])

    def test_suppressed_statuses_and_attempts_do_not_consume_verification_capacity(self):
        cases = [('applied', False), ('applied_verified', False), ('submission_unknown', False),
            ('submitting', False), ('interview', False), ('closed', False), ('rejected', False),
            ('withdrawn', False), ('manual_action_needed', False), ('prepared', True)]
        for posting, (status, attempt) in enumerate(cases, 100):
            self.seed(posting, status, attempt=attempt)
        original = deepcopy(self.fixture.store.records)
        transport = daily.DailyATSTransport()
        result = self.fixture.run_day(17, transport)
        self.assertEqual(self.reads(transport), [110, 111])
        self.assertEqual(result['candidates_seen'], 2)
        self.assertEqual(result['duplicates'], len(cases))
        self.assertEqual(result['submission_attempts'], 0)
        self.assertTrue(all(self.fixture.store.records[identity] == row for identity, row in original.items()))

    def test_known_tracking_url_alias_is_suppressed_before_budget(self):
        self.seed(100, 'submission_unknown', url='https://boards.greenhouse.io/acme/jobs/100')
        self.fixture.feed['jobs'][0]['url'] += '?utm_source=synthetic'
        transport = daily.DailyATSTransport()
        result = self.fixture.run_day(17, transport)
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual(self.reads(transport), [101, 102, 103, 104, 105])

    def test_broad_unknown_employer_hold_remains_blocked_for_different_requisitions(self):
        self.seed(900, 'submission_unknown', company='Held Employer', requisition=False,
            url='https://job-boards.greenhouse.io/held/jobs/900')
        template = self.fixture.feed['jobs'][0]
        self.fixture.feed['jobs'][:0] = [{**template, 'company': 'Held Employer',
            'requisition_id': f'different-{posting}', 'url': f'https://job-boards.greenhouse.io/held/jobs/{posting}'}
            for posting in range(901, 907)]
        original = deepcopy(self.fixture.store.records)
        transport = daily.DailyATSTransport()
        result = self.fixture.run_day(17, transport)
        self.assertEqual(result['duplicates'], 6)
        self.assertEqual(self.reads(transport), [100, 101, 102, 103, 104])
        self.assertTrue(all(self.fixture.store.records[identity] == row for identity, row in original.items()))
        self.assertFalse(any(row['payload'].get('company') == 'Held Employer'
            for row in self.fixture.store.query('applications') if row['id'] not in original))

    def test_verified_different_requisition_remains_new_work(self):
        self.seed(999, 'submission_unknown')
        transport = daily.DailyATSTransport()
        result = self.fixture.run_day(17, transport)
        self.assertEqual(result['duplicates'], 0)
        self.assertEqual(self.reads(transport), [100, 101, 102, 103, 104])

    def test_retryable_employer_placeholder_remains_covered_without_history_rewrite(self):
        self.seed(900, 'submission_blocked', company='Held Employer', requisition=False,
            url='https://job-boards.greenhouse.io/held/jobs/900')
        template = self.fixture.feed['jobs'][0]
        self.fixture.feed['jobs'].insert(0, {**template, 'company': 'Held Employer',
            'requisition_id': 'different-901', 'url': 'https://job-boards.greenhouse.io/held/jobs/901'})
        original = deepcopy(self.fixture.store.records)
        transport = daily.DailyATSTransport()
        self.fixture.run_day(17, transport)
        self.assertEqual(self.reads(transport), [100, 101, 102, 103, 104])
        self.assertTrue(all(self.fixture.store.records[identity] == row for identity, row in original.items()))

    def run_qualified_probe(self, transport, prepare, adapter=None):
        daily.FrozenDateTime.current = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        with patch('cloud.applications.dt.datetime', daily.FrozenDateTime), patch(
                'cloud.applications.grade_job', return_value={'qualified': True}):
            if adapter is None:
                return run_applications(self.fixture.store, self.fixture.config, False,
                    feed=self.fixture.feed, transport=transport, prepare=prepare)
            with patch('cloud.applications.adapter_for', return_value=adapter):
                return run_applications(self.fixture.store, self.fixture.config, False,
                    feed=self.fixture.feed, transport=transport, prepare=prepare)

    def test_explicit_unresolved_prepared_or_qualified_history_never_reaches_preparation(self):
        for status in ('prepared', 'qualified'):
            with self.subTest(status=status):
                self.setUp()
                self.fixture.feed['jobs'] = self.fixture.feed['jobs'][:1]
                self.seed(100, status, unresolved=True)
                original = deepcopy(self.fixture.store.records)
                transport, prepare = daily.DailyATSTransport(), Mock(return_value={'ready': False, 'reason': 'synthetic_stop'})
                result = self.run_qualified_probe(transport, prepare)
                self.assertEqual(transport.calls, [])
                prepare.assert_not_called()
                self.assertEqual(self.fixture.store.records, original)
                self.assertEqual(result['duplicates'], 1)
                self.assertEqual(result['exceptions'], {'ambiguous_application_history_requires_reconciliation': 1})
                self.assertEqual(result['submission_attempts'], 0)

    def test_employer_only_prepared_or_qualified_different_url_remains_held(self):
        for status in ('prepared', 'qualified'):
            with self.subTest(status=status):
                self.setUp()
                self.fixture.feed['jobs'] = self.fixture.feed['jobs'][:1]
                self.seed(999, status, requisition=False)
                original = deepcopy(self.fixture.store.records)
                transport, prepare = daily.DailyATSTransport(), Mock(return_value={'ready': False, 'reason': 'synthetic_stop'})
                result = self.run_qualified_probe(transport, prepare)
                self.assertEqual(transport.calls, [])
                prepare.assert_not_called()
                self.assertEqual(self.fixture.store.records, original)
                self.assertEqual(result['exceptions'], {'ambiguous_application_history_requires_reconciliation': 1})

    def test_post_official_canonical_match_cannot_rewrite_unresolved_history(self):
        for status, explicit in (('prepared', False), ('qualified', False), ('prepared', True), ('qualified', True)):
            with self.subTest(status=status, explicit=explicit):
                self.setUp()
                self.fixture.feed['jobs'] = self.fixture.feed['jobs'][:1]
                self.fixture.feed['jobs'][0]['company'] = 'Feed Company'
                self.seed(999, status, requisition=explicit, unresolved=explicit)
                original = deepcopy(self.fixture.store.records)
                job = {'open': True, 'company': 'Acme', 'requisition_id': 'req-100', 'posting_id': '100',
                    'aliases': [self.fixture.feed['jobs'][0]['url']], 'url': self.fixture.feed['jobs'][0]['url'],
                    'title': 'Data Engineer', 'jd': 'SQL', 'evidence_url': 'https://synthetic.test/official-job'}
                adapter = Mock(); adapter.verify.return_value = job
                transport, prepare = daily.DailyATSTransport(), Mock(return_value={'ready': False, 'reason': 'synthetic_stop'})
                result = self.run_qualified_probe(transport, prepare, adapter)
                self.assertEqual(adapter.verify.call_count, 1)
                prepare.assert_not_called()
                adapter.submit.assert_not_called()
                self.assertEqual(self.fixture.store.records, original)
                self.assertEqual(result['exceptions'], {'ambiguous_application_history_requires_reconciliation': 1})

    def test_exact_url_parser_placeholder_remains_retryable_without_clearing_identity(self):
        self.fixture.feed['jobs'] = self.fixture.feed['jobs'][:1]
        self.seed(100, 'submission_blocked', requisition=False)
        original = deepcopy(self.fixture.store.records)
        transport = daily.DailyATSTransport()
        result = self.fixture.run_day(17, transport)
        self.assertEqual(self.reads(transport), [100])
        self.assertEqual(result['exceptions'], {'qualification_gaps': 1})
        latest = fold_applications(self.fixture.store.query('applications'))['synthetic-prior-100']
        self.assertIsNone(latest['payload'].get('requisition_id'))
        self.assertEqual(latest['payload']['url'], original['synthetic-prior-100']['payload']['url'])
        self.assertTrue(all(self.fixture.store.records[identity] == row for identity, row in original.items()))

    def test_every_matching_suppression_wins_over_prepared_regardless_of_row_order(self):
        cases = [('submission_unknown', False, False, False), ('applied_verified', False, False, False),
            ('submitting', False, False, False), ('prepared', True, False, False),
            ('prepared', False, True, False), ('qualified', False, False, True)]
        for status, attempt, unresolved, broad in cases:
            for reverse in (False, True):
                with self.subTest(status=status, attempt=attempt, unresolved=unresolved, broad=broad, reverse=reverse):
                    self.setUp(); self.fixture.feed['jobs'] = self.fixture.feed['jobs'][:1]
                    self.seed(100, 'prepared')
                    payload = {'application_id': 'different-identity', 'company': 'Acme', 'revision': 1,
                        'url': 'https://boards.greenhouse.io/acme/jobs/' + ('999' if broad else '100')}
                    if not broad: payload['requisition_id'] = 'req-100'
                    if attempt: payload['attempt'] = {'idempotency_key': 'synthetic-attempt'}
                    if unresolved: payload['employer_history_unresolved'] = True
                    self.fixture.store.put(make_record('applications', payload, 'different-identity', status))
                    if reverse: self.fixture.store.records = dict(reversed(list(self.fixture.store.records.items())))
                    original = deepcopy(self.fixture.store.records)
                    transport, prepare = daily.DailyATSTransport(), Mock(return_value={'ready': False})
                    result = self.run_qualified_probe(transport, prepare)
                    self.assertEqual(transport.calls, [])
                    prepare.assert_not_called()
                    self.assertEqual(result['duplicates'], 1)
                    self.assertEqual(result['submission_attempts'], 0)
                    self.assertEqual(self.fixture.store.records, original)

    def test_post_canonical_alias_cannot_hide_a_suppressed_peer_behind_prepared(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.setUp(); self.fixture.feed['jobs'] = self.fixture.feed['jobs'][:1]
                self.fixture.feed['jobs'][0]['company'] = 'Feed Company'
                self.seed(100, 'prepared')
                payload = {'application_id': 'alias-peer', 'company': 'Other Employer', 'revision': 1,
                    'requisition_id': 'different-feed-id', 'url_aliases': ['https://boards.greenhouse.io/acme/jobs/100']}
                self.fixture.store.put(make_record('applications', payload, 'alias-peer', 'submission_unknown'))
                if reverse: self.fixture.store.records = dict(reversed(list(self.fixture.store.records.items())))
                original = deepcopy(self.fixture.store.records)
                job = {'open': True, 'company': 'Acme', 'requisition_id': 'req-100', 'posting_id': '100',
                    'aliases': ['https://boards.greenhouse.io/acme/jobs/100'], 'url': self.fixture.feed['jobs'][0]['url'],
                    'title': 'Data Engineer', 'jd': 'SQL', 'evidence_url': 'https://synthetic.test/official-job'}
                adapter = Mock(); adapter.verify.return_value = job
                prepare = Mock(return_value={'ready': False})
                self.run_qualified_probe(daily.DailyATSTransport(), prepare, adapter)
                prepare.assert_not_called(); adapter.submit.assert_not_called()
                self.assertEqual(self.fixture.store.records, original)


if __name__ == '__main__': unittest.main()
