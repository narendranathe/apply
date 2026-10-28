"""Synthetic authenticated custody and append tests; no private/native services."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cloud import index_custody_reconciliation as subject
from cloud.agent_index import build_index
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError
from cloud.outreach import record
from cloud.records import canonical_json, content_hash
from cloud.startup_reconciliation import SnapshotState, ReconciliationBlocked, core
from scripts.encrypted_backup import create_backup
from tests.test_startup_reconciliation import entry, states

CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': subject.REPOSITORY,
    'actor': 'narendranathe', 'triggering_actor': 'narendranathe', 'event': 'workflow_dispatch',
    'cloud_enabled': 'false', 'cloud_research_enabled': 'true', 'commit': 'b' * 40,
    'run_id': '900', 'mode': 'reconcile'}
BINDING = {'run_id': '900', 'head_sha': 'b' * 40, 'mode': 'reconcile', 'source_sha256': {'synthetic': 'c' * 64}}


class Store:
    def __init__(self, source):
        self.source, self.data_source_id = deepcopy(source), source['data_source_id']
        self.writes, self.reads = [], []
        self.failure = self.readback = self.mutation = None
        self.snapshots = 0

    def snapshot_all(self):
        self.snapshots += 1
        if self.mutation:
            self.mutation(self)
        return deepcopy(self.source)

    def get(self, identity):
        self.reads.append(identity)
        if self.writes and identity == self.writes[-1] and self.readback:
            return self.readback()
        return SnapshotState(self.source).get(identity)

    def put(self, row):
        self.writes.append(row['id'])
        self.source['pages'].append(entry(row))
        if self.failure == len(self.writes):
            raise RuntimeError('private body must never escape')
        return deepcopy(row)


class GitHub:
    def fetch(self, path):
        raise AssertionError('unpatched native access')


def fixture():
    exported, ready, before, _ = states()
    native = before['pages'][-1]
    native['blocks'] = [{'type': 'paragraph', 'paragraph': {'rich_text': []}} for _ in range(4)]
    native['blocks'][3]['paragraph']['rich_text'] = [
        {'type': 'text', 'text': {'content': 'synthetic prefix'}},
        {'type': 'mention', 'mention': {'type': 'page', 'page': {'id': 'synthetic-target'}}, 'plain_text': 'old'}]
    after = deepcopy(before)
    after['captured_at'] = '2026-10-26T00:00:00Z'
    after['pages'][-1]['blocks'][3]['paragraph']['rich_text'][1]['plain_text'] = 'new'
    for identity in subject.FAILURES[:-1]:
        after['pages'].append(entry(record('writer-failure:' + identity, 'audit_events', {
            'github_run_id': identity, 'reason': 'failed_writer_requires_reconciliation',
            'retry_external_effects': False}, 'needs_reconciliation')))
    after['pages'].append(entry(record('job:synthetic', 'jobs', {'open': True})))
    after['pages'].append(entry(record('projection:synthetic', 'entity_projections', {'synthetic': True})))
    trusted, observed = SnapshotState(before), SnapshotState(after)
    identity = native['page']['id']
    drift = {'page_id_sha256': content_hash(identity), 'before_raw_page_sha256': content_hash(trusted.pages[identity]),
        'after_raw_page_sha256': content_hash(observed.pages[identity]),
        'exact_changed_path': ['blocks', 3, 'paragraph', 'rich_text', 1, 'plain_text']}
    snapshots = {name: {'run_id': str(pos), 'artifact_id': pos, 'path': 'unused',
        'ciphertext_sha256': 'd' * 64, 'plaintext_member_sha256': 'e' * 64,
        'source_fingerprint': state.fingerprint(), 'pages': len(state.pages), 'cores': len(state.rows)}
        for pos, (name, state) in enumerate((('trusted', trusted), ('observed', observed)), 1)}
    proof = {'scope': {'original_publication_clock_known': False, 'exact_original_failure_cause_proven': False,
        'global_external_effects_reconciled': False}, 'raw_page_drift': drift,
        'delta': {'added_kind_counts': {'audit_events': 4, 'jobs': 1, 'entity_projections': 1},
            'added_cores': 6, 'added_generated_cores': 1, 'added_nongenerated_cores': 5,
            'later_exact_guard_audits': 4, 'added_business_cores_excluding_guards': 1},
        'diagnostic_candidate': {'clock': '2026-10-24T03:16:26+00:00', 'candidate_only': True}}
    return exported, trusted, observed, proof, snapshots


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.exported, self.before, self.after, self.proof, self.snapshots = fixture()
        self.store, self.github = Store(self.after.snapshot), GitHub()
        patches = [patch.object(subject.evidence, 'require_reviewed_pins'),
            patch.object(subject.evidence, 'reviewed_proof', side_effect=lambda: deepcopy(self.proof)),
            patch.object(subject.evidence, 'SNAPSHOTS', self.snapshots),
            patch.object(subject.evidence, 'UNMANAGED_DRIFT', self.proof['raw_page_drift']),
            patch.object(subject.evidence, 'verify_current_context', return_value=deepcopy(BINDING)),
            patch.object(subject.evidence, 'verify_incidents', return_value={'synthetic_native_proof': True}),
            patch.object(subject.evidence, 'native_incidents', return_value={'synthetic_native_proof': True}),
            patch.object(subject.evidence, 'validate_binding', side_effect=lambda github, binding: binding),
            patch.object(subject.evidence, '_workflow_history', return_value=[{'id': 900}]),
            patch.object(subject, 'BASELINE_HASH', content_hash(self.exported)),
            patch.object(subject, 'BASELINE_RECORDS', 1),
            patch.object(subject, '_preserved_native'), patch.object(subject, '_candidate_evidence'),
            patch.object(subject, 'verify_archived_inputs', return_value=(self.before, self.after))]
        for value in patches:
            value.start()
            self.addCleanup(value.stop)

    def execute(self, **kwargs):
        return subject.reconcile(self.store, self.exported, self.github, kwargs.pop('context', CONTEXT), b'k' * 32, **kwargs)

    def repin(self, state, name='observed'):
        self.snapshots[name].update(source_fingerprint=state.fingerprint(), pages=len(state.pages), cores=len(state.rows))

    def test_valid_plan_deterministic_bounded_and_truthful(self):
        plan = subject.build_plan(self.before, self.after, self.exported)
        self.assertEqual(plan, subject.reviewed_plan())
        self.assertLess(len(canonical_json(plan)), 20_000)
        self.assertNotIn('"plain_text":"old"', canonical_json(plan))
        for field in ('original_clock_verified', 'original_failure_cause_verified',
                      'original_index_publication_complete', 'external_effects_reconciled', 'activation_authorized'):
            self.assertIs(plan[field], False)
        self.assertTrue(plan['managed_cloud_custody_verified'])

    def test_append_all_peers_then_lead_exact_readback(self):
        result = self.execute()
        self.assertEqual(self.store.writes, ['writer-resolution:' + run for run in (*subject.FAILURES[1:], subject.FAILURES[0])])
        self.assertEqual(result['new_records'], 5)
        self.assertTrue(result['durable_hosted_verification_required'])
        rows = subject.resolution_records(subject.reviewed_plan(), BINDING)
        self.assertEqual(result['whole_core_sha256'], {row['id']: content_hash(core(row)) for row in rows})
        self.assertEqual(rows[-1]['payload']['peer_whole_core_sha256'],
                         {row['id']: content_hash(core(row)) for row in rows[:-1]})
        self.assertEqual(len(self.store.reads), 5)
        self.assertEqual(self.store.snapshots, 1)
        subject.evidence.verify_incidents.assert_called_once()

    def test_complete_repeat_no_put(self):
        self.execute()
        self.store.writes.clear()
        result = self.execute()
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['new_records'], 0)
        self.assertFalse(self.store.writes)

    def test_complete_verified_by_later_run_uses_original_binding(self):
        self.execute()
        self.store.writes.clear()
        later = {**BINDING, 'run_id': '901', 'mode': 'verify'}
        snapshots_before_verify = self.store.snapshots
        with patch.object(subject.evidence, 'verify_current_context', return_value=later):
            result = self.execute(context={**CONTEXT, 'run_id': '901', 'mode': 'verify'}, mode='verify')
        self.assertEqual(result['correction_binding'], BINDING)
        self.assertEqual(result['current_binding'], later)
        self.assertEqual(self.store.snapshots - snapshots_before_verify, 1)
        self.assertFalse(self.store.writes)

    def test_verify_absent_never_puts(self):
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_verify_requires_complete_cohort'):
            self.execute(context={**CONTEXT, 'mode': 'verify'}, mode='verify')
        self.assertFalse(self.store.writes)

    def test_all_proper_subsets_block(self):
        rows = subject.resolution_records(subject.reviewed_plan(), BINDING)
        for count in range(1, 5):
            with self.subTest(count=count):
                self.store = Store(self.after.snapshot)
                self.store.source['pages'].extend(entry(row) for row in rows[:count])
                with self.assertRaisesRegex(ReconciliationBlocked, 'custody_partial_cohort_requires_reconciliation'):
                    self.execute()
                self.assertFalse(self.store.writes)

    def test_changed_peer_blocks_without_put(self):
        self.execute()
        self.store.writes.clear()
        page = self.store.source['pages'][-2]
        identity = page['page']['id']
        row = SnapshotState(self.store.source).rows[next(run for run in SnapshotState(self.store.source).rows
              if SnapshotState(self.store.source).page_ids[run] == identity)]
        changed = record(row['id'], row['kind'], {**row['payload'], 'index_complete': True}, row['status'])
        self.store.source['pages'][-2] = entry(changed)
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_complete_cohort_changed'):
            self.execute()
        self.assertFalse(self.store.writes)

    def test_forged_marker_plan_cannot_influence_reconstruction(self):
        rows = subject.resolution_records(subject.reviewed_plan(), BINDING)
        lead = rows[-1]
        lead['payload']['evidence']['activation_authorized'] = True
        lead = record(lead['id'], lead['kind'], lead['payload'], lead['status'])
        self.store.source['pages'].extend(entry(row) for row in [*rows[:-1], lead])
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_complete_cohort_changed'):
            self.execute()
        self.assertFalse(self.store.writes)

    def test_first_or_later_write_exception_stops_without_retry(self):
        for position in (1, 3, 5):
            with self.subTest(position=position):
                self.store = Store(self.after.snapshot)
                self.store.failure = position
                with self.assertRaises(AmbiguousWriteError) as caught:
                    self.execute()
                self.assertEqual(len(self.store.writes), position)
                self.assertNotIn('private body', str(caught.exception))

    def test_missing_or_changed_readback_stops_once(self):
        for value in (None, record('wrong', 'audit_events', {})):
            self.store = Store(self.after.snapshot)
            self.store.readback = lambda: value
            with self.assertRaises(AmbiguousWriteError):
                self.execute()
            self.assertEqual(len(self.store.writes), 1)
            self.assertEqual(len(self.store.reads), 1)

    def test_safe_notion_http_status_code_operation_preserved_without_body(self):
        for status, code in ((403, 'restricted_resource'), (503, 'service_unavailable')):
            with self.subTest(status=status):
                error = NotionAPIError(status, code)
                error.operation = 'create_page'
                error.args = ('private upstream response body',)
                with patch.object(self.store, 'put', side_effect=error):
                    with self.assertRaises(AmbiguousWriteError) as caught:
                        self.execute()
                self.assertEqual(caught.exception.diagnostics(), {'http_status': status,
                    'error_code': code, 'operation': 'create_page'})
                self.assertNotIn('private', str(caught.exception))
                self.assertNotIn('private', canonical_json(caught.exception.diagnostics()))

    def test_safe_base_diagnostics_used_instead_of_untrusted_override(self):
        class UpstreamError(NotionAPIError):
            def diagnostics(self):
                raise AssertionError('private untrusted diagnostics override invoked')
        error = UpstreamError(503, 'service_unavailable')
        error.operation = 'query_data_source'
        with patch.object(self.store, 'get', side_effect=error):
            with self.assertRaises(AmbiguousWriteError) as caught:
                self.execute()
        self.assertEqual(caught.exception.diagnostics(), {'http_status': 503,
            'error_code': 'service_unavailable', 'operation': 'query_data_source'})
        self.assertEqual(len(self.store.writes), 1)

    def test_known_conflict_and_ambiguous_diagnostics_preserved(self):
        for error, expected in [(ConflictError('private body', operation='verify_cloud_body'),
                {'http_status': None, 'error_code': 'unknown', 'operation': 'verify_cloud_body'}),
            (AmbiguousWriteError('private body', diagnostics={'http_status': 503,
                'error_code': 'service_unavailable', 'operation': 'create_page', 'body': 'private body'}),
                {'http_status': 503, 'error_code': 'service_unavailable', 'operation': 'create_page'})]:
            with self.subTest(kind=type(error).__name__):
                with patch.object(self.store, 'put', side_effect=error):
                    with self.assertRaises(AmbiguousWriteError) as caught:
                        self.execute()
                self.assertEqual(caught.exception.diagnostics(), expected)
                self.assertNotIn('private', str(caught.exception))

    def test_missing_put_response_is_uncertain_after_one_post(self):
        original_put = self.store.put
        def put_without_response(row):
            original_put(row)
            return None
        with patch.object(self.store, 'put', side_effect=put_without_response):
            with self.assertRaises(AmbiguousWriteError):
                self.execute()
        self.assertEqual(len(self.store.writes), 1)

    def test_native_witness_change_mid_cohort_stops_remaining(self):
        initial = {'synthetic_native_proof': True}
        with patch.object(subject.evidence, 'native_incidents',
                side_effect=[deepcopy(initial), deepcopy(initial), {'changed': True}]):
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_incident_evidence_changed'):
                self.execute()
        self.assertEqual(len(self.store.writes), 1)
        self.assertEqual(self.store.snapshots, 1)

    def test_ambiguous_first_attempt_cannot_restart_even_if_row_missing(self):
        with patch.object(subject.evidence, '_workflow_history', return_value=[{'id': 899}, {'id': 900}]):
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_prior_attempt_forbids_new_posts'):
                self.execute()
        self.assertFalse(self.store.writes)

    def test_current_attempt_must_be_in_complete_history(self):
        with patch.object(subject.evidence, '_workflow_history', return_value=[]):
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_current_attempt_missing_from_history'):
                self.execute()
        self.assertFalse(self.store.writes)

    def test_invalid_context_before_native_or_archive_access(self):
        for field, value in [('actions', False), ('ref', 'refs/heads/other'), ('repository', 'other/apply'),
            ('actor', 'other'), ('triggering_actor', 'other'), ('event', 'schedule'),
            ('cloud_enabled', 'true'), ('cloud_research_enabled', 'false')]:
            with self.subTest(field=field):
                subject.evidence.verify_current_context.reset_mock()
                with self.assertRaisesRegex(ReconciliationBlocked, 'custody_owner_disabled_hosted_maintenance_required'):
                    self.execute(context={**CONTEXT, field: value})
                subject.evidence.verify_current_context.assert_not_called()
        self.assertFalse(self.store.writes)

    def test_invalid_modes_block(self):
        for mode, context in [('future', CONTEXT), ('verify', CONTEXT), ('reconcile', {**CONTEXT, 'mode': 'verify'})]:
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ReconciliationBlocked, 'custody_mode_invalid'):
                    self.execute(context=context, mode=mode)
        self.assertFalse(self.store.writes)

    def test_incident_or_current_native_change_before_put(self):
        for method, label in [('native_incidents', 'custody_incident_evidence_changed'),
                              ('verify_current_context', 'custody_current_execution_changed')]:
            with self.subTest(method=method):
                original = {'synthetic_native_proof': True} if method == 'native_incidents' else BINDING
                with patch.object(subject.evidence, method, side_effect=([{'changed': True}] if method == 'native_incidents'
                        else [deepcopy(original), {'changed': True}])):
                    with self.assertRaisesRegex(ReconciliationBlocked, label):
                        self.execute()
                self.assertFalse(self.store.writes)

    def test_fresh_extra_or_raw_drift_blocks_before_append(self):
        for extra in (True, False):
            self.store = Store(self.after.snapshot)
            if extra:
                self.store.source['pages'].append(entry(record('unrelated', 'jobs', {})))
            else:
                self.store.source['pages'][0]['page']['properties']['synthetic'] = {'type': 'checkbox', 'checkbox': True}
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_fresh_source_changed'):
                self.execute()
            self.assertFalse(self.store.writes)

    def test_context_change_mid_cohort_stops_remaining_without_snapshot_rescan(self):
        sequence = [deepcopy(BINDING) for _ in range(4)] + [{'changed': True}]
        with patch.object(subject.evidence, 'verify_current_context', side_effect=sequence):
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_current_execution_changed'):
                self.execute()
        self.assertEqual(len(self.store.writes), 2)
        self.assertEqual(self.store.snapshots, 1)

    def test_reserved_identity_blocks(self):
        identity = next(iter(subject.RESERVED_RECORD_IDS))
        self.store.source['pages'].append(entry(record(identity, 'audit_events', {})))
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_reserved_id_present'):
            self.execute()
        self.assertFalse(self.store.writes)

    def test_fresh_identity_and_time_must_match(self):
        for value in ('other', 'older'):
            self.store = Store(self.after.snapshot)
            if value == 'other':
                self.store.data_source_id = 'other'
            else:
                self.store.source['captured_at'] = '2020-01-01T00:00:00Z'
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_fresh_identity_or_time_changed'):
                self.execute()
            self.assertFalse(self.store.writes)

    def test_snapshot_pin_commits_every_core(self):
        altered = deepcopy(self.after.snapshot)
        altered['pages'][-1] = entry(record('projection:synthetic', 'entity_projections', {'synthetic': False}))
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_archived_snapshot_changed'):
            subject.build_plan(self.before, SnapshotState(altered), self.exported)

    def test_baseline_changed_blocks(self):
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_baseline_changed'):
            subject.build_plan(self.before, self.after, {**self.exported, 'synthetic': True})

    def test_prior_core_changed_even_if_fingerprint_repin(self):
        altered = deepcopy(self.after.snapshot)
        row = self.before.get(self.exported['records'][0]['id'])
        altered['pages'][0] = entry(record(row['id'], row['kind'], {'suppressed': False}, row['status']))
        state = SnapshotState(altered)
        self.repin(state)
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_prior_core_changed'):
            subject.build_plan(self.before, state, self.exported)

    def test_exact_one_unmanaged_mention_drift_passes_without_normalization(self):
        original = deepcopy(self.after.snapshot)
        self.assertEqual(subject._raw_drift(self.before, self.after), self.proof['raw_page_drift'])
        self.assertEqual(self.after.snapshot, original)

    def test_second_raw_drift_or_managed_page_drift_blocks(self):
        altered = deepcopy(self.after.snapshot)
        altered['pages'][0]['page']['icon'] = {'type': 'emoji', 'emoji': 'x'}
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_raw_drift_count_changed'):
            subject._raw_drift(self.before, SnapshotState(altered))

    def test_structured_mention_changed_blocks(self):
        altered = deepcopy(self.after.snapshot)
        altered['pages'][2]['blocks'][3]['paragraph']['rich_text'][1]['mention']['page']['id'] = 'other'
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_raw_drift_witness_changed'):
            subject._raw_drift(self.before, SnapshotState(altered))

    def test_source_schema_or_parent_changed_blocks(self):
        altered = deepcopy(self.after.snapshot)
        altered['data_source']['parent'] = {'type': 'workspace'}
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_prior_raw_source_changed'):
            subject._raw_drift(self.before, SnapshotState(altered))

    def test_plain_text_difference_has_no_generic_ignore(self):
        altered = deepcopy(self.after.snapshot)
        altered['pages'][2]['blocks'][3]['paragraph']['rich_text'][0]['text']['content'] = 'other'
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_raw_drift_witness_changed'):
            subject._raw_drift(self.before, SnapshotState(altered))

    def test_effect_signature_attempt_receipt_and_outreach_changes(self):
        old = record('application:x', 'applications', {})
        for changed in [record(old['id'], old['kind'], {'attempt': {'id': '1'}}),
            record(old['id'], old['kind'], {'receipt': {'id': '1'}}), record('outreach:x', 'outreach_events', {})]:
            self.assertNotEqual(subject._effect_signatures([old]), subject._effect_signatures([old, changed]))

    def test_delivery_claim_never_inferred_from_observation(self):
        for payload in ({'sent_at': 'x'}, {'delivered_at': 'x'}, {'delivery_state': 'sent verified'}):
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_delivery_flag_present'):
                subject._effect_signatures([record('observation:x', 'conversation_observations', payload)])

    def test_guard_audit_exact_and_predecessor_identity_retained(self):
        altered = deepcopy(self.after.snapshot)
        row = record('writer-failure:' + subject.FAILURES[0], 'audit_events', {'github_run_id': subject.FAILURES[1],
            'reason': 'failed_writer_requires_reconciliation', 'retry_external_effects': False}, 'needs_reconciliation')
        altered['pages'][3] = entry(row)
        state = SnapshotState(altered)
        self.repin(state)
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_guard_audit_changed'):
            subject.build_plan(self.before, state, self.exported)

    def test_mutated_plan_rejected(self):
        plan = subject.reviewed_plan()
        plan['original_clock_verified'] = True
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_plan_changed'):
            subject.resolution_records(plan, BINDING)

    def test_native_preservation_uses_frozen_cohort_predicate(self):
        with patch.object(subject, '_preserved_native', wraps=ORIGINAL_PRESERVED), \
             patch.object(subject.contained, '_cohort', side_effect=ReconciliationBlocked('contained_complete_exact_peer_cohort_required')):
            with self.assertRaisesRegex(ReconciliationBlocked, 'contained_complete_exact_peer_cohort_required'):
                subject.build_plan(self.before, self.after, self.exported)


ORIGINAL_ARCHIVE = subject.verify_archived_inputs
ORIGINAL_PRESERVED = subject._preserved_native


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        exported, self.before, self.after, self.proof, self.snapshots = fixture()
        self.folder = tempfile.TemporaryDirectory(prefix='custody-synthetic-archive-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.key = self.root / 'backup.key'
        self.key.write_bytes(b'k' * 32)
        self.key.chmod(0o600)
        for name, state in (('trusted', self.before), ('observed', self.after)):
            member = self.root / (name + '.json')
            member.write_text(json.dumps({'schema': 'apply-cloud-snapshot/v1', 'snapshot': state.snapshot}))
            archive = self.root / (name + '.aesgcm')
            create_backup({'cloud-records.json': member}, archive, key_path=self.key, private_destination_verified=True)
            self.snapshots[name].update(path=archive,
                ciphertext_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                plaintext_member_sha256=hashlib.sha256(member.read_bytes()).hexdigest())
        for value in (patch.object(subject.evidence, 'require_reviewed_pins'),
            patch.object(subject.evidence, 'reviewed_proof', return_value=self.proof),
            patch.object(subject.evidence, 'SNAPSHOTS', self.snapshots)):
            value.start()
            self.addCleanup(value.stop)

    def test_real_aes_restore_and_snapshot_fingerprints(self):
        before, after = ORIGINAL_ARCHIVE(b'k' * 32)
        self.assertIsInstance(before, SnapshotState)
        self.assertEqual(before.fingerprint(), self.before.fingerprint())
        self.assertEqual(after.fingerprint(), self.after.fingerprint())

    def test_wrong_key_sanitized(self):
        with self.assertRaisesRegex(ReconciliationBlocked, '^custody_archive_authentication_failed$'):
            ORIGINAL_ARCHIVE(b'x' * 32)

    def test_invalid_key_before_restore(self):
        for key in (None, 'k' * 32, b'short'):
            with self.assertRaisesRegex(ReconciliationBlocked, 'custody_backup_key_invalid'):
                ORIGINAL_ARCHIVE(key)

    def test_ciphertext_changed(self):
        path = self.snapshots['observed']['path']
        path.write_bytes(path.read_bytes()[:-1] + b'x')
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_archive_changed'):
            ORIGINAL_ARCHIVE(b'k' * 32)

    def test_missing_archive(self):
        self.snapshots['observed']['path'].unlink()
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_archive_unavailable'):
            ORIGINAL_ARCHIVE(b'k' * 32)

    def test_symlink_archive(self):
        path = self.snapshots['observed']['path']
        target = path.with_suffix('.target')
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_archive_unavailable'):
            ORIGINAL_ARCHIVE(b'k' * 32)

    def test_member_hash_mismatch(self):
        self.snapshots['observed']['plaintext_member_sha256'] = '0' * 64
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_archive_member_changed'):
            ORIGINAL_ARCHIVE(b'k' * 32)

    def test_restored_snapshot_mismatch(self):
        self.snapshots['observed']['source_fingerprint'] = '0' * 64
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_archived_snapshot_changed'):
            ORIGINAL_ARCHIVE(b'k' * 32)

    def test_malformed_reviewed_pin_precedes_archive_restore(self):
        self.snapshots['observed']['ciphertext_sha256'] = 'bad'
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_reviewed_pins_missing'):
            ORIGINAL_ARCHIVE(b'k' * 32)


class CandidateTests(unittest.TestCase):
    """Frozen semantic builder runs on generated synthetic data, never services."""
    def setUp(self):
        from cloud.activation import _time
        self.exported, trusted, _, _, _ = fixture()
        source = list(trusted.rows.values())
        previous = next(row for row in build_index(source, _time('2026-10-23T00:00:00Z'))
                        if row['kind'] == 'index_manifests')
        before = deepcopy(trusted.snapshot)
        before['pages'].append(entry(previous))
        self.before = SnapshotState(before)
        jobs = [record('job:synthetic-' + str(i), 'jobs', {'company': 'synthetic-' + str(i),
                'title': 'data engineer', 'url': 'https://example.test/jobs/' + str(i)}) for i in range(300)]
        source += jobs
        rebuilt = build_index(source, _time('2026-10-24T03:16:26Z'), previous_manifest=previous,
            source_snapshot_hash={'hash': subject.snapshot_hash(source), 'complete': True, 'conflicts': []})
        self.manifest = next(row for row in rebuilt if row['kind'] == 'index_manifests')
        children = [row for row in rebuilt if row['kind'] != 'index_manifests']
        after = deepcopy(before)
        after['pages'].extend(entry(row) for row in [*jobs, *children])
        self.after = SnapshotState(after)
        length = len(canonical_json(self.manifest))
        self.proof = {'diagnostic_candidate': {'clock': '2026-10-24T03:16:26Z',
            'source_records': len(source), 'source_hash': subject.snapshot_hash(source),
            'generated_children': len(children), 'children_matching_current': len(children),
            'child_core_set_sha256': content_hash({row['id']: content_hash(core(row)) for row in children})},
            'manifest_capacity': {'diagnostic_candidate_manifest': {'canonical_characters': length,
                'rich_text_chunks': (length + 1999) // 2000}}}

    def test_frozen_builder_children_match_without_claiming_original_clock(self):
        self.assertIsNone(subject._candidate_evidence(self.before, self.after, self.exported, {}, self.proof))
        self.assertGreater(self.proof['manifest_capacity']['diagnostic_candidate_manifest']['rich_text_chunks'], 100)
        self.assertIsNone(self.after.get(self.manifest['id']))

    def test_candidate_manifest_present_blocks(self):
        source = deepcopy(self.after.snapshot)
        source['pages'].append(entry(self.manifest))
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_candidate_manifest_unexpected'):
            subject._candidate_evidence(self.before, SnapshotState(source), self.exported, {}, self.proof)

    def test_changed_child_blocks(self):
        source = deepcopy(self.after.snapshot)
        row = next(row for row in self.after.rows.values() if row['kind'] == 'entity_projections')
        source['pages'] = [item for item in source['pages'] if item['page']['id'] != self.after.page_ids[row['id']]]
        changed = record(row['id'], row['kind'], {**row['payload'], 'synthetic_tamper': True}, row['status'])
        source['pages'].append(entry(changed))
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_candidate_children_changed'):
            subject._candidate_evidence(self.before, SnapshotState(source), self.exported, {}, self.proof)

    def test_unexplained_generated_core_blocks(self):
        source = deepcopy(self.after.snapshot)
        source['pages'].append(entry(record('projection:unexplained', 'entity_projections', {})))
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_unexplained_generated_record'):
            subject._candidate_evidence(self.before, SnapshotState(source), self.exported, {}, self.proof)

    def test_capacity_or_source_proof_change_blocks(self):
        changed = deepcopy(self.proof)
        changed['manifest_capacity']['diagnostic_candidate_manifest']['canonical_characters'] += 1
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_candidate_capacity_changed'):
            subject._candidate_evidence(self.before, self.after, self.exported, {}, changed)
        changed = deepcopy(self.proof)
        changed['diagnostic_candidate']['source_hash'] = '0' * 64
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_candidate_children_changed'):
            subject._candidate_evidence(self.before, self.after, self.exported, {}, changed)



if __name__ == '__main__':
    unittest.main()
