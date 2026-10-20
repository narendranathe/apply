"""Publication wrappers keep the entrypoint's closed diagnostic boundary."""
import json
import unittest

from cloud.index_publisher import IndexPublicationError
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError
from cloud.orchestrator import _failure_summary


class OrchestratorIndexDiagnosticsTests(unittest.TestCase):
    def test_index_wrapper_reports_only_closed_code_and_sanitized_notion_fields(self):
        for upstream, expected in (
            (NotionAPIError(403, 'restricted_resource'), {'http_status': 403, 'error_code': 'restricted_resource', 'operation': 'query_data_source'}),
            (NotionAPIError(429, 'rate_limited'), {'http_status': 429, 'error_code': 'rate_limited', 'operation': 'query_data_source'}),
            (AmbiguousWriteError('PRIVATE TOKEN BODY', diagnostics={'http_status': 503, 'error_code': 'service_unavailable', 'operation': 'create_page', 'body': 'PRIVATE TOKEN BODY'}), {'http_status': 503, 'error_code': 'service_unavailable', 'operation': 'create_page'}),
            (ConflictError('PRIVATE TOKEN BODY', operation='verify_cloud_body'), {'http_status': None, 'error_code': 'unknown', 'operation': 'verify_cloud_body'}),
        ):
            with self.subTest(error_type=type(upstream).__name__):
                if isinstance(upstream, NotionAPIError):
                    upstream.operation = 'query_data_source'
                    upstream.args = ('PRIVATE TOKEN BODY',)
                error = IndexPublicationError('authoritative_snapshot_unavailable')
                error.__context__ = upstream
                error._cloud_operation = 'publish_agent_index'
                summary = _failure_summary(error)
                self.assertEqual(summary, {'status': 'blocked', 'error_type': 'IndexPublicationError',
                    'reason': 'authoritative_snapshot_unavailable', 'cloud_operation': 'publish_agent_index',
                    'notion': expected})
                self.assertNotIn('PRIVATE', json.dumps(summary))

    def test_unknown_publication_or_context_content_cannot_escape_summary(self):
        class UntrustedDiagnostic(IndexPublicationError):
            def diagnostics(self):
                return {'error_code': 'PRIVATE TOKEN BODY', 'notion': {'body': 'PRIVATE TOKEN BODY'}}
        error = UntrustedDiagnostic('PRIVATE TOKEN BODY')
        error.__context__ = RuntimeError('PRIVATE TOKEN BODY')
        summary = _failure_summary(error)
        self.assertEqual(summary['reason'], 'index_publication_failed')
        self.assertNotIn('notion', summary)
        self.assertNotIn('PRIVATE', json.dumps(summary))


if __name__ == '__main__':
    unittest.main()
