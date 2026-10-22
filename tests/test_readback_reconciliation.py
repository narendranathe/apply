"""Synthetic exact-incident recovery; no live store, credential, or provider use."""
import base64
from collections import Counter
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
from types import SimpleNamespace

from cloud.coordination_quarantine import make_quarantine
import zipfile

import cloud.readback_reconciliation as subject
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
SOURCES['cloud/agent_index.py'] = Path('cloud/agent_index.py').read_bytes()


class GitHub:
    def __init__(self):
        self.runs = deepcopy(subject.FAILED_RUNS)
        self.jobs, self.logs = {}, {}
        for identity in subject.FAILURES:
            self.jobs[identity] = {'total_count': 1, 'jobs': [{'id': subject.FAILED_JOBS[identity]['id'],
                'name': 'orchestrate', 'status': 'completed', 'conclusion': 'failure', 'labels': ['ubuntu-latest'],
                'run_id': int(identity), 'run_attempt': 1, 'head_sha': subject.PINNED_COMMIT,
                'steps': [{'number': 7, 'name': 'Run one coordinated operation',
                           'status': 'completed', 'conclusion': 'failure'}]}]}
            self.logs[identity] = (json.dumps(subject.DIAGNOSTICS[identity]) + '\n').encode()
        self.sources, self.calls = deepcopy(SOURCES), []
        self.maintenance = {'id': 999, 'head_sha': CONTEXT['commit'], 'head_branch': 'main', 'status': 'in_progress',
            'conclusion': None, 'event': 'workflow_dispatch', 'run_attempt': 1, 'display_title': 'history:reconcile-readback',
            'actor': {'login': 'narendranathe'}, 'triggering_actor': {'login': 'narendranathe'},
            'path': '.github/workflows/history-reconciliation.yml'}
        self.maintenance_jobs = {'total_count': 1, 'jobs': [{'name': 'reconcile', 'status': 'in_progress',
            'conclusion': None, 'labels': ['ubuntu-latest'], 'steps': [{'name': 'Reconcile without replaying historical effects',
                'status': 'in_progress', 'conclusion': None}]}]}
        self.workflow = (b'concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n'
            b'          elif [ "$IMPORT_MODE" = \'reconcile-readback\' ]; then\n            python scripts/reconcile_readback.py\n')
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
        for identity in subject.FAILURES:
            if path == '/actions/runs/' + identity: return deepcopy(self.runs[identity])
            if path.startswith('/actions/runs/' + identity + '/jobs?'):
                result = deepcopy(self.jobs[identity])
                if self.after_jobs: self.after_jobs(identity)
                return result
        raise AssertionError('unexpected synthetic route')

    def download_path(self, path):
        for identity in subject.FAILURES:
            if path == '/actions/jobs/' + str(subject.FAILED_JOBS[identity]['id']) + '/logs':
                return self.logs[identity]
        raise AssertionError('unexpected synthetic log route')


class Store:
    def __init__(self, source):
        self.source, self.data_source_id = deepcopy(source), source['data_source_id']
        self.writes, self.mode, self.fail_at = [], None, 1
        self.snapshot_calls, self.snapshot_hook = 0, None

    def snapshot_all(self):
        self.snapshot_calls += 1
        if self.snapshot_hook: self.snapshot_hook(self)
        return deepcopy(self.source)

    def get(self, identity):
        row = subject.SnapshotState(self.source).get(identity)
        if row and len(self.writes) == self.fail_at and identity == self.writes[-1]['id']:
            if self.mode == 'missing_readback': return None
            if self.mode == 'changed_readback': row['status'] = 'changed'
        return row

    def put(self, row):
        self.writes.append(deepcopy(row))
        self.source['pages'].append(entry(row))
        if len(self.writes) == self.fail_at:
            if self.mode == 'raise': raise AmbiguousWriteError('private synthetic upstream text')
            if self.mode == 'missing_write': return None
        return deepcopy(row)


