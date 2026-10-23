"""Typed feed submission claims remain independent immutable suppression."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock

from cloud.applications import StateConflict, fold_applications, transition
from cloud.notion import AmbiguousWriteError
from cloud.records import content_hash, make_record
from cloud.shadow import ShadowStore
from tests import test_applications as base


class FeedClaimTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.ApplicationTests(); self.fixture.setUp()

    def tearDown(self): self.fixture.tearDown()

    def seeded(self, status='prepared', *, receipt=False):
        store = ShadowStore()
        payload = {'application_id': 'existing-application', 'revision': 1, 'company': 'Acme',
            'requisition_id': 'req-1', 'url': 'https://jobs.lever.co/acme/req-1'}
        if receipt: payload['receipt'] = {'receipt_id': 'synthetic-existing-receipt'}
        store.put(make_record('applications', payload, 'existing-record', status))
        return store

    def feed_claim(self, reported, reverse=False):
        feed = deepcopy(self.fixture.feed)
        claim = {**feed['jobs'][0], 'application_status': reported,
            'application_applied_at': '2026-10-20T12:00:00+00:00', 'is_active': False, 'relevance_score': 0}
        feed['jobs'] = [*feed['jobs'], claim] if reverse else [claim, *feed['jobs']]
        return feed

    def test_existing_prepared_never_hides_reported_submission_claim_in_either_feed_order(self):
        for reported in ('applied', 'applied_verified', 'submitting', 'submission_unknown', 'interview', 'rejected', 'withdrawn'):
            for reverse in (False, True):
                with self.subTest(reported=reported, reverse=reverse):
                    store = self.seeded(); original = deepcopy(store.records)
                    transport, prepare = base.ATSTransport(), Mock(return_value={'ready': False})
                    result = self.fixture.run_fixture(store, transport, feed=self.feed_claim(reported, reverse), prepare=prepare)
                    prepare.assert_not_called()
                    self.assertEqual(transport.calls, [])
                    self.assertEqual(result['duplicates'], 1)
                    self.assertEqual(result['submission_attempts'], 0)
                    self.assertTrue(all(store.records[identity] == row for identity, row in original.items()))
                    claims = [row for row in store.query('applications') if row['id'] not in original]
                    self.assertEqual(len(claims), 1)
                    self.assertEqual(claims[0]['status'], 'submission_unknown')
                    self.assertEqual(claims[0]['payload']['historical_reported_status'], reported)
                    self.assertNotEqual(claims[0]['payload']['application_id'], 'existing-application')

    def test_identical_claim_across_export_timestamps_has_no_additional_append(self):
        store = self.seeded(); feed = self.feed_claim('applied')
        self.fixture.run_fixture(store, feed=feed)
        original = deepcopy(store.records)
        feed['exported_at'] = (datetime.fromisoformat(feed['exported_at']) + timedelta(seconds=30)).isoformat()
        feed['jobs'][-1]['age_days'] = 99
        self.fixture.run_fixture(store, feed=feed)
        self.assertEqual(store.records, original)
        self.assertEqual(len(fold_applications(store.query('applications'))), 2)

    def test_verified_receipt_record_is_preserved_without_downgrade(self):
        store = self.seeded('applied_verified', receipt=True); original = deepcopy(store.records)
        transport = base.ATSTransport()
        self.fixture.run_fixture(store, transport, feed=self.feed_claim('applied'))
        self.assertTrue(all(store.records[identity] == row for identity, row in original.items()))
        self.assertEqual(store.records['existing-record']['status'], 'applied_verified')
        self.assertEqual(len(store.query('applications')), 2)
        self.assertEqual(transport.calls, [])

    def test_claim_write_or_readback_ambiguity_stops_without_retry_or_provider_call(self):
        for mode in ('write', 'absent', 'changed'):
            with self.subTest(mode=mode):
                class UncertainStore(ShadowStore):
                    writes = 0
                    def put(self, value):
                        self.writes += 1
                        result = super().put(value)
                        if mode == 'write': raise AmbiguousWriteError('synthetic unknown write')
                        return result
                    def get(self, identity):
                        value = super().get(identity)
                        if self.writes and value:
                            if mode == 'absent': return None
                            if mode == 'changed': return {**value, 'status': 'prepared'}
                        return value
                store, transport = UncertainStore(), base.ATSTransport()
                prepare = Mock(return_value={'ready': False})
                expected = AmbiguousWriteError if mode == 'write' else StateConflict
                with self.assertRaises(expected):
                    self.fixture.run_fixture(store, transport, feed=self.feed_claim('applied'), prepare=prepare)
                self.assertEqual(store.writes, 1)
                self.assertEqual(transport.calls, [])
                prepare.assert_not_called()

    def test_explicit_typed_application_event_cannot_be_hidden_by_prepared_work(self):
        store = self.seeded()
        store.put(make_record('outreach_events', {'kind': 'application', 'company': 'Acme',
            'requisition_id': 'req-1', 'job_url': 'https://jobs.lever.co/acme/req-1',
            'occurred_at': '2026-10-20T12:00:00+00:00'}, 'synthetic-typed-application-event'))
        original = deepcopy(store.records)
        transport, prepare = base.ATSTransport(), Mock(return_value={'ready': False})
        result = self.fixture.run_fixture(store, transport, prepare=prepare)
        prepare.assert_not_called()
        self.assertEqual(transport.calls, [])
        self.assertEqual(result['duplicates'], 1)
        self.assertTrue(all(store.records[identity] == row for identity, row in original.items()))
        unknown = [row for row in store.query('applications') if row['id'] not in original]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]['status'], 'submission_unknown')
        self.assertEqual(unknown[0]['payload']['source_event_id'], 'synthetic-typed-application-event')
        saved = deepcopy(store.records)
        self.fixture.run_fixture(store, transport, prepare=prepare)
        self.assertEqual(store.records, saved)

    def reconciled_claim(self, typed, status):
        store = self.seeded()
        feed = self.fixture.feed if typed else self.feed_claim('applied')
        if typed:
            store.put(make_record('outreach_events', {'kind': 'application', 'company': 'Acme',
                'requisition_id': 'req-1', 'job_url': 'https://jobs.lever.co/acme/req-1'}, 'typed-resolved-event'))
        self.fixture.run_fixture(store, feed=feed)
        claim = next(row for row in store.query('applications') if row['status'] == 'submission_unknown')
        updates = {'receipt': {'receipt_id': 'synthetic-verified-receipt'}} if status == 'applied_verified' else {}
        transition(store, claim['payload']['application_id'], status, updates, claim)
        return store, feed, claim

    def test_source_claim_remains_idempotent_after_valid_later_resolution_transition(self):
        for typed in (False, True):
            for status in ('closed', 'withdrawn', 'applied_verified'):
                with self.subTest(typed=typed, status=status):
                    store, feed, claim = self.reconciled_claim(typed, status)
                    original = deepcopy(store.records)
                    transport, prepare = base.ATSTransport(), Mock(return_value={'ready': False})
                    self.fixture.run_fixture(store, transport, feed=feed, prepare=prepare)
                    self.assertEqual(store.records, original)
                    self.assertEqual(transport.calls, [])
                    prepare.assert_not_called()
                    latest = fold_applications(store.query('applications'))[claim['payload']['application_id']]
                    self.assertEqual(latest['status'], status)

    def test_changed_original_claim_core_still_stops_after_later_resolution(self):
        for typed in (False, True):
            with self.subTest(typed=typed):
                store, feed, claim = self.reconciled_claim(typed, 'closed')
                corrupted = deepcopy(claim)
                corrupted['payload']['reason'] = 'synthetic-altered-original-source'
                corrupted['hash'] = content_hash(corrupted['payload'])
                store.records[claim['id']] = corrupted
                original = deepcopy(store.records)
                transport, prepare = base.ATSTransport(), Mock(return_value={'ready': False})
                with self.assertRaisesRegex(StateConflict, '^historical_application_readback_failed$'):
                    self.fixture.run_fixture(store, transport, feed=feed, prepare=prepare)
                self.assertEqual(store.records, original)
                self.assertEqual(transport.calls, [])
                prepare.assert_not_called()


if __name__ == '__main__': unittest.main()
