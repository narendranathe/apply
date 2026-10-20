from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from cloud.orchestrator import _reconciled_slot, execute
from cloud.records import make_record
from cloud.runtime_guard import verify_previous_writers
from cloud.shadow import Blocked, ShadowStore
import tests.test_scheduled_reconciliation as scheduled_tests
import cloud.scheduled_reconciliation as recovery


class ReconciledSlotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scheduled_tests.ScheduledTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        _, captured = self.fixture.reconcile()
        self.store = ShadowStore()
        self.store.records = deepcopy(recovery.SnapshotState(captured.source).rows)

    def test_exact_reconciled_slot_is_skipped_without_completion_or_application_retry(self):
        before = deepcopy(self.store.records)
        with patch('cloud.applications.run_applications') as applications, \
                patch('cloud.reporting.run_reporting', return_value={}), \
                patch('cloud.orchestrator._publish_agent_index', return_value={}):
            result = execute(self.store, {'destination_private': True, 'cloud_enabled': True,
                             'reconcile_lookback_days': 0}, datetime(2026, 10, 18, 12, tzinfo=timezone.utc),
                             requested='applications', mode='live')
        applications.assert_not_called()
        self.assertEqual(result['results'], [{'lane': 'applications', 'date': '2026-10-18',
            'status': 'reconciled_no_submission', 'external_effects': 0, 'retry_external_effects': False}])
        self.assertEqual(self.store.records, before)
        self.assertIsNone(self.store.get(recovery.SLOT + ':completed'))

    def test_generic_or_different_slot_resolution_cannot_clear_pinned_incident(self):
        generic = make_record('audit_events', {'run_id': recovery.SLOT,
            'external_effects_reconciled': True, 'evidence': 'generic'},
            record_id=recovery.SLOT + ':reconciled', status='reconciled')
        self.assertFalse(_reconciled_slot(self.store, recovery.SLOT, generic))
        actual = self.store.get(recovery.SLOT + ':reconciled')
        self.assertFalse(_reconciled_slot(self.store, 'run:2026-10-19:applications', actual))

    def test_pinned_failed_writer_rejects_schema_without_the_dedicated_prefix(self):
        marker = self.store.get('writer-resolution:' + recovery.FAILED)
        payload = {**marker['payload'], 'schema': 'generic-manual-clearance'}
        self.store.records[marker['id']] = make_record('audit_events', payload,
            record_id=marker['id'], status=marker['status'])
        env = {'GITHUB_REPOSITORY': recovery.REPOSITORY, 'CLOUD_GITHUB_TOKEN': 'fixture-token',
               'GITHUB_RUN_ID': '999'}
        with patch.dict('os.environ', env), self.assertRaises(Blocked):
            verify_previous_writers(self.store, transport=lambda _: {
                'workflow_runs': [self.fixture.github.runs[recovery.FAILED]]})


if __name__ == '__main__':
    unittest.main()
