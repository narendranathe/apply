"""A fresh coverage scan must not consume time reserved for recovery."""
from contextlib import ExitStack
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from cloud.applications import run_applications
from cloud.cutover import make_cutover_record
from cloud.orchestrator import execute
from cloud.runtime_budget import RuntimeBudget
from cloud.shadow import ShadowStore
import tests.test_applications as application_fixtures
from tests.test_cutover import evidence


NOW = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)
RESERVE_REASON = 'runtime_budget_reserved_for_receipts_and_backup'


class EffectBudgetCoverageTests(unittest.TestCase):
    def run_application_after_coverage(self, coverage_finished_at):
        fixture = application_fixtures.ApplicationTests(
            methodName='test_happy_path_requires_receipt_and_no_manual_approval_state')
        fixture.setUp()
        try:
            store = ShadowStore()
            store.put(make_cutover_record(evidence()))
            transport = application_fixtures.ATSTransport()
            clock, coverage_times = [0], []
            budget = RuntimeBudget(100, 20, clock=lambda: clock[0])

            def fresh_coverage(*_):
                coverage_times.append(clock[0])
                if len(coverage_times) == 2:
                    clock[0] = coverage_finished_at

            def applications(working, config, dry_run, *, effect_guard, **guards):
                self.assertFalse(dry_run)
                clock[0] = 79
                return run_applications(working, config, dry_run, feed=fixture.feed,
                    transport=transport, prepare=fixture.prepare, effect_guard=effect_guard, **guards)

            config = {**fixture.config, 'destination_private': True, 'cloud_enabled': True,
                      'reconcile_lookback_days': 0, '_reconciled_export': {'records': []}}
            with ExitStack() as stack:
                stack.enter_context(patch('cloud.baseline_guard.verify_application_coverage',
                                          side_effect=fresh_coverage))
                stack.enter_context(patch('cloud.reporting.effective_research_share', return_value=.6))
                stack.enter_context(patch('cloud.applications.run_applications', side_effect=applications))
                stack.enter_context(patch('cloud.reporting.run_reporting', return_value={'counts': {}}))
                stack.enter_context(patch('cloud.orchestrator._publish_agent_index', return_value={}))
                result = execute(store, config, NOW, requested='applications', mode='live', budget=budget)

            self.assertEqual(coverage_times, [0, 79])
            return result['results'][0], store, transport
        finally:
            fixture.tearDown()

    def test_coverage_scan_reaching_reserve_blocks_intent_and_provider(self):
        for finished_at in (80, 81):
            with self.subTest(coverage_finished_at=finished_at):
                result, store, transport = self.run_application_after_coverage(finished_at)

                self.assertEqual(result['submission_attempts'], 0)
                self.assertEqual(result['applied_verified'], 0)
                self.assertEqual(result['exceptions'], {RESERVE_REASON: 1})
                self.assertFalse(any(method == 'POST' for method, _, _ in transport.calls))
                applications = store.query('applications')
                self.assertFalse(any(row['status'] == 'submitting' or row['payload'].get('attempt')
                                     for row in applications))
                self.assertEqual(applications[-1]['status'], 'submission_blocked')

    def test_coverage_scan_with_capacity_allows_one_verified_sandbox_submission(self):
        result, store, transport = self.run_application_after_coverage(79)

        self.assertEqual(result['submission_attempts'], 1)
        self.assertEqual(result['applied_verified'], 1)
        self.assertEqual(result['exceptions'], {})
        self.assertEqual(sum(method == 'POST' for method, _, _ in transport.calls), 1)
        self.assertEqual(store.query('applications')[-1]['status'], 'applied_verified')


if __name__ == '__main__':
    unittest.main()
