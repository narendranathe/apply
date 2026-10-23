"""Safe aggregate failure artifacts without another storage/provider operation."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError
from cloud.startup_reconciliation import ReconciliationBlocked

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/reconcile_readback.py'
PRIVATE = 'synthetic-private-token-contact-response-content'
ENV = {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main',
       'GITHUB_REPOSITORY': 'narendranathe/apply', 'GITHUB_ACTOR': 'narendranathe',
       'GITHUB_TRIGGERING_ACTOR': 'narendranathe', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
       'CLOUD_ENABLED': 'false', 'NOTION_TOKEN': PRIVATE, 'MIGRATION_BACKUP_KEY': PRIVATE}


class ReadbackFailureArtifactTests(unittest.TestCase):
    def entrypoint(self, error, *, unavailable_output=False):
        output, stderr = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                if unavailable_output:
                    Path('run-output').write_text('Synthetic unavailable output directory')
                with patch.dict(os.environ, ENV, clear=True), \
                     patch('cloud.readback_reconciliation.require_reviewed_pins', side_effect=error), \
                     patch('cloud.backup.validate_key') as key, \
                     patch('cloud.baseline.restore_baseline') as baseline, \
                     patch('cloud.notion.NotionStore') as store, \
                     patch('cloud.readback_reconciliation.reconcile') as reconcile, \
                     patch('cloud.backup.backup_store') as backup, \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        runpy.run_path(str(SCRIPT), run_name='__main__')
                self.assertEqual(caught.exception.code, 2)
                for action in (key, baseline, store, reconcile, backup):
                    action.assert_not_called()
                printed = json.loads(output.getvalue())
                if unavailable_output:
                    stored = None
                else:
                    destination = Path('run-output/maintenance-summary.json')
                    self.assertTrue(destination.is_file(), 'failed reconciliation must leave an aggregate artifact')
                    stored = json.loads(destination.read_text())
                    self.assertEqual(destination.stat().st_mode & 0o077, 0)
                    self.assertEqual(stored, printed)
            finally:
                os.chdir(previous)
        self.assertNotIn(PRIVATE, output.getvalue() + stderr.getvalue())
        self.assertNotIn('Authorization', output.getvalue() + stderr.getvalue())
        self.assertEqual(stderr.getvalue(), '')
        return printed, stored

    def test_uncertain_transport_failure_persists_only_sanitized_notion_fields(self):
        error = AmbiguousWriteError(PRIVATE, diagnostics={'http_status': PRIVATE,
            'error_code': PRIVATE, 'operation': 'create_page', 'body': PRIVATE,
            'Authorization': PRIVATE})
        printed, _ = self.entrypoint(error)
        self.assertEqual(printed, {'status': 'blocked', 'error_type': 'AmbiguousWriteError',
            'operation': 'pinned_index_readback_reconciliation',
            'notion': {'http_status': None, 'error_code': 'unknown', 'operation': 'create_page'}})

    def test_verified_http_diagnostic_fields_are_preserved(self):
        printed, _ = self.entrypoint(NotionAPIError(403, 'restricted_resource'))
        self.assertEqual(printed['notion'], {'http_status': 403,
            'error_code': 'restricted_resource', 'operation': 'notion_request'})

    def test_conflict_operation_is_sanitized_without_exception_text(self):
        printed, _ = self.entrypoint(ConflictError(PRIVATE, operation=PRIVATE))
        self.assertEqual(printed['notion'], {'http_status': None,
            'error_code': 'unknown', 'operation': 'notion_request'})

    def test_known_reconciliation_reason_is_retained_and_unknown_reason_is_closed(self):
        printed, _ = self.entrypoint(ReconciliationBlocked('partial_resolution_batch_requires_reconciliation'))
        self.assertEqual(printed['reason'], 'partial_resolution_batch_requires_reconciliation')
        printed, _ = self.entrypoint(ReconciliationBlocked(PRIVATE))
        self.assertEqual(printed['reason'], 'reconciliation_requirements_not_verified')

    def test_unexpected_exception_name_and_text_are_not_serialized(self):
        private_type = type(PRIVATE, (RuntimeError,), {})
        printed, _ = self.entrypoint(private_type(PRIVATE))
        self.assertEqual(printed, {'status': 'blocked', 'error_type': 'UnexpectedError',
                                 'operation': 'pinned_index_readback_reconciliation'})

    def test_custom_diagnostic_override_cannot_serialize_private_values(self):
        class UnsafeOverride(AmbiguousWriteError):
            def diagnostics(self):
                return {'body': PRIVATE, 'Authorization': PRIVATE}
        printed, _ = self.entrypoint(UnsafeOverride(PRIVATE, diagnostics={'operation': 'create_page'}))
        self.assertEqual(printed['error_type'], 'AmbiguousWriteError')
        self.assertEqual(printed['notion'], {'http_status': None,
            'error_code': 'unknown', 'operation': 'create_page'})

    def test_artifact_filesystem_failure_preserves_original_exit_without_retry(self):
        printed, stored = self.entrypoint(AmbiguousWriteError(PRIVATE), unavailable_output=True)
        self.assertIsNone(stored)
        self.assertEqual(printed['error_type'], 'AmbiguousWriteError')


if __name__ == '__main__':
    unittest.main()
