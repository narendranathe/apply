"""Exercise the runner entry point without credentials or remote operations."""
import contextlib
import io
import json
import runpy
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from cloud.notion import NotionAPIError, NotionStore


class RunnerDiagnosticsTests(unittest.TestCase):
    def runner_failure(self, error):
        output = io.StringIO()
        with patch.dict('os.environ', {'NOTION_TOKEN': 'fixture-private-token', 'CLOUD_ENABLED': 'false'}), \
                patch('sys.argv', ['cloud.orchestrator', '--mode', 'dry-run']), \
                patch('cloud.baseline.restore_baseline', return_value=({'records': []}, 'a' * 64)), \
                patch.object(NotionStore, '_transport', side_effect=error) as transport, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_result:
                runpy.run_module('cloud.orchestrator', run_name='__main__')
        self.assertEqual(exit_result.exception.code, 1)
        self.assertEqual(transport.call_count, 1)
        return json.loads(output.getvalue())

    def test_runner_exposes_only_safe_notion_metadata(self):
        result = self.runner_failure(NotionAPIError(400, 'validation_error'))
        self.assertEqual(result, {
            'status': 'blocked', 'error_type': 'NotionAPIError',
            'reason': 'inspect_private_reconciliation_records',
            'cloud_operation': 'calculate_research_share',
            'notion': {'http_status': 400, 'error_code': 'validation_error',
                       'operation': 'query_data_source'},
        })
        self.assertNotIn('fixture-private-token', json.dumps(result))

    def test_runner_never_emits_unrecognized_api_code(self):
        result = self.runner_failure(NotionAPIError(400, 'private-contact-and-message-content'))
        self.assertNotIn('private-contact-and-message-content', json.dumps(result))
        self.assertEqual(result['notion']['error_code'], 'unknown')


class NotionTransportDiagnosticsTests(unittest.TestCase):
    def test_http_error_body_headers_and_request_data_are_never_diagnostics(self):
        private = 'fixture-private-token-contact-message'
        error = HTTPError('https://api.notion.com/v1/private-url', 403, private,
                          {'Authorization': private}, io.BytesIO(json.dumps({
                              'code': 'restricted_resource', 'message': private,
                              'additional_data': {'private': private},
                          }).encode()))
        store = NotionStore(private)
        with patch('cloud.notion.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(NotionAPIError) as raised:
                store._request('POST', '/data_sources/private-id/query', {'filter': private})
        self.assertEqual(raised.exception.diagnostics(), {
            'http_status': 403, 'error_code': 'restricted_resource',
            'operation': 'query_data_source',
        })
        self.assertNotIn(private, str(raised.exception))
        self.assertNotIn(private, json.dumps(raised.exception.diagnostics()))

    def test_malformed_error_payload_retains_http_status_without_body(self):
        for body in ([], None, {'code': ['private-message'], 'additional_data': []},
                     {'code': 'private_message', 'additional_data': {'retry_after': []}}):
            with self.subTest(body=type(body).__name__):
                error = HTTPError('https://api.notion.com/v1/private-url', 400,
                                  'private-response-message', {}, io.BytesIO(json.dumps(body).encode()))
                with patch('cloud.notion.build_opener') as opener:
                    opener.return_value.open.side_effect = error
                    with self.assertRaises(NotionAPIError) as raised:
                        NotionStore('fixture-token')._request('POST', '/data_sources/private-id/query')
                self.assertEqual(raised.exception.diagnostics(), {
                    'http_status': 400, 'error_code': 'unknown', 'operation': 'query_data_source',
                })
                self.assertNotIn('private', str(raised.exception))

    def test_unknown_operation_and_tampered_metadata_cannot_leak(self):
        error = NotionAPIError(400, 'validation_error')
        store = NotionStore('fixture-token', transport=lambda *args: self.raise_error(error))
        with self.assertRaises(NotionAPIError):
            store._request('POST', '/private-contact-message?token=fixture-token')
        self.assertEqual(error.diagnostics()['operation'], 'notion_request')
        error.status, error.api_code, error.operation = 'private-status', {'private': 'body'}, 'private-operation'
        self.assertEqual(error.diagnostics(), {
            'http_status': None, 'error_code': 'unknown', 'operation': 'notion_request',
        })

    @staticmethod
    def raise_error(error):
        raise error

    def test_pagination_routes_and_retry_budget_preserve_safe_metadata(self):
        routes = [('GET', '/data_sources/private-id', 'retrieve_data_source'),
                  ('POST', '/data_sources/private-id/query', 'query_data_source'),
                  ('GET', '/blocks/private-id/children?start_cursor=private-cursor', 'list_block_children'),
                  ('GET', '/pages/private-id/properties/private-property?start_cursor=private', 'retrieve_page_property'),
                  ('POST', '/pages', 'create_page')]
        for method, path, operation in routes:
            with self.subTest(operation=operation):
                error = NotionAPIError(429, 'rate_limited', 120)
                store = NotionStore('fixture-token', transport=lambda *args: self.raise_error(error))
                with self.assertRaises(NotionAPIError) as raised:
                    store._request(method, path, write=operation == 'create_page')
                self.assertEqual(raised.exception.diagnostics(), {
                    'http_status': 429, 'error_code': 'rate_limited', 'operation': operation,
                })
                self.assertEqual(raised.exception.code, 'retry_after_exceeds_budget')


if __name__ == '__main__':
    unittest.main()
