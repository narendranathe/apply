"""Synthetic GitHub cancellation proof; no network or operational state access."""
from copy import deepcopy
import io
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from cloud.notion import AmbiguousWriteError
from cloud.orchestrator import Blocked
from cloud.outreach import record
from cloud.records import content_hash
from cloud.runtime_guard import verify_previous_writers


ENV = {'GITHUB_REPOSITORY': 'narendranathe/apply', 'CLOUD_GITHUB_TOKEN': 'synthetic',
       'GITHUB_RUN_ID': '999'}


def cancelled(identity=123):
    return {'id': identity, 'display_title': 'cloud:live:accepted', 'status': 'completed',
            'conclusion': 'cancelled', 'head_branch': 'main', 'head_sha': 'a' * 40,
            'event': 'schedule', 'path': '.github/workflows/cloud-operations.yml',
            'run_attempt': 1, 'updated_at': '2026-10-17T15:40:00Z'}


class Store:
    def __init__(self):
        self.rows, self.writes = {}, []

    def get(self, identity):
        return deepcopy(self.rows.get(identity))

    def put(self, value):
        self.writes.append(deepcopy(value))
        self.rows[value['id']] = deepcopy(value)
        return deepcopy(value)


class GitHub:
    def __init__(self, runs=None):
        self.runs = deepcopy(runs if runs is not None else [cancelled()])
        self.actual = {str(row['id']): deepcopy(row) for row in self.runs}
        self.jobs = {str(row['id']): {'total_count': 0, 'jobs': []} for row in self.runs}
        self.calls, self.headers = [], {}
        self.after_jobs = None

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        self.calls.append(request.full_url)
        self.assert_get(request)
        base = '/repos/narendranathe/apply'
        path = url.path.removeprefix(base)
        if path == '/actions/workflows/cloud-operations.yml/runs':
            value = {'workflow_runs': self.runs}
        elif path.endswith('/jobs'):
            query = parse_qs(url.query)
            assert query == {'filter': ['all'], 'per_page': ['100'], 'page': ['1']}
            identity = path.split('/')[-2]
            value = self.jobs[identity]
            if isinstance(value, Exception):
                raise value
            if self.after_jobs:
                self.after_jobs(self.actual[identity])
        else:
            value = self.actual[path.rsplit('/', 1)[-1]]
        response = io.StringIO(json.dumps(value))
        response.headers = self.headers if path.endswith('/jobs') else {}
        return response

    @staticmethod
    def assert_get(request):
        assert request.get_method() == 'GET' and request.data is None


