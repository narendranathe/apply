"""Bounded read-only polling after an exact immutable index write."""
from copy import deepcopy
from datetime import timedelta
import unittest

from cloud.index_publisher import IndexPublicationError, _put_verified, publish_index
from cloud.notion import ConflictError, NotionAPIError, NotionStore
from cloud.records import make_record
from cloud.runtime_budget import BudgetExceeded, RuntimeBudget
from tests.test_agent_index import NOW, contact
from tests.test_index_publisher import FreshStore
from tests.test_notion import FakeNotion, page_list


class TransientVisibilityAPI(FakeNotion):
    """The successful put readback sees a page before one absent lookup."""
    def __init__(self):
        super().__init__()
        self.hide_queries = 0
        self.armed = False

    def __call__(self, method, path, body, headers):
        if method == 'POST' and path.endswith('/query') and self.hide_queries:
            self.hide_queries -= 1
            self.calls.append((method, path, deepcopy(body)))
            return page_list([])
        response = super().__call__(method, path, body, headers)
        if method == 'GET' and path.startswith('/blocks/') and not self.armed:
            # The internal put() lookup already has the exact page and body.
            # Hide only the following Cloud and legacy lookup responses.
            self.hide_queries = 2
            self.armed = True
        return response


class ReadbackStore:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.put_calls, self.get_calls, self.delays = [], [], []
        self.sleeper = self.delays.append

    def put(self, row):
        self.put_calls.append(row['id'])
        return deepcopy(row)

    def get(self, identity):
        self.get_calls.append(identity)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return deepcopy(outcome)


class IndexVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.row = make_record('entity_projections', {'synthetic': True}, 'projection:visibility')

    def test_native_store_recovers_one_absent_lookup_without_repeating_create(self):
        api, delays = TransientVisibilityAPI(), []
        store = NotionStore('synthetic-token', transport=api, sleeper=delays.append,
                            request_interval=0)
        self.assertEqual(_put_verified(store, self.row)['hash'], self.row['hash'])
        self.assertEqual(delays, [1])
        self.assertEqual(len(api.writes()), 1)

    def test_absence_polling_is_bounded_and_can_recover_at_last_read(self):
        store = ReadbackStore([None, None, None, self.row])
        self.assertEqual(_put_verified(store, self.row), self.row)
        self.assertEqual(store.delays, [1, 2, 4])
        self.assertEqual(store.get_calls, [self.row['id']] * 4)
        self.assertEqual(store.put_calls, [self.row['id']])

    def test_exhausted_absence_never_publishes_a_manifest_or_repeats_write(self):
        store = FreshStore([contact()])
        old = publish_index(store, NOW - timedelta(minutes=1))
        store.put_calls.clear()
        store.bad_readback_at = 1
        delays = []
        store.sleeper = delays.append
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_mismatch_stop_no_retry'):
            publish_index(store, NOW)
        self.assertEqual(delays, [1, 2, 4])
        self.assertEqual(len(store.put_calls), 1)
        self.assertFalse(store.put_calls[0].startswith('index_manifest:'))
        self.assertEqual(store.latest_index_manifest()['id'], old['manifest_id'])

    def test_non_none_mismatch_or_malformed_value_is_never_polled(self):
        for value in (dict(self.row, status='changed'), {}, [], False):
            with self.subTest(value=value):
                store = ReadbackStore([value, self.row])
                with self.assertRaisesRegex(IndexPublicationError, 'index_readback_mismatch_stop_no_retry'):
                    _put_verified(store, self.row)
                self.assertEqual(store.delays, [])
                self.assertEqual(len(store.get_calls), 1)
                self.assertEqual(len(store.put_calls), 1)

    def test_mismatch_after_absence_stops_without_more_reads(self):
        store = ReadbackStore([None, dict(self.row, status='changed'), self.row])
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_mismatch_stop_no_retry'):
            _put_verified(store, self.row)
        self.assertEqual(store.delays, [1])
        self.assertEqual(len(store.get_calls), 2)
        self.assertEqual(len(store.put_calls), 1)

    def test_conflict_after_absence_stops_with_safe_diagnostic(self):
        conflict = ConflictError('private synthetic diagnostic', operation='verify_cloud_body')
        store = ReadbackStore([None, conflict, self.row])
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_uncertain_stop_no_retry') as caught:
            _put_verified(store, self.row)
        self.assertEqual(caught.exception.diagnostics(), {
            'error_code': 'index_readback_uncertain_stop_no_retry',
            'notion': {'http_status': None, 'error_code': 'unknown', 'operation': 'verify_cloud_body'}})
        self.assertNotIn('private', str(caught.exception.diagnostics()))
        self.assertEqual(store.delays, [1])
        self.assertEqual(len(store.get_calls), 2)
        self.assertEqual(len(store.put_calls), 1)

    def test_api_error_after_absence_is_not_polled_and_keeps_closed_diagnostic(self):
        error = NotionAPIError(403, 'restricted_resource')
        error.operation = 'query_data_source'
        store = ReadbackStore([None, error, self.row])
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_uncertain_stop_no_retry') as caught:
            _put_verified(store, self.row)
        self.assertEqual(caught.exception.diagnostics(), {
            'error_code': 'index_readback_uncertain_stop_no_retry',
            'notion': {'http_status': 403, 'error_code': 'restricted_resource', 'operation': 'query_data_source'}})
        self.assertEqual(store.delays, [1])
        self.assertEqual(len(store.get_calls), 2)
        self.assertEqual(len(store.put_calls), 1)

    def test_initial_read_error_is_not_polled(self):
        store = ReadbackStore([ConflictError('synthetic conflict'), self.row])
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_uncertain_stop_no_retry'):
            _put_verified(store, self.row)
        self.assertEqual(store.delays, [])
        self.assertEqual(len(store.get_calls), 1)
        self.assertEqual(len(store.put_calls), 1)

    def test_exact_read_needs_no_delay_or_extra_request(self):
        store = ReadbackStore([self.row])
        self.assertEqual(_put_verified(store, self.row), self.row)
        self.assertEqual(store.delays, [])
        self.assertEqual(len(store.get_calls), 1)
        self.assertEqual(len(store.put_calls), 1)

    def test_missing_sleeper_preserves_immediate_fail_closed_behavior(self):
        store = ReadbackStore([None, self.row])
        del store.sleeper
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_mismatch_stop_no_retry'):
            _put_verified(store, self.row)
        self.assertEqual(store.delays, [])
        self.assertEqual(len(store.get_calls), 1)
        self.assertEqual(len(store.put_calls), 1)

    def test_native_transport_budget_remains_enforced_on_visibility_read(self):
        api, current, delays = TransientVisibilityAPI(), [0], []

        def sleeper(delay):
            delays.append(delay)
            current[0] += delay

        store = NotionStore('synthetic-token', transport=api, sleeper=sleeper, request_interval=0)
        RuntimeBudget(seconds=1, reserve_seconds=0, clock=lambda: current[0]).attach(store)
        with self.assertRaisesRegex(IndexPublicationError, 'index_readback_uncertain_stop_no_retry') as caught:
            _put_verified(store, self.row)
        self.assertIsInstance(caught.exception.__context__, BudgetExceeded)
        self.assertEqual(delays, [1])
        self.assertEqual(len(api.writes()), 1)
        self.assertEqual(api.hide_queries, 0)


if __name__ == '__main__':
    unittest.main()
