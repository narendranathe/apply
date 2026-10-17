"""Synthetic exact-source startup reconciliation; no remote or provider access."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

import cloud.startup_reconciliation as subject
from cloud.cutover import make_cutover_record
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash
from scripts.encrypted_backup import create_backup
from scripts.reconcile_startup import StartupGitHubEvidence
from tests.test_cutover import cloud_page, evidence, export, prop


CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': subject.REPOSITORY,
           'actor': 'narendranathe', 'triggering_actor': 'narendranathe',
           'event': 'workflow_dispatch', 'cloud_enabled': 'false', 'commit': 'b' * 40, 'run_id': '999'}
SOURCES = {path: ('synthetic source for ' + path).encode() for path in subject.SOURCE_HASHES}


def entry(row):
    value = cloud_page(row)
    value['page']['id'] = 'page-' + row['id']
    return value


def states():
    baseline = export([record('contact:synthetic', 'contacts', {'suppressed': True}, 'suppressed')])
    value = evidence()
    value['historical_manifest_sha256'] = content_hash(baseline)
    value['history']['historical_manifest_sha256'] = content_hash(baseline)
    value['archive']['historical_manifest_sha256'] = content_hash(baseline)
    value['history']['readback'].update(records=1, unchanged=1, families={'contacts': 1})
    ready = make_cutover_record(value)
    before = {'schema': 'notion-data-source-snapshot/v1', 'complete': True,
              'data_source_id': 'abc123', 'data_source': {'id': 'abc123', 'properties': {}},
              'captured_at': '2026-10-16T21:00:00Z',
              'pages': [entry(baseline['records'][0]), entry(ready),
                        {'page': {'id': 'native-contact', 'properties': {'No Contact': prop(True, 'checkbox')}},
                         'blocks': [{'type': 'paragraph', 'paragraph': {'rich_text': [{'type': 'text',
                                      'text': {'content': 'private original source'}}]}}]}]}
    after = deepcopy(before)
    after['captured_at'] = '2026-10-17T14:50:00Z'
    after['pages'].append(entry(subject.witness()))
    return baseline, ready, before, after


class GitHub:
    def __init__(self):
        self.run = {'id': int(subject.FAILED), 'display_title': 'cloud:live:30 6 * * *',
                    'head_sha': subject.PINNED_COMMIT, 'head_branch': 'main', 'status': 'completed',
                    'conclusion': 'failure', 'event': 'schedule', 'run_attempt': 1,
                    'path': '.github/workflows/cloud-operations.yml',
                    'updated_at': '2026-10-17T14:24:32Z', 'created_at': '2026-10-17T14:19:07Z',
                    'run_started_at': '2026-10-17T14:19:07Z'}
        self.prior = {**deepcopy(self.run), 'id': int(subject.PREDECESSOR),
                      'display_title': 'cloud:live:all', 'conclusion': 'cancelled'}
        names = ['Set up job', 'Run actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',
                 'Run actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
                 'Run pip install -r requirements.txt', 'Install isolated PDF build tools',
                 'Verify private destination', 'Run one coordinated operation']
        self.jobs = {'total_count': 1, 'jobs': [{'id': subject.FAILED_JOB, 'name': 'orchestrate',
            'status': 'completed', 'conclusion': 'failure', 'labels': ['ubuntu-latest'],
            'steps': [{'number': n, 'name': name, 'status': 'completed',
                       'conclusion': 'failure' if n == 7 else 'success'} for n, name in enumerate(names, 1)]}]}
        self.prior_jobs = {'total_count': 0, 'jobs': []}
        self.logs = ('2026-10-17T14:24:27Z ' + json.dumps(subject.DIAGNOSTIC) + '\n').encode()
        self.sources = deepcopy(SOURCES)
        self.main = CONTEXT['commit']
        self.maintenance = {'id': 999, 'head_sha': self.main, 'head_branch': 'main', 'status': 'in_progress',
            'conclusion': None, 'event': 'workflow_dispatch', 'run_attempt': 1, 'display_title': 'history:reconcile-startup',
            'actor': {'login': 'narendranathe'}, 'triggering_actor': {'login': 'narendranathe'},
            'path': '.github/workflows/history-reconciliation.yml'}
        self.maintenance_jobs = {'total_count': 1, 'jobs': [{'name': 'reconcile', 'status': 'in_progress',
            'conclusion': None, 'labels': ['ubuntu-latest'], 'steps': [{
                'name': 'Reconcile without replaying historical effects', 'status': 'in_progress', 'conclusion': None}]}]}
        self.workflow = Path('.github/workflows/history-reconciliation.yml').read_bytes()
        self.calls = []

    def verify_main(self, commit):
        subject.require(commit == self.main, 'deployed_private_main_changed')

    def fetch(self, path):
        self.calls.append(path)
        if path == '/actions/runs/999': return deepcopy(self.maintenance)
        if path.startswith('/actions/runs/999/jobs?'): return deepcopy(self.maintenance_jobs)
        if path == '/contents/.github/workflows/history-reconciliation.yml?ref=' + CONTEXT['commit']:
            return {'encoding': 'base64', 'content': base64.b64encode(self.workflow).decode()}
        if path == '/actions/runs/' + subject.FAILED: return deepcopy(self.run)
        if path == '/actions/runs/' + subject.PREDECESSOR: return deepcopy(self.prior)
        if path.startswith('/actions/runs/' + subject.FAILED + '/jobs?'): return deepcopy(self.jobs)
        if path.startswith('/actions/runs/' + subject.PREDECESSOR + '/jobs?'): return deepcopy(self.prior_jobs)
        if path.startswith('/contents/'):
            name = path.removeprefix('/contents/').split('?')[0]
            return {'encoding': 'base64', 'content': base64.b64encode(self.sources[name]).decode()}
        raise AssertionError('Unexpected synthetic GitHub route')

    def download_path(self, path):
        assert path == '/actions/jobs/' + str(subject.FAILED_JOB) + '/logs'
        return self.logs


class Store:
    def __init__(self, source):
        self.source = deepcopy(source)
        self.data_source_id = source['data_source_id']
        self.writes = []
        self.uncertain = False

    def snapshot_all(self): return deepcopy(self.source)

    def get(self, identity): return subject.SnapshotState(self.source).get(identity)

    def put(self, row):
        self.writes.append(deepcopy(row))
        self.source['pages'].append(entry(row))
        return None if self.uncertain else deepcopy(row)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.ready, self.before, self.after = states()
        self.github = GitHub()
        self.constants = patch.multiple(subject, BASELINE_HASH=content_hash(self.baseline), BASELINE_RECORDS=1,
            READY_CORE_HASH=content_hash(subject.core(self.ready)),
            SOURCE_HASHES={path: subject.sha(body) for path, body in SOURCES.items()})
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def reconcile(self, store=None, context=None):
        store = store or Store(self.after)
        before, after = subject.SnapshotState(self.before), subject.SnapshotState(self.after)
        def snapshot(client, identity, key):
            state = before if identity == subject.BEFORE else after
            return state, {'run_id': identity, 'state_sha256': state.fingerprint()}
        with patch.object(subject, 'read_snapshot', side_effect=snapshot):
            result = subject.reconcile(store, self.baseline, self.github, context or CONTEXT, b'x' * 32)
        return result, store

    def test_exact_guard_only_incident_appends_once_and_replay_reads_only(self):
        result, store = self.reconcile()
        self.assertTrue(result['verified'])
        self.assertFalse(result['unchanged'])
        self.assertEqual(len(store.writes), 1)
        row = store.writes[0]
        self.assertEqual(row['id'], 'writer-resolution:' + subject.FAILED)
        self.assertFalse(row['payload']['retry_external_effects'])
        self.assertEqual(row['payload']['evidence']['incident']['failed_run']['run_attempt'], 1)
        self.assertNotIn('private original source', json.dumps(row))
        replay, _ = self.reconcile(store)
        self.assertTrue(replay['unchanged'])
        self.assertEqual(len(store.writes), 1)

    def test_owner_cloud_disabled_main_and_hosted_are_required(self):
        for field, value in [('actions', False), ('ref', 'refs/heads/other'), ('actor', 'other'),
                             ('triggering_actor', 'other'), ('repository', 'other/repo'),
                             ('event', 'schedule'), ('cloud_enabled', 'true'), ('commit', 'bad')]:
            with self.subTest(field=field):
                store = Store(self.after)
                with self.assertRaises(subject.ReconciliationBlocked):
                    self.reconcile(store, {**CONTEXT, field: value})
                self.assertEqual(store.writes, [])
        self.github.main = 'c' * 40
        with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()

    def test_actual_failed_attempt_source_and_step_contract_are_required(self):
        for field, value in [('head_sha', 'c' * 40), ('run_attempt', 2), ('status', 'in_progress'),
                             ('conclusion', 'cancelled'), ('event', 'workflow_dispatch'),
                             ('display_title', 'cloud:research-only:all')]:
            with self.subTest(field=field):
                original = deepcopy(self.github.run)
                self.github.run[field] = value
                with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()
                self.github.run = original
        self.github.jobs['jobs'][0]['steps'][6]['conclusion'] = 'success'
        with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()

    def test_actual_current_central_writer_concurrency_and_hosted_job_are_required(self):
        for mutation in ('owner', 'workflow', 'host', 'step', 'partial', 'group'):
            self.github = GitHub()
            if mutation == 'owner': self.github.maintenance['triggering_actor']['login'] = 'other'
            if mutation == 'workflow': self.github.maintenance['path'] = '.github/workflows/other.yml'
            if mutation == 'host': self.github.maintenance_jobs['jobs'][0]['labels'] = ['self-hosted']
            if mutation == 'step': self.github.maintenance_jobs['jobs'][0]['steps'][0]['status'] = 'completed'
            if mutation == 'partial': self.github.maintenance_jobs['_next_page'] = True
            if mutation == 'group': self.github.workflow = self.github.workflow.replace(b'hiring-outreach-single-writer', b'wrong-writer')
            with self.subTest(mutation=mutation), self.assertRaises(subject.ReconciliationBlocked): self.reconcile()

    def test_wrong_diagnostic_duplicate_diagnostic_or_source_never_resolves(self):
        original = self.github.logs
        for logs in [original * 2, original + b'{"reason":"other", "status":"blocked"}\n',
                     b'no diagnostic', original.replace(b'Blocked', b'NotionAPIError'),
                     b'\xff' + original]:
            self.github.logs = logs
            with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()
        self.github.logs = original
        self.github.sources['cloud/orchestrator.py'] += b'changed'
        with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()

    def test_any_predecessor_job_or_partial_metadata_prevents_reconciliation(self):
        for jobs in [{'total_count': 1, 'jobs': [{'id': 1}]},
                     {'total_count': 0, 'jobs': [], '_next_page': True},
                     {'total_count': False, 'jobs': []}]:
            self.github.prior_jobs = jobs
            with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()
        self.github.prior_jobs = {'total_count': 0, 'jobs': []}
        self.github.jobs['_next_page'] = True
        with self.assertRaises(subject.ReconciliationBlocked): self.reconcile()

    def test_full_native_content_suppression_and_managed_core_are_preserved(self):
        for mutation in ('native', 'suppression', 'extra', 'missing', 'ready', 'schema', 'witness'):
            with self.subTest(mutation=mutation):
                original = deepcopy(self.after)
                if mutation == 'native': self.after['pages'][2]['blocks'][0]['paragraph']['rich_text'][0]['text']['content'] = 'changed'
                if mutation == 'suppression': self.after['pages'][2]['page']['properties']['No Contact'] = prop(False, 'checkbox')
                if mutation == 'extra': self.after['pages'].append(entry(record('unexpected', 'audit_events', {})))
                if mutation == 'missing': self.after['pages'].pop(0)
                if mutation == 'ready': self.after['pages'][1] = entry(record('deployment:cutover:v2', 'audit_events', {}))
                if mutation == 'schema': self.after['data_source']['properties']['new'] = {}
                if mutation == 'witness': self.after['pages'][-1] = entry(record(subject.witness()['id'], 'audit_events', {'changed': True}))
                store = Store(self.after)
                with self.assertRaises((subject.ReconciliationBlocked, ValueError)): self.reconcile(store)
                self.assertEqual(store.writes, [])
                self.after = original

    def test_fresh_whole_source_must_equal_independent_after_snapshot(self):
        store = Store(self.after)
        store.source['pages'].append(entry(record('later-write', 'audit_events', {})))
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'fresh_source_changed'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_only_edit_stamps_can_change_without_business_content_change(self):
        self.after['data_source'].update(last_edited_time='later', last_edited_by={'id': 'server'})
        self.after['pages'][2]['page'].update(last_edited_time='later', last_edited_by={'id': 'server'})
        result, _ = self.reconcile()
        self.assertTrue(result['verified'])

    def test_only_datasource_response_request_id_is_volatile(self):
        self.before['data_source']['request_id'] = 'first-response'
        self.after['data_source']['request_id'] = 'second-response'
        result, _ = self.reconcile()
        self.assertTrue(result['verified'])
        self.before['pages'][2]['page']['properties']['request_id'] = prop('original business value')
        self.after['pages'][2]['page']['properties']['request_id'] = prop('changed business value')
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'source_delta_not_exact_guard_witness'):
            self.reconcile()
        self.after['pages'][2]['page']['properties']['request_id'] = prop('original business value')
        self.before['pages'][2]['blocks'][0]['paragraph']['request_id'] = 'original body value'
        self.after['pages'][2]['blocks'][0]['paragraph']['request_id'] = 'changed body value'
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'source_delta_not_exact_guard_witness'):
            self.reconcile()

    def test_unknown_write_or_existing_conflict_stops_without_retry(self):
        store = Store(self.after)
        store.uncertain = True
        with self.assertRaises(AmbiguousWriteError): self.reconcile(store)
        self.assertEqual(len(store.writes), 1)
        self.assertEqual(store.writes[0]['id'], 'writer-resolution:' + subject.FAILED)
        store.source['pages'][-1] = entry(record('writer-resolution:' + subject.FAILED, 'audit_events', {'other': True}))
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'existing_startup_resolution_conflict'):
            self.reconcile(store)
        self.assertEqual(len(store.writes), 1)

    def test_incomplete_duplicate_or_corrupt_source_snapshot_is_rejected(self):
        for mutation in ('incomplete', 'duplicate', 'truncated', 'body'):
            source = deepcopy(self.before)
            if mutation == 'incomplete': source['complete'] = False
            if mutation == 'duplicate': source['pages'].append(deepcopy(source['pages'][0]))
            if mutation == 'truncated': source['pages'][0]['page']['properties']['Cloud ID']['has_more'] = True
            if mutation == 'body': source['pages'][0]['blocks'].append({'type': 'paragraph', 'paragraph': {}})
            with self.subTest(mutation=mutation), self.assertRaises(Exception): subject.SnapshotState(source)


class ArtifactTests(unittest.TestCase):
    def test_cli_outside_hosted_maintenance_blocks_before_credentials(self):
        result = subprocess.run([sys.executable, 'scripts/reconcile_startup.py'],
            env={'GITHUB_ACTIONS': 'false', 'NOTION_TOKEN': 'fixture-token'},
            text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('fixture-token', result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {'status': 'blocked', 'error_type': 'ReconciliationBlocked',
            'operation': 'pinned_startup_reconciliation', 'reason': 'owner_disabled_hosted_maintenance_required'})

    def test_job_log_redirect_strips_auth_and_rejects_untrusted_destinations(self):
        for location, allowed in [('https://safe.blob.core.windows.net/private/log', True),
                                  ('https://evil.example/log', False),
                                  ('https://safe.blob.core.windows.net.evil.example/log', False),
                                  ('http://safe.blob.core.windows.net/log', False)]:
            requests = []
            class Opener:
                def open(self, request, timeout):
                    requests.append(request)
                    if len(requests) == 1:
                        raise HTTPError(request.full_url, 302, 'redirect', {'Location': location}, None)
                    return io.BytesIO(b'private synthetic log')
            client = StartupGitHubEvidence('synthetic-private-bearer')
            with self.subTest(location=location), patch('scripts.reconcile_startup.build_opener', return_value=Opener()):
                if allowed:
                    self.assertEqual(client.download_path('/actions/jobs/1/logs'), b'private synthetic log')
                    self.assertIsNone(requests[1].get_header('Authorization'))
                else:
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'redirect_invalid'):
                        client.download_path('/actions/jobs/1/logs')
                    self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].get_header('Authorization'), 'Bearer synthetic-private-bearer')

    def test_actual_zip_ciphertext_authentication_and_all_members_are_required(self):
        _, _, before, _ = states()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            key = root / 'recovery.bin'
            key.write_bytes(b'x' * 32)
            key.chmod(0o600)
            member = root / 'cloud-records.json'
            member.write_text(json.dumps({'schema': 'apply-cloud-snapshot/v1', 'snapshot': before}))
            cipher = root / 'backup.aesgcm'
            create_backup({'cloud-records.json': member}, cipher, key_path=key, private_destination_verified=True)
            ciphertext = cipher.read_bytes()
            summary = {'operation': 'encrypted_source_snapshot', 'backup': {'restore_verified': True,
                'sha256': subject.sha(ciphertext), 'records': len(before['pages'])}}
            zipped = io.BytesIO()
            with zipfile.ZipFile(zipped, 'w') as archive:
                archive.writestr('maintenance-summary.json', json.dumps(summary))
                archive.writestr('cloud-backup-' + subject.BEFORE + '.aesgcm', ciphertext)
            class Client:
                def fetch(self, path):
                    if '/jobs?' in path:
                        return {'total_count': 1, 'jobs': [{'name': 'reconcile', 'status': 'completed',
                            'conclusion': 'success', 'labels': ['ubuntu-latest']}]}
                    if path.endswith('/artifacts?per_page=100&page=1'):
                        return {'total_count': 1, 'artifacts': [{'id': 1, 'name': 'reconciliation-evidence-' + subject.BEFORE,
                                                              'expired': False}]}
                    return {'id': int(subject.BEFORE), 'head_sha': subject.PINNED_COMMIT, 'head_branch': 'main',
                            'run_attempt': 1, 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
                            'display_title': 'history:snapshot', 'path': '.github/workflows/history-reconciliation.yml'}
                def download(self, artifact): return zipped.getvalue()
            with patch.object(subject, 'BEFORE_CIPHER_HASH', subject.sha(ciphertext)):
                state, proof = subject.read_snapshot(Client(), subject.BEFORE, b'x' * 32)
                self.assertEqual(proof['pages'], len(before['pages']))
                self.assertEqual(state.fingerprint(), subject.SnapshotState(before).fingerprint())
                with self.assertRaisesRegex(subject.ReconciliationBlocked, 'authentication_failed'):
                    subject.read_snapshot(Client(), subject.BEFORE, b'y' * 32)
            with self.assertRaisesRegex(subject.ReconciliationBlocked, 'ciphertext_mismatch'):
                subject.read_snapshot(Client(), subject.BEFORE, b'x' * 32)


if __name__ == '__main__':
    unittest.main()
