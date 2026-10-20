"""Synthetic pinned incident recovery; no network, Notion or provider effects."""
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

import cloud.scheduled_reconciliation as subject
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash
from scripts.encrypted_backup import create_backup
from tests.test_startup_reconciliation import entry, states as startup_states

CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': subject.REPOSITORY,
           'actor': 'narendranathe', 'triggering_actor': 'narendranathe', 'event': 'workflow_dispatch',
           'cloud_enabled': 'false', 'commit': 'b' * 40, 'run_id': '999'}
SOURCES = {path: ('synthetic source for ' + path).encode() for path in subject.SOURCE_HASHES}
SOURCES['config/cloud.json'] = b'{"applications":{"employers":{}}}'


def states():
    baseline, ready, before, _ = startup_states()
    before['captured_at'] = '2026-10-18T01:00:00Z'
    after = deepcopy(before)
    after['captured_at'] = '2026-10-19T22:00:00Z'
    for lane in ('accepted', 'first_touch', 'applications'):
        marker = {'date': '2026-10-18', 'lane': lane, 'actor': 'github-actions',
                  'dry_run': False, 'mode': 'live', 'persistent': True}
        identity = 'run:2026-10-18:' + lane
        after['pages'].append(entry(record(identity + ':started', 'agent_runs', marker, 'started')))
        if lane != 'applications':
            after['pages'].append(entry(record(identity + ':completed', 'agent_runs',
                                               {**marker, 'summary': {'sent': 0}}, 'completed')))
    after['pages'].append(entry(record(subject.SLOT + ':dead_letter', 'audit_events',
        {'run_id': subject.SLOT, 'retry_external_effects': False}, 'needs_reconciliation')))
    # An outreach draft/plan is permitted; it is not sent-message evidence.
    after['pages'].append(entry(record('plan:synthetic', 'conversation_observations', {'text': 'draft'}, 'draft')))
    return baseline, ready, before, after


class GitHub:
    def __init__(self):
        self.main = CONTEXT['commit']
        self.sources = deepcopy(SOURCES)
        self.runs, self.jobs, self.logs, self.calls = {}, {}, {}, []
        names = ['Set up job', 'Run actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',
                 'Run actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
                 'Run pip install -r requirements.txt', 'Install isolated PDF build tools',
                 'Verify private destination', 'Run one coordinated operation']
        for number, identity in enumerate(subject.FAILURES):
            self.runs[identity] = {'id': int(identity), 'display_title': subject.TITLES[identity],
                'head_sha': subject.PINNED_COMMIT, 'head_branch': 'main', 'status': 'completed',
                'conclusion': 'failure', 'event': 'schedule', 'run_attempt': 1,
                'path': '.github/workflows/cloud-operations.yml', 'created_at': '2026-10-18T14:00:00Z',
                'run_started_at': '2026-10-18T14:00:00Z', 'updated_at': '2026-10-19T18:00:00Z'}
            self.jobs[identity] = {'total_count': 1, 'jobs': [{'id': 100 + number, 'name': 'orchestrate',
                'status': 'completed', 'conclusion': 'failure', 'labels': ['ubuntu-latest'],
                'steps': [{'number': n, 'name': name, 'status': 'completed',
                           'conclusion': 'failure' if n == 7 else 'success'} for n, name in enumerate(names, 1)]}]}
            self.logs[str(100 + number)] = (json.dumps(subject.DIAGNOSTICS[identity]) + '\n').encode()
        self.maintenance = {'id': 999, 'head_sha': self.main, 'head_branch': 'main', 'status': 'in_progress',
            'conclusion': None, 'event': 'workflow_dispatch', 'run_attempt': 1, 'display_title': 'history:reconcile-scheduled',
            'actor': {'login': 'narendranathe'}, 'triggering_actor': {'login': 'narendranathe'},
            'path': '.github/workflows/history-reconciliation.yml'}
        self.maintenance_jobs = {'total_count': 1, 'jobs': [{'name': 'reconcile', 'status': 'in_progress',
            'conclusion': None, 'labels': ['ubuntu-latest'], 'steps': [{'name': 'Reconcile without replaying historical effects',
                                                                   'status': 'in_progress', 'conclusion': None}]}]}
        self.workflow = (b'concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n'
                         b'          if [ "$IMPORT_MODE" = \'reconcile-scheduled\' ]; then\n'
                         b'            python scripts/reconcile_scheduled.py\n')
        self.after_jobs = None

    def verify_main(self, commit):
        subject.require(commit == self.main, 'deployed_private_main_changed')

    def fetch(self, path):
        self.calls.append(path)
        if path == '/actions/runs/999': return deepcopy(self.maintenance)
        if path.startswith('/actions/runs/999/jobs?'): return deepcopy(self.maintenance_jobs)
        if path == '/contents/.github/workflows/history-reconciliation.yml?ref=' + CONTEXT['commit']:
            return {'encoding': 'base64', 'content': base64.b64encode(self.workflow).decode()}
        if path.startswith('/contents/'):
            name = path.removeprefix('/contents/').split('?')[0]
            return {'encoding': 'base64', 'content': base64.b64encode(self.sources[name]).decode()}
        if path.startswith('/actions/runs/'):
            identity = path.split('/')[3]
            if '/jobs?' in path:
                value = deepcopy(self.jobs[identity])
                if self.after_jobs: self.after_jobs(identity)
                return value
            return deepcopy(self.runs[identity])
        raise AssertionError('Unexpected synthetic route')

    def download_path(self, path):
        return self.logs[path.split('/')[3]]


