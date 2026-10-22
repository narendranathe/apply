"""Pure, exactly pinned containment of one unverified native outreach claim.

This module reads only its encrypted evidence packet. It performs no network,
Notion mutation, delivery verification, writer clearance, or activation. Private
record values remain in memory; public diagnostics contain fixed labels only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import MappingProxyType

from .coordination_quarantine import validate_quarantine
from .linkedin_ingestion import _contact_urls, profile_url
from .notion import _text
from .records import canonical_json, content_hash, validate_record
from .startup_reconciliation import ReconciliationBlocked, SnapshotState, _time, require, source_metadata
from scripts.encrypted_backup import MAGIC, MANIFEST_SCHEMA, safe_name

PACKET_PATH = Path(__file__).resolve().parents[1] / 'recovery-evidence/2026-10-21-native-claim.aesgcm'
INCIDENT_FINGERPRINT = '51ee7332471f1fdb64abc49bf78b944bd7e43ca49e6dbae7776abcf1b8d946d0'
INCIDENT_PAGES = 2897
INCIDENT_RECORDS = 2650
NATIVE_LOGICAL_SHA256 = '593cab69f7747625fa16ce2899284336a81668a4c8f95e6640990bf018e96413'
NATIVE_PAGE_ID_SHA256 = 'bcef628215020531eb441fd739beaa66bb77b86f1b322a11d3b175b459273146'
STOP_OBSERVED_AT = '2026-10-21T20:13:09Z'
ATTESTATION_NAME_SHA256 = '01793a092b7f7191a295ae5f2e356ff62e6ebd1fa0ef8568703222fc1dd98c18'
FAILURES = ('39608197676', '39629503187', '39631067173')
PROPOSAL_MEMBERS = ('proposal/quarantine.json', 'proposal/native-delivery-claim-observation.json',
                    'proposal/user-controller-stop-observation.json')
MAX_ARCHIVE_BYTES = 32_000_000
MAX_MEMBER_BYTES = 32_000_000

PACKET_SHA256 = '5cd3d290ce0275653b18c917d3a130383e42ccd7904450c54fbf08386d7d9908'

MEMBER_SHA256 = {'packet-manifest.json': '93120092debeee0a9e5e81463edb5d21b109738408ea80e71c0ebdad7359cfa8',
 'proposal/native-delivery-claim-observation.json': '146d5d5524777a8f641fdae8c17ba9fce4e56cd30ae1c8e80eb13f781c941593',
 'proposal/quarantine.json': '8e9dbe37e4dc60f83e0a45fb6320a558b5aafba79aa531b941159cde20603086',
 'proposal/user-controller-stop-observation.json': 'ae2b09a45390292619d7a2dd4619940b28d91491e951323c980c0952f9bff855',
 'source-evidence/trusted-user-stop-attestation.aesgcm': '9a702deaad17836d4d5c574d6f9bf7cd9682b0a63f0f305fb97b8ee374a3c1b7',
 'source/native-claim-logical.json': '593cab69f7747625fa16ce2899284336a81668a4c8f95e6640990bf018e96413',
 'source/native-claim-raw.json': '2b4c8427ccfc9482a7508dac42b968b3eeeb165de89aed2659f92de34814d876',
 'source/native-resource-fields.json': 'aa35291d2e74ccab3f40c466f1b5ff31a4bd9475b2769889b1a553fb65ef153e',
 'source/native-task-intent-fields.json': '61893c722eba368d278e5253be6bb8bbb62e997f4019c4e2222323ba1562045f',
 'tests/scope-and-idempotency.json': '2b7359aa036ec3c91bc81be6fa93613ff204d481d5dad149432fa91a779fab20'}

ATTESTATION_MEMBER_SHA256 = {'01793a092b7f7191a295ae5f2e356ff62e6ebd1fa0ef8568703222fc1dd98c18': '37ff9435f801774ebb8d45c23ee9bbd6d003e1af05c570d60df9853885d7a131',
 '314b98e70e57d4b35ea5a7edf84faa595afdc5cdb06b4767b3f1e1a0df816dd2': 'f0e20088f440ab109da9ba580ec5df7bac39e432f123e4694aaace830281a9f4',
 '2848d54c2143a6cdcca42a8155cfde3b28aa60c325642bd9eff17f393d570495': 'c62ea1d0dc890426fb383defe45e59b0bbc7a24da96d288daf274e57f0741ea7',
 '1351f5619e70cb01e3897a380d500a3126dab974758205ce9dafecc6ec4c848b': 'ac18afc576671255be8f56feecfa76804e3483ddabfb583f4c442cacdfe89763',
 '55ce1424e93bec37760709f27cc1cdd1d72450cdc1ed596f1efd9df956961dd2': '3691a777eda6b027cb5e7956cc6bee5d4967b0bc1094f1a59381621030910d8f',
 'ee97667685ddccaaf3acfd5f2494e1938aa42bf0896e35789196ae95bef816c5': '3360602c85aea3bcc25468f9e3d26411545caf126626b95fe065b6dc7f6e759d',
 '98e13a9e990494b710520f80116bac534b3f07adcbdea41a83a1d5548e2d745b': '442d6172a4ec60c8b446567386ea76570f86e2f04635be1ed4f29499114712c5',
 '5e9c0b135f56ca2a3a5c6dba498897a700c80bedcc55a921695c4cf1e88b7b6c': '752eb3d5bcf9ea11e84e7687ef00e45b3699bcdbed6f42789381aa4ec9bdd3d9',
 'f09c42e5f47ea4782dcebed503490249c050d197e1f4d8db0db5babb48bd5550': '21f2ec3e55416ac3acfe07d6aa0c04a2e369d9ede6773c58c03d8572da3cd512'}

_COHORT_SPECS = {'coordination-quarantine:2b4c8427ccfc9482a7508dac42b968b3eeeb165de89aed2659f92de34814d876': '8b9d595b38dfcc28c00add003f12ce75f12058e25b3dccdc7bb988be817fa606',
 'native-delivery-claim-observation:13eacd409b8c6dca076c4b1f62d9db1bf015b7759cb02360a7f5421fd8896208': 'fa85910a0c7c8eb1d5a1c94e190f531848b167db331b67354e78a1965bc99876',
 'user-controller-stop-observation:963a71822d95d8712a13e9ccec8522e2fe6046890c122eae07f94497fe0d9c6b': '14bb53f9a4ee01f8c45702d03e23cfd4b6a5f80b6c8269417eb97b370ffcb344'}


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Plan:
    """Immutable private plan; accessors return independent canonical copies."""
    native_page_id: str = field(repr=False)
    _records_json: str = field(repr=False)
    _proof_json: str = field(repr=False)

    @property
    def records(self):
        return tuple(json.loads(self._records_json))

    @property
    def proof(self):
        return json.loads(self._proof_json)


def cohort_specs():
    """Safe fixed IDs and whole-CORE hashes; no decryption or private aliases."""
    return MappingProxyType(dict(_COHORT_SPECS))


def _archive(ciphertext, key, expected, *, hashed_names=False):
    """Authenticate both nested archives entirely in memory before decoding."""
    require(isinstance(key, bytes) and len(key) == 32
            and isinstance(ciphertext, bytes) and len(ciphertext) <= MAX_ARCHIVE_BYTES
            and ciphertext.startswith(MAGIC) and len(ciphertext) > len(MAGIC) + 28,
            'native_packet_authentication_failed')
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        offset = len(MAGIC)
        plaintext = AESGCM(key).decrypt(ciphertext[offset:offset + 12], ciphertext[offset + 12:], MAGIC)
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode='r:gz') as archive:
            members = archive.getmembers()
            require(len(members) == len(expected) + 1
                    and len({member.name for member in members}) == len(members)
                    and all(member.isfile() and safe_name(member.name)
                            and 0 <= member.size <= MAX_MEMBER_BYTES for member in members),
                    'native_packet_members_invalid')
            manifest = json.loads(archive.extractfile('MANIFEST.json').read())
            require(isinstance(manifest, dict) and set(manifest) == {'schema', 'files'}
                    and manifest['schema'] == MANIFEST_SCHEMA
                    and isinstance(manifest['files'], list) and len(manifest['files']) == len(expected),
                    'native_packet_manifest_invalid')
            contents, named = {}, set()
            for item in manifest['files']:
                require(isinstance(item, dict) and set(item) == {'name', 'bytes', 'sha256'}
                        and isinstance(item['name'], str) and safe_name(item['name'])
                        and type(item['bytes']) is int and 0 <= item['bytes'] <= MAX_MEMBER_BYTES,
                        'native_packet_manifest_invalid')
                name = item['name']
                identity = _sha(name.encode()) if hashed_names else name
                require(identity in expected and identity not in contents,
                        'native_packet_members_invalid')
                body = archive.extractfile('files/' + name).read()
                require(len(body) == item['bytes'] and _sha(body) == item['sha256'] == expected[identity],
                        'native_packet_member_hash_mismatch')
                named.add('files/' + name)
                contents[identity] = body
            require(named | {'MANIFEST.json'} == {member.name for member in members},
                    'native_packet_members_invalid')
            return contents
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('native_packet_authentication_failed') from None


def _records_valid(records):
    """Pure semantic checks supplement the immutable whole-CORE pins."""
    try:
        require(len(records) == 3 and len(_COHORT_SPECS) == 3, 'native_cohort_invalid')
        canonical = tuple(validate_record(row) for row in records)
        require(tuple(row['id'] for row in canonical) == tuple(_COHORT_SPECS)
                and all(content_hash(row) == _COHORT_SPECS[row['id']] for row in canonical),
                'native_cohort_core_changed')
        quarantine, native, stop = canonical
        validate_quarantine(quarantine)
        scope = quarantine['payload']['identity']
        require(quarantine['payload']['classification'] == 'effect_unknown'
                and quarantine['payload']['active'] is True and set(scope) == {'profile_urls'}
                and len(scope['profile_urls']) == 1, 'native_quarantine_scope_invalid')
        profile = scope['profile_urls'][0]
        require(profile_url(profile) == profile, 'native_quarantine_scope_invalid')
        p = native['payload']
        require(native['kind'] == 'conversation_observations' and native['status'] == 'needs_reconciliation'
                and p['schema'] == 'apply-unverified-native-delivery-claim-observation/v1'
                and p['claimed_contact_profile_url'] == profile and p['canonical_contact_id'] is None
                and p['native_unknown_delivery'] is True and p['requires_affected_contact_hold'] is True
                and all(p[key] is False for key in ('activation_authorized', 'actual_sender_verified',
                    'actual_recipient_verified', 'delivery_verified', 'thread_verified', 'message_content_verified',
                    'history_complete', 'exact_user_confirmation_to_native_row_link_verified')),
                'native_claim_honesty_invalid')
        p = stop['payload']
        require(stop['kind'] == 'audit_events' and stop['status'] == 'history_incomplete'
                and p['schema'] == 'apply-user-confirmed-controller-stop-observation/v1'
                and p['controller'] == 'Muse' and p['source_kind'] == 'direct_human_user_answer'
                and p['controller_stopped_user_confirmed'] is True
                and p['morning_outreach_user_confirmed'] is True
                and p['future_fresh_post_stop_full_snapshot_required'] is True
                and p['exact_stop_effective_at'] is None
                and p['observed_at'] == STOP_OBSERVED_AT
                and quarantine['payload']['observed_at'] == STOP_OBSERVED_AT
                and all(p[key] is False for key in ('activation_authorized', 'delivery_verified',
                    'history_complete', 'native_row_linkage_verified')),
                'native_stop_honesty_invalid')
        return canonical
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('native_cohort_invalid') from None


def cohort_matches(read_record):
    """Fresh exact containment is necessary before any incident clearance."""
    try:
        rows = [read_record(identity) for identity in _COHORT_SPECS]
        _records_valid(rows)
        return True
    except Exception:
        return False


def _proposal_records(contents, manifest):
    wrappers = [json.loads(contents[name]) for name in PROPOSAL_MEMBERS]
    fields = {'schema', 'proposal_only', 'apply_status', 'activation_authorized', 'proposed_record',
        'requires_exact_source_and_scope_readback_before_central_apply',
        'requires_future_fresh_post_stop_full_snapshot', 'requires_stopped_single_writer_boundary_review'}
    for wrapper in wrappers:
        require(isinstance(wrapper, dict) and set(wrapper) == fields
                and wrapper['schema'] == 'apply-unapplied-central-reconciliation-proposal/v1'
                and wrapper['proposal_only'] is True and wrapper['apply_status'] == 'unapplied'
                and wrapper['activation_authorized'] is False
                and all(wrapper[key] is True for key in fields if key.startswith('requires_')),
                'native_proposal_applied_or_changed')
    records = _records_valid([wrapper['proposed_record'] for wrapper in wrappers])
    require(manifest['schema'] == 'apply-unapplied-central-reconciliation-packet/v1'
            and manifest['proposal_only'] is True and manifest['apply_status'] == 'unapplied'
            and manifest['activation_authorized'] is False and manifest['record_count'] == 3
            and manifest['writer_resolution_records_in_packet'] == 0
            and manifest['canonical_contact_id_created'] is False
            and manifest['claim_page_is_canonical_contact_page'] is False
            and manifest['existing_snapshot_is_post_stop'] is False
            and manifest['future_fresh_post_stop_full_snapshot_required'] is True
            and manifest['history_incomplete'] is True and manifest['native_unknown_delivery'] is True
            and manifest['affected_contact_hold_required'] is True
            and manifest['failed_writer_fences_retained'] == list(FAILURES)
            and manifest['quarantine_identity_types'] == ['profile_urls']
            and manifest['trusted_stop_observed_at'] == STOP_OBSERVED_AT,
            'native_packet_manifest_honesty_invalid')
    return records


def _source_binding(contents, records, incident):
    quarantine, native, stop = records
    logical = json.loads(contents['source/native-claim-logical.json'])
    raw = json.loads(contents['source/native-claim-raw.json'])
    resource = json.loads(contents['source/native-resource-fields.json'])
    task = json.loads(contents['source/native-task-intent-fields.json'])
    native_id = native['payload']['claim_source_notion_page_id']
    require(isinstance(native_id, str) and _sha(native_id.encode()) == NATIVE_PAGE_ID_SHA256
            and native_id not in incident.page_ids.values()
            and incident.pages.get(native_id) == logical and content_hash(logical) == NATIVE_LOGICAL_SHA256
            and logical == {'page': source_metadata(raw['page']), 'blocks': raw['blocks']},
            'native_exact_source_page_changed')
    page, props = logical['page'], logical['page']['properties']
    require(page['id'] == native_id and logical['blocks'] == []
            and all(not _text(props.get(key)) for key in
                ('Cloud ID', 'Cloud Envelope', 'Event ID', 'Content Hash', 'Payload JSON'))
            and _text(props.get('Actor')) == 'Muse'
            and _text(props.get('Delivery State')) == 'Sent verified',
            'native_unmanaged_claim_shape_changed')
    require(resource == {'Contact Key': props.get('Contact Key'), 'Link': props.get('Link'),
                'source_page_id': native_id, 'source_parent': page['parent']}
            and set(task) == {'Claim Token', 'Due', 'Intent ID', 'Kind', 'Lease Until', 'Next Action', 'Work Key', 'Worker'}
            and task == {name: props.get(name) for name in task}, 'native_source_fields_changed')
    profile = quarantine['payload']['identity']['profile_urls'][0]
    require(profile_url(_text(resource['Contact Key'])) == profile
            and not any(profile in _contact_urls(row['payload'])
                        for row in incident.rows.values() if row['kind'] == 'contacts'),
            'native_contact_scope_mapping_changed')
    provenance = {name: _sha(contents[member]) for name, member in (
        ('claim_sha256', 'source/native-claim-raw.json'),
        ('resource_sha256', 'source/native-resource-fields.json'),
        ('task_sha256', 'source/native-task-intent-fields.json'))}
    p = native['payload']
    require(quarantine['payload']['provenance'] == provenance
            and p['source_raw_claim_sha256'] == provenance['claim_sha256']
            and p['source_resource_fields_sha256'] == provenance['resource_sha256']
            and p['source_task_fields_sha256'] == provenance['task_sha256']
            and p['source_logical_page_sha256'] == NATIVE_LOGICAL_SHA256
            and p['source_snapshot_fingerprint'] == INCIDENT_FINGERPRINT
            and p['source_snapshot_captured_at'] == incident.snapshot['captured_at']
            and p['source_page_created_at'] == page['created_time']
            and p['claimed_actor_label'] == 'Muse' and p['claimed_delivery_label'] == 'Sent verified',
            'native_record_source_binding_changed')
    return native_id


def _load_plan(incident, post_stop, key, started_at):
    require(isinstance(incident, SnapshotState) and isinstance(post_stop, SnapshotState)
            and len(incident.pages) == INCIDENT_PAGES and len(incident.rows) == INCIDENT_RECORDS
            and incident.fingerprint() == INCIDENT_FINGERPRINT
            and post_stop.fingerprint() == incident.fingerprint(), 'native_full_source_changed')
    started, stopped = _time(started_at), _time(STOP_OBSERVED_AT)
    captured, original = _time(post_stop.snapshot['captured_at']), _time(incident.snapshot['captured_at'])
    require(started is not None and stopped is not None and captured is not None and original is not None
            and original < stopped < started <= captured, 'native_fresh_post_stop_snapshot_required')
    require(PACKET_PATH.is_file() and not PACKET_PATH.is_symlink(), 'native_packet_unavailable')
    ciphertext = PACKET_PATH.read_bytes()
    require(_sha(ciphertext) == PACKET_SHA256, 'native_packet_ciphertext_changed')
    contents = _archive(ciphertext, key, MEMBER_SHA256)
    manifest = json.loads(contents['packet-manifest.json'])
    records = _proposal_records(contents, manifest)
    native_id = _source_binding(contents, records, incident)
    attestation_cipher = contents['source-evidence/trusted-user-stop-attestation.aesgcm']
    stop = records[2]['payload']
    nested = _archive(attestation_cipher, key, ATTESTATION_MEMBER_SHA256, hashed_names=True)
    attestation = json.loads(nested[ATTESTATION_NAME_SHA256])
    require(attestation['schema'] == 'apply-user-controller-stop-attestation/v1'
            and attestation['observed_at'] == STOP_OBSERVED_AT
            and attestation['activation_authorized'] is False
            and attestation['affected_contact_hold_required'] is True
            and attestation['history_incomplete'] is True
            and attestation['independent_stop_claim_clears_delivery_history'] is False
            and attestation['writer_resolution_or_history_records_added'] == 0
            and attestation['failed_writer_ids'] == list(FAILURES)
            and set(attestation['unverified']) == {'complete_final_external_delta', 'definitive_delivery_evidence',
                'exact_stop_effective_time', 'message_content_hash', 'native_row_linkage', 'recipient', 'thread'}
            and all(value is True for value in attestation['unverified'].values())
            and attestation['native_observation']['delivery_verified'] is False
            and attestation['native_observation']['native_unknown_delivery'] is True
            and attestation['native_observation']['logical_page_sha256'] == NATIVE_LOGICAL_SHA256
            and attestation['trusted_user_claims'] == {
                'morning_outreach_activity_occurred': True, 'muse_controller_stopped_now': True}
            and attestation['source']['kind'] == stop['source_kind'] == 'direct_human_user_answer'
            and attestation['source']['question_id'] == stop['source_question_id']
            and attestation['source']['exact_answer'] == stop['exact_answer']
            and _sha(stop['exact_answer'].encode()) == stop['exact_answer_sha256']
                == attestation['source']['exact_answer_sha256']
            and stop['source_attestation_cipher_sha256'] == _sha(attestation_cipher),
            'native_trusted_stop_attestation_changed')
    proof = {'schema': 'apply-pinned-native-claim-containment-proof/v1',
        'packet_sha256': PACKET_SHA256, 'packet_member_hashes_sha256': content_hash(MEMBER_SHA256),
        'attestation_member_hashes_sha256': content_hash(ATTESTATION_MEMBER_SHA256),
        'incident_source_sha256': INCIDENT_FINGERPRINT, 'post_stop_source_sha256': post_stop.fingerprint(),
        'post_stop_operation_started_at': started_at, 'trusted_stop_observed_at': STOP_OBSERVED_AT,
        'native_logical_page_sha256': NATIVE_LOGICAL_SHA256, 'native_page_identity_sha256': NATIVE_PAGE_ID_SHA256,
        'source_pages_preserved': INCIDENT_PAGES, 'source_cores_preserved': INCIDENT_RECORDS,
        'proposed_core_sha256': dict(_COHORT_SPECS), 'proposed_records': 3,
        'canonical_contact_matches': 0, 'canonical_contact_created': False,
        'quarantine_scope_types': ['profile_urls'], 'native_delivery_verified': False,
        'user_to_native_linkage_verified': False, 'history_complete': False,
        'external_effects_reconciled': False, 'activation_authorized': False,
        'records_proposal_only': True, 'full_post_stop_snapshot_verified': True}
    return Plan(native_id, canonical_json(records), canonical_json(proof))


def load_plan(incident: SnapshotState, post_stop: SnapshotState, key: bytes, *, post_stop_started_at: str) -> Plan:
    """Validate exact source and proposals; never apply or clear their cohort."""
    try:
        return _load_plan(incident, post_stop, key, post_stop_started_at)
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('native_containment_evidence_invalid') from None
