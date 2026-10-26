"""Real module failures retain safe diagnostics without another backend call."""
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
import warnings

from cloud.index_publisher import IndexPublicationError
from cloud.notion import AmbiguousWriteError, NotionAPIError

PRIVATE = 'synthetic-private-token-contact-message-response-body'
DEFAULT = 'run-output/summary.json'


class OrchestratorFailureArtifactTests(unittest.TestCase):
    def entrypoint(self, error, *, output=None, setup=None, stored=True, write_failure=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory(prefix='orchestrator-failure-artifact-') as directory:
            root = Path(directory).resolve()
            previous = Path.cwd()
            try:
                os.chdir(root)
                config = root / 'config.json'
                config.write_text(json.dumps({'notion_data_source_id': 'synthetic-source'}))
                if setup:
                    setup(root)
                arguments = ['cloud.orchestrator', '--config', str(config), '--mode', 'research-only']
                if output is not None:
                    arguments += ['--output', output]
                transport = Mock(side_effect=AssertionError('No backend request allowed'))
                base = SimpleNamespace(transport=transport)
                failure = (patch('os.ftruncate', side_effect=OSError(PRIVATE))
                           if write_failure else nullcontext())
                with patch.dict(os.environ, {'DESTINATION_PRIVATE': 'true',
                        'CLOUD_ENABLED': 'false', 'CLOUD_RESEARCH_ENABLED': 'true',
                        'NOTION_TOKEN': PRIVATE}, clear=True), \
                     patch('sys.argv', arguments), \
                     patch('cloud.notion.NotionStore', return_value=base) as construct, \
                     patch('cloud.baseline.restore_baseline', side_effect=error) as baseline, \
                     patch('cloud.backup.backup_store') as backup, \
                     failure, \
                     redirect_stdout(stdout), redirect_stderr(stderr), warnings.catch_warnings():
                    warnings.filterwarnings('ignore', category=RuntimeWarning,
                        message="'cloud.orchestrator' found in sys.modules.*")
                    with self.assertRaises(SystemExit) as caught:
                        runpy.run_module('cloud.orchestrator', run_name='__main__')
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(construct.call_count, 1)
                self.assertEqual(baseline.call_count, 1)
                transport.assert_not_called()
                backup.assert_not_called()
                printed = json.loads(stdout.getvalue())
                destination = Path(output or DEFAULT)
                if stored:
                    self.assertTrue(destination.is_file(), 'orchestrator failure must retain its aggregate artifact')
                    self.assertFalse(destination.is_symlink())
                    retained = json.loads(destination.read_text())
                    self.assertEqual(retained, printed)
                    self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
                else:
                    retained = None
                if setup:
                    target = root / 'unrelated-target'
                    if target.exists():
                        self.assertEqual(target.read_text(), 'Unrelated target must remain unchanged')
                        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
                    self.assertFalse((root / 'outside' / 'nested').exists())
            finally:
                os.chdir(previous)
        self.assertNotIn(PRIVATE, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn('Authorization', stdout.getvalue() + stderr.getvalue())
        return printed, retained

    def test_actual_default_entrypoint_retains_safe_notion_failure(self):
        error = NotionAPIError(403, 'restricted_resource')
        error.operation = 'list_block_children'
        error.__cause__ = URLError(PRIVATE)
        printed, retained = self.entrypoint(error)
        self.assertEqual(printed, {'status': 'blocked', 'error_type': 'NotionAPIError',
            'reason': 'inspect_private_reconciliation_records',
            'cloud_operation': 'restore_authenticated_baseline',
            'notion': {'http_status': 403, 'error_code': 'restricted_resource',
                'operation': 'list_block_children'}})
        self.assertEqual(retained, printed)

    def test_actual_custom_fallback_output_uses_the_requested_path(self):
        error = AmbiguousWriteError(PRIVATE, diagnostics={'http_status': 503,
            'error_code': 'service_unavailable', 'operation': 'create_page',
            'response_body': PRIVATE, 'Authorization': PRIVATE})
        printed, _ = self.entrypoint(error, output='run-output/research-fallback-summary.json')
        self.assertEqual(printed['notion'], {'http_status': 503,
            'error_code': 'service_unavailable', 'operation': 'create_page'})

    def test_index_wrapper_retains_only_the_existing_safe_diagnostic(self):
        cause = NotionAPIError(400, 'validation_error')
        cause.operation = 'query_data_source'
        cause.__cause__ = URLError(PRIVATE)
        error = IndexPublicationError('authoritative_snapshot_unavailable')
        error.__cause__ = cause
        printed, _ = self.entrypoint(error, output='private-results/nested/failure.json')
        self.assertEqual(printed['error_type'], 'IndexPublicationError')
        self.assertEqual(printed['reason'], 'authoritative_snapshot_unavailable')
        self.assertEqual(printed['notion'], {'http_status': 400,
            'error_code': 'validation_error', 'operation': 'query_data_source'})

    def test_canonical_absolute_custom_output_is_supported(self):
        with tempfile.TemporaryDirectory(prefix='orchestrator-absolute-output-') as directory:
            destination = Path(directory).resolve() / 'nested' / 'failure.json'
            self.entrypoint(NotionAPIError(429, 'rate_limited'), output=str(destination))

    def test_existing_owned_output_is_restricted_and_stale_bytes_removed(self):
        def setup(root):
            parent = root / 'run-output'; parent.mkdir(mode=0o755); parent.chmod(0o755)
            destination = parent / 'summary.json'
            destination.write_text(PRIVATE * 20); destination.chmod(0o644)
        self.entrypoint(ValueError(PRIVATE), setup=setup)

    def test_unavailable_output_preserves_the_original_diagnostic_and_exit(self):
        def setup(root): (root / 'run-output').write_text('Directory is unavailable')
        printed, _ = self.entrypoint(NotionAPIError(401, 'unauthorized'), setup=setup, stored=False)
        self.assertEqual(printed['notion']['http_status'], 401)

    def test_write_failure_does_not_replace_the_original_safe_diagnostic(self):
        printed, _ = self.entrypoint(NotionAPIError(403, 'restricted_resource'),
            write_failure=True, stored=False)
        self.assertEqual(printed['error_type'], 'NotionAPIError')
        self.assertEqual(printed['notion']['http_status'], 403)

    def test_output_symlink_does_not_overwrite_its_target(self):
        def setup(root):
            target = root / 'unrelated-target'
            target.write_text('Unrelated target must remain unchanged'); target.chmod(0o644)
            parent = root / 'run-output'; parent.mkdir(mode=0o700)
            (parent / 'summary.json').symlink_to(target)
        self.entrypoint(NotionAPIError(400, 'validation_error'), setup=setup, stored=False)

    def test_ancestor_symlink_cannot_create_or_write_outside_output_tree(self):
        def setup(root):
            target = root / 'outside'; target.mkdir(mode=0o700)
            (root / 'run-output').symlink_to(target, target_is_directory=True)
        self.entrypoint(ValueError(PRIVATE), output='run-output/nested/failure.json',
            setup=setup, stored=False)

    def test_hard_link_is_rejected_before_truncating_or_changing_target_mode(self):
        def setup(root):
            target = root / 'unrelated-target'
            target.write_text('Unrelated target must remain unchanged'); target.chmod(0o644)
            parent = root / 'run-output'; parent.mkdir(mode=0o700)
            os.link(target, parent / 'summary.json')
        self.entrypoint(ValueError(PRIVATE), setup=setup, stored=False)

    def test_main_keeps_raising_the_original_exception_for_existing_callers(self):
        from cloud import orchestrator
        error = ValueError(PRIVATE)
        with patch('sys.argv', ['cloud.orchestrator']), \
             patch.object(orchestrator, '_run', side_effect=error) as run, \
             patch.object(orchestrator, '_persist_failure_summary') as persist:
            with self.assertRaises(ValueError) as caught:
                orchestrator.main()
        self.assertIs(caught.exception, error)
        run.assert_called_once()
        persist.assert_not_called()

    def test_successful_cli_keeps_the_runner_result_without_failure_persistence(self):
        from cloud import orchestrator
        expected = object()
        with patch('sys.argv', ['cloud.orchestrator', '--output', 'private/success.json']), \
             patch.object(orchestrator, '_run', return_value=expected) as run, \
             patch.object(orchestrator, '_persist_failure_summary') as persist:
            self.assertIs(orchestrator._cli(), expected)
        self.assertEqual(run.call_args.args[0].output, 'private/success.json')
        run.assert_called_once()
        persist.assert_not_called()


if __name__ == '__main__':
    unittest.main()
