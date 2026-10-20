"""The queue consumer shares production authority and cannot run in previews."""
from contextlib import ExitStack
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from cloud.cutover import make_cutover_record
from cloud.orchestrator import Blocked, ShadowStore, _agent_worker_authority, execute
from cloud.outreach import record
from tests.test_cutover import evidence


HOST = {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main',
    'GITHUB_REPOSITORY': 'narendranathe/apply', 'GITHUB_JOB': 'orchestrate',
    'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_RUN_ID': '12345',
    'GITHUB_WORKFLOW_REF': 'narendranathe/apply/.github/workflows/cloud-operations.yml@refs/heads/main'}
NOW = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)


class WorkerOrchestrationTests(unittest.TestCase):
    def test_deployment_registry_and_reserved_host_are_required(self):
        with patch.dict(os.environ, HOST, clear=True):
            registry, context = _agent_worker_authority()
        self.assertEqual(registry['narendranathe']['principal'], 'naren-github')
        self.assertNotIn('execute_provider_effect', registry['narendranathe']['capabilities'])
        self.assertEqual(context, {'concurrency_group': 'hiring-outreach-single-writer',
            'reservation_verified': True, 'orchestrator_verified': True, 'run_id': '12345'})
        for key in HOST:
            with self.subTest(key=key), patch.dict(os.environ, {**HOST, key: ''}, clear=True):
                with self.assertRaisesRegex(Blocked, 'trusted_central_agent_worker_required'):
                    _agent_worker_authority()

    def test_workflow_without_exclusive_group_cannot_grant_worker_authority(self):
        with patch.dict(os.environ, HOST, clear=True), patch('pathlib.Path.read_text', return_value='concurrency: other'):
            with self.assertRaisesRegex(Blocked, 'agent_worker_concurrency_contract_required'):
                _agent_worker_authority()

    def run_mode(self, mode, store=None, config=None):
        store = store or ShadowStore()
        if mode == 'live' and not store.get('deployment:cutover:v2'):
            store.put(make_cutover_record(evidence()))
        config = config or {'destination_private': True, 'cloud_enabled': True,
                            'cloud_research_enabled': True, 'reconcile_lookback_days': 0}
        with ExitStack() as stack:
            worker = stack.enter_context(patch('cloud.agent_worker.run_agent_worker',
                return_value={'completed': 0, 'external_effects': 0}))
            stack.enter_context(patch('cloud.orchestrator.run_outreach', return_value={'sent': 0}))
            stack.enter_context(patch('cloud.reporting.run_reporting', return_value={}))
            stack.enter_context(patch('cloud.orchestrator._publish_agent_index', return_value={'complete': True}))
            result = execute(store, config, NOW, requested='accepted', mode=mode,
                agent_registry={'trusted': {}}, writer_context={'host': True})
        return result, worker

    def test_nonpersistent_modes_never_consume_or_acknowledge_admitted_queue(self):
        for mode in ('dry-run', 'research-only'):
            with self.subTest(mode=mode):
                store = ShadowStore()
                result, worker = self.run_mode(mode, store)
                worker.assert_not_called()
                self.assertEqual(result['agent_worker']['status'], 'skipped_nonpersistent')
                self.assertEqual(store.records, {})

    def test_live_consumer_uses_same_store_and_budget_guard_even_on_completed_slots(self):
        store = ShadowStore()
        store.put(record('run:2026-10-20:accepted:completed', 'agent_runs', {}))
        result, worker = self.run_mode('live', store)
        worker.assert_called_once()
        self.assertIs(worker.call_args.args[0], store)
        self.assertEqual(worker.call_args.kwargs['trusted_registry'], {'trusted': {}})
        self.assertEqual(worker.call_args.kwargs['writer_context'], {'host': True})
        self.assertTrue(callable(worker.call_args.kwargs['effect_guard']))
        self.assertEqual(result['results'][0]['status'], 'already_completed')
        self.assertEqual(result['agent_worker']['external_effects'], 0)

    def test_unresolved_prior_slot_fences_worker_before_execution(self):
        store = ShadowStore()
        store.put(make_cutover_record(evidence()))
        store.put(record('run:2026-10-19:applications:started', 'agent_runs', {}))
        with patch('cloud.agent_worker.run_agent_worker') as worker:
            with self.assertRaisesRegex(Blocked, 'unresolved_previous_writer_run'):
                execute(store, {'destination_private': True, 'cloud_enabled': True}, NOW,
                    requested='accepted', mode='live', agent_registry={}, writer_context={})
        worker.assert_not_called()

    def test_disabled_production_cannot_consume_queue(self):
        with patch('cloud.agent_worker.run_agent_worker') as worker:
            with self.assertRaisesRegex(Blocked, 'cutover_not_enabled'):
                execute(ShadowStore(), {'destination_private': True}, NOW,
                    requested='accepted', mode='live', agent_registry={}, writer_context={})
        worker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
