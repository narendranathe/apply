"""Synthetic read-only witnesses: no key, live API, source data or private logs."""
import base64
from copy import deepcopy
import hashlib
import io
import unittest
from unittest.mock import patch
import zipfile

from cloud import index_custody_evidence as subject
from cloud.records import content_hash
from cloud.startup_reconciliation import ReconciliationBlocked


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


CONTEXT = {'actions': True, 'ref': 'refs/heads/main', 'repository': subject.REPOSITORY,
    'actor': subject.OWNER, 'triggering_actor': subject.OWNER, 'event': 'workflow_dispatch',
    'cloud_enabled': 'false', 'cloud_research_enabled': 'true', 'run_attempt': '1',
    'commit': 'b' * 40, 'run_id': '900', 'mode': 'reconcile'}
WORKFLOW = b'on: workflow_dispatch\nconcurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n'
SOURCES = {path: WORKFLOW if path == subject.WORKFLOW else ('synthetic ' + path).encode()
           for path in subject.PROTOCOL_FILES}
HASHES = {path: digest(raw) for path, raw in SOURCES.items()}


def run(identity=900, *, active=True):
    return {'id': identity, 'head_sha': 'b' * 40, 'head_branch': 'main', 'run_attempt': 1,
        'path': subject.WORKFLOW, 'event': 'workflow_dispatch', 'display_title': 'index-custody:reconcile',
        'status': 'in_progress' if active else 'completed', 'conclusion': None if active else 'success',
        'actor': {'login': subject.OWNER}, 'triggering_actor': {'login': subject.OWNER},
        'repository': {'private': True, 'full_name': subject.REPOSITORY, 'default_branch': 'main'},
        'created_at': '2026-10-28T01:00:00Z', 'updated_at': '2026-10-28T01:00:00Z',
        'run_started_at': '2026-10-28T01:00:00Z'}


def jobs(identity=900, *, active=True):
    return {'total_count': 1, 'jobs': [{'id': identity + 100, 'run_id': identity,
        'head_sha': 'b' * 40, 'run_attempt': 1, 'name': 'reconcile', 'labels': ['ubuntu-latest'],
        'runner_group_name': 'GitHub Actions', 'status': 'in_progress' if active else 'completed',
        'conclusion': None if active else 'success', 'steps': [{'name': subject.OPERATION_STEP,
        'status': 'in_progress' if active else 'completed', 'conclusion': None if active else 'success'}]}]}


