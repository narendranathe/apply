"""Research fallback stages evidence only; live effects retain strict gates."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cloud.cutover import make_cutover_record
from cloud.orchestrator import Blocked, ShadowStore, _failure_summary, _publish_agent_index, due_slots, execute, live_gate, main
from cloud.outreach import record
from cloud.runtime_budget import BudgetExceeded, RuntimeBudget
from cloud.submission import SubmissionBlocked
from tests.test_cutover import evidence

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


class ReadOnlyStore:
    def __init__(self, rows=()):
        self.records = {row['id']: row for row in rows}
        self.puts = []
        self.gets = []
        self.transport = Mock(return_value={})

    def get(self, identity):
        self.gets.append(identity)
        return self.records.get(identity)

    def query(self, kind=None):
        return [row for row in self.records.values() if kind is None or row['kind'] == kind]

    def put(self, value):
        self.puts.append(value)
        raise AssertionError('Research must not call the base writer')

    def current_contact_observations(self):
        return []


class WriteStore(ReadOnlyStore):
    def put(self, value):
        self.puts.append(value)
        self.records[value['id']] = value
        return value


def research_config(**updates):
    return {'destination_private': True, 'cloud_enabled': False, 'cloud_research_enabled': True,
            'reconcile_lookback_days': 0, **updates}


def live_store():
    return WriteStore([make_cutover_record(evidence())])


class ResearchFallbackTests(unittest.TestCase):
    def test_archived_uncertain_applications_suppress_preview_without_base_writes(self):
        import tests.test_applications as fixtures
        from cloud.applications import application_key, digest, run_applications
        fixture = fixtures.ApplicationTests(methodName='test_dry_run_has_no_state_or_external_mutation')
        fixture.setUp()
        try:
            old = record('archive:unknown', 'applications', {
                'application_id': 'application:' + digest(application_key('Acme', 'req-1')),
                'company': 'Acme', 'requisition_id': 'req-1', 'role': 'Data Engineer',
                'url': 'https://jobs.lever.co/acme/req-1', 'revision': 0,
                'historical_only': True}, 'submission_unknown')
            config = {**fixture.config, **research_config(), '_suppression_baseline': {'records': [old]}}
            base, transport = ReadOnlyStore(), fixtures.ATSTransport()
            def applications(store, config, dry_run, *, effect_guard, preview_packets):
                return run_applications(store, config, True, feed=fixture.feed, transport=transport,
                                        prepare=fixture.prepare, preview_packets=preview_packets)
            with patch('cloud.applications.run_applications', side_effect=applications), \
                 patch('cloud.reporting.run_reporting', return_value={'counts': {}}):
                result = execute(base, config, NOW, requested='applications', mode='research-only')
            self.assertEqual(result['results'][0]['duplicates'], 1)
            self.assertEqual(result['results'][0]['prepared'], 0)
            self.assertEqual(base.puts, [])
            self.assertFalse(any(call[0] == 'POST' for call in transport.calls))
        finally:
            fixture.tearDown()

    def test_archived_application_loss_stops_before_preparation(self):
        from cloud.notion import ConflictError
        base = live_store()
        archived = record('historical:receipt', 'applications', {'application_id': 'fixture'}, 'applied_verified')
        config = {'destination_private': True, 'cloud_enabled': True, 'reconcile_lookback_days': 0,
                  '_reconciled_export': {'records': [archived]}}
        with patch('cloud.applications.run_applications') as applications:
            with self.assertRaises(ConflictError):
                execute(base, config, NOW, requested='applications', mode='live')
        applications.assert_not_called()

    def test_live_cli_rejects_nondefault_branch_before_credentials_or_store(self):
        with patch('sys.argv', ['cloud.orchestrator', '--mode', 'live']), \
             patch.dict('os.environ', {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/codex/unvalidated'}, clear=True), \
             patch('cloud.notion.NotionStore') as store:
            with self.assertRaisesRegex(Blocked, 'live_requires_default_branch_hosted_writer'):
                main()
        store.assert_not_called()

    def pipeline(self, stack):
        seen = []
        def outreach(store, config, lane, now, dry_run):
            self.assertIsInstance(store, ShadowStore)
            self.assertTrue(dry_run)
            seen.append(store)
            store.put(record('research:synthetic:' + lane, 'conversation_observations', {'lane': lane}, 'research'))
            return {'lane': lane, 'sent': 0, 'research': 1}
        def discovery(store, config, now):
            self.assertIsInstance(store, ShadowStore)
            seen.append(store)
            store.put(record('research:synthetic:job', 'jobs', {'company': 'Synthetic Co'}, 'research'))
            return {'official_open': 1, 'research_tasks': 1}
        def applications(store, config, dry_run, *, effect_guard, preview_packets=False):
            self.assertIsInstance(store, ShadowStore)
            self.assertTrue(dry_run)
            self.assertIsNone(effect_guard)
            self.assertTrue(preview_packets)
            seen.append(store)
            return {'lane': 'applications', 'dry_run': True, 'qualified': 1, 'prepared': 1,
                    'submission_attempts': 0, 'applied_verified': 0}
        def reporting(store, config, now, dry_run):
            self.assertIsInstance(store, ShadowStore)
            self.assertTrue(dry_run)
            return {'dry_run': True, 'counts': {'source_jobs': 1}, 'external_messages_sent': 0}
        stack.enter_context(patch('cloud.reporting.effective_research_share', return_value=.6))
        stack.enter_context(patch('cloud.orchestrator.run_outreach', side_effect=outreach))
        stack.enter_context(patch('cloud.discovery.run_discovery', side_effect=discovery))
        stack.enter_context(patch('cloud.applications.run_applications', side_effect=applications))
        stack.enter_context(patch('cloud.reporting.run_reporting', side_effect=reporting))
        return seen

    def test_production_disabled_research_runs_without_cutover_or_base_puts(self):
        base = ReadOnlyStore()
        with ExitStack() as stack:
            seen = self.pipeline(stack)
            result = execute(base, research_config(), NOW, mode='research-only', dry_run=False)
        self.assertEqual(base.puts, [])
        self.assertEqual(base.records, {})
        self.assertEqual(result['mode'], 'research-only')
        self.assertFalse(result['persistent'])
        self.assertTrue(result['dry_run'])
        self.assertFalse(result['reporting']['persistent'])
        self.assertEqual(result['linkedin_inbox'], 'not checked')
        self.assertEqual(result['linkedin_sent'], 0)
        self.assertEqual(len(result['results']), 3)
        self.assertTrue(all(row['external_effects'] == 0 for row in result['results']))
        staged = seen[0].records
        markers = [row for row in staged.values() if row['kind'] == 'agent_runs']
        self.assertEqual(len(markers), 6)
        self.assertTrue(all(row['id'].startswith('research:run:') for row in markers))
        self.assertTrue(all(row['payload']['persistent'] is False for row in markers))
        self.assertTrue(all(row['status'] in {'research_started', 'research_completed'} for row in markers))
        self.assertFalse(any(row['id'].startswith('run:') for row in staged.values()))

    def test_research_flag_and_private_repository_are_independent_required_gates(self):
        for config in (research_config(destination_private=False), research_config(cloud_research_enabled=False),
                       research_config(cloud_research_enabled='true')):
            base = ReadOnlyStore()
            with self.subTest(config=config), patch('cloud.orchestrator.run_outreach') as outreach:
                with self.assertRaises(Blocked):
                    execute(base, config, NOW, mode='research-only')
                outreach.assert_not_called()
                self.assertEqual(base.gets, [])
                self.assertEqual(base.puts, [])

    def test_research_ignores_production_completion_and_unresolved_writer_markers(self):
        old = [record('run:2026-10-14:accepted:completed', 'agent_runs', {}),
               record('run:2026-10-14:first_touch:started', 'agent_runs', {})]
        base = ReadOnlyStore(old)
        with ExitStack() as stack:
            self.pipeline(stack)
            result = execute(base, research_config(), NOW, mode='research-only')
        self.assertEqual(len(result['results']), 3)
        self.assertFalse(any(row.get('status') in {'already_completed', 'needs_reconciliation'} for row in result['results']))
        self.assertEqual(base.puts, [])

    def test_originating_schedules_preserve_the_two_research_lanes(self):
        for cron, expected in (('0 6 * * *', ['accepted']), ('30 6 * * *', ['first_touch', 'applications'])):
            with self.subTest(cron=cron), ExitStack() as stack:
                self.pipeline(stack)
                result = execute(ReadOnlyStore(), research_config(), NOW, cron=cron, mode='research-only', reconcile=True)
                self.assertEqual([row['lane'] for row in result['results']], expected)

    def test_both_daily_slots_follow_chicago_across_dst_transitions(self):
        # The UTC hour changes; the due boundary remains 06:00/06:30 locally.
        for day, hour in ((datetime(2026, 3, 7, tzinfo=timezone.utc), 12),
                          (datetime(2026, 3, 8, tzinfo=timezone.utc), 11),
                          (datetime(2026, 10, 31, tzinfo=timezone.utc), 11),
                          (datetime(2026, 11, 1, tzinfo=timezone.utc), 12)):
            with self.subTest(day=day):
                accepted = day.replace(hour=hour)
                first_touch = accepted.replace(minute=30)
                self.assertEqual(due_slots(accepted, ['accepted', 'first_touch'], 0, True),
                                 [(day.date().isoformat(), 'accepted', False)])
                self.assertEqual(due_slots(first_touch, ['accepted', 'first_touch'], 0, True),
                                 [(day.date().isoformat(), 'accepted', False),
                                  (day.date().isoformat(), 'first_touch', False)])

    def test_real_dry_application_path_prepares_but_never_submits(self):
        import tests.test_applications as fixtures
        from cloud.applications import run_applications
        fixture = fixtures.ApplicationTests(methodName='test_dry_run_has_no_state_or_external_mutation')
        fixture.setUp()
        try:
            base, transport = ReadOnlyStore(), fixtures.ATSTransport()
            def applications(store, config, dry_run, *, effect_guard, preview_packets=False):
                self.assertTrue(dry_run)
                return run_applications(store, config, dry_run=dry_run, feed=fixture.feed,
                                        transport=transport, prepare=fixture.prepare, effect_guard=effect_guard,
                                        preview_packets=preview_packets)
            config = {**fixture.config, **research_config()}
            with patch('cloud.applications.run_applications', side_effect=applications), \
                 patch('cloud.reporting.run_reporting', return_value={'counts': {}}), \
                 patch('cloud.submission.LeverAdapter.submit') as lever, \
                 patch('cloud.submission.GreenhouseAdapter.submit') as greenhouse, \
                 patch('cloud.linkedin_ingestion.LiveUnavailable.fetch_evidence') as inbox:
                result = execute(base, config, NOW, requested='applications', mode='research-only')
            self.assertEqual(result['results'][0]['prepared'], 1)
            self.assertEqual(result['results'][0]['submission_attempts'], 0)
            self.assertEqual(result['staged_record_counts'].get('application_packets'), 1)
            self.assertFalse(any(call[0] == 'POST' for call in transport.calls))
            lever.assert_not_called()
            greenhouse.assert_not_called()
            inbox.assert_not_called()
            self.assertEqual(base.puts, [])
        finally:
            fixture.tearDown()

    def test_live_gate_rejects_four_boolean_fixture_and_invalid_hash(self):
        simple = record('deployment:cutover:v2', 'audit_events', {key: True for key in (
            'history_reconciled', 'backup_restore_verified', 'local_actors_retired', 'shadow_verified')})
        for cutover in (simple, {**make_cutover_record(evidence()), 'hash': 'bad'}):
            store = WriteStore([cutover])
            with self.subTest(cutover=cutover), self.assertRaises(Blocked):
                live_gate(store, {'destination_private': True, 'cloud_enabled': True})
            self.assertEqual(store.puts, [])

    def test_live_cutover_flag_cannot_be_bypassed_with_research_flag(self):
        store = live_store()
        with self.assertRaises(Blocked):
            execute(store, research_config(), NOW, requested='applications', mode='live')
        self.assertEqual(store.puts, [])

    def test_live_application_receives_guard_and_receipt_requests_keep_reserve(self):
        clock = [0]
        budget = RuntimeBudget(100, 20, clock=lambda: clock[0])
        base = live_store()
        original_transport = base.transport
        def applications(store, config, dry_run, *, effect_guard):
            self.assertFalse(dry_run)
            self.assertTrue(callable(effect_guard))
            effect_guard()
            clock[0] = 80
            with self.assertRaises(SubmissionBlocked):
                effect_guard()
            store.transport('GET', '/synthetic-receipt', None, {})
            return {'lane': 'applications', 'submission_attempts': 0, 'applied_verified': 0}
        with patch('cloud.applications.run_applications', side_effect=applications), \
             patch('cloud.reporting.run_reporting', return_value={'counts': {}}):
            result = execute(base, {'destination_private': True, 'cloud_enabled': True, 'reconcile_lookback_days': 0},
                             NOW, requested='applications', mode='live', budget=budget)
        original_transport.assert_called_once_with('GET', '/synthetic-receipt', None, {})
        self.assertTrue(result['persistent'])
        clock[0] = 100
        with self.assertRaises(BudgetExceeded):
            base.transport('GET', '/too-late', None, {})
        self.assertEqual(original_transport.call_count, 1)

    def test_expired_runtime_stops_before_reading_state_or_starting_lanes(self):
        clock = [0]
        budget = RuntimeBudget(100, 20, clock=lambda: clock[0])
        clock[0] = 100
        base = ReadOnlyStore()
        with self.assertRaises(BudgetExceeded):
            execute(base, research_config(), NOW, mode='research-only', budget=budget)
        self.assertEqual(base.gets, [])
        self.assertEqual(base.puts, [])

    def test_main_research_skips_writer_witness_and_archives_only_private_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / 'config.json'
            output = Path(folder) / 'summary.json'
            config_path.write_text(json.dumps({'notion_data_source_id': 'abc123', 'reconcile_lookback_days': 0}))
            argv = ['cloud.orchestrator', '--config', str(config_path), '--mode', 'research-only', '--output', str(output)]
            base = ReadOnlyStore()
            with ExitStack() as stack:
                self.pipeline(stack)
                stack.enter_context(patch('sys.argv', argv))
                stack.enter_context(patch.dict('os.environ', {'DESTINATION_PRIVATE': 'true', 'CLOUD_RESEARCH_ENABLED': 'true',
                    'CLOUD_ENABLED': 'false', 'NOTION_TOKEN': 'synthetic-secret'}, clear=True))
                stack.enter_context(patch('cloud.notion.NotionStore', return_value=base))
                stack.enter_context(patch('cloud.baseline.restore_baseline', return_value=({'records': []}, 'a' * 64)))
                witness = stack.enter_context(patch('cloud.runtime_guard.verify_previous_writers'))
                key = stack.enter_context(patch('cloud.backup.validate_key'))
                backup = stack.enter_context(patch('cloud.backup.backup_store', return_value={
                    'records': 1, 'sha256': 'a' * 64, 'restore_verified': True,
                    'path': 'run-output/cloud-backup-fixture.aesgcm'}))
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    main()
            witness.assert_not_called()
            key.assert_not_called()
            backup.assert_called_once()
            archived = backup.call_args.args[0]
            self.assertIsInstance(archived, ShadowStore)
            self.assertIsNone(archived.base)
            self.assertTrue(archived.records)
            summary = json.loads(output.read_text())
            self.assertFalse(summary['persistent'])
            self.assertNotIn('synthetic-secret', stdout.getvalue())
            self.assertNotIn('Synthetic Co', output.read_text())
            self.assertEqual(base.puts, [])

    def test_workflow_fallback_keeps_single_writer_and_ninety_minute_timeout(self):
        source = Path('.github/workflows/cloud-operations.yml').read_text()
        self.assertIn('group: hiring-outreach-single-writer', source)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn('timeout-minutes: 90', source)
        self.assertIn('options: [dry-run, research-only, live]', source)
        self.assertIn("vars.CLOUD_RESEARCH_ENABLED == 'true'", source)
        self.assertIn("inputs.mode || (vars.CLOUD_ENABLED == 'true' && 'live' || 'research-only')", source)
        self.assertEqual(source.count("timezone: 'America/Chicago'"), 2)

    def test_index_preview_reads_fresh_sources_without_remote_child_probes_or_private_ids(self):
        from tests.test_agent_index import contact, event
        base = ReadOnlyStore([contact(name='private-contact-marker'), event('recruiter_reply_confirmed', 'private-event-marker')])
        summary = _publish_agent_index(ShadowStore(base), NOW, True, RuntimeBudget())
        self.assertFalse(summary['complete'])
        self.assertFalse(summary['persistent'])
        self.assertGreater(summary['record_count'], 1)
        self.assertEqual(set(summary), {'complete', 'counts', 'record_count', 'persistent'})
        self.assertEqual(base.gets, [])
        self.assertEqual(base.puts, [])
        self.assertNotIn('private-', json.dumps(summary))
        self.assertNotIn('manifest', json.dumps(summary))

    def test_live_index_cannot_infer_source_authority_from_a_valid_cutover(self):
        from tests.test_agent_index import contact
        base = live_store()
        base.records['contact:one'] = contact()
        summary = _publish_agent_index(base, NOW, False, RuntimeBudget())
        manifest = [row for row in base.puts if row['kind'] == 'index_manifests'][0]
        self.assertFalse(summary['complete'])
        self.assertTrue(summary['persistent'])
        self.assertEqual(manifest['payload']['campaign_brief']['ready_tasks'], 0)

    def test_index_preview_forwards_the_bounded_source_reader(self):
        from tests.test_agent_index import contact
        class BoundedStore(ReadOnlyStore):
            calls = 0
            def index_source_records(self):
                self.calls += 1
                return list(self.records.values())
            def query(self, kind=None):
                if kind is None:
                    raise AssertionError('Full historical index scan is prohibited')
                return super().query(kind)
        base = BoundedStore([contact()])
        summary = _publish_agent_index(ShadowStore(base), NOW, True, RuntimeBudget())
        self.assertFalse(summary['complete'])
        self.assertEqual(base.calls, 1)
        self.assertEqual(base.puts, [])

    def test_uncertain_notion_errors_expose_only_allowlisted_three_field_diagnostics(self):
        from cloud.notion import AmbiguousWriteError
        failure = _failure_summary(AmbiguousWriteError('private-body-and-token', diagnostics={
            'http_status': 403, 'error_code': 'restricted_resource', 'operation': 'retrieve_page_property',
            'authorization': 'private-token', 'response_body': 'private-body'}))
        self.assertEqual(failure['notion'], {'http_status': 403, 'error_code': 'restricted_resource',
                                            'operation': 'retrieve_page_property'})
        self.assertNotIn('private-body', json.dumps(failure))
        self.assertNotIn('private-token', json.dumps(failure))


if __name__ == '__main__':
    unittest.main()
