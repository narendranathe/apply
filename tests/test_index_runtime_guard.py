"""The reviewed index incident cannot fall through to a generic clearance."""
import unittest
from unittest.mock import patch

from cloud.orchestrator import Blocked
from cloud.outreach import record
import test_runtime_guard as fixtures


class IndexWriterGuardTests(unittest.TestCase):
    verify = fixtures.CancelledWriterTests.verify

    def test_pinned_incident_rejects_missing_generic_and_unknown_schema(self):
        from cloud.index_reconciliation import FAILED, SCHEMA
        for schema in (None, 'manual', SCHEMA.replace('/v1', '/v2')):
            with self.subTest(schema=schema):
                github = fixtures.GitHub([{**fixtures.cancelled(int(FAILED)), 'conclusion': 'failure'}])
                store = fixtures.Store()
                if schema is not None:
                    store.rows['writer-resolution:' + FAILED] = record(
                        'writer-resolution:' + FAILED, 'audit_events',
                        {'schema': schema, 'external_effects_reconciled': True,
                         'evidence': 'Synthetic assertion is not reviewed proof'})
                with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                    self.verify(store, github)
                self.assertEqual([row['id'] for row in store.writes], ['writer-failure:' + FAILED])

    def test_matching_schema_must_pass_dedicated_fresh_evidence_validator(self):
        from cloud.index_reconciliation import FAILED, SCHEMA
        for valid in (False, True):
            with self.subTest(valid=valid):
                github = fixtures.GitHub([{**fixtures.cancelled(int(FAILED)), 'conclusion': 'failure'}])
                store = fixtures.Store()
                marker = record('writer-resolution:' + FAILED, 'audit_events',
                                {'schema': SCHEMA, 'external_effects_reconciled': True,
                                 'evidence': 'Synthetic validator routing fixture'})
                store.rows[marker['id']] = marker
                with patch('cloud.index_reconciliation.index_resolution_matches', return_value=valid) as check:
                    if valid:
                        self.verify(store, github)
                        self.assertEqual(store.writes, [])
                    else:
                        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                            self.verify(store, github)
                    check.assert_called_once()
                    self.assertEqual(check.call_args.args[0:2], (FAILED, marker))

    def test_index_schema_cannot_clear_an_unrelated_failed_run(self):
        from cloud.index_reconciliation import SCHEMA
        github, store = fixtures.GitHub([{**fixtures.cancelled(), 'conclusion': 'failure'}]), fixtures.Store()
        store.rows['writer-resolution:123'] = record('writer-resolution:123', 'audit_events',
            {'schema': SCHEMA, 'external_effects_reconciled': True, 'evidence': 'Synthetic'})
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(store, github)
        self.assertEqual([row['id'] for row in store.writes], ['writer-failure:123'])


if __name__ == '__main__':
    unittest.main()
