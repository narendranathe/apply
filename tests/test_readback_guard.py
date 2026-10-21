"""Known failed readback writers cannot inherit generic or future clearance."""
from unittest.mock import patch
import unittest

from cloud.orchestrator import Blocked
from cloud.outreach import record
from cloud.readback_reconciliation import FAILURES, SCHEMA
from tests import test_runtime_guard as guard_fixture


class ReadbackWriterGuardTests(unittest.TestCase):
    verify = guard_fixture.CancelledWriterTests.verify

    def fixture(self, identity, schema=None):
        failed = {**guard_fixture.cancelled(int(identity)), 'conclusion': 'failure'}
        github, store = guard_fixture.GitHub([failed]), guard_fixture.Store()
        payload = {'external_effects_reconciled': True,
                   'evidence': 'Synthetic generic evidence cannot clear a pinned incident'}
        if schema is not None:
            payload['schema'] = schema
        store.rows['writer-resolution:' + identity] = record(
            'writer-resolution:' + identity, 'audit_events', payload, 'reconciled')
        return github, store

    def test_all_known_readback_failures_reject_generic_truthy_clearance(self):
        for identity in FAILURES:
            with self.subTest(identity=identity):
                github, store = self.fixture(identity)
                with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                    self.verify(store, github)
                self.assertEqual([row['id'] for row in store.writes], ['writer-failure:' + identity])

    def test_future_schema_never_falls_through_to_generic_clearance(self):
        for identity in (*FAILURES, '123'):
            with self.subTest(identity=identity):
                github, store = self.fixture(identity, SCHEMA.replace('/v1', '/v2'))
                with patch('cloud.readback_reconciliation.readback_resolution_matches') as native:
                    with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                        self.verify(store, github)
                    native.assert_not_called()

    def test_exact_schema_routes_to_native_validation_without_mutation(self):
        identity = FAILURES[0]
        github, store = self.fixture(identity, SCHEMA)
        with patch('cloud.readback_reconciliation.readback_resolution_matches', return_value=True) as native:
            self.verify(store, github)
        self.assertEqual(native.call_count, 1)
        args = native.call_args.args
        self.assertEqual(args[0], identity)
        self.assertEqual(args[1], store.get('writer-resolution:' + identity))
        self.assertTrue(callable(args[2]))
        self.assertEqual(args[3], 'narendranathe/apply')
        self.assertEqual(store.writes, [])


if __name__ == '__main__':
    unittest.main()
