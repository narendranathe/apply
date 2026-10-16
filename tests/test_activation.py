"""Synthetic guarded activation; no provider, secret or remote service access."""
import base64
from contextlib import chdir
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cloud.activation import (ActivationBlocked, EVIDENCE_MEMBER, activate, load_boundary_bundle,
                              validate_boundary_members, validate_shadow_summary)
from cloud.notion import AmbiguousWriteError
from cloud.records import content_hash
from scripts.activate_cloud import GitHubEvidence
from scripts.archive_final_cutover import capture_final_cutover
from tests.test_archive_final_cutover import capture_fixture

COMMIT = 'b' * 40
CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': 'narendranathe/apply', 'actor': 'narendranathe',
           'event': 'workflow_dispatch', 'commit': COMMIT, 'run_id': '456', 'cloud_enabled': 'false'}


def shadow_summary():
    outreach = {'inbox': 'not checked', 'contacts_available': 1, 'contacts_reviewed': 1,
                'drafts': 0, 'research': 1, 'sent': 0, 'persistent': False, 'external_effects': 0}
    return {'mode': 'research-only', 'dry_run': True, 'persistent': False, 'cloud_hosted': True,
        'timezone': 'America/Chicago', 'linkedin_sent': 0, 'results': [
            {'lane': 'accepted', **outreach}, {'lane': 'first_touch', **outreach, 'discovery': {
                'roles_checked': 1, 'official_open': 1, 'research_tasks': 1, 'duplicates': 0, 'exceptions': {}}},
            {'lane': 'applications', 'dry_run': True, 'feed_checked': True, 'submission_attempts': 0,
             'applied_verified': 0, 'submission_unknown': 0, 'persistent': False, 'external_effects': 0}]}


class GitHubFixture:
    def __init__(self):
        self.summary = shadow_summary()
        self.main = COMMIT
        self.private = True
        self.failed_activation = False
        self.shadow_commit = COMMIT
        self.activation_success = True
        self.calls = []
        self.client = GitHubEvidence('synthetic', fetch=self.fetch, download=self.download)

    def fetch(self, path):
        self.calls.append(path)
        if path == '':
            return {'private': self.private, 'full_name': 'narendranathe/apply', 'default_branch': 'main'}
        if path == '/git/ref/heads/main':
            return {'ref': 'refs/heads/main', 'object': {'sha': self.main}}
        if path.startswith('/actions/workflows/history-reconciliation.yml/runs?'):
            return {'workflow_runs': [{'id': 100, 'display_title': 'history:activate', 'conclusion': 'failure'}] if self.failed_activation else []}
        if path.startswith('/actions/workflows/cloud-operations.yml/runs?'):
            return {'workflow_runs': [{'id': 123, 'head_sha': self.shadow_commit,
                'conclusion': 'success', 'display_title': 'cloud:research-only:all'}]}
        if path == '/actions/runs/123':
            return {'id': 123, 'head_sha': self.shadow_commit, 'head_branch': 'main', 'status': 'completed',
                'conclusion': 'success', 'event': 'workflow_dispatch', 'path': '.github/workflows/cloud-operations.yml'}
        if path == '/actions/runs/456':
            return {'id': 456, 'head_sha': COMMIT, 'head_branch': 'main', 'status': 'completed',
                'conclusion': 'success' if self.activation_success else 'failure', 'event': 'workflow_dispatch',
                'path': '.github/workflows/history-reconciliation.yml', 'display_title': 'history:activate'}
        if path == '/actions/runs/123/jobs?per_page=100':
            return {'jobs': [{'name': 'orchestrate', 'conclusion': 'success', 'labels': ['ubuntu-latest']}]}
        if path == '/actions/runs/123/artifacts?per_page=100':
            return {'artifacts': [{'id': 1, 'name': 'redacted-run-123', 'expired': False}]}
        raise AssertionError('Unexpected GitHub route')

    def download(self, artifact_id):
        assert artifact_id == 1
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('summary.json', json.dumps(self.summary))
        return data.getvalue()


class Store:
    def __init__(self, bundle):
        self.rows = {row['id']: deepcopy(row) for row in bundle['exported']['records']}
        source = json.loads(bundle['members'][bundle['evidence']['snapshot']['member']])['snapshot']
        self.data_source_id = source['data_source_id']
        self.writes = []

    def get(self, identity):
        return deepcopy(self.rows.get(identity))

    def put(self, record):
        self.writes.append(deepcopy(record))
        self.rows[record['id']] = deepcopy(record)
        return deepcopy(record)


def activation_fixture(root):
    """Authenticated final archive using only synthetic paused/claim sources."""
    kwargs, db, _, _ = capture_fixture(root)
    try:
        capture_final_cutover(**kwargs)
    finally:
        db.close()
    with chdir(root):
        bundle = load_boundary_bundle(key_value=base64.b64encode(kwargs['key'].read_bytes()).decode())
    return bundle, kwargs


def mutate_member(bundle, key, mutate):
    reference = bundle['evidence'][key]
    value = json.loads(bundle['members'][reference['member']])
    mutate(value)
    body = json.dumps(value, sort_keys=True).encode()
    bundle['members'][reference['member']] = body
    reference['sha256'] = __import__('hashlib').sha256(body).hexdigest()