class CancelledWriterTests(unittest.TestCase):
    def verify(self, store, github):
        with patch.dict('os.environ', ENV, clear=True), patch('cloud.runtime_guard.urlopen', side_effect=github.open):
            verify_previous_writers(store)

    def assert_fenced(self, github):
        store = Store()
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(store, github)
        self.assertFalse(any(row['id'].startswith('writer-resolution:') for row in store.writes))
        self.assertEqual(store.writes[-1]['id'], 'writer-failure:123')

    def test_zero_allocated_jobs_are_independently_proven_and_persisted(self):
        github, store = GitHub(), Store()
        self.verify(store, github)
        self.assertEqual(len(store.writes), 1)
        resolution = store.writes[0]
        self.assertEqual(resolution['id'], 'writer-resolution:123')
        self.assertTrue(resolution['payload']['external_effects_reconciled'])
        self.assertEqual(resolution['payload']['evidence']['jobs']['total_count'], 0)
        self.assertEqual(resolution['payload']['evidence']['jobs']['filter'], 'all')
        self.assertEqual(resolution['payload']['evidence']['jobs']['pages_checked'], [1])
        self.assertEqual(sum('/actions/runs/123?' not in call and call.endswith('/actions/runs/123')
                             for call in github.calls), 2)
        self.assertEqual(store.get(resolution['id']), resolution)
        self.assertNotIn('synthetic', json.dumps(resolution))

    def test_any_allocated_job_or_incomplete_jobs_response_retains_fence(self):
        for value in ({'total_count': 1, 'jobs': [{'id': 7, 'status': 'queued'}]},
                      {'total_count': 1, 'jobs': []}, {'total_count': 0},
                      {'total_count': False, 'jobs': []}, {'total_count': 0, 'jobs': {}},
                      {'total_count': 0, 'jobs': [], 'incomplete_results': True},
                      {'total_count': 0, 'jobs': [], 'has_more': True},
                      RuntimeError('private upstream details')):
            with self.subTest(value=type(value).__name__):
                github = GitHub()
                github.jobs['123'] = value
                self.assert_fenced(github)

    def test_next_page_header_with_zero_count_does_not_prove_complete_empty_result(self):
        github = GitHub()
        github.headers = {'Link': '<https://api.github.com/example?page=2>; rel="next"'}
        self.assert_fenced(github)

    def test_wrong_or_changing_actual_run_metadata_cannot_resolve_listing(self):
        for field, value in (('id', 124), ('status', 'in_progress'), ('conclusion', 'failure'),
                             ('head_branch', 'other'), ('path', '.github/workflows/other.yml'),
                             ('display_title', 'cloud:dry-run:accepted'), ('run_attempt', True)):
            with self.subTest(field=field):
                github = GitHub()
                github.actual['123'][field] = value
                self.assert_fenced(github)
        github = GitHub()
        github.after_jobs = lambda row: row.update(status='queued', conclusion=None)
        self.assert_fenced(github)

    def test_previous_zero_job_resolution_is_rechecked_and_never_hides_rerun_jobs(self):
        github, store = GitHub(), Store()
        self.verify(store, github)
        self.verify(store, github)
        self.assertEqual(len(store.writes), 1)
        github.actual['123']['run_attempt'] = 2
        github.jobs['123'] = {'total_count': 1, 'jobs': [{'id': 8, 'status': 'completed'}]}
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(store, github)
        self.assertEqual(store.writes[-1]['id'], 'writer-failure:123')

    def test_all_zero_job_cancellations_resolve_but_real_failure_still_blocks(self):
        failed = {**cancelled(124), 'conclusion': 'failure'}
        github, store = GitHub([cancelled(), failed]), Store()
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(store, github)
        self.assertEqual([row['id'] for row in store.writes], ['writer-resolution:123', 'writer-failure:124'])
        self.assertFalse(any('/actions/runs/124' in call for call in github.calls))

    def test_uncertain_resolution_write_or_readback_stops_without_retry(self):
        for mode in ('raise', 'missing_write', 'missing_readback', 'changed_readback'):
            with self.subTest(mode=mode):
                class Uncertain(Store):
                    def put(self, value):
                        actual = super().put(value)
                        if mode == 'raise': raise AmbiguousWriteError('Private upstream details')
                        return None if mode == 'missing_write' else actual

                    def get(self, identity):
                        actual = super().get(identity)
                        if actual and identity.startswith('writer-resolution:'):
                            if mode == 'missing_readback': return None
                            if mode == 'changed_readback': actual['status'] = 'edited'
                        return actual
                store = Uncertain()
                with self.assertRaises(AmbiguousWriteError):
                    self.verify(store, GitHub())
                self.assertEqual(len(store.writes), 1)

    def test_existing_explicit_manual_resolution_and_current_run_are_preserved(self):
        github, store = GitHub([cancelled(), cancelled(999)]), Store()
        store.rows['writer-resolution:123'] = record('writer-resolution:123', 'audit_events',
            {'external_effects_reconciled': True, 'evidence': 'Existing independently reviewed evidence'})
        self.verify(store, github)
        self.assertEqual(store.writes, [])
        self.assertEqual(len(github.calls), 1)


