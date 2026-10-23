"""Fixed uncertain observation identities cannot be recreated or aliased."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from cloud.notion import AmbiguousWriteError, NotionStore
from cloud.outreach import record
from cloud.storage_reservations import (NATIVE_SOURCE, RESERVED_RECORD_IDS, STOP_ATTESTATION)


class StorageReservationTests(unittest.TestCase):
    def store(self):
        transport = Mock(side_effect=AssertionError('No network may precede reservation rejection'))
        return NotionStore(token='synthetic-private-token', transport=transport), transport

    def reject(self, row, present=True):
        store, transport = self.store()
        store.preflight, store.get = Mock(), Mock(return_value=deepcopy(row) if present else None)
        with self.assertRaises(AmbiguousWriteError) as caught:
            store.put(row)
        store.preflight.assert_not_called()
        store.get.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(caught.exception.diagnostics(), {
            'http_status': None, 'error_code': 'unknown', 'operation': 'verify_create_readback'})
        self.assertNotIn('synthetic-private-token', str(caught.exception))

    def test_both_reserved_top_level_ids_never_reach_lookup_or_transport(self):
        for identity in RESERVED_RECORD_IDS:
            for present in (False, True):
                with self.subTest(identity=identity, present=present):
                    self.reject(record(identity, 'audit_events', {'synthetic': True}, 'observed'), present)

    def test_same_source_under_an_alternate_id_is_not_a_replay_escape(self):
        originals = (
            {'schema': 'apply-unverified-native-delivery-claim-observation/v1',
             'source_logical_page_sha256': NATIVE_SOURCE},
            {'schema': 'apply-user-confirmed-controller-stop-observation/v1',
             'source_attestation_cipher_sha256': STOP_ATTESTATION},
        )
        for payload in originals:
            for kind, status in (('audit_events', 'observed'), ('other', 'verified')):
                with self.subTest(schema=payload['schema'], kind=kind, status=status):
                    self.reject(record('alternate-id', kind, {**payload, 'observed_at': 'changed'}, status))

    def test_nested_original_ids_and_sources_are_permitted_as_new_evidence_custody(self):
        payload = {'schema': 'synthetic-correction-bundle', 'embedded_original_observation_records': [
            {'id': sorted(RESERVED_RECORD_IDS)[0], 'payload': {
                'schema': 'apply-unverified-native-delivery-claim-observation/v1',
                'source_logical_page_sha256': NATIVE_SOURCE}},
            {'id': sorted(RESERVED_RECORD_IDS)[1], 'payload': {
                'schema': 'apply-user-confirmed-controller-stop-observation/v1',
                'source_attestation_cipher_sha256': STOP_ATTESTATION}}]}
        row = record('new-evidence-bundle', 'audit_events', payload, 'storage_unknown_contained')
        self.normal_verified_create(row)

    def test_unrelated_normal_create_keeps_existing_post_write_readback(self):
        self.normal_verified_create(record('unrelated', 'audit_events', {'synthetic': True}, 'observed'))

    def normal_verified_create(self, row):
        store, transport = self.store()
        store.preflight = Mock()
        store.get = Mock(side_effect=[None, deepcopy(row)])
        store._request = Mock(return_value={})
        self.assertEqual(store.put(row), row)
        store.preflight.assert_called_once()
        self.assertEqual(store.get.call_count, 2)
        store._request.assert_called_once()
        self.assertEqual(store._request.call_args.args[:2], ('POST', '/pages'))
        self.assertTrue(store._request.call_args.kwargs['write'])
        transport.assert_not_called()
