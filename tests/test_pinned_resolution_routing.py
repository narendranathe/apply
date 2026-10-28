"""Strict known incidents cannot inherit a foreign startup/manual clearance.

All stores and GitHub responses are synthetic. The startup matcher is real;
only the HTTP transport is replaced. No private evidence files are required.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

import cloud.runtime_guard as guard
from cloud.index_reconciliation import FAILED as INDEX_FAILED
from cloud.orchestrator import Blocked
from cloud.outreach import record
from cloud.readback_reconciliation import FAILURES as READBACK_FAILURES
from cloud.scheduled_reconciliation import FAILURES as SCHEDULED_FAILURES
from cloud.index_custody_reconciliation import FAILURES as CUSTODY_FAILURES
from tests import test_runtime_guard as startup_fixtures

ENV = startup_fixtures.ENV

STRICT_INCIDENTS = (*READBACK_FAILURES, INDEX_FAILED, *SCHEDULED_FAILURES, *CUSTODY_FAILURES)


class PinnedResolutionRoutingTests(unittest.TestCase):
    def fixture(self, identity='123'):
        github, store = startup_fixtures.PinnedStartupResolutionTests().fixture()
        original = store.rows.pop('writer-resolution:123')
        github.runs[0]['id'] = int(identity)
        actual = github.actual.pop('123')
        actual['id'] = int(identity)
        github.actual[identity] = actual
        github.jobs[identity] = github.jobs.pop('123')
        payload = deepcopy(original['payload'])
        payload['github_run_id'] = identity
        payload['evidence']['run_url'] = (
            'https://github.com/narendranathe/apply/actions/runs/' + identity)
        payload['evidence']['incident']['failed_run']['id'] = int(identity)
        store.rows['writer-resolution:' + identity] = record(
            'writer-resolution:' + identity, 'audit_events', payload, original['status'])
        return github, store

    def verify(self, github, store):
        with patch.dict('os.environ', ENV, clear=True), \
             patch.object(guard, 'urlopen', side_effect=github.open):
            guard.verify_previous_writers(store)

    def assert_fenced(self, identity, github, store):
        marker_before = store.get('writer-resolution:' + identity)
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(github, store)
        self.assertEqual([row['id'] for row in store.writes], ['writer-failure:' + identity])
        self.assertEqual(store.get('writer-resolution:' + identity), marker_before)
        self.assertEqual(store.writes[0]['payload'], {
            'github_run_id': identity, 'reason': 'failed_writer_requires_reconciliation',
            'retry_external_effects': False})
        self.assertEqual(store.writes[0]['status'], 'needs_reconciliation')

    def test_real_valid_startup_shape_cannot_clear_any_known_strict_incident(self):
        for identity in STRICT_INCIDENTS:
            github, store = self.fixture(identity)

            def fetch(path):
                if '/jobs?' in path:
                    return deepcopy(github.jobs[identity])
                return deepcopy(github.actual[identity])

            # The proof is valid for the generic startup matcher. A known
            # incident must still use its own stricter reconciliation family.
            self.assertTrue(guard._startup_resolution_matches(
                identity, store.get('writer-resolution:' + identity), fetch,
                ENV['GITHUB_REPOSITORY']))
            with self.subTest(identity=identity), \
                 patch.object(guard, '_startup_resolution_matches',
                              wraps=guard._startup_resolution_matches) as startup:
                self.assert_fenced(identity, github, store)
                startup.assert_not_called()

    def test_known_incident_rejects_generic_or_future_foreign_schema(self):
        schemas = (
            'synthetic-manual-review/v1',
            'apply-pinned-startup-writer-resolution/v999',
            'apply-pinned-index-readback-writer-resolution/v999',
            'apply-pinned-index-writer-resolution/v999',
            'apply-pinned-scheduled-writer-resolution/v999',
            'apply-pinned-index-custody-writer-resolution/v999',
        )
        for identity in STRICT_INCIDENTS:
            for schema in schemas:
                github, store = self.fixture(identity)
                marker = store.get('writer-resolution:' + identity)
                payload = deepcopy(marker['payload'])
                payload['schema'] = schema
                store.rows[marker['id']] = record(
                    marker['id'], marker['kind'], payload, marker['status'])
                with self.subTest(identity=identity, schema=schema), \
                     patch.object(guard, '_startup_resolution_matches',
                                  wraps=guard._startup_resolution_matches) as startup:
                    self.assert_fenced(identity, github, store)
                    startup.assert_not_called()

    def test_unrelated_legitimate_startup_still_uses_real_matcher_without_write(self):
        github, store = self.fixture()
        before = deepcopy(store.rows)
        with patch.object(guard, '_startup_resolution_matches',
                          wraps=guard._startup_resolution_matches) as startup:
            self.verify(github, store)
            startup.assert_called_once()
        self.assertEqual(store.rows, before)
        self.assertEqual(store.writes, [])
        self.assertEqual(sum(call.endswith('/actions/runs/123') for call in github.calls), 2)
        self.assertEqual(sum('/actions/runs/123/jobs?' in call for call in github.calls), 1)

    def test_unrelated_future_startup_schema_never_falls_back_to_manual_truthy_clearance(self):
        github, store = self.fixture()
        marker = store.get('writer-resolution:123')
        payload = deepcopy(marker['payload'])
        payload['schema'] = 'apply-pinned-startup-writer-resolution/v999'
        store.rows[marker['id']] = record(marker['id'], marker['kind'], payload, marker['status'])
        self.assert_fenced('123', github, store)


if __name__ == '__main__':
    unittest.main()
