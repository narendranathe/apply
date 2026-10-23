"""Synthetic fixed-incident V3 gates; no credentials, network or private data."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cloud import contained_recovery as subject
from cloud.coordination_quarantine import make_quarantine
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash
from cloud.startup_reconciliation import SnapshotState
from tests.test_startup_reconciliation import entry

CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': subject.old.REPOSITORY,
    'actor': 'narendranathe', 'triggering_actor': 'narendranathe', 'event': 'workflow_dispatch',
    'cloud_enabled': 'false', 'commit': 'b' * 40, 'run_id': '900'}
SOURCES = {path: ('synthetic protocol ' + path).encode() for path in subject.PROTOCOL_FILES}
HASHES = {path: hashlib.sha256(value).hexdigest() for path, value in SOURCES.items()}


def run(identity=900, *, status='in_progress', conclusion=None):
    return {'id': identity, 'head_sha': CONTEXT['commit'], 'head_branch': 'main',
        'status': status, 'conclusion': conclusion, 'run_attempt': 1, 'display_title': subject.TITLE,
        'event': 'workflow_dispatch', 'actor': {'login': 'narendranathe'},
        'triggering_actor': {'login': 'narendranathe'}, 'path': subject.WORKFLOW,
        'created_at': '2026-10-22T15:00:' + ('00' if identity == 900 else '10') + 'Z',
        'run_started_at': '2026-10-22T15:00:20Z', 'updated_at': '2026-10-22T15:01:00Z'}


def jobs(identity, active=False, success=True):
    state = 'in_progress' if active else 'completed'
    result = None if active else 'success' if success else 'failure'
    names = [subject.OPERATION, 'Encrypted source snapshot and aggregate evidence', 'Remove plaintext staging']
    steps = [{'name': name, 'number': pos + 1, 'status': state,
              'conclusion': result, 'started_at': '2026-10-22T15:00:20Z'} for pos, name in enumerate(names)]
    return {'total_count': 1, 'jobs': [{'id': identity + 1000, 'run_id': identity,
        'head_sha': CONTEXT['commit'], 'run_attempt': 1, 'name': 'reconcile', 'labels': ['ubuntu-latest'],
        'status': state, 'conclusion': result, 'steps': steps}]}


class GitHub:
    def __init__(self, identity=900):
        self.identity, self.calls = identity, []
        self.runs = {identity: run(identity)}
        self.jobs = {identity: jobs(identity, active=True)}
        self.artifacts = {}
        self.history = None

    def verify_main(self, head):
        subject.require(head == CONTEXT['commit'], 'synthetic_main_changed')

    def fetch(self, path):
        self.calls.append(path)
        if path.startswith('/contents/'):
            name = path.removeprefix('/contents/').split('?')[0]
            return {'encoding': 'base64', 'content': base64.b64encode(SOURCES[name]).decode()}
        if path.startswith('/actions/workflows/'):
            values = deepcopy(self.history if self.history is not None else list(self.runs.values()))
            page = int(path.rsplit('page=', 1)[1])
            return {'total_count': len(values), 'workflow_runs': values[(page - 1) * 100:page * 100]}
        if path.startswith('/actions/runs/'):
            identity = int(path.removeprefix('/actions/runs/').split('/')[0])
            if '/jobs?' in path:
                return deepcopy(self.jobs[identity])
            if '/artifacts?' in path:
                return deepcopy(self.artifacts.get(identity, {'total_count': 0, 'artifacts': []}))
            return deepcopy(self.runs[identity])
        raise AssertionError('unexpected synthetic route')

    def successful(self, identity=900):
        self.runs[identity] = run(identity, status='completed', conclusion='success')
        self.jobs[identity] = jobs(identity)
        self.artifacts[identity] = {'total_count': 1, 'artifacts': [{'id': identity + 2000,
            'name': 'reconciliation-evidence-' + str(identity), 'expired': False, 'size_in_bytes': 99,
            'digest': 'sha256:' + 'e' * 64,
            'workflow_run': {'id': identity, 'head_sha': CONTEXT['commit']}}]}


class Store:
    def __init__(self, source):
        self.source, self.data_source_id = deepcopy(source), source['data_source_id']
        self.writes, self.delays, self.reads = [], [], []
        self.sleeper = self.delays.append
        self.snapshot_calls, self.hook, self.after_put_reads, self.fail_at = 0, None, [], None

    def snapshot_all(self):
        self.snapshot_calls += 1
        if self.hook:
            self.hook(self)
        return deepcopy(self.source)

    def get(self, identity):
        self.reads.append(identity)
        if self.writes and identity == self.writes[-1] and self.after_put_reads:
            value = self.after_put_reads.pop(0)
            if value != 'actual':
                return deepcopy(value)
        return SnapshotState(self.source).get(identity)

    def put(self, row):
        self.writes.append(row['id'])
        self.source['pages'].append(entry(row))
        if self.fail_at == len(self.writes):
            raise AmbiguousWriteError('synthetic uncertain write')
        return deepcopy(row)


def fixture():
    hold = make_quarantine({'schema': 'apply-coordination-quarantine/v1', 'active': True,
        'classification': 'effect_unknown', 'identity': {'profile_urls': ['https://www.linkedin.com/in/synthetic']},
        'provenance': {key: 'a' * 64 for key in ('claim_sha256', 'task_sha256', 'resource_sha256')},
        'observed_at': '2026-10-21T20:13:09Z', 'evidence_refs': ['synthetic-evidence']})
    hold['id'] = subject.HOLD_ID
    observations = [record(identity, 'audit_events', {'synthetic': pos}, 'history_incomplete')
                    for pos, identity in enumerate(subject.RESERVED_RECORD_IDS)]
    plan = SimpleNamespace(records=(hold, *observations), proof={'synthetic_plan': True})
    originals = [record('writer-resolution:' + identity, 'audit_events', {
        'schema': 'synthetic-original', 'github_run_id': identity,
        'external_effects_reconciled': False, 'managed_cloud_effects_reconciled': True,
        'native_delivery_verified': False, 'native_history_incomplete': True, 'index_complete': False,
        'retry_external_effects': False, 'evidence': {'synthetic': True}}, 'synthetic_reconciled')
        for identity in (*subject.FAILURES[1:], subject.old.FAILED)]
    source = {'schema': 'notion-data-source-snapshot/v1', 'complete': True, 'data_source_id': 'abc123',
        'data_source': {'id': 'abc123', 'properties': {}}, 'captured_at': '2026-10-22T14:00:00Z',
        'pages': [entry(hold), {'page': {'id': 'synthetic-native', 'properties': {}}, 'blocks': []}]}
    return SnapshotState(source), plan, originals, source


class ContainedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.partial, self.plan, self.originals, self.source = fixture()
        self.native_fixed_impl = subject._native_fixed
        self.patches = [patch.object(subject, 'require_reviewed_pins'),
            patch.object(subject, '_prepare', return_value=(self.partial, self.plan, self.originals)),
            patch.object(subject, '_native_fixed'), patch.object(subject, '_original_attempts'),
            patch.object(subject, '_local_sources', return_value=HASHES),
            patch.object(subject, 'RESERVED_CORE_HASHES', {row['id']: content_hash(row) for row in self.plan.records[1:]}),
            patch.object(subject, 'HOLD_CORE_HASH', content_hash(self.plan.records[0]))]
        for value in self.patches:
            value.start()
            self.addCleanup(value.stop)
        templates = subject._records(self.plan, self.originals, None)
        value = patch.object(subject, 'TEMPLATE_CORE_HASHES', {row['id']: content_hash(row) for row in templates})
        value.start()
        self.addCleanup(value.stop)
        self.github, self.store = GitHub(), Store(self.source)
        folder = tempfile.TemporaryDirectory(prefix='contained-synthetic-receipt-')
        self.addCleanup(folder.cleanup)
        self.receipt_path, self.archive_path = Path(folder.name) / 'receipt.json', Path(folder.name) / 'archive.aesgcm'
        for name, value in [('RECEIPT_PATH', self.receipt_path), ('COMPLETE_ARCHIVE_PATH', self.archive_path)]:
            patched = patch.object(subject, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def receipt(self, rows, identity=900):
        self.archive_path.write_bytes(subject.MAGIC + b'synthetic encrypted archive fixture' * 2)
        value = {'schema': subject.RECEIPT_SCHEMA, 'protocol_schema': subject.SCHEMA,
            'execution_binding': rows[0]['payload']['verified_correction_run_binding'],
            'verification_run': {key: self.github.runs[identity][key] for key in subject.old.RUN_FIELDS},
            'verification_job_id': self.github.jobs[identity]['jobs'][0]['id'],
            'verification_jobs_sha256': content_hash(self.github.jobs[identity]),
            'verification_steps_sha256': content_hash(self.github.jobs[identity]['jobs'][0]['steps']),
            'artifact_id': identity + 2000, 'artifact_zip_sha256': 'e' * 64,
            'archive_sha256': hashlib.sha256(self.archive_path.read_bytes()).hexdigest(), 'member_sha256': 'a' * 64,
            'complete_source_fingerprint': 'b' * 64, 'complete_pages': 2902, 'complete_core_records': 2655,
            'prior_source_fingerprint': subject.STATIC_PINS['post_failure_snapshot_fingerprint'],
            'prior_pages_unchanged': 2898, 'prior_core_unchanged': 2651, 'historical_records_unchanged': 757,
            'ready_core_sha256': subject.old.READY_CORE_HASH,
            'cohort_whole_core_sha256': {subject.HOLD_ID: subject.HOLD_CORE_HASH,
                **{row['id']: content_hash(row) for row in rows}},
            'independent_proof_sha256': 'c' * 64, 'independently_authenticated': True,
            'external_effects': 0, 'native_delivery_verified': False,
            'external_effects_reconciled': False, 'index_complete': False}
        value['receipt_content_sha256'] = content_hash(value)
        self.receipt_path.write_text(json.dumps(value))
        return value

    def execute(self, context=None):
        return subject.reconcile(self.store, {}, self.github, context or CONTEXT, b'x' * 32)

    def expected(self):
        binding, _ = subject._current(self.github, CONTEXT)
        return subject._records(self.plan, self.originals, binding)

    def test_exact_four_appends_lead_last_never_old_observations(self):
        result = self.execute()
        self.assertEqual(self.store.writes, [subject.BUNDLE_ID, *['writer-resolution:' + value
            for value in (*subject.FAILURES[1:], subject.old.FAILED)]])
        self.assertFalse(set(self.store.writes) & subject.RESERVED_RECORD_IDS)
        self.assertEqual(result['new_records'], 4)
        self.assertEqual(result['cohort_records'], 5)
        self.assertFalse(result['external_effects_reconciled'])
        self.assertFalse(result['native_delivery_verified'])

    def test_repeat_is_readonly_and_preserves_original_execution_binding(self):
        self.execute()
        self.store.writes.clear()
        self.github = GitHub(identity=901)
        self.github.runs[900] = run(900, status='completed', conclusion='failure')
        self.github.jobs[900] = jobs(900, success=False)
        result = self.execute({**CONTEXT, 'run_id': '901'})
        self.assertEqual(self.store.writes, [])
        self.assertTrue(result['unchanged'])
        self.assertEqual(result['execution_run_id'], '900')
        self.assertEqual(result['verification_run_id'], '901')

    def test_readonly_repeat_rejects_forged_or_rerun_original_executor(self):
        self.execute()
        self.store.writes.clear()
        self.github = GitHub(identity=901)
        self.github.runs[900] = run(900, status='completed', conclusion='failure')
        self.github.runs[900]['run_attempt'] = 2
        self.github.jobs[900] = jobs(900, success=False)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'executor_native_attempt'):
            self.execute({**CONTEXT, 'run_id': '901'})
        self.assertEqual(self.store.writes, [])

    def test_every_proper_new_partial_stops_and_never_finishes_batch(self):
        rows = self.expected()
        for count in (1, 2, 3):
            with self.subTest(count=count):
                self.store = Store(self.source)
                self.store.source['pages'] += [entry(row) for row in rows[:count]]
                with self.assertRaises(subject.ReconciliationBlocked):
                    self.execute()
                self.assertEqual(self.store.writes, [])

    def test_empty_after_any_previous_invocation_does_not_authorize_recreation(self):
        for conclusion in ('failure', 'cancelled', 'success'):
            with self.subTest(conclusion=conclusion):
                prior = run(899, status='completed', conclusion=conclusion)
                prior['created_at'] = '2026-10-22T14:00:00Z'
                self.github.history = [prior, self.github.runs[900]]
                with self.assertRaisesRegex(subject.ReconciliationBlocked, 'prior_attempt'):
                    self.execute()
                self.assertEqual(self.store.writes, [])

    def test_invalid_context_rejects_before_any_github_or_store_request(self):
        for change in ({'cloud_enabled': 'true'}, {'actions': False}, {'actor': 'other'},
                       {'event': 'schedule'}, {'ref': 'refs/heads/other'}):
            with self.subTest(change=change):
                with self.assertRaises(subject.ReconciliationBlocked):
                    self.execute({**CONTEXT, **change})
                self.assertEqual(self.github.calls, [])
                self.assertEqual(self.store.snapshot_calls, 0)

    def test_changed_source_and_late_reserved_record_block_before_append(self):
        for change in ('extra_native', 'reserved'):
            with self.subTest(change=change):
                self.store = Store(self.source)
                self.store.source['pages'].append(entry(self.plan.records[1]) if change == 'reserved'
                    else {'page': {'id': 'extra', 'properties': {}}, 'blocks': []})
                with self.assertRaises(subject.ReconciliationBlocked):
                    self.execute()
                self.assertEqual(self.store.writes, [])

    def test_second_full_snapshot_is_a_barrier_before_any_resolution(self):
        def change(store):
            if store.snapshot_calls == 2:
                store.source['pages'].append(entry(self.plan.records[1]))
        self.store.hook = change
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'reserved_id'):
            self.execute()
        self.assertEqual(self.store.writes, [subject.BUNDLE_ID])

    def test_original_failed_attempts_are_rechecked_after_full_bundle_snapshot(self):
        with patch.object(subject, '_original_attempts', side_effect=subject.ReconciliationBlocked(
                'contained_original_failed_attempt_changed')) as verify:
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'original_failed_attempt'):
                self.execute()
        verify.assert_called_once()
        self.assertEqual(self.store.snapshot_calls, 2)
        self.assertEqual(self.store.writes, [subject.BUNDLE_ID])

    def test_transport_or_readback_ambiguity_never_replays_a_create(self):
        self.store.fail_at = 1
        with self.assertRaises(AmbiguousWriteError):
            self.execute()
        self.assertEqual(self.store.writes, [subject.BUNDLE_ID])

    def test_absence_only_readback_poll_and_exhaustion(self):
        self.store.after_put_reads = [None, None, 'actual']
        self.execute()
        self.assertEqual(self.store.delays, [1, 2])
        self.assertEqual(self.store.writes.count(subject.BUNDLE_ID), 1)
        self.store = Store(self.source)
        self.store.after_put_reads = [None] * 4
        with self.assertRaises(AmbiguousWriteError):
            self.execute()
        self.assertEqual(self.store.writes, [subject.BUNDLE_ID])
        self.assertEqual(self.store.delays, [1, 2, 4])

    def test_nonabsent_mismatch_is_not_polled(self):
        self.store.after_put_reads = [record('wrong', 'audit_events', {}, 'wrong')]
        with self.assertRaises(AmbiguousWriteError):
            self.execute()
        self.assertEqual(self.store.delays, [])
        self.assertEqual(self.store.writes, [subject.BUNDLE_ID])

    def test_templates_reject_payload_schema_peer_and_forged_source_changes(self):
        rows = self.expected()
        self.assertTrue(subject._template_valid(rows))
        for mode in ('schema', 'delivery', 'peer', 'extra'):
            altered = deepcopy(rows)
            if mode == 'schema':
                altered[1]['payload']['schema'] = 'foreign-v3'
            elif mode == 'delivery':
                altered[0]['payload']['native_delivery_verified'] = True
            elif mode == 'peer':
                altered[2]['payload']['evidence']['correction_epoch']['bundle_whole_core_sha256'] = 'f' * 64
            else:
                altered[0]['payload']['extra'] = True
            altered = [record(row['id'], row['kind'], row['payload'], row['status']) for row in altered]
            self.assertFalse(subject._template_valid(altered))

    def test_complete_attempt_history_rejects_incomplete_and_duplicate_pages(self):
        values = [run(i) for i in range(1, 102)]
        for value in values:
            value['display_title'] = 'history:snapshot'
        self.github.history = values
        self.assertEqual(len(subject._history(self.github.fetch)), 101)
        self.github.history = [values[0], values[0]]
        with self.assertRaises(subject.ReconciliationBlocked):
            subject._history(self.github.fetch)
        with self.assertRaises(subject.ReconciliationBlocked):
            subject._history(lambda path: {'total_count': 2, 'workflow_runs': [values[0]]})

    def test_terminal_and_incomplete_history_markers_never_hide_prior_attempt(self):
        for marker in ('_next_page', 'next_page', 'has_more', 'next_cursor', 'incomplete_results'):
            with self.subTest(marker=marker):
                value = {'total_count': 1, 'workflow_runs': [run()], marker: True}
                with self.assertRaises(subject.ReconciliationBlocked):
                    subject._history(lambda path: value)
        values = [run(i) for i in range(1, 102)]
        def paginate(path):
            page = int(path.rsplit('page=', 1)[1])
            return {'total_count': 101, 'workflow_runs': values[:100] if page == 1 else values[100:],
                    '_next_page': 2 if page == 1 else None}
        self.assertEqual(len(subject._history(paginate)), 101)

    def test_successful_backup_is_required_even_when_all_five_records_exist(self):
        rows = self.expected()
        binding = rows[0]['payload']['verified_correction_run_binding']
        self.github.runs[900] = run(900, status='completed', conclusion='failure')
        self.github.jobs[900] = jobs(900, success=False)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'durable_verification_receipt'):
            subject._successful_verification(self.github.fetch, binding, rows)
        self.github.successful(901)
        self.receipt(rows, 901)
        self.assertTrue(subject._successful_verification(self.github.fetch, binding, rows))
        # The durable authenticated receipt survives hosted artifact expiration.
        self.github.artifacts[901] = {'total_count': 0, 'artifacts': []}
        self.assertTrue(subject._successful_verification(self.github.fetch, binding, rows))
        self.archive_path.unlink()
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'durable_verification_receipt'):
            subject._successful_verification(self.github.fetch, binding, rows)

    def test_executor_rerun_or_forged_protocol_hash_never_clears(self):
        rows = self.expected()
        binding = rows[0]['payload']['verified_correction_run_binding']
        self.github.successful()
        self.receipt(rows)
        self.github.runs[900]['run_attempt'] = 2
        with self.assertRaises(subject.ReconciliationBlocked):
            subject._successful_verification(self.github.fetch, binding, rows)
        self.github.successful()
        binding['source_sha256'][subject.PROTOCOL_FILES[0]] = 'f' * 64
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'source_changed'):
            subject._successful_verification(self.github.fetch, binding, rows)

    def test_durable_receipt_rejects_changed_cipher_cohort_flags_and_self_hash(self):
        rows = self.expected()
        binding = rows[0]['payload']['verified_correction_run_binding']
        self.github.successful()
        for mode in ('cipher', 'cohort', 'delivery', 'self_hash', 'extra'):
            with self.subTest(mode=mode):
                receipt = self.receipt(rows)
                if mode == 'cipher': self.archive_path.write_bytes(b'changed fixture')
                elif mode == 'cohort': receipt['cohort_whole_core_sha256'][subject.HOLD_ID] = 'f' * 64
                elif mode == 'delivery': receipt['native_delivery_verified'] = True
                elif mode == 'self_hash': receipt['receipt_content_sha256'] = 'f' * 64
                else: receipt['extra'] = True
                if mode != 'self_hash':
                    receipt['receipt_content_sha256'] = content_hash({k: v for k, v in receipt.items()
                        if k != 'receipt_content_sha256'})
                self.receipt_path.write_text(json.dumps(receipt))
                with self.assertRaises(subject.ReconciliationBlocked):
                    subject._receipt(rows, binding)

    def installed_cohort(self):
        rows = self.expected()
        self.store.source['pages'] += [entry(row) for row in rows]
        self.github.successful()
        self.receipt(rows)
        return rows

    def matches(self, marker, identity=None):
        return subject.contained_resolution_matches(identity or subject.old.FAILED, marker,
            self.github.fetch, subject.old.REPOSITORY, read_record=self.store.get)

    def test_startup_requires_every_peer_hold_and_absence_of_both_reserved_ids(self):
        rows = self.installed_cohort()
        marker = rows[-1]
        self.assertTrue(self.matches(marker))
        source = deepcopy(self.store.source)
        for identity in (subject.HOLD_ID, *[row['id'] for row in rows]):
            with self.subTest(missing=identity):
                self.store.source = deepcopy(source)
                state = SnapshotState(self.store.source)
                page = state.page_ids[identity]
                self.store.source['pages'] = [item for item in self.store.source['pages'] if item['page']['id'] != page]
                self.assertFalse(self.matches(marker))
        for reserved in self.plan.records[1:]:
            self.store.source = deepcopy(source)
            self.store.source['pages'].append(entry(reserved))
            self.assertFalse(self.matches(marker))

    def test_startup_rejects_foreign_schema_wrong_marker_and_changed_native_attempt(self):
        rows = self.installed_cohort()
        marker = rows[-1]
        altered = record(marker['id'], marker['kind'], {**marker['payload'], 'schema': 'foreign-v3'}, marker['status'])
        self.assertFalse(self.matches(altered))
        self.assertFalse(self.matches(rows[1]))
        self.assertFalse(subject.contained_resolution_matches('future', marker, self.github.fetch,
            subject.old.REPOSITORY, read_record=self.store.get))
        with patch.object(subject, '_original_attempts', side_effect=subject.ReconciliationBlocked('changed')):
            self.assertFalse(self.matches(marker))
        self.github.runs[900]['run_attempt'] = 2
        self.assertFalse(self.matches(marker))

    def test_native_maintenance_and_partial_snapshot_are_fresh_and_artifact_free_at_startup(self):
        pins = deepcopy(subject.STATIC_PINS)
        failed_id = pins['failed_maintenance_run']['id']
        partial_id = int(subject.PARTIAL_RUN)
        for identity, key, success in [(failed_id, 'failed_maintenance_run', False),
                                      (partial_id, 'post_failure_snapshot_run', True)]:
            value = deepcopy(pins[key])
            value.update(actor={'login': 'narendranathe'}, triggering_actor={'login': 'narendranathe'})
            self.github.runs[identity] = value
            value = jobs(identity, success=success)
            value['jobs'][0]['head_sha'] = pins[key]['head_sha']
            self.github.jobs[identity] = value
        pins['failed_maintenance_jobs_sha256'] = content_hash(self.github.jobs[failed_id])
        pins['failed_maintenance_steps_sha256'] = content_hash(self.github.jobs[failed_id]['jobs'][0]['steps'])
        pins['post_failure_snapshot_jobs_sha256'] = content_hash(self.github.jobs[partial_id])
        pins['post_failure_snapshot_steps_sha256'] = content_hash(self.github.jobs[partial_id]['jobs'][0]['steps'])
        original_sources = {name: ('synthetic original ' + name).encode()
                            for name in pins['original_maintenance_source_sha256']}
        pins['original_maintenance_source_sha256'] = {name: hashlib.sha256(value).hexdigest()
                                                     for name, value in original_sources.items()}
        with patch.object(subject, 'STATIC_PINS', pins), patch.dict(SOURCES, original_sources), \
             patch.object(subject, 'FAILED_JOB_HASH', content_hash(self.github.jobs[failed_id]['jobs'][0])), \
             patch.object(subject, 'PARTIAL_JOB_HASH', content_hash(self.github.jobs[partial_id]['jobs'][0])), \
             patch.object(subject, '_source_capture_metadata'):
            # Undo this test's broad fixture mock; invoke the real implementation.
            native_fixed = self.native_fixed_impl
            native_fixed(self.github.fetch)
            self.assertFalse(any('/artifacts?' in path for path in self.github.calls))
            self.github.runs[failed_id]['run_attempt'] = 2
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'maintenance_metadata'):
                native_fixed(self.github.fetch)
            self.github.runs[failed_id]['run_attempt'] = 1
            self.github.jobs[partial_id]['jobs'][0]['steps'][0]['conclusion'] = 'failure'
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'snapshot_native_metadata'):
                native_fixed(self.github.fetch)


if __name__ == '__main__':
    unittest.main()
