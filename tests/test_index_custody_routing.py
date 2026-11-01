"""Custody incidents and separate maintenance failures cannot inherit clearance."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from cloud.index_custody_reconciliation import FAILURES, SCHEMA
from cloud.orchestrator import Blocked
from cloud.outreach import record
from cloud.runtime_guard import verify_previous_writers
from tests.test_runtime_guard import ENV, GitHub, Store, cancelled


class CustodyHistory(GitHub):
    def __init__(self, cloud=(), maintenance=(), *, unavailable=False):
        super().__init__(list(cloud))
        self.maintenance, self.unavailable = list(maintenance), unavailable

    def open(self, request, timeout):
        if urlsplit(request.full_url).path.endswith('/index-custody-reconciliation.yml/runs'):
            self.calls.append(request.full_url)
            self.assert_get(request)
            if self.unavailable:
                raise RuntimeError('PRIVATE SOURCE OR TOKEN MUST NOT APPEAR')
            return io.StringIO(json.dumps({'total_count': len(self.maintenance),
                                          'workflow_runs': self.maintenance}))
        return super().open(request, timeout)


class CustodyRoutingTests(unittest.TestCase):
    def verify(self, github, store):
        with patch.dict('os.environ', ENV, clear=True), \
             patch('cloud.runtime_guard.urlopen', side_effect=github.open):
            verify_previous_writers(store)

    def test_empty_maintenance_history_and_no_failed_cloud_run_need_no_write(self):
        github, store = CustodyHistory(), Store()
        self.verify(github, store)
        self.assertEqual(store.writes, [])

    def test_failed_or_unstarted_maintenance_stays_fenced_without_cloud_history(self):
        for status, conclusion in (('completed', 'failure'), ('queued', None),
                                   ('completed', 'cancelled'), ('completed', 'success')):
            with self.subTest(status=status, conclusion=conclusion):
                github = CustodyHistory(maintenance=[{'id': 888, 'status': status,
                                                     'conclusion': conclusion}])
                store = Store()
                with self.assertRaisesRegex(Blocked, 'custody_maintenance_requires_reconciliation'):
                    self.verify(github, store)
                self.assertEqual(store.writes, [])

    def test_unavailable_maintenance_history_never_assumes_empty(self):
        github, store = CustodyHistory(unavailable=True), Store()
        with self.assertRaisesRegex(Blocked, 'custody_maintenance_requires_reconciliation') as caught:
            self.verify(github, store)
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(store.writes, [])

    def test_known_incident_foreign_truthy_marker_cannot_reach_generic_clearance(self):
        for identity in FAILURES:
            github = CustodyHistory([{**cancelled(int(identity)), 'conclusion': 'failure'}])
            store = Store()
            marker = record('writer-resolution:' + identity, 'audit_events',
                {'schema': 'manual-clearance/v1', 'external_effects_reconciled': True,
                 'evidence': 'An assertion cannot clear this pinned incident'})
            store.rows[marker['id']] = marker
            before = deepcopy(marker)
            with self.subTest(identity=identity), \
                 self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                self.verify(github, store)
            self.assertEqual(store.rows[marker['id']], before)
            self.assertEqual([r['id'] for r in store.writes], ['writer-failure:' + identity])

    def test_typed_route_requires_dedicated_validator_and_never_mutates_marker(self):
        for valid in (False, True):
            identity = FAILURES[0]
            github = CustodyHistory([{**cancelled(int(identity)), 'conclusion': 'failure'}])
            store = Store()
            marker = record('writer-resolution:' + identity, 'audit_events',
                {'schema': SCHEMA, 'external_effects_reconciled': True,
                 'evidence': 'Synthetic routing fixture only'})
            store.rows[marker['id']] = marker
            with patch('cloud.index_custody_receipt.custody_resolution_matches',
                       return_value=valid) as matcher:
                if valid:
                    self.verify(github, store)
                    self.assertEqual(store.writes, [])
                else:
                    with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
                        self.verify(github, store)
                matcher.assert_called_once()
                self.assertEqual(matcher.call_args.args[:2], (identity, marker))
            self.assertEqual(store.rows[marker['id']], marker)

    def test_custody_prefix_never_clears_an_unrelated_failure(self):
        github = CustodyHistory([{**cancelled(123), 'conclusion': 'failure'}])
        store = Store()
        store.rows['writer-resolution:123'] = record('writer-resolution:123', 'audit_events',
            {'schema': SCHEMA, 'external_effects_reconciled': True, 'evidence': 'Synthetic'})
        with self.assertRaisesRegex(Blocked, 'previous_failed_writer_requires_reconciliation'):
            self.verify(github, store)
        self.assertEqual([r['id'] for r in store.writes], ['writer-failure:123'])
