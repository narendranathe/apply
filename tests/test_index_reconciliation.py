"""Synthetic pinned index recovery; no operational stores or provider access."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import cloud.index_reconciliation as subject
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash
from scripts.encrypted_backup import create_backup
from tests.test_startup_reconciliation import entry, states

CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': subject.REPOSITORY,
    'actor': 'narendranathe', 'triggering_actor': 'narendranathe', 'event': 'workflow_dispatch',
    'cloud_enabled': 'false', 'commit': 'b' * 40, 'run_id': '999'}
SOURCES = {path: ('synthetic source ' + path).encode() for path in subject.SOURCE_HASHES}
SOURCES['config/cloud.json'] = b'{"applications":{"employers":{}}}'
DELTA = {'schema': 'synthetic-reviewed-index-proof/v1', 'index_complete': False,
         'new_attempts': 0, 'new_receipts': 0, 'new_outreach_events': 0}


class GitHub:
    def __init__(self):
        self.run = deepcopy(subject.FAILED_RUN)
        self.jobs = {'total_count': 1, 'jobs': [{'id': subject.FAILED_JOB, 'name': 'orchestrate',
            'status': 'completed', 'conclusion': 'failure', 'labels': ['ubuntu-latest'],
            'steps': [{'number': 7, 'name': 'Run one coordinated operation', 'status': 'completed', 'conclusion': 'failure'}]}]}
        self.logs = (json.dumps(subject.DIAGNOSTIC) + '\n').encode()
        self.sources, self.calls = deepcopy(SOURCES), []
        self.maintenance = {'id': 999, 'head_sha': CONTEXT['commit'], 'head_branch': 'main', 'status': 'in_progress',
            'conclusion': None, 'event': 'workflow_dispatch', 'run_attempt': 1, 'display_title': 'history:reconcile-index',
            'actor': {'login': 'narendranathe'}, 'triggering_actor': {'login': 'narendranathe'},
            'path': '.github/workflows/history-reconciliation.yml'}
        self.maintenance_jobs = {'total_count': 1, 'jobs': [{'name': 'reconcile', 'status': 'in_progress',
            'conclusion': None, 'labels': ['ubuntu-latest'], 'steps': [{'name': 'Reconcile without replaying historical effects',
                'status': 'in_progress', 'conclusion': None}]}]}
        self.workflow = (b'concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n'
            b'          elif [ "$IMPORT_MODE" = \'reconcile-index\' ]; then\n            python scripts/reconcile_index.py\n')
        self.after_jobs = None

    def verify_main(self, commit): subject.require(commit == CONTEXT['commit'], 'deployed_private_main_changed')

    def fetch(self, path):
        self.calls.append(path)
        if path == '/actions/runs/999': return deepcopy(self.maintenance)
        if path.startswith('/actions/runs/999/jobs?'): return deepcopy(self.maintenance_jobs)
        if path == '/contents/.github/workflows/history-reconciliation.yml?ref=' + CONTEXT['commit']:
            return {'encoding': 'base64', 'content': base64.b64encode(self.workflow).decode()}
        if path.startswith('/contents/'):
            name = path.removeprefix('/contents/').split('?')[0]
            return {'encoding': 'base64', 'content': base64.b64encode(self.sources[name]).decode()}
        if path == '/actions/runs/' + subject.FAILED: return deepcopy(self.run)
        if path.startswith('/actions/runs/' + subject.FAILED + '/jobs?'):
            result = deepcopy(self.jobs)
            if self.after_jobs: self.after_jobs()
            return result
        raise AssertionError('unexpected synthetic route')

    def download_path(self, path):
        assert path == '/actions/jobs/' + str(subject.FAILED_JOB) + '/logs'
        return self.logs


class Store:
    def __init__(self, source):
        self.source, self.data_source_id = deepcopy(source), source['data_source_id']
        self.writes, self.mode = [], None

    def snapshot_all(self): return deepcopy(self.source)

    def get(self, identity):
        row = subject.SnapshotState(self.source).get(identity)
        if row and identity == 'writer-resolution:' + subject.FAILED:
            if self.mode == 'missing_readback': return None
            if self.mode == 'changed_readback': row['status'] = 'changed'
        return row

    def put(self, row):
        self.writes.append(deepcopy(row))
        self.source['pages'].append(entry(row))
        if self.mode == 'raise': raise AmbiguousWriteError('private synthetic upstream error')
        return None if self.mode == 'missing_write' else deepcopy(row)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.baseline, _, self.before, self.after = states()
        self.before['captured_at'], self.after['captured_at'] = '2026-10-20T10:00:00Z', '2026-10-20T15:00:00Z'
        self.github = GitHub()
        self.constants = patch.multiple(subject,
            REVIEWED_DELTA_PROOF_HASH=content_hash(DELTA),
            FAILED_STEPS_HASH=content_hash(self.github.jobs['jobs'][0]['steps']),
            FAILED_LOG_HASH=subject.sha(self.github.logs),
            SOURCE_HASHES={path: subject.sha(body) for path, body in SOURCES.items()})
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def snapshot(self, client, identity, key):
        before = identity == subject.BEFORE
        state = subject.SnapshotState(self.before if before else self.after)
        return state, {'run_id': identity, 'artifact_id': subject.BEFORE_ARTIFACT_ID if before else subject.AFTER_ARTIFACT_ID,
            'artifact_zip_sha256': 'a' * 64,
            'archive_sha256': subject.BEFORE_CIPHER_HASH if before else subject.AFTER_CIPHER_HASH,
            'member_sha256': subject.BEFORE_MEMBER_HASH if before else subject.AFTER_MEMBER_HASH,
            'state_sha256': subject.BEFORE_FINGERPRINT if before else subject.AFTER_FINGERPRINT,
            'pages': subject.BEFORE_PAGES if before else subject.AFTER_PAGES}

    def reconcile(self, store=None, context=None):
        store = store or Store(self.after)
        with patch.object(subject, 'read_snapshot', side_effect=self.snapshot), \
             patch.object(subject, 'verify_delta_and_index', return_value=DELTA), \
             patch.object(subject.Path, 'read_bytes', return_value=self.github.workflow), \
             patch('cloud.submission.HTTPTransport.request', side_effect=AssertionError('provider request forbidden')):
            result = subject.reconcile(store, self.baseline, self.github, context or CONTEXT, b'x' * 32)
        return result, store

    def test_one_honest_resolution_only_and_exact_replay_is_read_only(self):
        result, store = self.reconcile()
        self.assertTrue(result['verified'])
        self.assertFalse(result['index_complete'])
        self.assertTrue(result['business_lanes_completed'])
        self.assertEqual(len(store.writes), 1)
        row = store.writes[0]
        self.assertEqual(row['id'], 'writer-resolution:' + subject.FAILED)
        self.assertEqual(row['status'], 'reconciled_business_complete_index_incomplete')
        self.assertFalse(row['payload']['retry_external_effects'])
        self.assertFalse(row['payload']['index_complete'])
        self.assertEqual(row['kind'], 'audit_events')
        replay, _ = self.reconcile(store)
        self.assertTrue(replay['unchanged'])
        self.assertEqual(len(store.writes), 1)
        self.assertTrue(subject.index_resolution_matches(subject.FAILED, row, self.github.fetch, subject.REPOSITORY))

    def test_empty_pin_or_wrong_context_never_appends(self):
        with patch.object(subject, 'REVIEWED_DELTA_PROOF_HASH', ''):
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'review_pending'): self.reconcile()
        for key, value in [('actions', False), ('actor', 'other'), ('triggering_actor', 'other'),
                           ('event', 'schedule'), ('ref', 'refs/heads/other'), ('cloud_enabled', 'true')]:
            store = Store(self.after)
            with self.subTest(key=key), self.assertRaisesRegex(subject.ReconciliationBlocked, 'owner_disabled'):
                self.reconcile(store, {**CONTEXT, key: value})
            self.assertEqual(store.writes, [])

    def test_partial_changed_or_later_attempt_run_job_source_and_logs_rejected(self):
        for mode in ('attempt', 'commit', 'job', 'partial', 'steps', 'source', 'diagnostic', 'duplicate_log', 'invalid_log', 'changing', 'bool_after'):
            self.github = GitHub()
            if mode == 'attempt': self.github.run['run_attempt'] = 2
            if mode == 'commit': self.github.run['head_sha'] = 'c' * 40
            if mode == 'job': self.github.jobs['jobs'][0]['id'] += 1
            if mode == 'partial': self.github.jobs['_next_page'] = True
            if mode == 'steps': self.github.jobs['jobs'][0]['steps'][0]['conclusion'] = 'success'
            if mode == 'source': self.github.sources['cloud/agent_worker.py'] += b'changed'
            if mode == 'diagnostic': self.github.logs = b'{"status":"blocked","cloud_operation":"run_applications"}\n'
            if mode == 'duplicate_log': self.github.logs *= 2
            if mode == 'invalid_log': self.github.logs = b'\xff'
            if mode == 'changing': self.github.after_jobs = lambda: self.github.run.update(run_attempt=2)
            if mode == 'bool_after': self.github.after_jobs = lambda: self.github.run.update(run_attempt=True)
            store = Store(self.after)
            with self.subTest(mode=mode), self.assertRaises(subject.ReconciliationBlocked): self.reconcile(store)
            self.assertFalse(store.writes)

    def test_fresh_snapshot_changes_or_partial_source_block_append(self):
        for mode in ('new', 'old', 'partial', 'duplicate'):
            store = Store(self.after)
            if mode == 'new': store.source['pages'].append(entry(record('later', 'audit_events', {})))
            if mode == 'old': store.source['pages'][2]['blocks'][0]['paragraph']['rich_text'][0]['text']['content'] = 'changed'
            if mode == 'partial': store.source['complete'] = False
            if mode == 'duplicate': store.source['pages'].append(deepcopy(store.source['pages'][0]))
            with self.subTest(mode=mode), self.assertRaises(Exception): self.reconcile(store)
            self.assertFalse(store.writes)

    def test_changed_delta_even_with_zero_effect_claim_stops(self):
        store = Store(self.after)
        with patch.object(subject, 'read_snapshot', side_effect=self.snapshot), \
             patch.object(subject, 'verify_delta_and_index', return_value={**DELTA, 'new_attempts': 1}), \
             patch.object(subject.Path, 'read_bytes', return_value=self.github.workflow):
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'delta_proof_changed'):
                subject.reconcile(store, self.baseline, self.github, CONTEXT, b'x' * 32)
        self.assertFalse(store.writes)

    def test_unknown_write_or_readback_stops_after_one_append(self):
        for mode in ('raise', 'missing_write', 'missing_readback', 'changed_readback'):
            store = Store(self.after)
            store.mode = mode
            with self.subTest(mode=mode), self.assertRaises(AmbiguousWriteError): self.reconcile(store)
            self.assertEqual(len(store.writes), 1)
        store.mode = None
        replay, _ = self.reconcile(store)
        self.assertTrue(replay['unchanged'])
        self.assertEqual(len(store.writes), 1)

    def test_existing_resolution_conflict_never_overwrites(self):
        store = Store(self.after)
        store.source['pages'].append(entry(record('writer-resolution:' + subject.FAILED, 'audit_events', {'other': True})))
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'existing_index_resolution_conflict'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_marker_hash_schema_scope_snapshot_delta_and_honesty_are_strict(self):
        _, store = self.reconcile()
        original = store.writes[0]
        for mode in ('hash', 'schema', 'scope', 'reason', 'retry', 'status', 'index', 'business', 'snapshot', 'delta',
                     'log', 'wrong_log_hash', 'artifact_id', 'bool_attempt'):
            row = deepcopy(original)
            if mode == 'hash': row['hash'] = '0' * 64
            if mode == 'schema': row['payload']['schema'] = 'apply-pinned-index-writer-resolution/v2'
            if mode == 'scope': row['payload']['github_run_id'] = 'other'
            if mode == 'reason': row['payload']['reason'] = 'manual_override'
            if mode == 'retry': row['payload']['retry_external_effects'] = True
            if mode == 'status': row['status'] = 'reconciled_no_business_execution'
            if mode == 'index': row['payload']['index_complete'] = True
            if mode == 'business': row['payload']['business_lanes_completed'] = False
            if mode == 'snapshot': row['payload']['evidence']['after']['state_sha256'] = '0' * 64
            if mode == 'delta': row['payload']['evidence']['delta']['new_receipts'] = 1
            if mode == 'log': row['payload']['evidence']['incident']['log_sha256'] = 'not a hash'
            if mode == 'wrong_log_hash': row['payload']['evidence']['incident']['log_sha256'] = '0' * 64
            if mode == 'artifact_id': row['payload']['evidence']['before']['artifact_id'] += 1
            if mode == 'bool_attempt': row['payload']['evidence']['incident']['failed_run']['run_attempt'] = True
            if mode != 'hash': row['hash'] = content_hash(row['payload'])
            with self.subTest(mode=mode):
                self.assertFalse(subject.index_resolution_matches(subject.FAILED, row, self.github.fetch, subject.REPOSITORY))
        self.assertFalse(subject.index_resolution_matches('other', original, self.github.fetch, subject.REPOSITORY))

    def test_runtime_matcher_rechecks_complete_jobs_and_metadata_before_after(self):
        _, store = self.reconcile()
        row = store.writes[0]
        for mode in ('attempt', 'partial', 'job', 'steps', 'changing', 'bool_attempt', 'bool_after'):
            self.github = GitHub()
            if mode == 'attempt': self.github.run['run_attempt'] = 2
            if mode == 'partial': self.github.jobs['has_more'] = True
            if mode == 'job': self.github.jobs['jobs'][0]['id'] += 1
            if mode == 'steps': self.github.jobs['jobs'][0]['steps'][0]['name'] = 'changed'
            if mode == 'changing': self.github.after_jobs = lambda: self.github.run.update(updated_at='2026-10-21T00:00:00Z')
            if mode == 'bool_attempt': self.github.run['run_attempt'] = True
            if mode == 'bool_after': self.github.after_jobs = lambda: self.github.run.update(run_attempt=True)
            with self.subTest(mode=mode):
                self.assertFalse(subject.index_resolution_matches(subject.FAILED, row, self.github.fetch, subject.REPOSITORY))


class ArchiveTests(unittest.TestCase):
    def test_local_cli_rejected_before_credentials_or_network(self):
        result = subprocess.run([sys.executable, 'scripts/reconcile_index.py'],
            env={'GITHUB_ACTIONS': 'false', 'NOTION_TOKEN': 'synthetic-private-token'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('synthetic-private-token', result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['reason'], 'owner_disabled_hosted_maintenance_required')

    def test_ciphertext_all_members_and_wrapper_bytes_are_authenticated(self):
        _, _, source, _ = states()
        source['captured_at'] = '2026-10-20T10:00:00Z'
        for identity in (subject.BEFORE, subject.AFTER):
            before = identity == subject.BEFORE
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                key = root / 'key.bin'; key.write_bytes(b'x' * 32); key.chmod(0o600)
                member = root / 'cloud-records.json'
                member.write_text(json.dumps({'schema': 'apply-cloud-snapshot/v1', 'exported_at': '2026-10-20T11:00:00Z', 'snapshot': source}))
                cipher = root / 'backup.aesgcm'
                create_backup({'cloud-records.json': member}, cipher, key_path=key, private_destination_verified=True)
                ciphertext = cipher.read_bytes()
                summary = {'operation': 'pinned_scheduled_writer_reconciliation' if before else 'encrypted_source_snapshot',
                    'verified': True, 'backup': {'restore_verified': True, 'sha256': subject.sha(ciphertext), 'records': len(source['pages'])}}
                zipped = io.BytesIO()
                with zipfile.ZipFile(zipped, 'w') as archive:
                    archive.writestr('maintenance-summary.json', json.dumps(summary))
                    archive.writestr('cloud-backup-' + identity + '.aesgcm', ciphertext)
                class Client:
                    def fetch(self, path):
                        if '/jobs?' in path: return {'total_count': 1, 'jobs': [{'name': 'reconcile', 'status': 'completed', 'conclusion': 'success', 'labels': ['ubuntu-latest']}]}
                        if '/artifacts?' in path: return {'total_count': 1, 'artifacts': [{
                            'id': subject.BEFORE_ARTIFACT_ID if before else subject.AFTER_ARTIFACT_ID,
                            'name': 'reconciliation-evidence-' + identity, 'expired': False}]}
                        return {'id': int(identity), 'head_sha': subject.BEFORE_COMMIT if before else subject.PINNED_COMMIT,
                            'head_branch': 'main', 'run_attempt': 1, 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
                            'display_title': 'history:reconcile-scheduled' if before else 'history:snapshot',
                            'path': '.github/workflows/history-reconciliation.yml'}
                    def download(self, artifact): return zipped.getvalue()
                prefix = 'BEFORE' if before else 'AFTER'
                with patch.multiple(subject, **{prefix + '_CIPHER_HASH': subject.sha(ciphertext), prefix + '_MEMBER_HASH': subject.sha(member.read_bytes()),
                        prefix + '_FINGERPRINT': subject.SnapshotState(source).fingerprint(), prefix + '_PAGES': len(source['pages'])}):
                    state, proof = subject.read_snapshot(Client(), identity, b'x' * 32)
                    self.assertEqual(proof['pages'], len(source['pages']))
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'authentication_failed'):
                        subject.read_snapshot(Client(), identity, b'y' * 32)
                    with zipfile.ZipFile(zipped, 'a') as archive: archive.writestr('unexpected', 'private')
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'members_invalid'):
                        subject.read_snapshot(Client(), identity, b'x' * 32)


if __name__ == '__main__':
    unittest.main()