def fixture():
    baseline, ready, before, _ = states()
    before['captured_at'] = '2026-10-21T05:00:00Z'
    source = [subject.validate_record(row) for row in subject.SnapshotState(before).rows.values()]
    old_index = subject.build_index(source, datetime(2026, 10, 20, 13, tzinfo=timezone.utc),
        source_snapshot_hash={'hash': subject.snapshot_hash(source), 'complete': True, 'conflicts': []})
    before['pages'] += [entry(row) for row in old_index]
    after = deepcopy(before)
    after['captured_at'] = '2026-10-21T18:00:00Z'
    now = datetime(2026, 10, 21, 13, 30, tzinfo=timezone.utc)
    rows = []
    for lane in ('accepted', 'first_touch', 'applications'):
        marker = {'date': '2026-10-21', 'lane': lane, 'actor': 'github-actions', 'dry_run': False,
                  'mode': 'live', 'persistent': True}
        summary = {'lane': lane, 'sent': 0} if lane != 'applications' else {
            'lane': lane, 'dry_run': False, 'submission_attempts': 0, 'applied_verified': 0, 'submission_unknown': 0}
        identity = 'run:2026-10-21:' + lane
        rows += [record(identity + ':started', 'agent_runs', marker, 'started'),
                 record(identity + ':completed', 'agent_runs', {**marker, 'summary': summary}, 'completed')]
    report = record('report:synthetic', 'funnel_reports', {'schema': 'qualified-funnel/v1', 'observed_at': now.isoformat()})
    rows += [report, record('cursor:synthetic', 'sync_cursors', {'complete': True, 'observed_at': now.isoformat(),
        'report_id': report['id']}, 'readback_verified'), record('job:synthetic-new', 'jobs', {
            'job_id': 'job:synthetic-new', 'company': 'Synthetic employer', 'requisition_id': 'fixture-only',
            'url': 'https://jobs.lever.co/example/fixture-only', 'open': True})]
    after['pages'] += [entry(row) for row in rows]
    guards = {}
    for unresolved, created in ((subject.FAILED, '2026-10-21T15:53:00Z'),
                                (subject.FAILURES[1], '2026-10-21T16:04:00Z')):
        row = record('writer-failure:' + unresolved, 'audit_events', {'github_run_id': unresolved,
            'reason': 'failed_writer_requires_reconciliation', 'retry_external_effects': False}, 'needs_reconciliation')
        item = entry(row); item['page']['created_time'] = created
        after['pages'].append(item); guards[row['id']] = row
    after_state = subject.SnapshotState(after)
    index_source = [subject.validate_record(row) for identity, row in after_state.rows.items()
                    if not subject._generated(row) and identity not in guards]
    previous = next(row for row in old_index if row['kind'] == 'index_manifests')
    expected = subject.build_index(index_source, now, previous_manifest=previous,
        source_snapshot_hash={'hash': subject.snapshot_hash(index_source), 'complete': True, 'conflicts': []})
    before_state = subject.SnapshotState(before)
    prefix = next(index for index, row in enumerate(expected) if row['id'] not in before_state.rows) + 1
    generated = [row for row in expected[:prefix] if row['id'] not in before_state.rows]
    for row in generated:
        item = entry(row); item['page']['created_time'] = '2026-10-21T13:31:00Z'
        after['pages'].append(item)
    after_state = subject.SnapshotState(after)
    added = {identity: subject.validate_record(row) for identity, row in after_state.rows.items() if identity not in before_state.rows}
    business = {identity: row for identity, row in added.items() if not subject._generated(row) and identity not in guards}
    last = expected[prefix - 1]
    pins = {'BASELINE_HASH': content_hash(baseline), 'BASELINE_RECORDS': 1,
        'READY_CORE_HASH': content_hash(subject.core(ready)), 'BEFORE_FINGERPRINT': before_state.fingerprint(),
        'AFTER_FINGERPRINT': after_state.fingerprint(), 'BEFORE_PAGES': len(before['pages']),
        'AFTER_PAGES': len(after['pages']), 'BEFORE_RECORDS': len(before_state.rows), 'AFTER_RECORDS': len(after_state.rows),
        'AFTER_ARTIFACT_ID': 777, 'AFTER_CIPHER_HASH': 'a' * 64, 'AFTER_MEMBER_HASH': 'b' * 64,
        'ADDED_RECORDS': len(added), 'ADDED_COUNTS': dict(Counter(row['kind'] for row in added.values())),
        'ADDED_CORES_HASH': content_hash({identity: content_hash(row) for identity, row in added.items()}),
        'BUSINESS_RECORDS': len(business), 'BUSINESS_CORES_HASH': content_hash({identity: content_hash(row) for identity, row in business.items()}),
        'GUARD_AUDIT_CORES_HASH': content_hash({identity: content_hash(row) for identity, row in guards.items()}),
        'EXPECTED_INDEX_COUNTS': dict(Counter(row['kind'] for row in expected)),
        'EXPECTED_INDEX_MANIFEST_HASH': next(row['hash'] for row in expected if row['kind'] == 'index_manifests'),
        'EXPECTED_PREFIX_RECORDS': prefix, 'EXPECTED_NEW_GENERATED': len(generated),
        'EXPECTED_LAST_CHILD_ID_HASH': subject.sha(last['id'].encode()), 'EXPECTED_LAST_CHILD_PAYLOAD_HASH': last['hash'],
        'EXPECTED_INDEX_SOURCE_HASH': subject.snapshot_hash(index_source), 'EXPECTED_PREVIOUS_MANIFEST_HASH': previous['hash'],
        'REVIEWED_CONSTRUCTOR_AUDIT_HASH': 'c' * 64, 'REVIEWED_DELTA_PROOF_HASH': 'd' * 64}
    return baseline, before, after, pins


