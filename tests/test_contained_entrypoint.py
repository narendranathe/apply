"""Owner-only contained maintenance and safe evidence, without live I/O."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from cloud.notion import AmbiguousWriteError

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/reconcile_contained.py'
SECRET = 'synthetic-private-notion-token-and-contact-body'
ENV = {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main',
       'GITHUB_REPOSITORY': 'narendranathe/apply', 'GITHUB_ACTOR': 'narendranathe',
       'GITHUB_TRIGGERING_ACTOR': 'narendranathe', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
       'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '9000', 'GITHUB_RUN_ATTEMPT': '1',
       'CLOUD_ENABLED': 'false', 'NOTION_TOKEN': SECRET, 'GITHUB_TOKEN': SECRET,
       'MIGRATION_BACKUP_KEY': SECRET}


class ContainedEntrypointTests(unittest.TestCase):
    def run_script(self, env=None, error=None, backup_error=None):
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            (root / 'config').mkdir()
            (root / 'config/cloud.json').write_text(json.dumps({'notion_data_source_id': 'a' * 32}))
            previous = Path.cwd()
            os.chdir(root)
            stack.callback(os.chdir, previous)
            stack.enter_context(patch.dict(os.environ, ENV if env is None else env, clear=True))
            mocks = {}
            for name, target in {
                'pins': 'cloud.contained_recovery.require_reviewed_pins',
                'key': 'cloud.backup.validate_key',
                'baseline': 'cloud.baseline.restore_baseline',
                'store': 'cloud.notion.NotionStore',
                'github': 'scripts.reconcile_startup.StartupGitHubEvidence',
                'reconcile': 'cloud.contained_recovery.reconcile',
                'backup': 'cloud.backup.backup_store',
            }.items():
                mocks[name] = stack.enter_context(patch(target))
            mocks['key'].return_value = b'k' * 32
            mocks['baseline'].return_value = ({'records': []}, {})
            mocks['reconcile'].return_value = {'operation': 'pinned_contained_storage_reconciliation',
                                               'verified': True, 'external_effects': 0}
            mocks['reconcile'].side_effect = error
            mocks['backup'].return_value = {'path': 'run-output/synthetic.aesgcm'}
            mocks['backup'].side_effect = backup_error
            exit_code = 0
            with redirect_stdout(output), redirect_stderr(errors):
                try:
                    runpy.run_path(str(SCRIPT), run_name='__main__')
                except SystemExit as caught:
                    exit_code = caught.code
            destination = root / 'run-output/maintenance-summary.json'
            stored = json.loads(destination.read_text()) if destination.exists() else None
            private = not destination.stat().st_mode & 0o077 if destination.exists() else None
        self.assertNotIn(SECRET, output.getvalue() + errors.getvalue())
        self.assertEqual(errors.getvalue(), '')
        return exit_code, stored, private, mocks, output.getvalue()

    def test_rejects_every_untrusted_context_before_credentials(self):
        variants = {'GITHUB_ACTIONS': 'false', 'GITHUB_REF': 'refs/heads/codex/test',
                    'GITHUB_REPOSITORY': 'other/apply', 'GITHUB_ACTOR': 'other',
                    'GITHUB_TRIGGERING_ACTOR': 'other', 'GITHUB_EVENT_NAME': 'schedule',
                    'CLOUD_ENABLED': 'true', 'GITHUB_RUN_ATTEMPT': '2'}
        for field, value in variants.items():
            with self.subTest(field=field):
                env = {**ENV, field: value}
                code, summary, private, mocks, _ = self.run_script(env)
                self.assertEqual(code, 2)
                self.assertEqual(summary['status'], 'blocked')
                self.assertTrue(private)
                for key in ('pins', 'key', 'baseline', 'store', 'github', 'reconcile', 'backup'):
                    mocks[key].assert_not_called()

    def test_delegates_only_fixed_context_then_backs_up_without_effects(self):
        code, summary, _, mocks, _ = self.run_script()
        self.assertEqual(code, 0)
        self.assertTrue(summary['verified'])
        self.assertEqual(summary['external_effects'], 0)
        self.assertEqual(summary['backup'], {'path': 'run-output/synthetic.aesgcm'})
        mocks['reconcile'].assert_called_once()
        mocks['backup'].assert_called_once_with(mocks['store'].return_value)
        args = mocks['reconcile'].call_args.args
        self.assertEqual(args[3]['run_id'], '9000')
        self.assertEqual(args[3]['commit'], 'a' * 40)
        self.assertEqual(args[3]['cloud_enabled'], 'false')
        self.assertEqual(args[4], b'k' * 32)

    def test_uncertain_append_leaves_safe_artifact_and_never_calls_backup_or_retries(self):
        error = AmbiguousWriteError(SECRET, diagnostics={'operation': 'create_page',
            'http_status': None, 'error_code': SECRET, 'response': SECRET})
        code, summary, private, mocks, printed = self.run_script(error=error)
        self.assertEqual(code, 2)
        self.assertTrue(private)
        self.assertEqual(summary['notion'], {'http_status': None, 'error_code': 'unknown',
                                            'operation': 'create_page'})
        self.assertEqual(summary['operation'], 'pinned_contained_storage_reconciliation')
        self.assertEqual(json.loads(printed), summary)
        mocks['reconcile'].assert_called_once()
        mocks['backup'].assert_not_called()

    def test_backup_failure_preserves_nonzero_result_without_reentering_reconciliation(self):
        code, summary, private, mocks, _ = self.run_script(backup_error=RuntimeError(SECRET))
        self.assertEqual(code, 2)
        self.assertEqual(summary['status'], 'blocked')
        self.assertTrue(private)
        mocks['reconcile'].assert_called_once()
        mocks['backup'].assert_called_once()
