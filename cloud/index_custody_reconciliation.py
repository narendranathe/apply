"""Pinned October 24 managed-index custody, without replay or activation.

The archived post-failure fingerprint commits every observed CORE and raw page.
The candidate semantic reconstruction is diagnostic only. A complete cohort is
necessary but never sufficient runtime clearance: the receipt module separately
requires an authenticated successful hosted verification.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from cloud import contained_recovery as contained
from cloud import index_custody_evidence as evidence
from cloud.agent_index import GENERATED_KINDS, SCHEMA as INDEX_SCHEMA, build_index
from cloud.cutover import validate_cutover_record, validate_import
from cloud.index_publisher import _reconciled_source_proof, snapshot_hash
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError
from cloud.outreach import record
from cloud.records import canonical_json, content_hash
from cloud.startup_reconciliation import (BASELINE_HASH, BASELINE_RECORDS, READY_CORE_HASH,
    SnapshotState, ReconciliationBlocked, core, source_metadata)
from cloud.storage_reservations import RESERVED_RECORD_IDS
from scripts.encrypted_backup import MAGIC, restore_archive
from cloud.activation import _time

FAILURES = ('40050463437', '40101990960', '40103832704', '40204362589', '40204819102')
SCHEMA = 'apply-pinned-index-custody-writer-resolution/v1'
WORKFLOW = '.github/workflows/index-custody-reconciliation.yml'
TITLE = 'index-custody:${mode}'
OPERATION_STEP = 'Reconcile managed index custody without replay'
PLAN_SCHEMA = 'apply-pinned-index-custody-plan/v1'
REPOSITORY = 'narendranathe/apply'
MAX_ARCHIVE_BYTES = 128_000_000
ROOT = Path(__file__).resolve().parents[1]


def require(condition, reason):
    if not condition:
        raise ReconciliationBlocked(reason)


def require_reviewed_pins():
    """Only code-reviewed pins; no input/environment incident overrides."""
    evidence.require_reviewed_pins()
    require(tuple(evidence.FAILURES) == FAILURES, 'custody_reviewed_pins_missing')
    proof = evidence.reviewed_proof()
    require(proof['scope']['original_publication_clock_known'] is False
            and proof['scope']['exact_original_failure_cause_proven'] is False
            and proof['scope']['global_external_effects_reconciled'] is False,
            'custody_reviewed_pins_missing')
    for name in ('trusted', 'observed'):
        pins = evidence.SNAPSHOTS[name]
        require(all(isinstance(pins.get(field), str) and len(pins[field]) == 64
                    and all(char in '0123456789abcdef' for char in pins[field])
                    for field in ('ciphertext_sha256', 'plaintext_member_sha256', 'source_fingerprint'))
                and type(pins.get('pages')) is int and type(pins.get('cores')) is int,
                'custody_reviewed_pins_missing')


def _snapshot_pin(state, name):
    pins = evidence.SNAPSHOTS[name]
    require(state.fingerprint() == pins['source_fingerprint'] and len(state.pages) == pins['pages']
            and len(state.rows) == pins['cores'], 'custody_archived_snapshot_changed')


def verify_archived_inputs(key):
    """Authenticate two committed encrypted snapshots in private ephemeral staging."""
    require_reviewed_pins()
    require(isinstance(key, bytes) and len(key) == 32, 'custody_backup_key_invalid')
    states = []
    try:
        with tempfile.TemporaryDirectory(prefix='apply-custody-source-') as folder:
            staging = Path(folder)
            staging.chmod(0o700)
            key_path = staging / 'archive.key'
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            for name in ('trusted', 'observed'):
                pins = evidence.SNAPSHOTS[name]
                archive = Path(pins['path'])
                if not archive.is_absolute():
                    archive = ROOT / archive
                require(archive.is_file() and not archive.is_symlink()
                        and 0 < archive.stat().st_size <= MAX_ARCHIVE_BYTES,
                        'custody_archive_unavailable')
                cipher = archive.read_bytes()
                require(cipher.startswith(MAGIC)
                        and hashlib.sha256(cipher).hexdigest() == pins['ciphertext_sha256'],
                        'custody_archive_changed')
                restored = staging / name
                report = restore_archive(archive, restored, key_path=key_path)
                require(report.get('hashes_verified') is True and report.get('restored_files') == 1
                        and {p.name for p in restored.iterdir()} == {'cloud-records.json'},
                        'custody_archive_members_changed')
                member = (restored / 'cloud-records.json').read_bytes()
                require(hashlib.sha256(member).hexdigest() == pins['plaintext_member_sha256'],
                        'custody_archive_member_changed')
                wrapper = json.loads(member)
                require(isinstance(wrapper, dict) and wrapper.get('schema') == 'apply-cloud-snapshot/v1',
                        'custody_archive_wrapper_invalid')
                state = SnapshotState(wrapper['snapshot'])
                _snapshot_pin(state, name)
                states.append(state)
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('custody_archive_authentication_failed') from None
    return tuple(states)


def _raw_drift(before, after):
    require(before.data_source_id == after.data_source_id
            and source_metadata(before.snapshot['data_source'], data_source=True)
                == source_metadata(after.snapshot['data_source'], data_source=True)
            and set(before.pages) <= set(after.pages), 'custody_prior_raw_source_changed')
    changed = [identity for identity, page in before.pages.items() if after.pages[identity] != page]
    require(len(changed) == 1, 'custody_raw_drift_count_changed')
    identity = changed[0]
    require(identity not in before.page_ids.values() and identity not in after.page_ids.values(),
            'custody_raw_drift_managed_page')
    paths = []

    def walk(a, b, path=()):
        require(type(a) is type(b), 'custody_raw_drift_structure_changed')
        if isinstance(a, dict):
            require(a.keys() == b.keys(), 'custody_raw_drift_structure_changed')
            for field in a:
                walk(a[field], b[field], path + (field,))
        elif isinstance(a, list):
            require(len(a) == len(b), 'custody_raw_drift_structure_changed')
            for position, (old, new) in enumerate(zip(a, b)):
                walk(old, new, path + (position,))
        elif a != b:
            paths.append(path)

    old, new = before.pages[identity], after.pages[identity]
    walk(old, new)
    expected = evidence.UNMANAGED_DRIFT
    require(paths == [tuple(expected['exact_changed_path'])]
            and content_hash(identity) == expected['page_id_sha256']
            and content_hash(old) == expected['before_raw_page_sha256']
            and content_hash(new) == expected['after_raw_page_sha256'], 'custody_raw_drift_witness_changed')
    old_mention, new_mention = old['blocks'][3]['paragraph']['rich_text'][1], new['blocks'][3]['paragraph']['rich_text'][1]
    require(old_mention.get('type') == new_mention.get('type') == 'mention'
            and 'text' not in old_mention and 'text' not in new_mention
            and type(old_mention.get('plain_text')) is str and type(new_mention.get('plain_text')) is str,
            'custody_raw_drift_mention_changed')
    return deepcopy(expected)


def _generated(row):
    return row['kind'] in GENERATED_KINDS or (row['kind'] == 'agent_tasks'
                                           and row['payload'].get('schema') == INDEX_SCHEMA)


def _effect_signatures(rows):
    attempts, receipts, outreach = set(), set(), set()
    for row in rows:
        payload = row['payload']
        if row['kind'] == 'outreach_events':
            outreach.add(content_hash(core(row)))
        if row['kind'] == 'conversation_observations':
            require(not payload.get('sent_at') and not payload.get('delivered_at')
                    and str(payload.get('delivery_state', '')).casefold() not in {'sent', 'sent verified', 'delivered'},
                    'custody_delivery_flag_present')
        if row['kind'] == 'applications':
            identity = payload.get('application_id') or row['id']
            if payload.get('attempt') is not None:
                attempts.add(content_hash([identity, payload['attempt']]))
            if payload.get('receipt') is not None:
                receipts.add(content_hash([identity, payload['receipt']]))
    return attempts, receipts, outreach


def _preserved_native(state):
    contained._cohort(state.get)  # Frozen strict V3 hold/peer/reservation predicate.
    ready = state.get('deployment:cutover:v2')
    require(ready is not None and content_hash(core(ready)) == READY_CORE_HASH,
            'custody_ready_record_changed')
    validate_cutover_record(ready)


def _candidate_evidence(before, after, exported, guards, proof):
    """Rebuild children purely; never return or publish a candidate manifest."""
    candidate = proof['diagnostic_candidate']
    source = [core(row) for identity, row in after.rows.items() if identity not in guards and not _generated(row)]
    previous = max((row for row in before.rows.values() if row['kind'] == 'index_manifests'),
        key=lambda row: (_time(row['payload']['generated_at']), row['id']), default=None)
    require(previous is not None, 'custody_previous_manifest_missing')
    complete, conflicts = _reconciled_source_proof(source, exported, core(after.get('deployment:cutover:v2')))
    require(complete and not conflicts, 'custody_candidate_baseline_invalid')
    rebuilt = build_index(source, _time(candidate['clock']), previous_manifest=previous,
        source_snapshot_hash={'hash': snapshot_hash(source), 'complete': True, 'conflicts': []})
    manifests = [row for row in rebuilt if row['kind'] == 'index_manifests']
    children = [row for row in rebuilt if row['kind'] != 'index_manifests']
    require(len(manifests) == 1 and after.get(manifests[0]['id']) is None,
            'custody_candidate_manifest_unexpected')
    require(len(source) == candidate['source_records'] and snapshot_hash(source) == candidate['source_hash']
            and len(children) == candidate['generated_children'] == candidate['children_matching_current']
            and all(after.get(row['id']) is not None and core(after.get(row['id'])) == core(row) for row in children)
            and content_hash({row['id']: content_hash(core(row)) for row in children})
                == candidate['child_core_set_sha256'], 'custody_candidate_children_changed')
    child_ids = {row['id'] for row in children}
    require(all(not _generated(row) or identity in child_ids for identity, row in after.rows.items()
                if identity not in before.rows), 'custody_unexplained_generated_record')
    manifest_characters = len(canonical_json(manifests[0]))
    capacity = proof['manifest_capacity']['diagnostic_candidate_manifest']
    require(manifest_characters == capacity['canonical_characters']
            and (manifest_characters + 1999) // 2000 == capacity['rich_text_chunks'] > 100,
            'custody_candidate_capacity_changed')


def reviewed_plan():
    """Bounded public commitments derived only from exact code-reviewed evidence."""
    require_reviewed_pins()
    proof = evidence.reviewed_proof()
    value = {'schema': PLAN_SCHEMA, 'incident_run_ids': list(FAILURES),
        'reviewed_proof_sha256': evidence.PROOF_SHA256,
        'snapshots': {name: {field: evidence.SNAPSHOTS[name][field] for field in
            ('run_id', 'artifact_id', 'ciphertext_sha256', 'plaintext_member_sha256',
             'source_fingerprint', 'pages', 'cores')} for name in ('trusted', 'observed')},
        'baseline_sha256': BASELINE_HASH, 'baseline_records': BASELINE_RECORDS,
        'ready_core_sha256': READY_CORE_HASH, 'hold_core_sha256': contained.HOLD_CORE_HASH,
        'reserved_ids': sorted(RESERVED_RECORD_IDS), 'delta': deepcopy(proof['delta']),
        'raw_page_drift': deepcopy(proof['raw_page_drift']),
        'diagnostic_candidate': deepcopy(proof['diagnostic_candidate']),
        'original_clock_verified': False, 'original_failure_cause_verified': False,
        'original_index_publication_complete': False, 'index_complete': False,
        'external_effects_reconciled': False, 'retry_external_effects': False,
        'native_delivery_verified': False, 'native_history_complete': False,
        'activation_authorized': False, 'managed_cloud_custody_verified': True}
    value['plan_sha256'] = content_hash(value)
    return value


def build_plan(before, after, exported):
    """Validate actual immutable state and return the same reviewable commitments."""
    require_reviewed_pins()
    _snapshot_pin(before, 'trusted')
    _snapshot_pin(after, 'observed')
    proof = evidence.reviewed_proof()
    require(content_hash(exported) == BASELINE_HASH and len(exported.get('records', [])) == BASELINE_RECORDS,
            'custody_baseline_changed')
    require(set(before.rows) <= set(after.rows)
            and all(core(after.rows[identity]) == core(row) for identity, row in before.rows.items()),
            'custody_prior_core_changed')
    _raw_drift(before, after)
    for state in (before, after):
        _preserved_native(state)
        report = validate_import(state, exported)
        require(report['records'] == report['unchanged'] == BASELINE_RECORDS
                and report['blocked'] == report['conflicted'] == 0, 'custody_historical_import_changed')
        require(all(state.get('writer-resolution:' + identity) is None for identity in FAILURES),
                'custody_archived_resolution_present')
    require(_effect_signatures(before.rows.values()) == _effect_signatures(after.rows.values()),
            'custody_effect_signatures_changed')
    added = {identity: row for identity, row in after.rows.items() if identity not in before.rows}
    guards = {}
    for identity in FAILURES[:-1]:
        row = record('writer-failure:' + identity, 'audit_events', {'github_run_id': identity,
            'reason': 'failed_writer_requires_reconciliation', 'retry_external_effects': False},
            'needs_reconciliation')
        require(row['id'] in added and core(added[row['id']]) == core(row), 'custody_guard_audit_changed')
        guards[row['id']] = row
    counts = dict(Counter(row['kind'] for row in added.values()))
    generated = sum(_generated(row) for row in added.values())
    require(counts == proof['delta']['added_kind_counts'] and len(added) == proof['delta']['added_cores']
            and generated == proof['delta']['added_generated_cores']
            and len(added) - generated == proof['delta']['added_nongenerated_cores']
            and len(guards) == proof['delta']['later_exact_guard_audits']
            and len(added) - generated - len(guards) == proof['delta']['added_business_cores_excluding_guards']
            and not any(row['kind'] == 'index_manifests' for row in added.values()), 'custody_added_core_set_changed')
    _candidate_evidence(before, after, exported, guards, proof)
    return reviewed_plan()


def resolution_records(plan, binding):
    """Noncircular template commitment; lead commits actual peer whole COREs."""
    require(plan == reviewed_plan(), 'custody_plan_changed')
    common = {'schema': SCHEMA, 'plan_sha256': plan['plan_sha256'], 'evidence': deepcopy(plan),
        'correction_binding': deepcopy(binding), 'cohort_ids': ['writer-resolution:' + run for run in FAILURES],
        'managed_cloud_custody_verified': True, 'external_effects_reconciled': False,
        'retry_external_effects': False, 'original_clock_verified': False,
        'original_failure_cause_verified': False, 'original_index_publication_complete': False,
        'index_complete': False, 'activation_authorized': False,
        'native_delivery_verified': False, 'native_history_complete': False}
    templates = [record('writer-resolution:' + run, 'audit_events', {**deepcopy(common),
        'github_run_id': run}, 'reconciled_managed_custody_index_incomplete' if run == FAILURES[0]
        else 'reconciled_guard_stopped_no_business_operation') for run in FAILURES]
    commitments = {row['id']: content_hash(core(row)) for row in templates}
    peers = []
    for template in templates[1:]:
        payload = {**template['payload'], 'cohort_template_core_sha256': commitments}
        peers.append(record(template['id'], template['kind'], payload, template['status']))
    lead = templates[0]
    payload = {**lead['payload'], 'cohort_template_core_sha256': commitments,
        'peer_whole_core_sha256': {row['id']: content_hash(core(row)) for row in peers}}
    return [*peers, record(lead['id'], lead['kind'], payload, lead['status'])]


def validate_complete_cohort(read_record, github):
    """Reconstruct from reviewed commitments; never trust a marker-supplied plan."""
    plan = reviewed_plan()
    lead = read_record('writer-resolution:' + FAILURES[0])
    require(isinstance(lead, dict) and isinstance(lead.get('payload'), dict)
            and isinstance(lead['payload'].get('correction_binding'), dict), 'custody_complete_cohort_required')
    binding = deepcopy(lead['payload']['correction_binding'])
    evidence.validate_binding(github, binding)
    expected = resolution_records(plan, binding)
    try:
        require(all(core(read_record(row['id'])) == core(row) for row in expected),
                'custody_complete_cohort_changed')
    except (KeyError, TypeError):
        raise ReconciliationBlocked('custody_complete_cohort_changed') from None
    _preserved_native(type('ReadOnlyState', (), {'get': staticmethod(read_record)})())
    return expected, binding


def _context(context, mode):
    require(mode in ('reconcile', 'verify') and context.get('mode') == mode,
            'custody_mode_invalid')
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == REPOSITORY
            and context.get('actor') == context.get('triggering_actor') == 'narendranathe'
            and context.get('event') == 'workflow_dispatch'
            and context.get('cloud_enabled') == 'false' and context.get('cloud_research_enabled') == 'true',
            'custody_owner_disabled_hosted_maintenance_required')


def _prior_attempts(github, binding):
    history = evidence._workflow_history(github.fetch)
    require(sum(str(run.get('id')) == binding['run_id'] for run in history) == 1,
            'custody_current_attempt_missing_from_history')
    # No automatic second execution after any earlier dispatch, including one
    # whose uncertain POST is not yet visible. An exact full cohort is read-only.
    require(all(str(run.get('id')) == binding['run_id'] for run in history),
            'custody_prior_attempt_forbids_new_posts')


def _trimmed_current(current, store, observed, expected, written_ids=()):
    require(current.data_source_id == getattr(store, 'data_source_id', None) == observed.data_source_id
            and _time(current.snapshot['captured_at']) >= _time(observed.snapshot['captured_at']),
            'custody_fresh_identity_or_time_changed')
    require(all(current.get(identity) is None for identity in RESERVED_RECORD_IDS), 'custody_reserved_id_present')
    for row in expected:
        actual = current.get(row['id'])
        if row['id'] in written_ids:
            require(actual is not None and core(actual) == core(row), 'custody_written_cohort_changed')
            current.pages.pop(current.page_ids[row['id']])
        else:
            require(actual is None, 'custody_unexpected_cohort_record')
    require(current.fingerprint() == observed.fingerprint(), 'custody_fresh_source_changed')
    return current


def _write_verified(store, row):
    try:
        written = store.put(deepcopy(row))
        observed = store.get(row['id'])
        exact = isinstance(written, dict) and isinstance(observed, dict) and core(written) == core(observed) == core(row)
    except Exception as error:
        diagnostics = {'operation': 'verify_create_readback'}
        # Invoke only the known base implementation, never an upstream override
        # or arbitrary diagnostics method. These methods return three closed fields.
        try:
            if isinstance(error, NotionAPIError):
                diagnostics = NotionAPIError.diagnostics(error)
            elif isinstance(error, AmbiguousWriteError):
                diagnostics = AmbiguousWriteError.diagnostics(error)
            elif isinstance(error, ConflictError):
                diagnostics = ConflictError.diagnostics(error)
        except Exception:
            diagnostics = {'operation': 'verify_create_readback'}
        raise AmbiguousWriteError('Index custody write uncertain; stop without retry',
                                  diagnostics=diagnostics) from None
    if not exact:
        raise AmbiguousWriteError('Index custody readback uncertain; stop without retry',
                                  diagnostics={'operation': 'verify_create_readback'})


def reconcile(store, exported, github, context, key, *, mode='reconcile'):
    """Only five immutable audit appends; backup belongs to the hosted wrapper."""
    _context(context, mode)  # Before native reads, archive restore and mutation.
    require_reviewed_pins()
    current_binding = evidence.verify_current_context(github, context)
    incident = evidence.verify_incidents(github)
    before, observed = verify_archived_inputs(key)
    plan = build_plan(before, observed, exported)
    current = SnapshotState(store.snapshot_all())
    existing = [current.get('writer-resolution:' + run) for run in FAILURES]
    require(not any(row is not None for row in existing) or all(row is not None for row in existing),
            'custody_partial_cohort_requires_reconciliation')
    unchanged = all(row is not None for row in existing)
    if unchanged:
        expected, original_binding = validate_complete_cohort(current.get, github)
    else:
        require(mode == 'reconcile', 'custody_verify_requires_complete_cohort')
        original_binding = current_binding
        _prior_attempts(github, current_binding)
        expected = resolution_records(plan, original_binding)
    _trimmed_current(current, store, observed, expected, [row['id'] for row in expected] if unchanged else [])
    require(evidence.native_incidents(github.fetch) == incident, 'custody_incident_evidence_changed')
    require(evidence.verify_current_context(github, context) == current_binding, 'custody_current_execution_changed')
    if not unchanged:
        _prior_attempts(github, current_binding)
        for row in expected:
            require(evidence.verify_current_context(github, context) == current_binding,
                    'custody_current_execution_changed')
            require(evidence.native_incidents(github.fetch) == incident, 'custody_incident_evidence_changed')
            _write_verified(store, row)
    # The wrapper must authenticate the final complete snapshot and compare it
    # with the observed snapshot plus this exact cohort before durable success.
    return {'operation': 'pinned_index_custody_reconciliation', 'verified': True,
        'mode': mode, 'unchanged': unchanged, 'new_records': 0 if unchanged else len(expected),
        'cohort_records': len(expected), 'resolved_run_ids': list(FAILURES),
        'plan_sha256': plan['plan_sha256'], 'correction_binding': deepcopy(original_binding),
        'current_binding': deepcopy(current_binding),
        'record_sha256': {row['id']: row['hash'] for row in expected},
        'whole_core_sha256': {row['id']: content_hash(core(row)) for row in expected},
        'observed_source_fingerprint': observed.fingerprint(),
        'external_effects': 0, 'external_effects_reconciled': False, 'retry_external_effects': False,
        'original_clock_verified': False, 'original_failure_cause_verified': False,
        'original_index_publication_complete': False, 'index_complete': False,
        'native_delivery_verified': False, 'native_history_complete': False,
        'managed_cloud_custody_verified': True, 'activation_authorized': False,
        'durable_hosted_verification_required': True, 'cloud_enabled_changed': False}


SAFE_REASONS = frozenset({
    'custody_reviewed_pins_missing', 'custody_backup_key_invalid', 'custody_archive_unavailable',
    'custody_archive_changed', 'custody_archive_members_changed', 'custody_archive_member_changed',
    'custody_archive_wrapper_invalid', 'custody_archived_snapshot_changed', 'custody_archive_authentication_failed',
    'custody_prior_raw_source_changed', 'custody_raw_drift_count_changed', 'custody_raw_drift_managed_page',
    'custody_raw_drift_structure_changed', 'custody_raw_drift_witness_changed', 'custody_raw_drift_mention_changed',
    'custody_delivery_flag_present', 'custody_ready_record_changed', 'custody_previous_manifest_missing',
    'custody_candidate_baseline_invalid', 'custody_candidate_manifest_unexpected', 'custody_candidate_children_changed',
    'custody_unexplained_generated_record', 'custody_candidate_capacity_changed', 'custody_baseline_changed',
    'custody_prior_core_changed', 'custody_historical_import_changed', 'custody_archived_resolution_present',
    'custody_effect_signatures_changed', 'custody_guard_audit_changed', 'custody_added_core_set_changed',
    'custody_plan_changed', 'custody_complete_cohort_required', 'custody_complete_cohort_changed',
    'custody_mode_invalid', 'custody_owner_disabled_hosted_maintenance_required',
    'custody_current_attempt_missing_from_history', 'custody_prior_attempt_forbids_new_posts',
    'custody_fresh_identity_or_time_changed', 'custody_reserved_id_present', 'custody_written_cohort_changed',
    'custody_unexpected_cohort_record', 'custody_fresh_source_changed',
    'custody_partial_cohort_requires_reconciliation', 'custody_verify_requires_complete_cohort',
    'custody_incident_evidence_changed', 'custody_current_execution_changed'})