def snapshot_metadata(identity):
    before = identity == subject.BEFORE
    commit = subject.BEFORE_COMMIT if before else subject.INCIDENT_COMMIT if identity == subject.INCIDENT else subject.AFTER_COMMIT
    started = ('2026-10-21T04:00:00Z' if before else '2026-10-21T17:00:00Z'
               if identity == subject.INCIDENT else '2026-10-21T21:00:00Z')
    finished = ('2026-10-21T05:00:00Z' if before else '2026-10-21T18:00:00Z'
                if identity == subject.INCIDENT else '2026-10-21T21:30:00Z')
    run = {'id': int(identity), 'head_sha': commit, 'head_branch': 'main', 'run_attempt': 1,
        'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'success',
        'display_title': 'history:reconcile-index' if before else 'history:snapshot',
        'path': '.github/workflows/history-reconciliation.yml', 'created_at': started,
        'run_started_at': started, 'updated_at': finished,
        'actor': {'login': 'narendranathe'}, 'triggering_actor': {'login': 'narendranathe'}}
    jobs = {'total_count': 1, 'jobs': [{'id': 800 + (0 if before else 1 if identity == subject.INCIDENT else 2),
        'name': 'reconcile', 'status': 'completed', 'conclusion': 'success', 'labels': ['ubuntu-latest'],
        'steps': [{'name': 'Reconcile without replaying historical effects', 'status': 'completed',
                   'conclusion': 'success', 'started_at': started, 'completed_at': finished}]}]}
    return run, jobs


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.before, bare_after, self.pins = fixture()
        native_page = {'page': {'id': 'native-claim-fixture', 'properties': {},
            'created_time': '2026-10-21T14:38:00Z'}, 'blocks': []}
        self.incident_after = deepcopy(bare_after)
        self.incident_after['pages'].append(native_page)
        self.after = deepcopy(self.incident_after)
        self.after['captured_at'] = '2026-10-21T21:30:00Z'
        after_state = subject.SnapshotState(self.after)
        q = make_quarantine({'schema': 'apply-coordination-quarantine/v1', 'active': True,
            'classification': 'effect_unknown', 'identity': {'profile_urls': ['https://www.linkedin.com/in/synthetic-quarantine']},
            'provenance': {'claim_sha256': '1' * 64, 'task_sha256': '2' * 64, 'resource_sha256': '3' * 64},
            'observed_at': '2026-10-21T20:13:09Z', 'evidence_refs': ['synthetic-encrypted-source']})
        # Canonical synthetic fixtures exercise orchestration only. The pure
        # native-plan validator has separate encrypted-evidence tests.
        self.plan = SimpleNamespace(records=(q,
            record('synthetic-native-claim-observation', 'audit_events', {'schema': 'synthetic-native-claim/v1',
                'delivery_verified': False, 'history_complete': False}, 'needs_reconciliation'),
            record('synthetic-controller-stop-observation', 'audit_events', {'schema': 'synthetic-user-stop/v1',
                'stopped_user_confirmed': True, 'row_link_verified': False}, 'history_incomplete')),
            native_page_id=native_page['page']['id'], proof={'schema': 'synthetic-native-containment-proof/v1',
                'effect_unknown_contained': True, 'delivery_verified': False, 'history_complete': False})
        self.github = GitHub()
        self.snapshot_identities = []
        metadata = {identity: snapshot_metadata(identity) for identity in (subject.BEFORE, subject.INCIDENT, subject.AFTER)}
        self.pins.update(SOURCE_HASHES={path: subject.sha(body) for path, body in SOURCES.items()},
            FAILED_JOBS={identity: {'id': jobs['jobs'][0]['id'],
                'steps_sha256': content_hash(jobs['jobs'][0]['steps']), 'jobs_sha256': content_hash(jobs),
                'log_sha256': subject.sha(self.github.logs[identity])} for identity, jobs in self.github.jobs.items()},
            AFTER_FINGERPRINT=after_state.fingerprint(), AFTER_PAGES=len(after_state.pages),
            INCIDENT_FINGERPRINT=subject.SnapshotState(self.incident_after).fingerprint(),
            INCIDENT_PAGES=len(self.incident_after['pages']), INCIDENT_RECORDS=len(after_state.rows),
            NATIVE_LOGICAL_HASH=content_hash(after_state.pages[self.plan.native_page_id]),
            REVIEWED_CONTAINMENT_PROOF_HASH=content_hash(self.plan.proof),
            SNAPSHOT_RUNS={identity: {key: run[key] for key in subject.RUN_FIELDS} for identity, (run, _) in metadata.items()},
            SNAPSHOT_JOBS={identity: {'id': jobs['jobs'][0]['id'], 'jobs_sha256': content_hash(jobs),
                'steps_sha256': content_hash(jobs['jobs'][0]['steps']),
                'operation_started_at': jobs['jobs'][0]['steps'][0]['started_at']} for identity, (_, jobs) in metadata.items()})
        self.constants = patch.multiple(subject, **self.pins)
        self.constants.start(); self.addCleanup(self.constants.stop)
        self.delta = subject.verify_delta_and_index(subject.SnapshotState(self.before),
            subject.SnapshotState(self.after), self.baseline, self.plan)
        self.delta_pin = patch.object(subject, 'REVIEWED_DELTA_PROOF_HASH', content_hash(self.delta))
        self.delta_pin.start(); self.addCleanup(self.delta_pin.stop)

    def snapshot(self, client, identity, key):
        self.snapshot_identities.append(identity)
        prefix = 'BEFORE' if identity == subject.BEFORE else 'INCIDENT' if identity == subject.INCIDENT else 'AFTER'
        value = self.before if prefix == 'BEFORE' else self.incident_after if prefix == 'INCIDENT' else self.after
        state = subject.SnapshotState(value)
        return state, {'run_id': identity, 'artifact_id': getattr(subject, prefix + '_ARTIFACT_ID'),
            'artifact_zip_sha256': 'a' * 64, 'archive_sha256': getattr(subject, prefix + '_CIPHER_HASH'),
            'member_sha256': getattr(subject, prefix + '_MEMBER_HASH'),
            'state_sha256': getattr(subject, prefix + '_FINGERPRINT'), 'pages': getattr(subject, prefix + '_PAGES'),
            'operation_started_at': subject.SNAPSHOT_JOBS[identity]['operation_started_at']}

    def synthetic_cohort_matches(self, read_record):
        return all((row := read_record(expected['id'])) is not None
                   and subject.core(row) == subject.core(expected) for expected in self.plan.records)

    def matcher(self, identity, marker, github, store):
        with patch('cloud.native_claim_reconciliation.cohort_matches', side_effect=self.synthetic_cohort_matches):
            return subject.readback_resolution_matches(identity, marker, github.fetch, subject.REPOSITORY,
                                                       read_record=store.get)

    def reconcile(self, store=None, context=None):
        store = store or Store(self.after)
        with patch.object(subject, 'read_snapshot', side_effect=self.snapshot), \
             patch.object(subject.Path, 'read_bytes', return_value=self.github.workflow), \
             patch('cloud.native_claim_reconciliation.load_plan', return_value=self.plan), \
             patch('cloud.submission.HTTPTransport.request', side_effect=AssertionError('provider request forbidden')), \
             patch.object(subject, 'verify_delta_and_index', return_value=self.delta):
            result = subject.reconcile(store, self.baseline, self.github, context or CONTEXT, b'x' * 32)
        return result, store

    def test_six_honest_appends_containment_first_lead_last_and_repeat_read_only(self):
        result, store = self.reconcile()
        expected_ids = [row['id'] for row in self.plan.records] + [
            'writer-resolution:' + identity for identity in (*subject.FAILURES[1:], subject.FAILED)]
        self.assertTrue(result['verified']); self.assertFalse(result['index_complete'])
        self.assertEqual(self.snapshot_identities, [subject.BEFORE, subject.INCIDENT, subject.AFTER])
        self.assertEqual([row['id'] for row in store.writes], expected_ids)
        resolutions = store.writes[3:]
        self.assertTrue(resolutions[-1]['payload']['business_lanes_completed'])
        self.assertTrue(all(not row['payload']['business_lanes_completed'] for row in resolutions[:-1]))
        for row in resolutions:
            payload = row['payload']
            self.assertFalse(payload['external_effects_reconciled'])
            self.assertTrue(payload['managed_cloud_effects_reconciled'])
            self.assertTrue(payload['native_effect_unknown_contained'])
            self.assertFalse(payload['native_delivery_verified'])
            self.assertTrue(payload['native_history_incomplete'])
            self.assertFalse(payload['retry_external_effects']); self.assertFalse(payload['index_complete'])
            self.assertIn('incident_snapshot', payload['evidence'])
            self.assertTrue(self.matcher(payload['github_run_id'], row, self.github, store))
        self.assertEqual(result['guard_storage_audits_preserved'], 2)
        repeated, _ = self.reconcile(store)
        self.assertTrue(repeated['unchanged']); self.assertEqual(len(store.writes), 6)

    def test_each_missing_review_pin_rejects_before_network_or_write(self):
        for field, value in [('AFTER_CIPHER_HASH', ''), ('AFTER_MEMBER_HASH', ''), ('AFTER_FINGERPRINT', ''),
                ('AFTER_PAGES', 0), ('AFTER_RECORDS', 0), ('AFTER_ARTIFACT_ID', 0), ('ADDED_CORES_HASH', ''),
                ('BUSINESS_CORES_HASH', ''), ('GUARD_AUDIT_CORES_HASH', ''), ('REVIEWED_DELTA_PROOF_HASH', ''),
                ('REVIEWED_CONSTRUCTOR_AUDIT_HASH', ''), ('EXPECTED_INDEX_COUNTS', {}), ('ADDED_COUNTS', {}),
                ('EXPECTED_LAST_CHILD_ID_HASH', ''), ('EXPECTED_LAST_CHILD_PAYLOAD_HASH', ''),
                ('REVIEWED_CONTAINMENT_PROOF_HASH', ''), ('SNAPSHOT_RUNS', {}), ('SNAPSHOT_JOBS', {})]:
            self.github.calls.clear(); store = Store(self.after)
            with self.subTest(field=field), patch.object(subject, field, value), self.assertRaisesRegex(subject.ReconciliationBlocked, 'review_pending'):
                self.reconcile(store)
            self.assertEqual(self.github.calls, []); self.assertEqual(store.writes, [])

    def test_malformed_snapshot_pin_contract_rejects_before_fetch_or_append(self):
        for mode in ('missing_run_field', 'missing_phase', 'extra_job_pin', 'bool_job_id', 'bad_operation_start'):
            runs, jobs = deepcopy(subject.SNAPSHOT_RUNS), deepcopy(subject.SNAPSHOT_JOBS)
            if mode == 'missing_run_field': runs[subject.INCIDENT].pop('run_attempt')
            if mode == 'missing_phase': runs.pop(subject.INCIDENT)
            if mode == 'extra_job_pin': jobs[subject.AFTER]['unexpected'] = True
            if mode == 'bool_job_id': jobs[subject.AFTER]['id'] = True
            if mode == 'bad_operation_start': jobs[subject.AFTER]['operation_started_at'] = None
            store = Store(self.after); self.github.calls.clear()
            with self.subTest(mode=mode), patch.multiple(subject, SNAPSHOT_RUNS=runs, SNAPSHOT_JOBS=jobs), \
                 self.assertRaisesRegex(subject.ReconciliationBlocked, 'review_pending'):
                self.reconcile(store)
            self.assertFalse(self.github.calls); self.assertFalse(store.writes)

    def test_disabled_owner_current_main_hosted_context_required(self):
        for key, value in [('actions', False), ('actor', 'other'), ('triggering_actor', 'other'), ('event', 'schedule'),
                ('repository', 'other/repo'), ('ref', 'refs/heads/other'), ('cloud_enabled', 'true'), ('commit', 'bad')]:
            store = Store(self.after)
            with self.subTest(key=key), self.assertRaises(subject.ReconciliationBlocked): self.reconcile(store, {**CONTEXT, key: value})
            self.assertFalse(store.writes)

    def test_changed_attempt_job_whole_job_source_and_logs_block_all_appends(self):
        for identity in subject.FAILURES:
            for mode in ('attempt', 'bool_attempt', 'job', 'partial', 'steps', 'runner', 'source', 'log', 'duplicate_log', 'changing'):
                self.github = GitHub()
                if mode == 'attempt': self.github.runs[identity]['run_attempt'] = 2
                if mode == 'bool_attempt': self.github.runs[identity]['run_attempt'] = True
                if mode == 'job': self.github.jobs[identity]['jobs'][0]['id'] += 1
                if mode == 'partial': self.github.jobs[identity]['_next_page'] = True
                if mode == 'steps': self.github.jobs[identity]['jobs'][0]['steps'][0]['conclusion'] = 'success'
                if mode == 'runner': self.github.jobs[identity]['jobs'][0]['runner_name'] = 'changed'
                if mode == 'source': self.github.sources['cloud/runtime_guard.py'] += b'changed'
                if mode == 'log': self.github.logs[identity] = b'{"status":"blocked"}\n'
                if mode == 'duplicate_log': self.github.logs[identity] *= 2
                if mode == 'changing': self.github.after_jobs = lambda seen: self.github.runs[seen].update(run_attempt=2)
                store = Store(self.after)
                with self.subTest(identity=identity, mode=mode), self.assertRaises(subject.ReconciliationBlocked): self.reconcile(store)
                self.assertFalse(store.writes)

    def test_fresh_source_extra_missing_edited_and_partial_block(self):
        for mode in ('extra', 'missing', 'edited', 'partial'):
            store = Store(self.after)
            if mode == 'extra': store.source['pages'].append(entry(record('later', 'audit_events', {})))
            if mode == 'missing': store.source['pages'].pop(0)
            if mode == 'edited': store.source['pages'][2]['blocks'][0]['paragraph']['rich_text'][0]['text']['content'] = 'changed'
            if mode == 'partial': store.source['complete'] = False
            with self.subTest(mode=mode), self.assertRaises(Exception): self.reconcile(store)
            self.assertFalse(store.writes)

    def test_changed_delta_stops_before_any_append(self):
        self.delta = {**self.delta, 'new_receipts': 1}
        store = Store(self.after)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'delta_proof_changed'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_uncertain_append_at_each_position_stops_without_remaining_writes(self):
        for fail_at in range(1, 7):
            for mode in ('raise', 'missing_write', 'missing_readback', 'changed_readback'):
                store = Store(self.after); store.mode = mode; store.fail_at = fail_at
                with self.subTest(fail_at=fail_at, mode=mode), self.assertRaises(AmbiguousWriteError):
                    self.reconcile(store)
                self.assertEqual(len(store.writes), fail_at)
                store.mode = None
                if fail_at < 6:
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'partial_resolution_batch'):
                        self.reconcile(store)
                else:
                    completed, _ = self.reconcile(store)
                    self.assertTrue(completed['unchanged'])
                self.assertEqual(len(store.writes), fail_at)

    def test_every_proper_existing_six_row_subset_blocks_without_new_append(self):
        _, complete = self.reconcile()
        for mask in range(1, 63):
            store = Store(self.after)
            store.source['pages'] += [entry(row) for index, row in enumerate(complete.writes) if mask & (1 << index)]
            with self.subTest(existing_subset_mask=mask), self.assertRaisesRegex(subject.ReconciliationBlocked, 'partial_resolution_batch'):
                self.reconcile(store)
            self.assertFalse(store.writes)

    def test_source_change_or_missing_containment_before_clearance_stops_after_three(self):
        for mode in ('extra', 'native_changed', 'quarantine_missing'):
            store = Store(self.after)
            def mutate(current):
                if current.snapshot_calls != 2: return
                if mode == 'extra': current.source['pages'].append(entry(record('human-later-change', 'audit_events', {})))
                if mode == 'native_changed':
                    page = next(item for item in current.source['pages'] if item['page']['id'] == self.plan.native_page_id)
                    page['blocks'].append({'type': 'paragraph', 'paragraph': {'rich_text': []}})
                if mode == 'quarantine_missing':
                    current.source['pages'] = [item for item in current.source['pages']
                        if item['page']['id'] != 'page-' + self.plan.records[0]['id']]
            store.snapshot_hook = mutate
            with self.subTest(mode=mode), self.assertRaises(subject.ReconciliationBlocked): self.reconcile(store)
            self.assertEqual([row['id'] for row in store.writes], [row['id'] for row in self.plan.records])
            self.assertFalse(any(row['id'].startswith('writer-resolution:') for row in store.writes))

    def test_changed_containment_plan_stops_before_any_append(self):
        self.plan.proof = {**self.plan.proof, 'delivery_verified': True}
        store = Store(self.after)
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'containment_proof_changed'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_conflicting_existing_marker_never_overwritten(self):
        store = Store(self.after)
        store.source['pages'].append(entry(record('writer-resolution:' + subject.FAILED, 'audit_events', {'other': True})))
        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'existing_readback_resolution_conflict'): self.reconcile(store)
        self.assertFalse(store.writes)

    def test_native_matcher_rejects_forged_honesty_snapshot_delta_and_unknown_scope(self):
        _, store = self.reconcile()
        for original in store.writes[3:]:
            identity = original['payload']['github_run_id']
            for mode in ('hash', 'schema', 'scope', 'reason', 'retry', 'status', 'index', 'business', 'operation', 'snapshot', 'incident_snapshot', 'delta', 'log', 'global_effect', 'managed_effect',
                         'contained', 'native_delivery', 'native_history'):
                row = deepcopy(original)
                if mode == 'hash': row['hash'] = '0' * 64
                if mode == 'schema': row['payload']['schema'] = subject.SCHEMA.rsplit('/', 1)[0] + '/v999'
                if mode == 'scope': row['payload']['github_run_id'] = 'other'
                if mode == 'reason': row['payload']['reason'] = 'override'
                if mode == 'retry': row['payload']['retry_external_effects'] = True
                if mode == 'status': row['status'] = 'completed'
                if mode == 'index': row['payload']['index_complete'] = True
                if mode == 'business': row['payload']['business_lanes_completed'] = not row['payload']['business_lanes_completed']
                if mode == 'operation': row['payload']['business_operation_entered'] = not row['payload']['business_operation_entered']
                if mode == 'snapshot': row['payload']['evidence']['after']['state_sha256'] = '0' * 64
                if mode == 'incident_snapshot': row['payload']['evidence']['incident_snapshot']['state_sha256'] = '0' * 64
                if mode == 'global_effect': row['payload']['external_effects_reconciled'] = True
                if mode == 'managed_effect': row['payload']['managed_cloud_effects_reconciled'] = False
                if mode == 'contained': row['payload']['native_effect_unknown_contained'] = False
                if mode == 'native_delivery': row['payload']['native_delivery_verified'] = True
                if mode == 'native_history': row['payload']['native_history_incomplete'] = False
                if mode == 'delta': row['payload']['evidence']['delta']['new_receipts'] = 1
                if mode == 'log': row['payload']['evidence']['incident']['failed_runs'][subject.FAILED]['failed_job']['log_sha256'] = '0' * 64
                if mode != 'hash': row['hash'] = content_hash(row['payload'])
                with self.subTest(identity=identity, mode=mode):
                    self.assertFalse(self.matcher(identity, row, self.github, store))
            self.assertFalse(self.matcher('future', original, self.github, store))

    def test_native_matcher_rechecks_each_original_complete_job_and_changing_run(self):
        _, store = self.reconcile()
        for row in store.writes[3:]:
            identity = row['payload']['github_run_id']
            for mode in ('attempt', 'partial', 'job', 'runner', 'changing'):
                self.github = GitHub()
                if mode == 'attempt': self.github.runs[identity]['run_attempt'] = 2
                if mode == 'partial': self.github.jobs[identity]['has_more'] = True
                if mode == 'job': self.github.jobs[identity]['jobs'][0]['id'] += 1
                if mode == 'runner': self.github.jobs[identity]['jobs'][0]['runner_name'] = 'changed'
                if mode == 'changing': self.github.after_jobs = lambda seen: self.github.runs[seen].update(run_attempt=2)
                with self.subTest(identity=identity, mode=mode):
                    self.assertFalse(self.matcher(identity, row, self.github, store))

    def test_lead_matcher_rejects_peer_rerun_success_or_changed_peer_job_and_steps(self):
        _, store = self.reconcile()
        lead = store.writes[-1]

        def former_target_only_check(github):
            # Differential witness: all immutable Notion cohort records and the
            # target's own GitHub metadata can remain valid while a peer rerun
            # disappears from the failed-run listing. Target-only verification
            # incorrectly accepted this case; every original peer is required.
            self.assertTrue(self.synthetic_cohort_matches(store.get))
            self.assertTrue(all(subject._resolution_valid(identity, store.get('writer-resolution:' + identity))
                                for identity in subject.FAILURES))
            path = '/actions/runs/' + subject.FAILED
            before = github.fetch(path)
            jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
            after_jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
            after = github.fetch(path)
            return (subject._run_matches(subject.FAILED, before) and subject._job_matches(subject.FAILED, jobs)
                    and subject._run_matches(subject.FAILED, after) and before == after
                    and subject._job_matches(subject.FAILED, after_jobs) and jobs == after_jobs)

        for peer in subject.FAILURES[1:]:
            for mode in ('rerun_success', 'job', 'steps', 'job_changed_during_read', 'run_changed_during_read'):
                github = GitHub()
                if mode == 'rerun_success': github.runs[peer].update(run_attempt=2, conclusion='success')
                if mode == 'job': github.jobs[peer]['jobs'][0]['id'] += 1
                if mode == 'steps': github.jobs[peer]['jobs'][0]['steps'][0]['conclusion'] = 'success'
                if mode == 'job_changed_during_read':
                    github.after_jobs = lambda seen: github.jobs[seen]['jobs'][0].update(runner_name='changed') if seen == peer else None
                if mode == 'run_changed_during_read':
                    github.after_jobs = lambda seen: github.runs[seen].update(run_attempt=2, conclusion='success') if seen == peer else None
                with self.subTest(peer=peer, mode=mode):
                    self.assertTrue(former_target_only_check(github))
                    self.assertFalse(self.matcher(subject.FAILED, lead, github, store))
        self.assertEqual(len(store.writes), 6)

    def test_native_matcher_requires_exact_cohort_all_peers_and_record_reader(self):
        _, complete = self.reconcile()
        markers = complete.writes[3:]
        for mode in ('reader_missing', 'quarantine_missing', 'native_observation_missing', 'stop_observation_missing',
                     'peer_missing', 'peer_changed', 'peer_different_evidence'):
            store = Store(complete.source)
            remove = {'quarantine_missing': self.plan.records[0]['id'], 'native_observation_missing': self.plan.records[1]['id'],
                      'stop_observation_missing': self.plan.records[2]['id'], 'peer_missing': 'writer-resolution:' + subject.FAILURES[1]}.get(mode)
            if remove:
                store.source['pages'] = [item for item in store.source['pages'] if item['page']['id'] != 'page-' + remove]
            if mode in ('peer_changed', 'peer_different_evidence'):
                peer_id = 'writer-resolution:' + subject.FAILURES[1]
                peer = subject.validate_record(store.get(peer_id))
                if mode == 'peer_changed': peer['payload']['native_delivery_verified'] = True
                else: peer['payload']['evidence']['before']['artifact_zip_sha256'] = 'f' * 64
                changed = record(peer_id, 'audit_events', peer['payload'], peer['status'])
                store.source['pages'] = [entry(changed) if item['page']['id'] == 'page-' + peer_id else item for item in store.source['pages']]
            for marker in markers:
                identity = marker['payload']['github_run_id']
                with self.subTest(mode=mode, identity=identity):
                    if mode == 'reader_missing':
                        self.assertFalse(subject.readback_resolution_matches(identity, marker, self.github.fetch, subject.REPOSITORY))
                    else:
                        self.assertFalse(self.matcher(identity, marker, self.github, store))

    def test_real_delta_requires_explicit_exact_native_containment_and_no_extra_native_page(self):
        for mode in ('no_plan', 'changed_proof', 'changed_native', 'extra_native'):
            state = deepcopy(self.after); plan = deepcopy(self.plan)
            if mode == 'changed_proof': plan.proof['delivery_verified'] = True
            if mode == 'changed_native':
                next(item for item in state['pages'] if item['page']['id'] == plan.native_page_id)['blocks'].append(
                    {'type': 'paragraph', 'paragraph': {'rich_text': []}})
            if mode == 'extra_native': state['pages'].append({'page': {'id': 'other-native', 'properties': {}}, 'blocks': []})
            snapshot = subject.SnapshotState(state)
            with self.subTest(mode=mode), patch.multiple(subject, AFTER_FINGERPRINT=snapshot.fingerprint(), AFTER_PAGES=len(snapshot.pages)), \
                 self.assertRaises(subject.ReconciliationBlocked):
                subject.verify_delta_and_index(subject.SnapshotState(self.before), snapshot, self.baseline,
                                               None if mode == 'no_plan' else plan)

    def test_actual_delta_verifier_reconstructs_synthetic_exact_index_and_guard_audits(self):
        proof = subject.verify_delta_and_index(subject.SnapshotState(self.before), subject.SnapshotState(self.after), self.baseline, self.plan)
        self.assertEqual(proof, self.delta)
        self.assertEqual(proof['later_guard_audit_records'], 2)
        self.assertTrue(proof['last_child_later_exact_readback'])
        self.assertFalse(proof['index_complete'])
        self.assertEqual(proof['original_mismatch_cause'], 'unverified')

    def test_actual_delta_verifier_blocks_edits_missing_old_page_and_core_and_guard_marker(self):
        for mode in ('prior_page', 'prior_core', 'guard', 'new_completion', 'new_effect', 'index_child'):
            after = deepcopy(self.after)
            if mode == 'prior_page': after['pages'].pop(2)
            if mode == 'prior_core': after['pages'][0] = entry(record('contact:synthetic', 'contacts', {'suppressed': False}, 'suppressed'))
            if mode == 'guard':
                item = next(item for item in after['pages'] if item['page']['id'] == 'page-writer-failure:' + subject.FAILED)
                item['page']['created_time'] = '2026-10-21T13:00:00Z'
            if mode == 'new_completion':
                after['pages'] = [item for item in after['pages'] if item['page']['id'] != 'page-run:2026-10-21:applications:completed']
            if mode == 'new_effect': after['pages'].append(entry(record('effect', 'outreach_events', {'content': 'synthetic'}, 'sent')))
            if mode == 'index_child': after['pages'].pop()
            state = subject.SnapshotState(after)
            # Even with a newly claimed fingerprint, the independently pinned
            # full CORE map/count, immutable source, marker timing, or prefix fails.
            with self.subTest(mode=mode), patch.object(subject, 'AFTER_FINGERPRINT', state.fingerprint()), self.assertRaises(subject.ReconciliationBlocked):
                subject.verify_delta_and_index(subject.SnapshotState(self.before), state, self.baseline, self.plan)