class ActivationTests(unittest.TestCase):
    def test_ready_is_outside_manifest_and_exact_replay_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle, _ = activation_fixture(Path(folder))
            store, github = Store(bundle), GitHubFixture()
            historical_hash = content_hash(bundle['exported'])
            result = activate(store, bundle, github.client, CONTEXT)
            self.assertTrue(result['verified'])
            self.assertFalse(result['cloud_enabled_changed'])
            self.assertEqual(len(store.writes), 1)
            ready = store.rows['deployment:cutover:v2']
            self.assertEqual(ready['payload']['historical_manifest_sha256'], historical_hash)
            self.assertFalse(any(row['id'] == ready['id'] for row in bundle['exported']['records']))
            self.assertEqual(content_hash(bundle['exported']), historical_hash)
            replay = activate(store, bundle, github.client, {**CONTEXT, 'run_id': '789', 'cloud_enabled': 'true'})
            self.assertTrue(replay['unchanged'])
            self.assertEqual(len(store.writes), 1)
            self.assertEqual(store.rows[ready['id']], ready)

    def test_owner_main_privacy_shadow_and_full_history_are_mandatory(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle, _ = activation_fixture(Path(folder))
            for mode in ('owner', 'branch', 'enabled', 'source', 'private', 'commit', 'shadow', 'history', 'uncertain_prior'):
                with self.subTest(mode=mode):
                    store, github, context = Store(bundle), GitHubFixture(), dict(CONTEXT)
                    if mode == 'owner': context['actor'] = 'other'
                    if mode == 'branch': context['ref'] = 'refs/heads/other'
                    if mode == 'enabled': context['cloud_enabled'] = 'true'
                    if mode == 'source': store.data_source_id = 'other-source'
                    if mode == 'private': github.private = False
                    if mode == 'commit': github.main = 'c' * 40
                    if mode == 'shadow': github.shadow_commit = 'c' * 40
                    if mode == 'history': store.rows.pop(next(iter(store.rows)))
                    if mode == 'uncertain_prior': github.failed_activation = True
                    with self.assertRaises(ActivationBlocked):
                        activate(store, bundle, github.client, context)
                    self.assertEqual(store.writes, [])

    def test_worker_order_native_grant_scope_and_member_integrity_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            original, _ = activation_fixture(Path(folder))
            for mode in ('worker_busy', 'worker_after_snapshot', 'milo', 'native_grant', 'company_scope', 'hash'):
                with self.subTest(mode=mode):
                    bundle = deepcopy(original)
                    if mode == 'worker_busy':
                        mutate_member(bundle, 'workers', lambda value: value['workers'][0].update(status='running'))
                    if mode == 'worker_after_snapshot':
                        mutate_member(bundle, 'workers', lambda value: value['workers'][0].update(observed_at=datetime.now(timezone.utc).isoformat()))
                    if mode == 'milo': mutate_member(bundle, 'milo', lambda value: value.update(answer='not confirmed'))
                    if mode == 'native_grant':
                        def wrong(value):
                            page = next(e['page'] for e in value['snapshot']['pages'] if e['page']['properties'].get('Claim Token'))
                            page['properties']['Claim Token']['rich_text'][0]['text']['content'] = 'different-token'
                        mutate_member(bundle, 'snapshot', wrong)
                    if mode == 'company_scope':
                        row = next(r for r in bundle['exported']['records'] if r['kind'] == 'exceptions' and r['payload']['classification'] == 'unstarted_unverified')
                        row['payload']['identity']['company_ids'] = ['company:wrong']
                        row['hash'] = content_hash(row['payload'])
                    if mode == 'hash': bundle['evidence']['claims']['sha256'] = '0' * 64
                    store = Store(bundle)
                    with self.assertRaises(ActivationBlocked):
                        activate(store, bundle, GitHubFixture().client, CONTEXT)
                    self.assertEqual(store.writes, [])

    def test_incomplete_shadow_metrics_or_unpopulated_contacts_are_rejected(self):
        for mutate in (lambda s: s.update(results=[{'lane': name} for name in ('accepted', 'first_touch', 'applications')]),
                       lambda s: s['results'][0].pop('sent'), lambda s: s['results'][2].pop('submission_attempts'),
                       lambda s: s['results'][2].update(feed_checked=False), lambda s: s['results'][2].update(submission_unknown=1),
                       lambda s: s['results'][1].pop('discovery'), lambda s: s['results'][0].update(contacts_available=0)):
            summary = shadow_summary()
            mutate(summary)
            with self.subTest(mutate=mutate), self.assertRaises(ActivationBlocked):
                validate_shadow_summary(summary, minimum_contacts=1)

    def test_uncertain_ready_readback_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle, _ = activation_fixture(Path(folder))
            class Uncertain(Store):
                def get(self, identity):
                    if identity == 'deployment:cutover:v2': return None
                    return super().get(identity)
            store = Uncertain(bundle)
            with self.assertRaises(AmbiguousWriteError):
                activate(store, bundle, GitHubFixture().client, CONTEXT)
            self.assertEqual(len(store.writes), 1)

    def test_authenticated_archive_corruption_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            _, kwargs = activation_fixture(Path(folder))
            ciphertext = kwargs['output'].read_bytes()
            kwargs['output'].write_bytes(ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))
            with chdir(Path(folder)), self.assertRaises(ActivationBlocked):
                load_boundary_bundle(key_value=base64.b64encode(kwargs['key'].read_bytes()).decode())

    def test_cli_outside_hosted_owner_context_fails_nonzero_without_secret_access(self):
        with patch.dict('os.environ', {}, clear=True):
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/activate_cloud.py')],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, '')
        self.assertEqual(json.loads(result.stdout)['reason'], 'activation_requires_owner_default_branch_hosted_writer')


if __name__ == '__main__':
    unittest.main()
