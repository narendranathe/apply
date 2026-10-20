"""Index failures expose bounded codes without private upstream content."""
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock

from cloud.index_publisher import IndexPublicationError, publish_index
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError
from tests.test_agent_index import NOW, contact
from tests.test_index_publisher import FreshStore


PRIVATE = 'private-contact-message-token-response-body'


class IndexDiagnosticsTests(unittest.TestCase):
    def test_failed_source_fetch_retains_safe_http_diagnostics_without_retry(self):
        for status, code in ((403, 'restricted_resource'), (429, 'rate_limited')):
            with self.subTest(status=status):
                failure = NotionAPIError(status, code)
                failure.operation = 'query_data_source'
                failure.args = (PRIVATE,)

                class FailedSource(FreshStore):
                    def query(self, kind=None):
                        self.query_calls.append(kind)
                        raise failure

                store = FailedSource([contact(name=PRIVATE)])
                with self.assertRaises(IndexPublicationError) as caught:
                    publish_index(store, NOW)

                diagnostics = caught.exception.diagnostics()
                self.assertEqual(diagnostics, {
                    'error_code': 'authoritative_snapshot_unavailable',
                    'notion': {'http_status': status, 'error_code': code,
                               'operation': 'query_data_source'},
                })
                self.assertNotIn(PRIVATE, json.dumps(diagnostics))
                self.assertEqual(store.query_calls, [None])
                self.assertEqual(store.put_calls, [])

    def test_manifest_fetch_write_and_readback_failures_stay_distinct_and_never_retry(self):
        for stage, expected, operation in (
            ('manifest', 'previous_index_unavailable', 'query_data_source'),
            ('write', 'index_write_uncertain_stop_no_retry', 'create_page'),
            ('readback', 'index_readback_uncertain_stop_no_retry', 'list_block_children'),
        ):
            for status, code in ((403, 'restricted_resource'), (429, 'rate_limited')):
                with self.subTest(stage=stage, status=status):
                    diagnostic = {'http_status': status, 'error_code': code,
                                  'operation': operation, 'body': PRIVATE}
                    if stage == 'write':
                        failure = AmbiguousWriteError(PRIVATE, diagnostics=diagnostic)
                    else:
                        failure = NotionAPIError(status, code)
                        failure.operation = operation
                        failure.args = (PRIVATE,)

                    class FailedStore(FreshStore):
                        fail = False

                        def latest_index_manifest(self):
                            result = super().latest_index_manifest()
                            if self.fail and stage == 'manifest':
                                raise failure
                            return result

                        def put(self, row):
                            result = super().put(row)
                            if self.fail and stage == 'write':
                                raise failure
                            return result

                        def get(self, identity):
                            result = super().get(identity)
                            if self.fail and stage == 'readback':
                                raise failure
                            return result

                    store = FailedStore([contact(name=PRIVATE)])
                    old = publish_index(store, NOW - timedelta(minutes=1))
                    store.fail = True
                    store.query_calls.clear()
                    store.put_calls.clear()
                    store.get_calls.clear()
                    store.latest_calls = 0
                    with self.assertRaises(IndexPublicationError) as caught:
                        publish_index(store, NOW)

                    diagnostic.pop('body')
                    self.assertEqual(caught.exception.diagnostics(), {
                        'error_code': expected, 'notion': diagnostic,
                    })
                    self.assertNotIn(PRIVATE, json.dumps(caught.exception.diagnostics()))
                    self.assertEqual(store.query_calls, [None, 'outreach_events'])
                    self.assertEqual(store.latest_calls, 1)
                    self.assertEqual(len(store.put_calls), 0 if stage == 'manifest' else 1)
                    self.assertEqual(len(store.get_calls), 1 if stage == 'readback' else 0)
                    self.assertFalse(any(identity.startswith('index_manifest:') for identity in store.put_calls))
                    store.fail = False
                    self.assertEqual(store.latest_index_manifest()['id'], old['manifest_id'])

    def test_suppressed_context_and_explicit_cause_keep_only_notion_safe_fields(self):
        upstream = NotionAPIError(429, 'rate_limited')
        upstream.operation = 'query_data_source'
        upstream.args = (PRIVATE,)
        try:
            try:
                raise upstream
            except NotionAPIError as error:
                raise RuntimeError(PRIVATE) from error
        except RuntimeError:
            try:
                raise IndexPublicationError('authoritative_snapshot_unavailable') from None
            except IndexPublicationError as error:
                diagnostic = error.diagnostics()

        self.assertEqual(diagnostic, {
            'error_code': 'authoritative_snapshot_unavailable',
            'notion': {'http_status': 429, 'error_code': 'rate_limited',
                       'operation': 'query_data_source'},
        })
        self.assertNotIn(PRIVATE, json.dumps(diagnostic))

    def test_unknown_codes_and_arbitrary_diagnostics_cannot_leak(self):
        for code in (PRIVATE, ['authoritative_snapshot_unavailable'], None):
            with self.subTest(code=code):
                error = IndexPublicationError(code)
                upstream = RuntimeError(PRIVATE)
                upstream.diagnostics = Mock(return_value={'body': PRIVATE})
                error.__cause__ = upstream

                self.assertEqual(error.diagnostics(), {'error_code': 'index_publication_failed'})
                upstream.diagnostics.assert_not_called()

    def test_only_native_notion_error_types_supply_sanitized_diagnostics(self):
        class UntrustedDiagnostics(NotionAPIError):
            def diagnostics(self):
                return {'body': PRIVATE}

        upstream = UntrustedDiagnostics(403, PRIVATE)
        upstream.operation = PRIVATE
        error = IndexPublicationError('index_readback_uncertain_stop_no_retry')
        error.__cause__ = upstream
        self.assertEqual(error.diagnostics(), {
            'error_code': 'index_readback_uncertain_stop_no_retry',
        })

        upstream = NotionAPIError(403, PRIVATE)
        upstream.operation = PRIVATE
        upstream.diagnostics = Mock(return_value={'body': PRIVATE})
        error.__cause__ = upstream
        self.assertEqual(error.diagnostics(), {
            'error_code': 'index_readback_uncertain_stop_no_retry',
            'notion': {'http_status': 403, 'error_code': 'unknown',
                       'operation': 'notion_request'},
        })
        upstream.diagnostics.assert_not_called()

    def test_conflict_context_exposes_only_the_closed_verification_operation(self):
        for operation, expected in (('verify_cloud_body', 'verify_cloud_body'),
                                    (PRIVATE, 'notion_request')):
            with self.subTest(operation=operation):
                upstream = ConflictError(PRIVATE, operation=operation)
                error = IndexPublicationError('authoritative_snapshot_unavailable')
                error.__context__ = upstream
                self.assertEqual(error.diagnostics(), {
                    'error_code': 'authoritative_snapshot_unavailable',
                    'notion': {'http_status': None, 'error_code': 'unknown',
                               'operation': expected},
                })
                self.assertNotIn(PRIVATE, json.dumps(error.diagnostics()))

    def test_exception_cycles_and_deep_chains_remain_bounded(self):
        error = IndexPublicationError('authoritative_snapshot_invalid')
        context = RuntimeError(PRIVATE)
        error.__context__, context.__cause__ = context, error
        self.assertEqual(error.diagnostics(), {'error_code': 'authoritative_snapshot_invalid'})

        current = error
        for _ in range(32):
            nested = RuntimeError(PRIVATE)
            current.__cause__ = nested
            current = nested
        current.__cause__ = NotionAPIError(403, 'restricted_resource')
        self.assertEqual(error.diagnostics(), {'error_code': 'authoritative_snapshot_invalid'})


if __name__ == '__main__':
    unittest.main()