class CLIAndPinsTests(unittest.TestCase):
    def test_real_not_yet_reviewed_pins_are_closed(self):
        with patch.object(subject, 'REVIEWED_DELTA_PROOF_HASH', ''), self.assertRaisesRegex(subject.ReconciliationBlocked, 'review_pending'):
            subject.require_reviewed_pins()

    def test_local_cli_stops_before_credentials_and_network(self):
        result = subprocess.run([sys.executable, 'scripts/reconcile_readback.py'],
            env={'GITHUB_ACTIONS': 'false', 'NOTION_TOKEN': 'synthetic-unread'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('synthetic-unread', result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['reason'], 'owner_disabled_hosted_maintenance_required')

    def test_real_complete_job_digests_and_closed_incident_records_are_well_formed(self):
        self.assertEqual(set(subject.FAILED_RUNS), set(subject.FAILURES))
        for identity in subject.FAILURES:
            self.assertTrue(subject._run_matches(identity, subject.FAILED_RUNS[identity]))
            self.assertTrue(all(subject._hash(subject.FAILED_JOBS[identity][key])
                for key in ('steps_sha256', 'jobs_sha256', 'log_sha256')))
        self.assertIn('cloud/runtime_guard.py', subject.SOURCE_HASHES)


class ArchiveTests(unittest.TestCase):
    def archived_fixture(self, folder, identity):
        _, _, source, _ = states()
        run, jobs = snapshot_metadata(identity)
        source['captured_at'] = run['updated_at']
        root = Path(folder)
        key = root / 'key.bin'; key.write_bytes(b'x' * 32); key.chmod(0o600)
        member = root / 'cloud-records.json'
        member.write_text(json.dumps({'schema': 'apply-cloud-snapshot/v1',
            'exported_at': '2026-10-21T22:00:00Z', 'snapshot': source}))
        cipher = root / 'backup.aesgcm'
        create_backup({'cloud-records.json': member}, cipher, key_path=key, private_destination_verified=True)
        ciphertext = cipher.read_bytes()
        summary = {'operation': 'pinned_index_writer_reconciliation' if identity == subject.BEFORE else 'encrypted_source_snapshot',
            'verified': True, 'resolved_run_id': '39434825788',
            'backup': {'restore_verified': True, 'sha256': subject.sha(ciphertext), 'records': len(source['pages'])}}
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, 'w') as archive:
            archive.writestr('maintenance-summary.json', json.dumps(summary))
            archive.writestr('cloud-backup-' + identity + '.aesgcm', ciphertext)
        prefix = 'BEFORE' if identity == subject.BEFORE else 'INCIDENT' if identity == subject.INCIDENT else 'AFTER'
        artifact_id = getattr(subject, prefix + '_ARTIFACT_ID')
        class Client:
            def __init__(self):
                self.run, self.jobs = deepcopy(run), deepcopy(jobs)
                self.after_jobs = None
            def fetch(self, path):
                if '/jobs?' in path:
                    value = deepcopy(self.jobs)
                    if self.after_jobs: self.after_jobs(self)
                    return value
                if '/artifacts?' in path: return {'total_count': 1, 'artifacts': [{
                    'id': artifact_id, 'name': 'reconciliation-evidence-' + identity, 'expired': False}]}
                return deepcopy(self.run)
            def download(self, artifact): return zipped.getvalue()
        patches = {prefix + '_CIPHER_HASH': subject.sha(ciphertext), prefix + '_MEMBER_HASH': subject.sha(member.read_bytes()),
            prefix + '_FINGERPRINT': subject.SnapshotState(source).fingerprint(), prefix + '_PAGES': len(source['pages']),
            prefix + '_RECORDS': len(subject.SnapshotState(source).rows),
            'SNAPSHOT_RUNS': {identity: {key: run[key] for key in subject.RUN_FIELDS}},
            'SNAPSHOT_JOBS': {identity: {'id': jobs['jobs'][0]['id'], 'jobs_sha256': content_hash(jobs),
                'steps_sha256': content_hash(jobs['jobs'][0]['steps']),
                'operation_started_at': jobs['jobs'][0]['steps'][0]['started_at']}}}
        return Client, zipped, patches, prefix

    def test_all_three_snapshot_shapes_require_key_member_wrapper_and_exact_zip_members(self):
        for identity in (subject.BEFORE, subject.INCIDENT, subject.AFTER):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as folder:
                Client, zipped, patches, prefix = self.archived_fixture(folder, identity)
                with patch.multiple(subject, **patches):
                    state, proof = subject.read_snapshot(Client(), identity, b'x' * 32)
                    self.assertEqual(proof['pages'], len(state.pages))
                    self.assertEqual(proof['operation_started_at'], subject.SNAPSHOT_JOBS[identity]['operation_started_at'])
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'authentication_failed'):
                        subject.read_snapshot(Client(), identity, b'y' * 32)
                    with patch.object(subject, prefix + '_MEMBER_HASH', '0' * 64):
                        with self.assertRaisesRegex(subject.ReconciliationBlocked, 'member_or_wrapper_invalid'):
                            subject.read_snapshot(Client(), identity, b'x' * 32)
                    with zipfile.ZipFile(zipped, 'a') as archive: archive.writestr('unexpected', 'synthetic-private')
                    with self.assertRaisesRegex(subject.ReconciliationBlocked, 'members_invalid'):
                        subject.read_snapshot(Client(), identity, b'x' * 32)

    def test_snapshot_owner_attempt_whole_jobs_and_operation_start_are_pinned(self):
        for identity in (subject.BEFORE, subject.INCIDENT, subject.AFTER):
            with tempfile.TemporaryDirectory() as folder:
                Client, _, patches, _ = self.archived_fixture(folder, identity)
                with patch.multiple(subject, **patches):
                    for mode in ('owner', 'triggering_owner', 'attempt', 'bool_attempt', 'job', 'partial_jobs',
                                 'runner', 'operation_start', 'changing_run', 'changing_jobs'):
                        client = Client()
                        if mode == 'owner': client.run['actor']['login'] = 'other'
                        if mode == 'triggering_owner': client.run['triggering_actor']['login'] = 'other'
                        if mode == 'attempt': client.run['run_attempt'] = 2
                        if mode == 'bool_attempt': client.run['run_attempt'] = True
                        if mode == 'job': client.jobs['jobs'][0]['id'] += 1
                        if mode == 'partial_jobs': client.jobs['_next_page'] = True
                        if mode == 'runner': client.jobs['jobs'][0]['runner_name'] = 'changed'
                        if mode == 'operation_start': client.jobs['jobs'][0]['steps'][0]['started_at'] = '2026-10-22T01:00:00Z'
                        if mode == 'changing_run': client.after_jobs = lambda c: c.run.update(run_attempt=2)
                        if mode == 'changing_jobs': client.after_jobs = lambda c: c.jobs['jobs'][0].update(runner_name='changed')
                        with self.subTest(identity=identity, mode=mode), self.assertRaises(subject.ReconciliationBlocked):
                            subject.read_snapshot(client, identity, b'x' * 32)


if __name__ == '__main__': unittest.main()