class Store:
    def __init__(self, snapshot):
        self.source, self.data_source_id = deepcopy(snapshot), snapshot['data_source_id']
        self.writes, self.mode = [], None

    def snapshot_all(self): return deepcopy(self.source)

    def get(self, identity):
        value = subject.SnapshotState(self.source).get(identity)
        if value and identity.startswith('writer-resolution:'):
            if self.mode == 'missing_readback': return None
            if self.mode == 'changed_readback': value['status'] = 'changed'
        return value

    def put(self, row):
        self.writes.append(deepcopy(row))
        self.source['pages'].append(entry(row))
        if self.mode == 'raise': raise AmbiguousWriteError('synthetic private upstream diagnostic')
        return None if self.mode == 'missing_write' else deepcopy(row)


class ScheduledTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.ready, self.before, self.after = states()
        self.github = GitHub()
        before, after = subject.SnapshotState(self.before), subject.SnapshotState(self.after)
        self.constants = patch.multiple(subject, BASELINE_HASH=content_hash(self.baseline), BASELINE_RECORDS=1,
            READY_CORE_HASH=content_hash(subject.core(self.ready)), BEFORE_PAGES=len(before.pages), AFTER_PAGES=len(after.pages),
            BEFORE_FINGERPRINT=before.fingerprint(), AFTER_FINGERPRINT=after.fingerprint(),
            AFTER_CIPHER_HASH='a' * 64,
            ADDED_RECORD_CORE_HASHES={key: content_hash(subject.core(after.rows[key])) for key in set(after.rows) - set(before.rows)},
            SOURCE_HASHES={path: subject.sha(body) for path, body in SOURCES.items()})
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def reconcile(self, store=None, context=None):
        store = store or Store(self.after)
        before, after = subject.SnapshotState(self.before), subject.SnapshotState(self.after)
        def snapshot(client, identity, key):
            state = before if identity == subject.BEFORE else after
            return state, {'run_id': identity, 'archive_sha256': subject.BEFORE_CIPHER_HASH if identity == subject.BEFORE else subject.AFTER_CIPHER_HASH,
                           'state_sha256': state.fingerprint(), 'pages': len(state.pages)}
        with patch.object(subject, 'read_snapshot', side_effect=snapshot), patch.object(subject.Path, 'read_bytes', return_value=self.github.workflow):
            result = subject.reconcile(store, self.baseline, self.github, context or CONTEXT, b'x' * 32)
        return result, store

    def test_exact_recovery_appends_only_five_and_replay_is_read_only(self):
        result, store = self.reconcile()
        self.assertTrue(result['verified'])
        self.assertEqual(result['appended'], 5)
        self.assertEqual([row['id'] for row in store.writes],
                         ['writer-resolution:' + identity for identity in subject.FAILURES] + [subject.SLOT + ':reconciled'])
        self.assertEqual(store.writes[0]['status'], 'reconciled_no_submission')
        self.assertTrue(all(row['status'] == 'reconciled_no_business_execution' for row in store.writes[1:4]))
        self.assertFalse(any(row['id'].endswith(':completed') for row in store.writes))
        replay, _ = self.reconcile(store)
        self.assertTrue(replay['unchanged'])
        self.assertEqual(len(store.writes), 5)
        slot = store.get(subject.SLOT + ':reconciled')
        self.assertTrue(subject.slot_resolution_matches(subject.SLOT, slot, store.writes[0]))

    def test_review_pending_or_wrong_owner_context_cannot_write(self):
        with patch.object(subject, 'AFTER_FINGERPRINT', ''):
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'review_pending'): self.reconcile()
        for field, value in [('actions', False), ('ref', 'refs/heads/other'), ('actor', 'other'),
                             ('triggering_actor', 'other'), ('event', 'schedule'), ('cloud_enabled', 'true')]:
            with self.subTest(field=field), self.assertRaisesRegex(subject.ReconciliationBlocked, 'owner_disabled'):
                self.reconcile(context={**CONTEXT, field: value})

    def test_complete_original_run_attempt_jobs_and_exact_diagnostics_required(self):
        for mutation in ('attempt', 'title', 'source', 'job', 'partial', 'steps', 'log', 'duplicate_log', 'bad_json', 'utf8'):
            with self.subTest(mutation=mutation):
                self.github = GitHub()
                identity = subject.FAILURES[1]
                if mutation == 'attempt': self.github.runs[identity]['run_attempt'] = 2
                if mutation == 'title': self.github.runs[identity]['display_title'] = 'cloud:dry-run:all'
                if mutation == 'source': self.github.sources['cloud/applications.py'] += b'changed'
                if mutation == 'job': self.github.jobs[identity]['jobs'].append(deepcopy(self.github.jobs[identity]['jobs'][0]))
                if mutation == 'partial': self.github.jobs[identity]['_next_page'] = True
                if mutation == 'steps': self.github.jobs[identity]['jobs'][0]['steps'][6]['conclusion'] = 'success'
                if mutation == 'log': self.github.logs['101'] = b'{"status":"blocked","error_type":"StateConflict"}\n'
                if mutation == 'duplicate_log': self.github.logs['101'] *= 2
                if mutation == 'bad_json': self.github.logs['101'] += b'{"status":broken}\n'
                if mutation == 'utf8': self.github.logs['101'] = b'\xff'
                store = Store(self.after)
                with self.assertRaises(subject.ReconciliationBlocked): self.reconcile(store)
                self.assertFalse(store.writes)

    def test_cascade_must_have_no_application_diagnostic(self):
        self.github.logs['101'] = json.dumps(subject.DIAGNOSTICS[subject.FAILED]).encode()
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'diagnostic_mismatch'): self.reconcile()

    def test_changed_source_and_unreviewed_delta_remain_fenced(self):
        for mutation in ('native', 'missing', 'new', 'schema', 'unfinished', 'complete_failed'):
            with self.subTest(mutation=mutation):
                original = deepcopy(self.after)
                if mutation == 'native': self.after['pages'][2]['page']['properties']['changed'] = {'type': 'checkbox', 'checkbox': True}
                if mutation == 'missing': self.after['pages'].pop(0)
                if mutation == 'new': self.after['pages'].append(entry(record('unreviewed', 'audit_events', {})))
                if mutation == 'schema': self.after['data_source']['properties']['extra'] = {}
                if mutation == 'unfinished': self.after['pages'].append(entry(record('run:other:started', 'agent_runs', {}, 'started')))
                if mutation == 'complete_failed': self.after['pages'].append(entry(record(subject.SLOT + ':completed', 'agent_runs', {}, 'completed')))
                store = Store(self.after)
                with self.assertRaises(subject.ReconciliationBlocked): self.reconcile(store)
                self.assertFalse(store.writes)
                self.after = original

    def test_explicit_effect_detection_rejects_attempt_receipt_and_send_even_if_manifest_reapproved(self):
        for row in [record('new-attempt', 'applications', {'attempt': {'payload_hash': 'new'}}, 'submitting'),
                    record('new-receipt', 'applications', {'receipt': {'receipt_id': 'new'}}, 'applied_verified'),
                    record('new-event', 'outreach_events', {}, 'historical'),
                    record('new-send', 'conversation_observations', {'delivery_state': 'Sent verified'}, 'sent')]:
            changed = deepcopy(self.after)
            changed['pages'].append(entry(row))
            after = subject.SnapshotState(changed)
            before = subject.SnapshotState(self.before)
            pins = {key: content_hash(subject.core(after.rows[key])) for key in set(after.rows) - set(before.rows)}
            with self.subTest(row=row['id']), patch.object(subject, 'AFTER_FINGERPRINT', after.fingerprint()), patch.object(subject, 'ADDED_RECORD_CORE_HASHES', pins):
                with self.assertRaisesRegex(subject.ReconciliationBlocked, 'new_attempt_receipt_or_outreach_effect'):
                    subject.verify_delta(before, after, self.baseline)

    def test_fresh_whole_source_change_blocks_all_appends(self):
        store = Store(self.after)
        store.source['pages'].append(entry(record('later-write', 'audit_events', {})))
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'fresh_source_changed'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_uncertain_write_or_readback_stops_without_second_append(self):
        for mode in ('raise', 'missing_write', 'missing_readback', 'changed_readback'):
            store = Store(self.after)
            store.mode = mode
            with self.subTest(mode=mode), self.assertRaises(AmbiguousWriteError): self.reconcile(store)
            self.assertEqual(len(store.writes), 1)
        store = Store(self.after)
        store.mode = 'missing_write'
        with self.assertRaises(AmbiguousWriteError): self.reconcile(store)
        store.mode = None
        result, _ = self.reconcile(store)
        self.assertEqual(result['appended'], 4)
        self.assertEqual(len(store.writes), 5)

    def test_existing_conflicting_resolution_cannot_be_overwritten(self):
        store = Store(self.after)
        store.source['pages'].append(entry(record('writer-resolution:' + subject.FAILED, 'audit_events', {'other': True})))
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'existing_scheduled_resolution_conflict'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_runtime_proof_rechecks_run_jobs_steps_and_changing_attempt(self):
        _, store = self.reconcile()
        original = store.writes[0]
        self.assertTrue(subject.scheduled_resolution_matches(subject.FAILED, original, self.github.fetch, subject.REPOSITORY))
        for mode in ('attempt', 'job', 'partial', 'steps', 'changing'):
            self.github = GitHub()
            if mode == 'attempt': self.github.runs[subject.FAILED]['run_attempt'] = 2
            if mode == 'job': self.github.jobs[subject.FAILED]['jobs'][0]['id'] = 888
            if mode == 'partial': self.github.jobs[subject.FAILED]['_next_page'] = True
            if mode == 'steps': self.github.jobs[subject.FAILED]['jobs'][0]['steps'][6]['conclusion'] = 'success'
            if mode == 'changing': self.github.after_jobs = lambda identity: self.github.runs[identity].update(run_attempt=2)
            with self.subTest(mode=mode):
                self.assertFalse(subject.scheduled_resolution_matches(subject.FAILED, original, self.github.fetch, subject.REPOSITORY))

    def test_runtime_and_slot_reject_tampered_schema_hash_scope_or_claim(self):
        _, store = self.reconcile()
        writer, slot = store.writes[0], store.writes[-1]
        for mode in ('schema', 'hash', 'id', 'reason', 'retry', 'effect', 'snapshot', 'diagnostic'):
            changed = deepcopy(writer)
            if mode == 'schema': changed['payload']['schema'] = 'apply-pinned-scheduled-writer-resolution/v2'
            if mode == 'hash': changed['hash'] = '0' * 64
            if mode == 'id': changed['id'] = 'writer-resolution:other'
            if mode == 'reason': changed['payload']['reason'] = 'manual_override'
            if mode == 'retry': changed['payload']['retry_external_effects'] = True
            if mode == 'effect': changed['payload']['external_effects_reconciled'] = False
            if mode == 'snapshot': changed['payload']['evidence']['after']['state_sha256'] = '0' * 64
            if mode == 'diagnostic': changed['payload']['evidence']['incidents'][subject.FAILED]['diagnostic'] = {}
            if mode != 'hash': changed['hash'] = content_hash(changed['payload'])
            with self.subTest(mode=mode):
                self.assertFalse(subject.scheduled_resolution_matches(subject.FAILED, changed, self.github.fetch, subject.REPOSITORY))
                self.assertFalse(subject.slot_resolution_matches(subject.SLOT, slot, changed))
        wrong_slot = deepcopy(slot)
        wrong_slot['payload']['evidence']['writer_resolution_sha256'] = '0' * 64
        wrong_slot['hash'] = content_hash(wrong_slot['payload'])
        self.assertFalse(subject.slot_resolution_matches(subject.SLOT, wrong_slot, writer))
        self.assertFalse(subject.slot_resolution_matches('run:other', slot, writer))

    def test_maintenance_owner_job_and_actual_workflow_contract(self):
        for mode in ('actor', 'job', 'concurrency', 'script'):
            self.github = GitHub()
            if mode == 'actor': self.github.maintenance['triggering_actor']['login'] = 'other'
            if mode == 'job': self.github.maintenance_jobs['jobs'][0]['labels'] = ['self-hosted']
            if mode == 'concurrency': self.github.workflow = self.github.workflow.replace(b'cancel-in-progress: false', b'cancel-in-progress: true')
            if mode == 'script': self.github.workflow = self.github.workflow.replace(b'python scripts/reconcile_scheduled.py', b'python other.py')
            with self.subTest(mode=mode), patch.object(subject.Path, 'read_bytes', return_value=self.github.workflow):
                with self.assertRaises(subject.ReconciliationBlocked): subject.verify_maintenance_context(self.github, CONTEXT)


