"""Custody entry point ordering, safe failures and real post-backup validation."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cloud.notion import AmbiguousWriteError
from cloud.startup_reconciliation import ReconciliationBlocked
from scripts import reconcile_index_custody as hosted
from scripts import verify_index_custody as offline
from tests.test_index_custody_evidence import CONTEXT
from tests import test_index_custody_receipt as receipt_fixture


class CustodyCLITests(unittest.TestCase):
    def run_hosted(self, *, result=None, backup=None, reconcile_error=None, backup_error=None):
        fixture = self.fixture
        expected = result or {'verified': True, 'correction_binding': deepcopy(fixture.binding)}
        copied = deepcopy(expected)
        report = backup or {'path': str(fixture.archive), 'sha256': fixture.summary['backup']['sha256'],
            'restore_verified': True, 'records': fixture.backup_proof['complete_pages']}
        store = Mock(name='notion-store')
        budget = Mock(); budget.attach.return_value = store
        patches = [patch.object(hosted, 'context', return_value=deepcopy(CONTEXT)),
            patch.object(hosted, 'validate_key', return_value=fixture.key),
            patch.object(hosted, 'restore_baseline', return_value=(fixture.exported, 'baseline')),
            patch.object(hosted, 'RuntimeBudget', return_value=budget),
            patch.object(hosted, 'NotionStore', return_value=store),
            patch.object(hosted, 'StartupGitHubEvidence', return_value=fixture.github),
            patch.object(hosted.recovery, 'reconcile', side_effect=reconcile_error, return_value=copied),
            patch.object(hosted, 'backup_store', side_effect=backup_error, return_value=report),
            patch.object(hosted, 'persist_summary'),
            patch.dict(os.environ, {'NOTION_TOKEN': 'synthetic-token', 'GITHUB_TOKEN': 'synthetic-token'})]
        handles = [item.start() for item in patches]
        try:
            output = io.StringIO()
            with redirect_stdout(output):
                try:
                    value = hosted.cli()
                except SystemExit as error:
                    value = error.code
            return value, output.getvalue(), handles
        finally:
            for item in reversed(patches): item.stop()

    def setUp(self):
        self.fixture = receipt_fixture.ReceiptTests(methodName='test_full_cohort_missing_receipt_never_clears')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_local_gate_precedes_keys_restore_native_and_store(self):
        for field, value in [('actions', False), ('run_attempt', '2'), ('cloud_enabled', 'true'),
                ('cloud_research_enabled', 'false'), ('event', 'schedule'), ('mode', 'other')]:
            with self.subTest(field=field), patch.object(hosted, 'context', return_value={**CONTEXT, field: value}), \
                    patch.object(hosted, 'validate_key') as key, \
                    patch.object(hosted, 'restore_baseline') as baseline, \
                    patch.object(hosted, 'NotionStore') as store, \
                    patch.object(hosted, 'StartupGitHubEvidence') as github:
                with self.assertRaises(ReconciliationBlocked): hosted.main()
                key.assert_not_called(); baseline.assert_not_called()
                store.assert_not_called(); github.assert_not_called()

    def test_success_requires_actual_authenticated_complete_postbackup(self):
        value, output, mocks = self.run_hosted()
        self.assertIsInstance(value, dict)
        self.assertTrue(value['verified'])
        self.assertEqual(value['backup_verification'], self.fixture.backup_proof)
        self.assertEqual(json.loads(output)['backup_verification']['historical_records_unchanged'], 757)
        mocks[6].assert_called_once()  # The one reconciliation invocation.
        mocks[7].assert_called_once()  # The one backup invocation.
        mocks[8].assert_called_once()

    def test_failed_backup_stops_with_no_success_and_no_retry(self):
        value, output, mocks = self.run_hosted(backup_error=RuntimeError('private snapshot should never escape'))
        self.assertEqual(value, 2)
        failure = json.loads(output)
        self.assertEqual(failure['status'], 'blocked')
        self.assertNotIn('private snapshot', output)
        self.assertNotIn('verified', failure)
        mocks[6].assert_called_once(); mocks[7].assert_called_once()
        self.assertEqual(mocks[8].call_args.args[0], failure)

    def test_claimed_restore_success_with_invalid_backup_is_blocked(self):
        with tempfile.TemporaryDirectory(prefix='apply-test-custody-bad-backup-') as temporary:
            path = Path(temporary) / 'bad.aesgcm'; path.write_bytes(b'corrupt')
            backup = {**self.fixture.summary['backup'], 'path': str(path)}
            value, output, mocks = self.run_hosted(backup=backup)
        self.assertEqual(value, 2)
        self.assertEqual(json.loads(output)['status'], 'blocked')
        self.assertNotIn('"verified": true', output)
        mocks[7].assert_called_once()

    def test_backup_hash_record_count_and_restore_flag_are_independent_gates(self):
        for field, wrong in [('sha256', 'a' * 64), ('records', 0), ('restore_verified', False)]:
            backup = {**self.fixture.summary['backup'], 'path': str(self.fixture.archive), field: wrong}
            with self.subTest(field=field):
                value, output, _ = self.run_hosted(backup=backup)
                self.assertEqual(value, 2)
                self.assertEqual(json.loads(output)['reason'], 'custody_post_backup_verification_failed')

    def test_ambiguous_write_diagnostics_never_retry_or_echo_private_details(self):
        class UnsafeDiagnostic(AmbiguousWriteError):
            def diagnostics(self):
                return {'private_body': 'must never escape'}
        error = UnsafeDiagnostic('private authorization/body', diagnostics={'operation': 'verify_create_readback'})
        value, output, mocks = self.run_hosted(reconcile_error=error)
        self.assertEqual(value, 2)
        failure = json.loads(output)
        self.assertEqual(failure['error_type'], 'AmbiguousWriteError')
        self.assertNotIn('private', output)
        self.assertNotIn('authorization', output)
        mocks[6].assert_called_once(); mocks[7].assert_not_called()

    def test_unknown_blocked_reason_stays_closed(self):
        value, output, mocks = self.run_hosted(reconcile_error=ReconciliationBlocked('private arbitrary reason'))
        self.assertEqual(value, 2)
        self.assertNotIn('private arbitrary reason', output)
        self.assertEqual(json.loads(output)['reason'], 'reconciliation_requirements_not_verified')
        mocks[7].assert_not_called()

    def test_safe_failure_persistence_failure_preserves_exit_two(self):
        with patch.object(hosted, 'main', side_effect=ReconciliationBlocked('custody_post_backup_verification_failed')), \
                patch.object(hosted, 'persist_summary', side_effect=OSError('private path')), \
                redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error: hosted.cli()
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(json.loads(output.getvalue())['status'], 'blocked')
        self.assertNotIn('private path', output.getvalue())

    def test_offline_parser_requires_exactly_one_mode_before_key(self):
        for args in [[], ['--archives-only', '--run-id', '900']]:
            with self.subTest(args=args), patch('sys.argv', ['verify_index_custody.py', *args]), \
                    patch.object(offline, 'validate_key') as key, \
                    patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as caught: offline.main()
                self.assertEqual(caught.exception.code, 2)
                key.assert_not_called()

    def test_offline_archive_result_never_claims_hosted_activation(self):
        fixture = self.fixture
        with patch('sys.argv', ['verify_index_custody.py', '--archives-only']), \
                patch.object(offline, 'validate_key', return_value=fixture.key), \
                patch.object(offline, 'restore_baseline', return_value=(fixture.exported, 'baseline')), \
                patch.object(offline, 'verify_archived_inputs', return_value=(fixture.before, fixture.observed)), \
                patch.object(offline, 'build_plan', return_value={'synthetic': 'plan', 'plan_sha256': 'a' * 64}), \
                patch.object(offline, 'StartupGitHubEvidence') as github, \
                redirect_stdout(io.StringIO()):
            result = offline.main()
        github.assert_not_called()
        self.assertEqual(result['operational_writes'], 0)
        self.assertEqual(result['plan_sha256'], 'a' * 64)
        self.assertFalse(result['hosted_verification'])
        self.assertFalse(result['activation_authorized'])

    def test_hosted_receipt_cli_writes_exact_actual_admitted_bytes_once(self):
        fixture = self.fixture
        with patch('sys.argv', ['verify_index_custody.py', '--run-id', '900']), \
                patch.dict(os.environ, {'GITHUB_TOKEN': 'synthetic-token'}), \
                patch.object(offline, 'validate_key', return_value=fixture.key), \
                patch.object(offline, 'restore_baseline', return_value=(fixture.exported, 'baseline')), \
                patch.object(offline, 'verify_archived_inputs', return_value=(fixture.before, fixture.observed)), \
                patch.object(offline, 'build_plan', return_value={'synthetic': 'plan'}), \
                patch.object(offline, 'StartupGitHubEvidence', return_value=fixture.github), \
                patch.object(offline, 'RECEIPT_PATH', fixture.root / 'cli-receipt.json'), \
                patch.object(offline, 'COMPLETE_ARCHIVE_PATH', fixture.root / 'cli-complete.aesgcm'), \
                redirect_stdout(io.StringIO()):
            result = offline.main()
            receipt = json.loads(offline.RECEIPT_PATH.read_bytes())
            self.assertEqual(offline.COMPLETE_ARCHIVE_PATH.read_bytes(), fixture.archive.read_bytes())
            self.assertEqual(result['receipt_content_sha256'], receipt['receipt_content_sha256'])
            self.assertFalse(result['activation_authorized'])
            with self.assertRaisesRegex(ValueError, 'durable_evidence_destination_exists'): offline.main()

    def test_write_once_refuses_existing_destination_and_symlinks(self):
        path = self.fixture.root / 'once.json'
        offline._write_once(path, b'first')
        with self.assertRaises(FileExistsError): offline._write_once(path, b'second')
        self.assertEqual(path.read_bytes(), b'first')
        symlink = self.fixture.root / 'link.json'; symlink.symlink_to(path)
        with self.assertRaises(OSError): offline._write_once(symlink, b'changed')
        self.assertEqual(path.read_bytes(), b'first')


if __name__ == '__main__':
    unittest.main()
