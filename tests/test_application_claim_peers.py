"""Fresh claim validation must not hide another matching identity behind self."""
from copy import deepcopy
import unittest

from cloud.applications import StateConflict, fold_applications
from cloud.records import content_hash, make_record
from cloud.shadow import ShadowStore
from tests import test_applications as base


class FreshPeerStore(ShadowStore):
    def __init__(self, status, reverse=False, attempt=False, mutate_current=False):
        super().__init__()
        self.peer_status, self.reverse, self.attempt = status, reverse, attempt
        self.mutate_current, self.injected = mutate_current, False

    def query(self, kind=None):
        rows = super().query(kind)
        if kind != 'applications': return rows
        eligible = [row for row in fold_applications(rows).values() if row['status'] == 'submission_eligible']
        if not eligible: return rows
        self.injected = True
        current = eligible[0]
        if self.mutate_current:
            changed = deepcopy(current)
            changed['payload']['unexpected_new_fact'] = True
            changed['hash'] = content_hash(changed['payload'])
            return [changed if row['id'] == current['id'] else row for row in rows]
        payload = {'application_id': 'synthetic-peer', 'revision': 1, 'company': 'Acme',
            'requisition_id': 'req-1', 'url': 'https://jobs.lever.co/acme/req-1'}
        if self.attempt: payload['attempt'] = {'idempotency_key': 'synthetic-unknown-attempt'}
        peer = make_record('applications', payload, 'synthetic-peer', self.peer_status)
        return [peer, *rows] if self.reverse else [*rows, peer]


class FreshClaimPeerTests(unittest.TestCase):
    def test_any_peer_or_changed_current_core_stops_before_provider_post(self):
        cases = [('submission_unknown', False, False), ('applied_verified', False, False),
            ('submitting', False, False), ('prepared', True, False),
            ('prepared', False, False), ('discovered', False, False), ('prepared', False, True)]
        for status, attempt, mutate in cases:
            for reverse in (False, True):
                with self.subTest(status=status, attempt=attempt, mutate=mutate, reverse=reverse):
                    fixture = base.ApplicationTests(); fixture.setUp()
                    try:
                        store = FreshPeerStore(status, reverse, attempt, mutate)
                        transport = base.ATSTransport()
                        with self.assertRaisesRegex(StateConflict, '^application_claim_changed$'):
                            fixture.run_fixture(store, transport)
                        self.assertTrue(store.injected)
                        self.assertFalse(any(method == 'POST' for method, _, _ in transport.calls))
                        self.assertFalse(any(row['status'] == 'submitting' for row in store.records.values() if row['kind'] == 'applications'))
                        self.assertFalse(any(row['payload'].get('attempt') for row in store.records.values()
                                             if row['kind'] == 'applications'))
                    finally:
                        fixture.tearDown()


if __name__ == '__main__': unittest.main()
