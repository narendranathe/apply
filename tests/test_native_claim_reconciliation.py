"""Synthetic authenticated packets and pure, fresh containment validation."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import cloud.native_claim_reconciliation as subject
from cloud.coordination_quarantine import make_quarantine
from cloud.notion import _rich_text
from cloud.records import canonical_json, content_hash, make_record
from cloud.startup_reconciliation import SnapshotState
from scripts.encrypted_backup import MAGIC, MANIFEST_SCHEMA
from tests.test_startup_reconciliation import entry, states

KEY = b's' * 32
PROFILE = 'https://www.linkedin.com/in/synthetic-contained-profile'
STARTED = '2026-10-21T21:12:00Z'


def body(value):
    return canonical_json(value).encode()


def encrypted(contents, key=KEY, *, extra_member=None):
    manifest = {'schema': MANIFEST_SCHEMA, 'files': [
        {'name': name, 'bytes': len(value), 'sha256': subject._sha(value)}
        for name, value in contents.items()]}
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w:gz') as target:
        members = [('MANIFEST.json', body(manifest)),
                   *[('files/' + name, value) for name, value in contents.items()]]
        if extra_member:
            members.append(extra_member)
        for name, value in members:
            info = tarfile.TarInfo(name)
            info.size = len(value)
            target.addfile(info, io.BytesIO(value))
    nonce = b'n' * 12  # Synthetic fixtures only, never used to create real evidence.
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, archive.getvalue(), MAGIC)


def fixture():
    _, _, source, _ = states()
    source['captured_at'] = '2026-10-21T18:27:02+00:00'
    props = {'Contact Key': {'type': 'rich_text', 'rich_text': _rich_text(PROFILE)},
        'Link': {'type': 'url', 'url': None},
        'Actor': {'select': {'name': 'Muse'}},
        'Delivery State': {'select': {'name': 'Sent verified'}}}
    task_names = ('Claim Token', 'Due', 'Intent ID', 'Kind', 'Lease Until', 'Next Action', 'Work Key', 'Worker')
    props.update({name: {'rich_text': _rich_text('')} for name in task_names})
    page = {'id': 'synthetic-native-page', 'object': 'page', 'properties': props,
        'parent': {'data_source_id': source['data_source_id']}, 'created_time': '2026-10-21T14:38:00Z'}
    logical = {'page': page, 'blocks': []}
    raw = {'page': {**page, 'last_edited_time': '2026-10-21T14:38:00Z'}, 'blocks': []}
    source['pages'].append(logical)
    incident = SnapshotState(source)
    later = deepcopy(source)
    later['captured_at'] = '2026-10-21T21:30:00Z'
    post_stop = SnapshotState(later)
    resource = {'Contact Key': props['Contact Key'], 'Link': props['Link'],
        'source_page_id': page['id'], 'source_parent': page['parent']}
    task = {name: props[name] for name in task_names}
    contents = {'source/native-claim-logical.json': body(logical),
        'source/native-claim-raw.json': body(raw), 'source/native-resource-fields.json': body(resource),
        'source/native-task-intent-fields.json': body(task)}
    provenance = {'claim_sha256': subject._sha(body(raw)), 'resource_sha256': subject._sha(body(resource)),
        'task_sha256': subject._sha(body(task))}
    quarantine = make_quarantine({'schema': 'apply-coordination-quarantine/v1', 'active': True,
        'classification': 'effect_unknown', 'identity': {'profile_urls': [PROFILE]},
        'provenance': provenance, 'observed_at': subject.STOP_OBSERVED_AT,
        'evidence_refs': ['synthetic-encrypted-evidence']})
    native_payload = {'schema': 'apply-unverified-native-delivery-claim-observation/v1',
        'claimed_contact_profile_url': PROFILE, 'canonical_contact_id': None,
        'claim_source_notion_page_id': page['id'], 'claimed_actor_label': 'Muse',
        'claimed_delivery_label': 'Sent verified', 'native_unknown_delivery': True,
        'requires_affected_contact_hold': True,
        'source_raw_claim_sha256': provenance['claim_sha256'],
        'source_resource_fields_sha256': provenance['resource_sha256'],
        'source_task_fields_sha256': provenance['task_sha256'],
        'source_logical_page_sha256': content_hash(logical),
        'source_snapshot_fingerprint': incident.fingerprint(),
        'source_snapshot_captured_at': source['captured_at'], 'source_page_created_at': page['created_time']}
    native_payload.update({name: False for name in ('activation_authorized', 'actual_sender_verified',
        'actual_recipient_verified', 'delivery_verified', 'thread_verified', 'message_content_verified',
        'history_complete', 'exact_user_confirmation_to_native_row_link_verified')})
    native = make_record('conversation_observations', native_payload,
                         'native-delivery-claim-observation:' + 'a' * 64, 'needs_reconciliation')
    attestation = {'schema': 'apply-user-controller-stop-attestation/v1',
        'observed_at': subject.STOP_OBSERVED_AT, 'activation_authorized': False,
        'affected_contact_hold_required': True, 'history_incomplete': True,
        'independent_stop_claim_clears_delivery_history': False,
        'writer_resolution_or_history_records_added': 0, 'failed_writer_ids': list(subject.FAILURES),
        'unverified': {name: True for name in ('complete_final_external_delta', 'definitive_delivery_evidence',
            'exact_stop_effective_time', 'message_content_hash', 'native_row_linkage', 'recipient', 'thread')},
        'native_observation': {'delivery_verified': False, 'native_unknown_delivery': True,
                               'logical_page_sha256': content_hash(logical)},
        'trusted_user_claims': {'morning_outreach_activity_occurred': True, 'muse_controller_stopped_now': True},
        'source': {'kind': 'direct_human_user_answer', 'question_id': 'synthetic-question',
                   'exact_answer': 'Synthetic stopped-controller statement',
                   'exact_answer_sha256': subject._sha(b'Synthetic stopped-controller statement')}}
    nested = {'attestation.json': body(attestation),
              **{'evidence/fixture-' + str(i) + '.json': body({'fixture': i}) for i in range(8)}}
    attestation_cipher = encrypted(nested)
    contents['source-evidence/trusted-user-stop-attestation.aesgcm'] = attestation_cipher
    stop_payload = {'schema': 'apply-user-confirmed-controller-stop-observation/v1', 'controller': 'Muse',
        'source_kind': attestation['source']['kind'], 'source_question_id': attestation['source']['question_id'],
        'exact_answer': attestation['source']['exact_answer'],
        'exact_answer_sha256': attestation['source']['exact_answer_sha256'],
        'source_attestation_cipher_sha256': subject._sha(attestation_cipher),
        'observed_at': subject.STOP_OBSERVED_AT, 'controller_stopped_user_confirmed': True,
        'morning_outreach_user_confirmed': True, 'future_fresh_post_stop_full_snapshot_required': True,
        'exact_stop_effective_at': None, 'activation_authorized': False, 'delivery_verified': False,
        'history_complete': False, 'native_row_linkage_verified': False}
    stopped = make_record('audit_events', stop_payload,
                          'user-controller-stop-observation:' + 'b' * 64, 'history_incomplete')
    records = [quarantine, native, stopped]
    for name, row in zip(subject.PROPOSAL_MEMBERS, records):
        contents[name] = body({'schema': 'apply-unapplied-central-reconciliation-proposal/v1',
            'proposal_only': True, 'apply_status': 'unapplied', 'activation_authorized': False,
            'requires_exact_source_and_scope_readback_before_central_apply': True,
            'requires_future_fresh_post_stop_full_snapshot': True,
            'requires_stopped_single_writer_boundary_review': True, 'proposed_record': row})
    manifest = {'schema': 'apply-unapplied-central-reconciliation-packet/v1',
        'proposal_only': True, 'apply_status': 'unapplied', 'activation_authorized': False,
        'record_count': 3, 'writer_resolution_records_in_packet': 0, 'canonical_contact_id_created': False,
        'claim_page_is_canonical_contact_page': False, 'existing_snapshot_is_post_stop': False,
        'future_fresh_post_stop_full_snapshot_required': True, 'history_incomplete': True,
        'native_unknown_delivery': True, 'affected_contact_hold_required': True,
        'failed_writer_fences_retained': list(subject.FAILURES), 'quarantine_identity_types': ['profile_urls'],
        'trusted_stop_observed_at': subject.STOP_OBSERVED_AT}
    contents['packet-manifest.json'] = body(manifest)
    contents['tests/scope-and-idempotency.json'] = body({'fixture_only': True})
    pins = {'INCIDENT_FINGERPRINT': incident.fingerprint(), 'INCIDENT_PAGES': len(incident.pages),
        'INCIDENT_RECORDS': len(incident.rows), 'NATIVE_LOGICAL_SHA256': content_hash(logical),
        'NATIVE_PAGE_ID_SHA256': subject._sha(page['id'].encode()),
        'ATTESTATION_NAME_SHA256': subject._sha(b'attestation.json'),
        'ATTESTATION_MEMBER_SHA256': {subject._sha(name.encode()): subject._sha(value) for name, value in nested.items()},
        '_COHORT_SPECS': {row['id']: content_hash(row) for row in records}}
    return incident, post_stop, contents, nested, records, pins


class NativeContainmentTests(unittest.TestCase):
    def setUp(self):
        self.incident, self.post_stop, self.contents, self.nested, self.records, self.pins = fixture()
        self.temporary = tempfile.TemporaryDirectory(prefix='apply-native-test-')
        self.addCleanup(self.temporary.cleanup)
        self.packet = Path(self.temporary.name) / 'synthetic-packet.aesgcm'

    def load(self, *, key=KEY, started=STARTED, repin=False):
        ciphertext = encrypted(self.contents)
        self.packet.write_bytes(ciphertext)
        pins = {**self.pins, 'PACKET_PATH': self.packet, 'PACKET_SHA256': subject._sha(ciphertext),
                'MEMBER_SHA256': {name: subject._sha(value) for name, value in self.contents.items()}}
        if repin:
            pins['_COHORT_SPECS'] = {row['id']: content_hash(row) for row in self.records}
        with patch.multiple(subject, **pins), \
             patch('cloud.notion.NotionStore.put', side_effect=AssertionError('no operational writes')), \
             patch('cloud.submission.HTTPTransport.request', side_effect=AssertionError('no provider requests')):
            return subject.load_plan(self.incident, self.post_stop, key, post_stop_started_at=started)

    def change_record(self, index, field, value):
        self.records[index]['payload'][field] = value
        self.records[index]['hash'] = content_hash(self.records[index]['payload'])
        name = subject.PROPOSAL_MEMBERS[index]
        wrapper = json.loads(self.contents[name])
        wrapper['proposed_record'] = self.records[index]
        self.contents[name] = body(wrapper)

    def test_real_archive_boundary_yields_immutable_private_unapplied_plan(self):
        plan = self.load()
        self.assertEqual(plan.records, tuple(self.records))
        self.assertEqual(plan.native_page_id, 'synthetic-native-page')
        self.assertEqual(repr(plan), 'Plan()')
        self.assertFalse(plan.proof['activation_authorized'])
        self.assertFalse(plan.proof['external_effects_reconciled'])
        self.assertFalse(plan.proof['native_delivery_verified'])
        self.assertTrue(plan.proof['full_post_stop_snapshot_verified'])
        self.assertNotIn(PROFILE, canonical_json(plan.proof))
        self.assertNotIn(plan.native_page_id, canonical_json(plan.proof))
        copy = plan.records
        copy[0]['payload']['active'] = False
        self.assertTrue(plan.records[0]['payload']['active'])
        proof = plan.proof; proof['activation_authorized'] = True
        self.assertFalse(plan.proof['activation_authorized'])
        with self.assertRaises(FrozenInstanceError): plan.native_page_id = 'changed'

    def test_packet_wrong_key_cipher_member_and_unexpected_member_reject(self):
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'authentication_failed'):
            self.load(key=b'x' * 32)
        ciphertext = encrypted(self.contents)
        self.packet.write_bytes(ciphertext + b'changed')
        with patch.multiple(subject, **{**self.pins, 'PACKET_PATH': self.packet, 'PACKET_SHA256': subject._sha(ciphertext)}):
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'ciphertext_changed'):
                subject.load_plan(self.incident, self.post_stop, KEY, post_stop_started_at=STARTED)
        pins = {name: subject._sha(value) for name, value in self.contents.items()}
        changed = dict(self.contents, **{'tests/scope-and-idempotency.json': b'changed'})
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'member_hash_mismatch'):
            subject._archive(encrypted(changed), KEY, pins)
        for member in (('files/extra', b'changed'), ('files/../unsafe', b'changed'),
                       ('MANIFEST.json', body({}))):
            with self.subTest(member=member[0]), self.assertRaises(subject.ReconciliationBlocked):
                subject._archive(encrypted(self.contents, extra_member=member), KEY, pins)

    def test_nested_attestation_tampering_rejects_even_with_repacked_outer_archive(self):
        nested = dict(self.nested, **{'evidence/fixture-0.json': body({'changed': True})})
        self.contents['source-evidence/trusted-user-stop-attestation.aesgcm'] = encrypted(nested)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'member_hash_mismatch'):
            self.load()

    def test_pre_stop_naive_missing_or_after_capture_operation_time_rejects(self):
        for started in ('2026-10-21T20:13:08Z', subject.STOP_OBSERVED_AT,
                        '2026-10-21T21:31:00Z', '2026-10-21T21:12:00', None, 'bad'):
            with self.subTest(started=started), self.assertRaisesRegex(subject.ReconciliationBlocked, 'post_stop_snapshot_required'):
                self.load(started=started)

    def test_unexplained_native_or_managed_post_stop_change_rejects(self):
        for mode in ('native', 'managed', 'extra', 'missing', 'partial'):
            source = deepcopy(self.post_stop.snapshot)
            if mode == 'native': source['pages'][-1]['page']['properties']['Actor'] = {'select': {'name': 'changed'}}
            if mode == 'managed': source['pages'][0]['page']['icon'] = {'type': 'emoji', 'emoji': 'x'}
            if mode == 'extra': source['pages'].append({'page': {'id': 'extra', 'properties': {}}, 'blocks': []})
            if mode == 'missing': source['pages'].pop()
            if mode == 'partial': source['complete'] = False
            with self.subTest(mode=mode), self.assertRaises(subject.ReconciliationBlocked):
                self.post_stop = SnapshotState(source)
                self.load()
            self.post_stop = fixture()[1]

    def test_proposals_cannot_upgrade_delivery_linkage_or_create_a_contact(self):
        for index, field, value in ((1, 'delivery_verified', True), (1, 'canonical_contact_id', 'invented-contact'),
                (1, 'actual_sender_verified', True), (1, 'exact_user_confirmation_to_native_row_link_verified', True),
                (2, 'native_row_linkage_verified', True), (2, 'history_complete', True),
                (2, 'exact_stop_effective_at', subject.STOP_OBSERVED_AT)):
            with self.subTest(index=index, field=field):
                self.setUp()
                self.change_record(index, field, value)
                with self.assertRaisesRegex(subject.ReconciliationBlocked, 'honesty_invalid'):
                    self.load(repin=True)

    def test_company_contact_page_duplicate_and_other_profile_scope_reject(self):
        for scope in ({'company_ids': ['synthetic-company']}, {'source_notion_ids': ['a' * 32]},
                      {'contact_ids': ['synthetic-native-page']}, {'profile_urls': [PROFILE, PROFILE]},
                      {'profile_urls': ['https://www.linkedin.com/in/synthetic-other-profile']}):
            with self.subTest(scope=scope):
                self.setUp(); self.change_record(0, 'identity', scope)
                with self.assertRaises(subject.ReconciliationBlocked): self.load(repin=True)

    def test_existing_canonical_contact_match_blocks_instead_of_inventing_scope(self):
        source = deepcopy(self.incident.snapshot)
        source['pages'].append(entry(make_record('contacts', {'profile_url': PROFILE}, 'synthetic-exact-contact')))
        self.incident = SnapshotState(source)
        later = deepcopy(source); later['captured_at'] = self.post_stop.snapshot['captured_at']
        self.post_stop = SnapshotState(later)
        self.pins.update(INCIDENT_FINGERPRINT=self.incident.fingerprint(), INCIDENT_PAGES=len(self.incident.pages),
                         INCIDENT_RECORDS=len(self.incident.rows))
        self.change_record(1, 'source_snapshot_fingerprint', self.incident.fingerprint())
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'contact_scope_mapping_changed'):
            self.load(repin=True)

    def test_exact_native_source_and_resource_binding_cannot_be_changed(self):
        original_contents = dict(self.contents)
        logical = json.loads(self.contents['source/native-claim-logical.json'])
        logical['page']['properties']['Actor'] = {'select': {'name': 'changed'}}
        self.contents['source/native-claim-logical.json'] = body(logical)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'exact_source_page_changed'): self.load()
        self.contents = original_contents
        resource = json.loads(self.contents['source/native-resource-fields.json'])
        resource['source_page_id'] = 'different-source-page'
        self.contents['source/native-resource-fields.json'] = body(resource)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'source_fields_changed'): self.load()

    def test_three_fresh_cohort_rows_required_without_decryption_key(self):
        with patch.multiple(subject, **self.pins):
            specs = subject.cohort_specs()
            rows = {row['id']: deepcopy(row) for row in self.records}
            calls = []
            def read(identity):
                calls.append(identity)
                return rows.get(identity)
            with patch.object(subject, 'load_plan', side_effect=AssertionError('must not decrypt')):
                self.assertTrue(subject.cohort_matches(read))
                self.assertEqual(calls, list(specs))
                for identity in specs:
                    saved = rows.pop(identity)
                    self.assertFalse(subject.cohort_matches(read))
                    rows[identity] = saved
                    altered = deepcopy(saved); altered['status'] = 'changed'
                    rows[identity] = altered
                    self.assertFalse(subject.cohort_matches(read))
                    rows[identity] = saved
                rows[self.records[0]['id']]['payload']['active'] = False
                self.assertFalse(subject.cohort_matches(read))
                self.assertFalse(subject.cohort_matches(lambda identity: (_ for _ in ()).throw(RuntimeError('synthetic private text'))))
            with self.assertRaises(TypeError): specs['changed'] = 'changed'


if __name__ == '__main__':
    unittest.main()
