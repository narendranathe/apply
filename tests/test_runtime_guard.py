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


if __name__ == '__main__':
    unittest.main()
