import json
import unittest

from cloud.notion import AmbiguousWriteError, NotionAPIError, NotionStore
from cloud.records import make_record
from tests.test_notion import FakeNotion


class WriteDiagnosticsTests(unittest.TestCase):
    def test_visibility_lag_only_repeats_absent_reads_not_creation(self):
        api = FakeNotion()
        delays = []
        store = NotionStore('test-token', transport=api, request_interval=0, sleeper=delays.append)
        original = store.get
        calls = 0
        def get(identity):
            nonlocal calls
            calls += 1
            if 1 < calls < 5:
                return None
            return original(identity)
        store.get = get
        row = make_record('contacts', {}, 'contact:visibility')
        self.assertEqual(store.put(row)['hash'], row['hash'])
        self.assertEqual(len(api.writes()), 1)
        self.assertEqual(delays, [1, 2, 4])
        self.assertNotIn(row['id'], store._uncertain)

    def test_readback_api_error_keeps_only_safe_fields_and_quarantines(self):
        api = FakeNotion()
        store = NotionStore('private-token', transport=api, request_interval=0)
        original = store.get
        calls = 0

        def get(identity):
            nonlocal calls
            calls += 1
            if calls > 1:
                error = NotionAPIError(403, 'restricted_resource')
                error.operation = 'retrieve_page_property'
                raise error
            return original(identity)

        store.get = get
        with self.assertRaises(AmbiguousWriteError) as caught:
            store.put(make_record('contacts', {'message': 'private message'}, 'private-id'))
        self.assertEqual(caught.exception.diagnostics(), {
            'http_status': 403, 'error_code': 'restricted_resource',
            'operation': 'retrieve_page_property'})
        self.assertIn('private-id', store._uncertain)
        self.assertEqual(len(api.writes()), 1)
        self.assertNotIn('private', json.dumps(caught.exception.diagnostics()))

    def test_write_http_failure_keeps_route_only_without_replay(self):
        api = FakeNotion()
        api.faults = [('POST', '/pages', NotionAPIError(503, 'service_unavailable'))]
        store = NotionStore('private-token', transport=api, request_interval=0)
        with self.assertRaises(AmbiguousWriteError) as caught:
            store.put(make_record('contacts', {}, 'private-id'))
        self.assertEqual(caught.exception.diagnostics(), {
            'http_status': 503, 'error_code': 'service_unavailable', 'operation': 'create_page'})
        self.assertEqual(len([x for x in api.calls if x[:2] == ('POST', '/pages')]), 1)

    def test_arbitrary_diagnostic_fields_cannot_escape(self):
        error = AmbiguousWriteError('private error', diagnostics={
            'http_status': 'private', 'error_code': 'private token',
            'operation': 'private-id', 'body': 'private message'})
        self.assertEqual(error.diagnostics(), {
            'http_status': None, 'error_code': 'unknown', 'operation': 'notion_request'})