class PinnedStartupResolutionTests(unittest.TestCase):
    verify = CancelledWriterTests.verify

    def fixture(self):
        failed = {**cancelled(), 'conclusion': 'failure',
                  'created_at': '2026-10-17T15:20:00Z', 'run_started_at': '2026-10-17T15:21:00Z'}
        github, store = GitHub([failed]), Store()
        steps = [{'number': 1, 'name': 'Set up job', 'status': 'completed', 'conclusion': 'success'},
                 {'number': 7, 'name': 'Run one coordinated operation', 'status': 'completed', 'conclusion': 'failure'}]
        github.jobs['123'] = {'total_count': 1, 'jobs': [{'id': 777, 'name': 'orchestrate',
            'status': 'completed', 'conclusion': 'failure', 'labels': ['ubuntu-latest'], 'steps': steps}]}
        payload = {'schema': 'apply-pinned-startup-writer-resolution/v1', 'github_run_id': '123',
            'reason': 'startup_guard_blocked_before_business_execution',
            'external_effects_reconciled': True, 'retry_external_effects': False,
            'evidence': {'run_url': 'https://github.com/narendranathe/apply/actions/runs/123',
                'commit': failed['head_sha'], 'incident': {'failed_run': deepcopy(failed),
                    'failed_job': {'id': 777, 'steps_sha256': content_hash(steps)}}}}
        store.rows['writer-resolution:123'] = record('writer-resolution:123', 'audit_events', payload,
                                                    'reconciled_no_business_execution')
        return github, store

    def assert_fenced(self, github, store):
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(store, github)
        self.assertEqual([row['id'] for row in store.writes], ['writer-failure:123'])

    def test_exact_startup_proof_is_accepted_only_after_fresh_readonly_run_and_job_checks(self):
        github, store = self.fixture()
        before = deepcopy(store.rows)
        self.verify(store, github)
        self.assertEqual(store.rows, before)
        self.assertEqual(store.writes, [])
        self.assertEqual(sum(call.endswith('/actions/runs/123') for call in github.calls), 2)
        self.assertEqual(sum('/actions/runs/123/jobs?' in call for call in github.calls), 1)

    def test_later_attempt_or_changed_run_metadata_never_inherits_startup_resolution(self):
        for field, value in (('run_attempt', 2), ('status', 'in_progress'), ('head_sha', 'b' * 40),
                             ('head_branch', 'other'), ('conclusion', 'cancelled'),
                             ('path', '.github/workflows/other.yml'), ('updated_at', '2026-10-18T00:00:00Z')):
            with self.subTest(field=field):
                github, store = self.fixture()
                github.actual['123'][field] = value
                self.assert_fenced(github, store)

    def test_changed_allocated_jobs_steps_or_partial_api_retains_fence(self):
        for mode in ('new_job', 'wrong_job', 'queued', 'changed_steps', 'partial', 'missing_jobs', 'api_error', 'next_page'):
            with self.subTest(mode=mode):
                github, store = self.fixture()
                jobs = github.jobs['123']
                if mode == 'new_job':
                    jobs['total_count'] = 2
                    jobs['jobs'].append({**deepcopy(jobs['jobs'][0]), 'id': 778})
                if mode == 'wrong_job': jobs['jobs'][0]['id'] = 778
                if mode == 'queued': jobs['jobs'][0]['status'] = 'queued'
                if mode == 'changed_steps': jobs['jobs'][0]['steps'][1]['conclusion'] = 'success'
                if mode == 'partial': jobs['jobs'] = []
                if mode == 'missing_jobs': jobs.pop('jobs')
                if mode == 'api_error': github.jobs['123'] = RuntimeError('Private upstream content')
                if mode == 'next_page': github.headers = {'Link': '<https://api.github.com/example?page=2>; rel="next"'}
                self.assert_fenced(github, store)

    def test_absent_or_changing_actual_run_evidence_retains_fence(self):
        github, store = self.fixture()
        github.actual['123'] = {}
        self.assert_fenced(github, store)
        github, store = self.fixture()
        github.after_jobs = lambda row: row.update(run_attempt=2)
        self.assert_fenced(github, store)

    def test_invalid_canonical_audit_scope_or_proof_never_gets_manual_fallback(self):
        for mode in ('hash', 'identity', 'kind', 'status', 'payload_id', 'reason', 'retry', 'job_digest', 'proof_fields', 'schema'):
            with self.subTest(mode=mode):
                github, store = self.fixture()
                row = store.rows['writer-resolution:123']
                if mode == 'hash': row['hash'] = '0' * 64
                if mode == 'identity': row['id'] = 'writer-resolution:other'
                if mode == 'kind': row['kind'] = 'reports'
                if mode == 'status': row['status'] = 'unverified'
                if mode == 'payload_id': row['payload']['github_run_id'] = '124'
                if mode == 'reason': row['payload']['reason'] = 'manual_override'
                if mode == 'retry': row['payload']['retry_external_effects'] = True
                if mode == 'job_digest': row['payload']['evidence']['incident']['failed_job']['steps_sha256'] = '0' * 64
                if mode == 'proof_fields': row['payload']['evidence']['incident']['failed_run'].pop('run_attempt')
                if mode == 'schema': row['payload']['schema'] = 'apply-pinned-startup-writer-resolution/v2'
                if mode != 'hash': row['hash'] = content_hash(row['payload'])
                self.assert_fenced(github, store)


if __name__ == '__main__':
    unittest.main()