class GitHub:
    def __init__(self, *, active=True):
        self.run, self.jobs = run(active=active), jobs(active=active)
        self.artifacts = {'total_count': 0, 'artifacts': []}
        self.history = [deepcopy(self.run)]
        self.calls, self.binaries = [], {}
        self.callback = None

    def verify_main(self, head):
        if head != 'b' * 40:
            raise RuntimeError('private detail')

    def fetch(self, path):
        self.calls.append(path)
        if self.callback:
            self.callback(path, self)
        if path.startswith('/contents/'):
            name = path.removeprefix('/contents/').partition('?')[0]
            return {'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(SOURCES[name]).decode()}
        if path.startswith('/actions/workflows/'):
            page = int(path.rsplit('page=', 1)[1])
            return {'total_count': len(self.history), 'workflow_runs': deepcopy(self.history[(page-1)*100:page*100])}
        if '/jobs?' in path:
            return deepcopy(self.jobs)
        if '/artifacts?' in path:
            return deepcopy(self.artifacts)
        if path.startswith('/actions/runs/'):
            return deepcopy(self.run)
        raise AssertionError('unexpected synthetic route')

    def download_path(self, path):
        self.calls.append(path)
        return self.binaries[path]

    def download(self, identity):
        self.calls.append('download:' + str(identity))
        return self.binaries[identity]


def native_pins(github):
    return {'run': subject._projection(github.run), 'jobs_sha256': content_hash(github.jobs),
        'job_count': 1, 'artifacts_sha256': content_hash(github.artifacts), 'artifact_count': 0,
        'logs_sha256': {}, 'artifacts': {}}


class EvidenceTests(unittest.TestCase):
    def context(self, github, context=None):
        with patch.object(subject, '_local_sources', return_value=HASHES):
            return subject.verify_current_context(github, context or CONTEXT)

    def test_actual_safe_pins_and_committed_cipher_bytes(self):
        subject.require_reviewed_pins()
        proof = subject.reviewed_proof()
        self.assertEqual(proof['raw_page_drift'], subject.UNMANAGED_DRIFT)
        self.assertEqual(len(subject.FAILURES), 5)
        self.assertFalse(subject.incident_proof()['original_failure_cause_verified'])

    def test_current_context_binds_all_protocol_sources(self):
        github = GitHub()
        binding = self.context(github)
        self.assertTrue(subject.binding_valid(binding))
        self.assertEqual(binding['source_sha256'], HASHES)
        self.assertTrue(any('filter=all' in path for path in github.calls))
        self.assertGreaterEqual(github.calls.count('/actions/runs/900'), 2)

    def test_context_owner_flags_attempt_mode_are_required(self):
        for field, wrong in [('actor', 'other'), ('triggering_actor', 'other'), ('actions', False),
                ('run_attempt', '2'), ('cloud_enabled', 'true'), ('cloud_research_enabled', 'false'),
                ('ref', 'refs/heads/other'), ('repository', 'other/repo'), ('mode', 'unknown')]:
            with self.subTest(field=field), self.assertRaises(ReconciliationBlocked):
                self.context(GitHub(), {**CONTEXT, field: wrong})

    def test_context_self_hosted_or_wrong_runner_group_is_blocked(self):
        for field, value in [('labels', ['ubuntu-latest', 'self-hosted']), ('runner_group_name', 'Default')]:
            github = GitHub(); github.jobs['jobs'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ReconciliationBlocked):
                self.context(github)

    def test_context_missing_current_history_is_blocked(self):
        github = GitHub(); github.history = []
        with self.assertRaises(ReconciliationBlocked):
            self.context(github)

    def test_native_metadata_uses_filter_all_and_rereads(self):
        github = GitHub(active=False); pins = native_pins(github)
        subject._native_witness(github.fetch, '900', pins)
        self.assertEqual(github.calls.count('/actions/runs/900'), 3)
        self.assertEqual(sum('filter=all' in p for p in github.calls), 2)

    def test_native_rejects_changed_run_jobs_artifacts_and_partial_lists(self):
        for mutation in ['run', 'jobs', 'artifact', 'pagination', 'rerun']:
            github = GitHub(active=False); pins = native_pins(github)
            if mutation == 'run': github.run['triggering_actor']['login'] = 'other'
            if mutation == 'jobs': github.jobs['jobs'][0]['steps'][0]['conclusion'] = 'failure'
            if mutation == 'artifact': github.artifacts['artifacts'] = [{'id': 7}]
            if mutation == 'pagination': github.jobs['_next_page'] = True
            if mutation == 'rerun': github.run['run_attempt'] = 2
            with self.subTest(mutation=mutation), self.assertRaises(ReconciliationBlocked):
                subject._native_witness(github.fetch, '900', pins)

    def test_native_reread_detects_rerun_after_logs(self):
        github = GitHub(active=False); pins = native_pins(github)
        def mutate(path, value):
            if path == '/actions/runs/900' and value.calls.count(path) == 2:
                value.run['run_attempt'] = 2
        github.callback = mutate
        with self.assertRaises(ReconciliationBlocked):
            subject._native_witness(github.fetch, '900', pins)

    def test_full_witness_exact_log_zip_and_members(self):
        github = GitHub(active=False); pins = native_pins(github)
        log = b'synthetic bounded diagnostic\n'; cipher = b'synthetic opaque ciphertext'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('snapshot.aesgcm', cipher)
        zipped = stream.getvalue()
        pins['logs_sha256'] = {'1000': digest(log)}
        pins['artifacts'] = {'2000': {'zip_sha256': digest(zipped), 'members': {'snapshot.aesgcm': digest(cipher)}}}
        github.binaries = {'/actions/jobs/1000/logs': log, 2000: zipped}
        subject._native_witness(github.fetch, '900', pins, github)
        github.binaries['/actions/jobs/1000/logs'] += b'changed'
        with self.assertRaises(ReconciliationBlocked):
            subject._native_witness(github.fetch, '900', pins, github)

    def test_native_binary_errors_never_echo_private_error(self):
        github = GitHub(active=False); pins = native_pins(github)
        pins['logs_sha256'] = {'1000': 'a' * 64}
        with self.assertRaisesRegex(ReconciliationBlocked, '^custody_native_binary_unavailable$'):
            subject._native_witness(github.fetch, '900', pins, github)

    def test_original_binding_terminal_exact_sources_required(self):
        binding = self.context(GitHub())
        github = GitHub(active=False)
        with patch.object(subject, '_local_sources', return_value=HASHES):
            self.assertEqual(subject.validate_binding(github, binding), binding)
            wrong = deepcopy(binding); wrong['source_sha256'][subject.WORKFLOW] = 'a' * 64
            with self.assertRaises(ReconciliationBlocked): subject.validate_binding(github, wrong)
            wrong = {**binding, 'mode': 'verify'}
            with self.assertRaises(ReconciliationBlocked): subject.validate_binding(github, wrong)

    def test_workflow_history_paginates_complete_and_rejects_duplicates(self):
        github = GitHub(); github.history = [run(i) for i in range(1, 102)]
        self.assertEqual(len(subject._workflow_history(github.fetch)), 101)
        github.history[-1] = deepcopy(github.history[0])
        with self.assertRaises(ReconciliationBlocked): subject._workflow_history(github.fetch)

    def test_runtime_native_incidents_never_fetches_retained_artifacts(self):
        github = GitHub(active=False); pins = native_pins(github)
        with patch.object(subject, 'FAILURES', ('900',)), patch.object(subject, 'INCIDENTS', {'900': pins}), \
                patch.object(subject, 'SNAPSHOT_NATIVE', {}), \
                patch.object(subject, 'HISTORICAL_SOURCES', {subject.WORKFLOW: HASHES[subject.WORKFLOW]}):
            subject.native_incidents(github.fetch)
        self.assertFalse(any('/artifacts' in call or '/logs' in call or 'download:' in call for call in github.calls))

    def test_workflow_history_includes_foreign_triggers(self):
        github = GitHub()
        foreign = run(901); foreign['event'] = 'push'
        github.history.append(foreign)
        history = subject._workflow_history(github.fetch)
        self.assertEqual([row['event'] for row in history], ['workflow_dispatch', 'push'])
        self.assertTrue(all('event=' not in path for path in github.calls))

    def test_workflow_history_rejects_partial_pagination(self):
        def fetch(path):
            return {'total_count': 101, 'workflow_runs': [run(1)]}
        with self.assertRaisesRegex(ReconciliationBlocked, 'custody_maintenance_history_incomplete'):
            subject._workflow_history(fetch)

    def test_pin_mutation_fails_closed(self):
        with patch.object(subject, 'STATIC_PINS_SHA256', 'a' * 64):
            with self.assertRaises(ReconciliationBlocked): subject.require_reviewed_pins()


if __name__ == '__main__':
    unittest.main()