class ArtifactTests(unittest.TestCase):
    def test_cli_rejects_local_use_before_credentials_or_network(self):
        result = subprocess.run([sys.executable, 'scripts/reconcile_scheduled.py'],
            env={'GITHUB_ACTIONS': 'false', 'NOTION_TOKEN': 'synthetic-private-token'}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('synthetic-private-token', result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['reason'], 'owner_disabled_hosted_maintenance_required')

    def test_actual_zip_ciphertext_and_all_members_are_authenticated(self):
        _, _, before, after = states()
        for identity, source in ((subject.BEFORE, before), (subject.AFTER, after)):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                key = root / 'key.bin'
                key.write_bytes(b'x' * 32)
                key.chmod(0o600)
                member = root / 'cloud-records.json'
                member.write_text(json.dumps({'schema': 'apply-cloud-snapshot/v1', 'snapshot': source}))
                cipher = root / 'backup.aesgcm'
                create_backup({'cloud-records.json': member}, cipher, key_path=key, private_destination_verified=True)
                ciphertext = cipher.read_bytes()
                is_before = identity == subject.BEFORE
                summary = {'backup': {'restore_verified': True, 'sha256': subject.sha(ciphertext), 'records': len(source['pages'])},
                           **({'mode': 'live', 'persistent': True} if is_before else {'operation': 'encrypted_source_snapshot'})}
                zipped = io.BytesIO()
                with zipfile.ZipFile(zipped, 'w') as archive:
                    archive.writestr('summary.json' if is_before else 'maintenance-summary.json', json.dumps(summary))
                    archive.writestr('cloud-backup-' + identity + '.aesgcm', ciphertext)
                class Client:
                    def fetch(self, path):
                        if '/jobs?' in path:
                            return {'total_count': 1, 'jobs': [{'name': 'orchestrate' if is_before else 'reconcile',
                                'status': 'completed', 'conclusion': 'success', 'labels': ['ubuntu-latest']}]}
                        if '/artifacts?' in path:
                            return {'total_count': 1, 'artifacts': [{'id': 1, 'name': ('redacted-run-' if is_before else 'reconciliation-evidence-') + identity,
                                                                  'expired': False}]}
                        return {'id': int(identity), 'head_sha': subject.BEFORE_COMMIT if is_before else subject.PINNED_COMMIT,
                            'head_branch': 'main', 'run_attempt': 1, 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
                            'display_title': 'cloud:live:all' if is_before else 'history:snapshot',
                            'path': '.github/workflows/cloud-operations.yml' if is_before else '.github/workflows/history-reconciliation.yml'}
                    def download(self, artifact): return zipped.getvalue()
                prefix = 'BEFORE' if is_before else 'AFTER'
                with patch.multiple(subject, **{prefix + '_CIPHER_HASH': subject.sha(ciphertext),
                        prefix + '_FINGERPRINT': subject.SnapshotState(source).fingerprint(), prefix + '_PAGES': len(source['pages'])}):
                    state, proof = subject.read_snapshot(Client(), identity, b'x' * 32)
                    self.assertEqual(proof['pages'], len(source['pages']))
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'authentication_failed'):
                        subject.read_snapshot(Client(), identity, b'y' * 32)
                    original = zipped.getvalue()
                    with zipfile.ZipFile(zipped, 'a') as archive: archive.writestr('unexpected', 'private')
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'members_invalid'):
                        subject.read_snapshot(Client(), identity, b'x' * 32)
                    zipped = io.BytesIO(original)
                with self.assertRaisesRegex(subject.ReconciliationBlocked, 'ciphertext_mismatch'):
                    subject.read_snapshot(Client(), identity, b'x' * 32)


if __name__ == '__main__':
    unittest.main()
