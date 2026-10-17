"""Surgical reconciliation of the October 17 pre-execution guard failure.

This is not a general failure override. Only the pinned run, reviewed source
bytes, exact diagnostic, zero-job predecessor and authenticated source delta
below can establish that business execution was never entered.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile

from cloud.activation import _time
from cloud.cutover import _blocks_complete, _CapturedStore, validate_cutover_record, validate_import
from cloud.notion import AmbiguousWriteError, _text
from cloud.outreach import record
from cloud.records import content_hash
from cloud.runtime_guard import _zero_job_cancellation
from scripts.encrypted_backup import restore_archive, safe_name

REPOSITORY = 'narendranathe/apply'
FAILED = '39040229897'
PREDECESSOR = '39039307942'
BEFORE = '38968157943'
AFTER = '39041567449'
PINNED_COMMIT = 'e6c6141f9ba1233116d43e7439e191491ca92d78'
FAILED_JOB = 117418898422
BASELINE_HASH = '505295bb6da160c2ecbb4f846f81a5238a275ffe43b0fea7cf35f443628add8f'
BASELINE_RECORDS = 757
READY_CORE_HASH = 'ee2712cecffa56250275edb98b9a591027befb95b5e0d8b48f2de538cf644f04'
BEFORE_CIPHER_HASH = '847fa6f18ce611cc591c0f6c99aaee88342ab7832feda30830ec97b6866c67bc'
CORE = ('id', 'kind', 'payload', 'status', 'hash')
SOURCE_HASHES = {
    'cloud/orchestrator.py': 'bd645d245ef6dadb08b3c4dae05a0aa45f9b5875eb01ade4ebc32da16df7b18d',
    'cloud/runtime_guard.py': '1ba86d90c16ad06a4c6a1ea0049e3c73009fc03f274b117b93ee1ba19c6a6080',
    '.github/workflows/cloud-operations.yml': '85ee5287db4846e7cb13f94c6436b24ee0dd418fe5e20cbf0e0e709075bc7dd2',
}
DIAGNOSTIC = {'status': 'blocked', 'error_type': 'Blocked',
              'reason': 'previous_failed_writer_requires_reconciliation'}


class ReconciliationBlocked(ValueError):
    """Closed reason labels only; never upstream private diagnostics."""


def require(condition, reason):
    if not condition:
        raise ReconciliationBlocked(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def core(row):
    return {key: row[key] for key in CORE}


def source_metadata(value, *, data_source=False):
    # Adding a child may change the parent's edit stamps. Every identity,
    # property, permission flag, parent, and complete body remains compared.
    excluded = {'last_edited_time', 'last_edited_by'}
    if data_source:
        # Verified on the two actual captures: this top-level HTTP response
        # correlation ID changes per GET. No property/body key is excluded.
        excluded.add('request_id')
    return {key: value for key, value in value.items() if key not in excluded}


def witness():
    return record('writer-failure:' + PREDECESSOR, 'audit_events', {
        'github_run_id': PREDECESSOR, 'reason': 'failed_writer_requires_reconciliation',
        'retry_external_effects': False}, 'needs_reconciliation')


class SnapshotState:
    """Decode every managed/legacy record and retain every raw source page."""
    def __init__(self, snapshot):
        require(isinstance(snapshot, dict) and snapshot.get('schema') == 'notion-data-source-snapshot/v1'
                and snapshot.get('complete') is True and isinstance(snapshot.get('pages'), list)
                and isinstance(snapshot.get('data_source'), dict)
                and isinstance(snapshot['data_source'].get('properties'), dict)
                and _time(snapshot.get('captured_at')) is not None, 'source_snapshot_incomplete')
        self.snapshot = deepcopy(snapshot)
        self.data_source_id = snapshot.get('data_source_id')
        require(isinstance(self.data_source_id, str) and self.data_source_id
                and str(snapshot['data_source'].get('id', '')).replace('-', '').lower()
                    == self.data_source_id.replace('-', '').lower(), 'source_identity_invalid')
        self.pages, self.rows, self.page_ids = {}, {}, {}
        bodies = {}
        for item in snapshot['pages']:
            require(isinstance(item, dict) and isinstance(item.get('page'), dict), 'source_page_invalid')
            page = item['page']
            identity, props = page.get('id'), page.get('properties')
            require(isinstance(identity, str) and identity and identity not in self.pages
                    and isinstance(props, dict) and page.get('object', 'page') == 'page'
                    and all(isinstance(prop, dict) and not prop.get('has_more') for prop in props.values()),
                    'source_page_invalid')
            _blocks_complete(item.get('blocks'))
            self.pages[identity] = {**deepcopy(item), 'page': source_metadata(deepcopy(page))}
            bodies[identity] = deepcopy(item['blocks'])
        decoder = _CapturedStore(bodies)
        for identity, item in self.pages.items():
            page, props = item['page'], item['page']['properties']
            managed = (_text(props.get('Cloud ID')) or _text(props.get('Cloud Envelope'))
                       or _text(props.get('Section')) == 'Cloud')
            row = decoder._decode_cloud(deepcopy(page)) if managed else (
                decoder._decode_legacy(deepcopy(page)) if _text(props.get('Event ID')) else None)
            if row is not None:
                require(row['id'] not in self.rows, 'source_record_identity_conflict')
                self.rows[row['id']], self.page_ids[row['id']] = row, identity

    def get(self, identity):
        return deepcopy(self.rows.get(identity))

    def fingerprint(self):
        return content_hash({'data_source_id': self.data_source_id,
                             'data_source': source_metadata(self.snapshot['data_source'], data_source=True),
                             'pages': self.pages})


def _complete_list(response, field, count):
    return (isinstance(response, dict) and type(response.get('total_count')) is int
            and isinstance(response.get(field), list) and response['total_count'] == len(response[field]) == count
            and not any(response.get(key) for key in ('_next_page', 'has_more', 'incomplete_results', 'next_cursor')))


def verify_maintenance_context(github, context):
    """Prove this invocation holds the deployed central maintenance writer."""
    identity = context.get('run_id', '')
    require(re.fullmatch('[0-9]+', str(identity)) is not None, 'maintenance_run_identity_missing')
    run = github.fetch('/actions/runs/' + str(identity))
    require(str(run.get('id')) == str(identity) and run.get('head_sha') == context['commit']
            and run.get('head_branch') == 'main' and run.get('status') == 'in_progress'
            and run.get('conclusion') is None and run.get('event') == 'workflow_dispatch'
            and run.get('run_attempt') == 1 and run.get('display_title') == 'history:reconcile-startup'
            and run.get('actor', {}).get('login') == 'narendranathe'
            and run.get('triggering_actor', {}).get('login') == 'narendranathe'
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/history-reconciliation.yml',
            'central_maintenance_run_mismatch')
    response = github.fetch('/actions/runs/' + str(identity) + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(response, 'jobs', 1), 'maintenance_jobs_incomplete')
    job = response['jobs'][0]
    require(job.get('name') == 'reconcile' and job.get('status') == 'in_progress'
            and job.get('conclusion') is None and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', [])
            and sum(row.get('name') == 'Reconcile without replaying historical effects'
                    and row.get('status') == 'in_progress' and row.get('conclusion') is None
                    for row in job.get('steps', [])) == 1, 'central_maintenance_job_mismatch')
    source = github.fetch('/contents/.github/workflows/history-reconciliation.yml?ref=' + context['commit'])
    try:
        body = base64.b64decode(source['content'], validate=False)
        text = body.decode('utf-8')
    except Exception:
        raise ReconciliationBlocked('maintenance_workflow_source_unavailable') from None
    require(source.get('encoding') == 'base64'
            and body == Path('.github/workflows/history-reconciliation.yml').read_bytes()
            and len(re.findall(r'^concurrency:\s*$', text, re.MULTILINE)) == 1
            and re.search(r'^concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n', text, re.MULTILINE)
            and "if [ \"$IMPORT_MODE\" = 'reconcile-startup' ]; then\n            python scripts/reconcile_startup.py" in text,
            'central_writer_concurrency_contract_mismatch')


def verify_incident(github):
    """Independent GitHub witnesses plus the reviewed immutable source contract."""
    run = github.fetch('/actions/runs/' + FAILED)
    require(isinstance(run, dict) and str(run.get('id')) == FAILED
            and run.get('head_sha') == PINNED_COMMIT and run.get('head_branch') == 'main'
            and run.get('status') == 'completed' and run.get('conclusion') == 'failure'
            and run.get('event') == 'schedule' and run.get('run_attempt') == 1
            and run.get('display_title') == 'cloud:live:30 6 * * *'
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/cloud-operations.yml',
            'pinned_failed_run_mismatch')
    jobs = github.fetch('/actions/runs/' + FAILED + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'failed_job_history_incomplete')
    job = jobs['jobs'][0]
    require(job.get('id') == FAILED_JOB and job.get('name') == 'orchestrate'
            and job.get('status') == 'completed' and job.get('conclusion') == 'failure'
            and 'ubuntu-latest' in job.get('labels', []) and 'self-hosted' not in job.get('labels', []),
            'failed_hosted_job_mismatch')
    steps = job.get('steps')
    require(isinstance(steps, list) and len({row.get('number') for row in steps}) == len(steps),
            'failed_steps_incomplete')
    by_number = {row.get('number'): row for row in steps}
    names = {1: 'Set up job', 2: 'Run actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683',
             3: 'Run actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065',
             4: 'Run pip install -r requirements.txt', 5: 'Install isolated PDF build tools',
             6: 'Verify private destination', 7: 'Run one coordinated operation'}
    require(all(number in by_number and by_number[number].get('name') == name
                and by_number[number].get('status') == 'completed'
                and by_number[number].get('conclusion') == ('failure' if number == 7 else 'success')
                for number, name in names.items())
            and [row.get('number') for row in steps if row.get('conclusion') == 'failure'] == [7],
            'failure_not_at_startup_operation')
    logs = github.download_path('/actions/jobs/' + str(FAILED_JOB) + '/logs')
    require(isinstance(logs, bytes) and 0 < len(logs) <= 2_000_000, 'startup_log_unavailable')
    diagnostics = []
    try:
        for line in logs.decode('utf-8').splitlines():
            if '{' not in line:
                continue
            try:
                candidate = json.loads(line[line.index('{'):])
            except ValueError:
                require('"status"' not in line, 'startup_log_invalid')
                continue
            if isinstance(candidate, dict) and 'status' in candidate:
                diagnostics.append(candidate)
    except (ValueError, UnicodeError):
        raise ReconciliationBlocked('startup_log_invalid') from None
    require(diagnostics == [DIAGNOSTIC], 'startup_diagnostic_mismatch')
    for path, expected in SOURCE_HASHES.items():
        source = github.fetch('/contents/' + path + '?ref=' + PINNED_COMMIT)
        try:
            body = base64.b64decode(source['content'], validate=False)
        except Exception:
            raise ReconciliationBlocked('pinned_source_unavailable') from None
        require(source.get('encoding') == 'base64' and sha(body) == expected, 'pinned_source_contract_mismatch')
    prior = github.fetch('/actions/runs/' + PREDECESSOR)
    cancellation = _zero_job_cancellation(PREDECESSOR, prior, github.fetch, REPOSITORY)
    require(cancellation is not None and cancellation['evidence']['run']['run_attempt'] == 1
            and cancellation['evidence']['run']['head_sha'] == PINNED_COMMIT,
            'predecessor_no_job_proof_missing')
    require(github.fetch('/actions/runs/' + FAILED) == run, 'failed_run_changed_during_verification')
    run_proof = {key: run.get(key) for key in ('id', 'display_title', 'head_sha', 'head_branch',
        'status', 'conclusion', 'event', 'run_attempt', 'path', 'updated_at', 'created_at', 'run_started_at')}
    return {'failed_run': run_proof, 'failed_job': {'id': FAILED_JOB, 'steps_sha256': content_hash(steps)},
            'log_sha256': sha(logs), 'diagnostic': DIAGNOSTIC, 'source_contract': SOURCE_HASHES,
            'predecessor': cancellation}


def read_snapshot(github, run_id, key):
    """Authenticate the actual hosted artifact ZIP, archive and every member."""
    run = github.fetch('/actions/runs/' + run_id)
    require(str(run.get('id')) == run_id and run.get('head_sha') == PINNED_COMMIT
            and run.get('head_branch') == 'main' and run.get('run_attempt') == 1
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success' and run.get('display_title') == 'history:snapshot'
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/history-reconciliation.yml',
            'source_snapshot_hosted_run_invalid')
    jobs = github.fetch('/actions/runs/' + run_id + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'source_snapshot_jobs_incomplete')
    job = jobs['jobs'][0]
    require(job.get('name') == 'reconcile' and job.get('status') == 'completed'
            and job.get('conclusion') == 'success' and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', []), 'source_snapshot_hosted_job_invalid')
    response = github.fetch('/actions/runs/' + run_id + '/artifacts?per_page=100&page=1')
    require(_complete_list(response, 'artifacts', 1), 'source_snapshot_artifact_history_incomplete')
    artifact = response['artifacts'][0]
    require(artifact.get('name') == 'reconciliation-evidence-' + run_id and artifact.get('expired') is False
            and type(artifact.get('id')) is int, 'source_snapshot_artifact_invalid')
    zipped = github.download(artifact['id'])
    try:
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)) == 2 and all(safe_name(name) for name in names)
                    and set(names) == {'maintenance-summary.json', 'cloud-backup-' + run_id + '.aesgcm'}
                    and all(item.file_size <= 64_000_000 for item in archive.infolist()),
                    'source_snapshot_artifact_members_invalid')
            summary = json.loads(archive.read('maintenance-summary.json'))
            ciphertext = archive.read('cloud-backup-' + run_id + '.aesgcm')
        backup = summary.get('backup', {})
        require(summary.get('operation') == 'encrypted_source_snapshot'
                and backup.get('restore_verified') is True and backup.get('sha256') == sha(ciphertext)
                and (run_id != BEFORE or sha(ciphertext) == BEFORE_CIPHER_HASH),
                'source_snapshot_ciphertext_mismatch')
        with tempfile.TemporaryDirectory(prefix='apply-startup-reconciliation-') as folder:
            root = Path(folder)
            key_path, cipher_path = root / 'recovery.bin', root / 'snapshot.aesgcm'
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            cipher_path.write_bytes(ciphertext)
            cipher_path.chmod(0o600)
            restored = root / 'restored'
            report = restore_archive(cipher_path, restored, key_path=key_path)
            require(report.get('hashes_verified') is True and report.get('restored_files') == 1,
                    'source_snapshot_member_hashes_unverified')
            require(set(path.name for path in restored.iterdir()) == {'cloud-records.json'},
                    'source_snapshot_member_set_invalid')
            member = (restored / 'cloud-records.json').read_bytes()
            wrapper = json.loads(member)
        require(wrapper.get('schema') == 'apply-cloud-snapshot/v1', 'source_snapshot_wrapper_invalid')
        state = SnapshotState(wrapper['snapshot'])
        require(type(backup.get('records')) is int and backup['records'] == len(state.pages),
                'source_snapshot_page_count_mismatch')
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('source_snapshot_authentication_failed') from None
    return state, {'run_id': run_id, 'artifact_id': artifact['id'], 'artifact_zip_sha256': sha(zipped),
                   'archive_sha256': sha(ciphertext), 'member_sha256': sha(member),
                   'state_sha256': state.fingerprint(), 'pages': len(state.pages)}


def verify_delta(before, after, exported):
    require(content_hash(exported) == BASELINE_HASH and len(exported.get('records', [])) == BASELINE_RECORDS,
            'authenticated_baseline_mismatch')
    require(before.data_source_id == after.data_source_id
            and source_metadata(before.snapshot['data_source'], data_source=True)
                == source_metadata(after.snapshot['data_source'], data_source=True),
            'source_schema_changed')
    require(_time(before.snapshot['captured_at']) < _time(after.snapshot['captured_at']),
            'source_snapshot_order_invalid')
    old_ids, new_ids = set(before.pages), set(after.pages)
    require(old_ids <= new_ids and len(new_ids - old_ids) == 1
            and all(before.pages[identity] == after.pages[identity] for identity in old_ids),
            'source_delta_not_exact_guard_witness')
    expected = witness()
    require(expected['id'] not in before.rows and set(after.rows) - set(before.rows) == {expected['id']}
            and all(core(before.rows[identity]) == core(after.rows[identity]) for identity in before.rows)
            and core(after.rows[expected['id']]) == core(expected)
            and after.page_ids[expected['id']] in new_ids - old_ids, 'guard_witness_record_mismatch')
    for state in (before, after):
        ready = state.get('deployment:cutover:v2')
        require(ready is not None and content_hash(core(ready)) == READY_CORE_HASH, 'ready_record_changed')
        validate_cutover_record(ready)
        report = validate_import(state, exported)
        require(report['records'] == report['unchanged'] == BASELINE_RECORDS
                and report['conflicted'] == report['blocked'] == 0, 'baseline_readback_changed')
    return {'baseline_records': BASELINE_RECORDS, 'baseline_unchanged': BASELINE_RECORDS,
            'historical_manifest_sha256': BASELINE_HASH, 'ready_core_sha256': READY_CORE_HASH,
            'raw_pages_unchanged': len(before.pages), 'added_records': [expected['id']],
            'added_record_core_sha256': content_hash(core(expected))}


def reconcile(store, exported, github, context, key):
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == REPOSITORY and context.get('actor') == 'narendranathe'
            and context.get('triggering_actor', 'narendranathe') == 'narendranathe'
            and context.get('event') == 'workflow_dispatch' and context.get('cloud_enabled') == 'false'
            and re.fullmatch('[0-9a-f]{40}', context.get('commit', ''))
            and isinstance(key, bytes) and len(key) == 32, 'owner_disabled_hosted_maintenance_required')
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    incident = verify_incident(github)
    before, before_proof = read_snapshot(github, BEFORE, key)
    after, after_proof = read_snapshot(github, AFTER, key)
    delta = verify_delta(before, after, exported)
    failed_end = _time(incident['failed_run'].get('updated_at'))
    require(failed_end is not None and _time(before.snapshot['captured_at']) < failed_end
            < _time(after.snapshot['captured_at']), 'snapshot_does_not_bracket_failure')
    payload = {'schema': 'apply-pinned-startup-writer-resolution/v1', 'github_run_id': FAILED,
               'reason': 'startup_guard_blocked_before_business_execution',
               'external_effects_reconciled': True, 'retry_external_effects': False,
               'evidence': {'run_url': 'https://github.com/' + REPOSITORY + '/actions/runs/' + FAILED,
                            'commit': PINNED_COMMIT, 'incident': incident,
                            'before': before_proof, 'after': after_proof, 'delta': delta}}
    expected = record('writer-resolution:' + FAILED, 'audit_events', payload, 'reconciled_no_business_execution')
    # Fresh whole-source capture under the shared writer lock, rather than an
    # absent run marker, verifies no later writes/effects escaped reconciliation.
    current = SnapshotState(store.snapshot_all())
    require(current.data_source_id == getattr(store, 'data_source_id', None) == after.data_source_id,
            'fresh_source_identity_mismatch')
    require(_time(current.snapshot['captured_at']) >= _time(after.snapshot['captured_at']),
            'fresh_source_capture_predates_snapshot')
    old = current.get(expected['id'])
    if old is not None:
        require(core(old) == core(expected), 'existing_startup_resolution_conflict')
        current.pages.pop(current.page_ids[expected['id']])
    require(current.fingerprint() == after.fingerprint(), 'fresh_source_changed_since_snapshot')
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    require(verify_incident(github) == incident, 'incident_evidence_changed_before_append')
    if old is None:
        written = store.put(expected)
        observed = store.get(expected['id'])
        if (not isinstance(written, dict) or not isinstance(observed, dict)
                or core(written) != core(expected) or core(observed) != core(expected)):
            raise AmbiguousWriteError('Startup reconciliation readback uncertain; stop for reconciliation')
    return {'operation': 'pinned_startup_writer_reconciliation', 'verified': True, 'unchanged': old is not None,
            'resolved_run_id': FAILED, 'resolution_sha256': expected['hash'], 'external_effects': 0,
            'cloud_enabled_changed': False, 'baseline_unchanged': BASELINE_RECORDS,
            'ready_record_changed': False}
