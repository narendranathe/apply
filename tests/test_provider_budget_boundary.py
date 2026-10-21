"""Real pipeline regressions for budget exhaustion after durable intent."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from cloud.applications import run_applications
from cloud.cutover import make_cutover_record
from cloud.notion import AmbiguousWriteError
from cloud.orchestrator import execute
from cloud.runtime_budget import RuntimeBudget
from cloud.shadow import ShadowStore
from cloud.submission import (GreenhouseAdapter, LeverAdapter, ProviderBudgetTransport,
                              Response, SubmissionBlocked, multipart)
import tests.test_applications as fixtures
from tests.test_cutover import evidence


NOW = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)
DENIAL = {'status': 'submission_unknown', 'reason': 'provider_not_invoked_runtime_reserve',
          'provider_request_made': False}


class ProviderBudgetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ApplicationTests(
            methodName='test_happy_path_requires_receipt_and_no_manual_approval_state')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def scenario(self, stage, finished_at, *, fail_unknown=False):
        clock = [0]
        budget = RuntimeBudget(100, 20, clock=lambda: clock[0])

        class Store(ShadowStore):
            def put(self, row):
                if fail_unknown and row['kind'] == 'applications' and row['status'] == 'submission_unknown':
                    raise AmbiguousWriteError('synthetic unknown persistence')
                result = super().put(row)
                if stage == 'intent' and row['kind'] == 'applications' and row['status'] == 'submitting':
                    clock[0] = finished_at
                return result

        store, transport = Store(), fixtures.ATSTransport()
        store.put(make_cutover_record(evidence()))
        config = {**self.fixture.config, 'destination_private': True, 'cloud_enabled': True,
                  'reconcile_lookback_days': 0, '_reconciled_export': {'records': []}}

        def applications(working, settings, dry_run, *, effect_guard, **guards):
            clock[0] = 79
            return run_applications(working, settings, dry_run, feed=self.fixture.feed,
                transport=transport, prepare=self.fixture.prepare, effect_guard=effect_guard, **guards)

        def slow_multipart(*args, **kwargs):
            result = multipart(*args, **kwargs)
            if stage == 'multipart':
                clock[0] = finished_at
            return result

        with ExitStack() as stack:
            stack.enter_context(patch('cloud.baseline_guard.verify_application_coverage'))
            stack.enter_context(patch('cloud.reporting.effective_research_share', return_value=.6))
            stack.enter_context(patch('cloud.applications.run_applications', side_effect=applications))
            report = stack.enter_context(patch('cloud.reporting.run_reporting', return_value={}))
            stack.enter_context(patch('cloud.orchestrator._publish_agent_index', return_value={}))
            stack.enter_context(patch('cloud.submission.multipart', side_effect=slow_multipart))
            if fail_unknown:
                with self.assertRaises(AmbiguousWriteError):
                    execute(store, config, NOW, requested='applications', mode='live', budget=budget)
                report.assert_not_called()
                self.assertFalse(any(method == 'POST' for method, _, _ in transport.calls))
                self.assertEqual(store.query('applications')[-1]['status'], 'submitting')
                repeated = run_applications(store, config, False, feed=self.fixture.feed,
                    transport=transport, prepare=self.fixture.prepare)
                self.assertEqual(repeated['duplicates'], 1)
                self.assertEqual(repeated['submission_attempts'], 0)
                self.assertFalse(any(method == 'POST' for method, _, _ in transport.calls))
                return
            result = execute(store, config, NOW, requested='applications', mode='live', budget=budget)

        original = deepcopy(store.records)
        repeated = run_applications(store, config, False, feed=self.fixture.feed,
            transport=transport, prepare=self.fixture.prepare)
        self.assertEqual(repeated['duplicates'], 1)
        self.assertEqual(repeated['submission_attempts'], 0)
        self.assertEqual(store.records, original)
        return result['results'][0], store, transport

    def test_slow_intent_or_multipart_preserves_unknown_intent_without_provider_request(self):
        for stage in ('intent', 'multipart'):
            for finished_at in (80, 81):
                with self.subTest(stage=stage, finished_at=finished_at):
                    result, store, transport = self.scenario(stage, finished_at)
                    self.assertFalse(any(method == 'POST' for method, _, _ in transport.calls))
                    self.assertEqual(result['submission_attempts'], 1)
                    self.assertEqual(result['submission_unknown'], 1)
                    self.assertEqual(result['applied_verified'], 0)
                    rows = store.query('applications')
                    intents = [row for row in rows if row['status'] == 'submitting']
                    self.assertEqual(len(intents), 1)
                    self.assertEqual(rows[-1]['status'], 'submission_unknown')
                    self.assertEqual(rows[-1]['payload']['receipt'], DENIAL)
                    self.assertEqual(rows[-1]['payload']['attempt'], intents[0]['payload']['attempt'])
                    packet = store.query('application_packets')[0]
                    self.assertEqual(intents[0]['payload']['attempt']['resume_hash'], packet['payload']['resume_sha256'])
                    self.assertEqual(intents[0]['payload']['attempt']['answers_hash'], packet['payload']['answers_hash'])

    def test_remaining_capacity_allows_exactly_one_verified_sandbox_request(self):
        result, store, transport = self.scenario('multipart', 79)
        self.assertEqual(sum(method == 'POST' for method, _, _ in transport.calls), 1)
        self.assertEqual(result['submission_attempts'], 1)
        self.assertEqual(result['applied_verified'], 1)
        self.assertEqual(result['submission_unknown'], 0)
        self.assertEqual(store.query('applications')[-1]['status'], 'applied_verified')

    def test_unknown_persistence_failure_fences_the_enclosing_run(self):
        self.scenario('intent', 81, fail_unknown=True)

    def test_both_adapters_distinguish_guard_denial_from_an_ambiguous_request(self):
        employer = self.fixture.config['applications']['employers']['acme']
        packet = self.fixture.prepare({}, {}, {})
        for adapter_type in (GreenhouseAdapter, LeverAdapter):
            with self.subTest(adapter=adapter_type.__name__):
                transport = fixtures.ATSTransport()
                budget = RuntimeBudget(100, 20, clock=lambda: 0)
                budget.clock = lambda: 80
                adapter = adapter_type(employer, ProviderBudgetTransport(transport, budget.require_effect_capacity))
                result = adapter.submit({'endpoint': 'https://sandbox.example.test/apply'}, packet)
                self.assertEqual(result, DENIAL)
                self.assertEqual(transport.calls, [])

                uncertain = fixtures.ATSTransport(uncertain=True)
                adapter = adapter_type(employer, ProviderBudgetTransport(uncertain, lambda: None))
                result = adapter.submit({'endpoint': 'https://sandbox.example.test/apply'}, packet)
                self.assertEqual(result, {'status': 'submission_unknown', 'reason': 'submission_response_uncertain'})
                self.assertEqual(len(uncertain.calls), 1)
                self.assertNotIn('provider_request_made', result)

    def test_default_transport_is_wrapped_once_and_previews_do_not_call_the_guard(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                store, transport = ShadowStore(), fixtures.ATSTransport()
                budget = RuntimeBudget(100, 20, clock=lambda: 0)
                budget.clock = lambda: 80
                guard = Mock(side_effect=budget.require_effect_capacity)
                with patch('cloud.applications.HTTPTransport', return_value=transport) as factory:
                    result = run_applications(store, self.fixture.config, dry_run, feed=self.fixture.feed,
                        transport=None if not dry_run else transport, prepare=self.fixture.prepare,
                        provider_budget_guard=guard)
                self.assertFalse(any(method == 'POST' for method, _, _ in transport.calls))
                if dry_run:
                    guard.assert_not_called()
                    factory.assert_not_called()
                    self.assertEqual(store.records, {})
                else:
                    factory.assert_called_once_with()
                    guard.assert_called_once_with()
                    self.assertEqual(result['submission_attempts'], 1)
                    self.assertEqual(result['submission_unknown'], 1)

    def test_transport_preserves_reads_and_never_forwards_unchecked_mutations(self):
        response = Response(200, {'synthetic': True})
        inner, guard = Mock(), Mock()
        inner.request.return_value = response
        transport = ProviderBudgetTransport(inner, guard)
        self.assertIs(transport.request('GET', 'https://sandbox.example.test/job', headers={'X-Test': 'value'}), response)
        inner.request.assert_called_once_with('GET', 'https://sandbox.example.test/job', headers={'X-Test': 'value'}, body=None)
        guard.assert_not_called()
        inner.reset_mock()
        with self.assertRaisesRegex(SubmissionBlocked, '^unsupported_provider_request_method$'):
            transport.request('PUT', 'https://sandbox.example.test/job', body=b'synthetic')
        inner.request.assert_not_called()
        guard.assert_not_called()


if __name__ == '__main__':
    unittest.main()
