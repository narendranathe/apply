"""Scheduled preview recovery stays separate from an unresolved live writer."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_workflows
from cloud.applications import run_applications
from cloud.orchestrator import Blocked, ShadowStore, main
from cloud.outreach import record
from cloud.runtime_guard import verify_previous_writers
import tests.test_applications as applications_fixture
import tests.test_research_fallback as research_fixture
import tests.test_runtime_guard as guard_fixture


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/cloud-operations.yml'


def job_block(source, name):
    found = re.search(r'^  ' + name + r':\n(.*?)(?=^  [a-z_]+:\n|\Z)', source,
                      re.MULTILINE | re.DOTALL)
    return found.group(1) if found else ''


class ScheduledFallbackWorkflowTests(unittest.TestCase):
    def check_source(self, source):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / '.github/workflows/cloud-operations.yml'
            target.parent.mkdir(parents=True)
            target.write_text(source)
            maintenance = WORKFLOW.with_name('history-reconciliation.yml')
            target.with_name(maintenance.name).write_text(maintenance.read_text())
            with patch.object(check_workflows, 'ROOT', root), redirect_stdout(io.StringIO()):
                check_workflows.check()

    def test_fallback_waits_for_failure_on_its_own_runner_and_budget(self):
        source = WORKFLOW.read_text()
        fallback = job_block(source, 'research_fallback')
        self.assertTrue(fallback, 'A scheduled live failure needs an isolated research job')
        self.assertIn('    needs: orchestrate\n', fallback)
        self.assertIn('    runs-on: ubuntu-latest\n', fallback)
        self.assertIn('    timeout-minutes: 150\n', fallback)
        self.assertEqual(source.count('\nconcurrency:\n'), 1)
        self.assertNotIn('concurrency:', fallback)
        self.assertNotIn('continue-on-error:', source)

    def test_checker_rejects_every_missing_fallback_gate(self):
        source = WORKFLOW.read_text()
        for term in ("!cancelled()", "github.event_name == 'schedule'",
                     "github.ref == 'refs/heads/main'", "vars.CLOUD_ENABLED == 'true'",
                     "vars.CLOUD_RESEARCH_ENABLED == 'true'",
                     "needs.orchestrate.result == 'failure'"):
            with self.subTest(term=term):
                changed = source.replace(' && ' + term, '', 1)
                self.assertNotEqual(changed, source)
                with self.assertRaises(AssertionError):
                    self.check_source(changed)
        with self.assertRaises(AssertionError):
            self.check_source(source.replace('${{ always() && ', '${{ ', 1))

    def test_checker_rejects_status_masking_and_shared_artifact_or_lock(self):
        source = WORKFLOW.read_text()
        self.check_source(source)
        changes = (
            ('  orchestrate:\n', '  orchestrate:\n    continue-on-error: true\n'),
            ('  research_fallback:\n', '  research_fallback:\n    concurrency: another-lock\n'),
            ('name: research-fallback-${{ github.run_id }}-${{ github.run_attempt }}',
             'name: redacted-run-${{ github.run_id }}'),
            ('run-output/research-fallback-summary.json', 'run-output/summary.json'),
            ('  actions: read', '  actions: write'),
            ('  research_fallback:\n',
             '  research_fallback:\n    extra: actions/download-artifact@' + 'a' * 40 + '\n'),
        )
        for old, new in changes:
            with self.subTest(change=old):
                with self.assertRaises(AssertionError):
                    self.check_source(source.replace(old, new))

    def test_checker_rejects_live_mode_or_missing_private_and_research_gates(self):
        source = WORKFLOW.read_text()
        changes = (
            ('--mode research-only', '--mode live'),
            ("          CLOUD_ENABLED: 'false'", "          CLOUD_ENABLED: 'true'"),
            ('          CLOUD_RESEARCH_ENABLED: ${{ vars.CLOUD_RESEARCH_ENABLED }}',
             "          CLOUD_RESEARCH_ENABLED: 'true'"),
            ('test "$PRIVATE" = \'true\'', 'true'),
            ('          ref: ${{ github.sha }}', '          ref: main'),
            ('    timeout-minutes: 150', '    timeout-minutes: 149'),
        )
        for old, new in changes:
            with self.subTest(change=old):
                with self.assertRaises(AssertionError):
                    self.check_source(source.replace(old, new))


class ScheduledFallbackSafetyTests(unittest.TestCase):
    def config_file(self, folder, config=None):
        path = Path(folder) / 'config.json'
        path.write_text(json.dumps({'notion_data_source_id': 'synthetic',
                                    'reconcile_lookback_days': 0, **(config or {})}))
        return path

    def test_research_main_with_live_enabled_preserves_unresolved_state_and_never_submits(self):
        fixture = applications_fixture.ApplicationTests(
            methodName='test_dry_run_has_no_state_or_external_mutation')
        fixture.setUp()
        try:
            started = record('run:2026-10-14:applications:started', 'agent_runs',
                             {'mode': 'live', 'persistent': True}, 'started')
            failure = record('writer-failure:synthetic', 'audit_events',
                             {'retry_external_effects': False}, 'needs_reconciliation')
            base = research_fixture.ReadOnlyStore([started, failure])
            original = deepcopy(base.records)
            transport = applications_fixture.ATSTransport()
            def applications(store, config, dry_run, *, effect_guard, preview_packets):
                return run_applications(store, config, dry_run=dry_run, feed=fixture.feed,
                    transport=transport, prepare=fixture.prepare, effect_guard=effect_guard,
                    preview_packets=preview_packets)
            with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                config = self.config_file(folder, fixture.config)
                output = Path(folder) / 'research-fallback-summary.json'
                stack.enter_context(patch('sys.argv', ['cloud.orchestrator', '--config', str(config),
                    '--mode', 'research-only', '--lane', 'applications', '--output', str(output)]))
                stack.enter_context(patch.dict('os.environ', {'DESTINATION_PRIVATE': 'true',
                    'CLOUD_ENABLED': 'true', 'CLOUD_RESEARCH_ENABLED': 'true',
                    'NOTION_TOKEN': 'synthetic-private-token', 'TEST_APPLICATION_TOKEN': 'sandbox',
                    'TEST_SCHEMA_TOKEN': 'sandbox'}, clear=True))
                stack.enter_context(patch('cloud.notion.NotionStore', return_value=base))
                stack.enter_context(patch('cloud.baseline.restore_baseline',
                    return_value=({'records': []}, 'a' * 64)))
                stack.enter_context(patch('cloud.reporting.effective_research_share', return_value=.6))
                stack.enter_context(patch('cloud.reporting.run_reporting', return_value={'counts': {}}))
                stack.enter_context(patch('cloud.applications.run_applications', side_effect=applications))
                witness = stack.enter_context(patch('cloud.runtime_guard.verify_previous_writers'))
                gate = stack.enter_context(patch('cloud.orchestrator.live_gate'))
                authority = stack.enter_context(patch('cloud.orchestrator._agent_worker_authority'))
                worker = stack.enter_context(patch('cloud.agent_worker.run_agent_worker'))
                lever = stack.enter_context(patch('cloud.submission.LeverAdapter.submit'))
                greenhouse = stack.enter_context(patch('cloud.submission.GreenhouseAdapter.submit'))
                backup = stack.enter_context(patch('cloud.backup.backup_store', return_value={
                    'records': 1, 'sha256': 'a' * 64, 'restore_verified': True,
                    'path': 'run-output/cloud-backup-synthetic.aesgcm'}))
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    main()
                summary = json.loads(output.read_text())
            for forbidden in (witness, gate, authority, worker, lever, greenhouse):
                forbidden.assert_not_called()
            self.assertEqual(base.puts, [])
            self.assertEqual(base.records, original)
            self.assertFalse(any(call[0] == 'POST' for call in transport.calls))
            self.assertEqual(summary['results'][0]['prepared'], 1)
            self.assertEqual(summary['results'][0]['submission_attempts'], 0)
            self.assertFalse(summary['persistent'])
            self.assertEqual(summary['mode'], 'research-only')
            self.assertEqual(summary['agent_worker']['status'], 'skipped_nonpersistent')
            self.assertFalse(summary['agent_index']['complete'])
            self.assertFalse(summary['research_artifact']['operational_source_of_truth'])
            self.assertNotIn('synthetic-private-token', stdout.getvalue())
            staged = backup.call_args.args[0]
            self.assertIsInstance(staged, ShadowStore)
            self.assertIsNone(staged.base)
            self.assertTrue(staged.records)
            self.assertFalse(any(row['id'].startswith('writer-resolution:') for row in staged.records.values()))
        finally:
            fixture.tearDown()

    def test_research_private_or_enable_gate_failure_stops_before_store_creation(self):
        for env in ({'DESTINATION_PRIVATE': 'false', 'CLOUD_RESEARCH_ENABLED': 'true'},
                    {'DESTINATION_PRIVATE': 'true', 'CLOUD_RESEARCH_ENABLED': 'false'}):
            with self.subTest(env=env), tempfile.TemporaryDirectory() as folder:
                config = self.config_file(folder)
                with patch('sys.argv', ['cloud.orchestrator', '--config', str(config),
                    '--mode', 'research-only']), patch.dict('os.environ',
                    {'CLOUD_ENABLED': 'true', 'NOTION_TOKEN': 'synthetic', **env}, clear=True), \
                    patch('cloud.notion.NotionStore') as store:
                    with self.assertRaises(Blocked):
                        main()
                store.assert_not_called()

    def test_successful_fallback_job_never_clears_native_failed_writer_fence(self):
        run = {**guard_fixture.cancelled(), 'conclusion': 'failure'}
        github, store = guard_fixture.GitHub([run]), guard_fixture.Store()
        github.jobs['123'] = {'total_count': 2, 'jobs': [
            {'id': 7, 'name': 'orchestrate', 'status': 'completed', 'conclusion': 'failure'},
            {'id': 8, 'name': 'research_fallback', 'status': 'completed', 'conclusion': 'success'}]}
        with patch.dict('os.environ', guard_fixture.ENV, clear=True), \
             patch('cloud.runtime_guard.urlopen', side_effect=github.open):
            with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                verify_previous_writers(store)
        self.assertEqual([row['id'] for row in store.writes], ['writer-failure:123'])
        self.assertFalse(any(row['id'].startswith('writer-resolution:') for row in store.writes))

    def test_disabled_live_environment_blocks_an_accidentally_changed_cli_mode(self):
        base = research_fixture.ReadOnlyStore()
        with tempfile.TemporaryDirectory() as folder:
            config = self.config_file(folder)
            with patch('sys.argv', ['cloud.orchestrator', '--config', str(config), '--mode', 'live']), \
                 patch.dict('os.environ', {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main',
                     'DESTINATION_PRIVATE': 'true', 'CLOUD_ENABLED': 'false',
                     'CLOUD_RESEARCH_ENABLED': 'true', 'NOTION_TOKEN': 'synthetic'}, clear=True), \
                 patch('cloud.notion.NotionStore', return_value=base), \
                 patch('cloud.backup.validate_key'), \
                 patch('cloud.runtime_guard.verify_previous_writers') as witness:
                with self.assertRaisesRegex(Blocked, 'cutover_not_enabled'):
                    main()
            witness.assert_not_called()
        self.assertEqual(base.puts, [])
        self.assertEqual(base.gets, [])


if __name__ == '__main__':
    unittest.main()
