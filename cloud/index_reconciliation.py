"""One pinned index-publication recovery; never replays completed business work."""
from __future__ import annotations

import base64
from collections import Counter
import io
import json
from pathlib import Path
import re
import tempfile
import zipfile
from zoneinfo import ZoneInfo

from cloud.agent_index import GENERATED_KINDS, SCHEMA as INDEX_SCHEMA, build_index
from cloud.cutover import validate_cutover_record, validate_import
from cloud.index_publisher import _reconciled_source_proof, snapshot_hash
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash, validate_record
from cloud.scheduled_reconciliation import _diagnostics, _effects, RUN_FIELDS, SLOT, slot_resolution_matches
from cloud.startup_reconciliation import (
    BASELINE_HASH, BASELINE_RECORDS, READY_CORE_HASH, REPOSITORY,
    ReconciliationBlocked, SnapshotState, _complete_list, _time, core, require, sha, source_metadata,
)
from scripts.encrypted_backup import restore_archive, safe_name

SCHEMA = 'apply-pinned-index-writer-resolution/v1'
FAILED = '39434825788'
PINNED_COMMIT = '82b3ac7462baa083ff3f7a95b4586616bff8045d'
BEFORE = '39363139229'
BEFORE_COMMIT = '4f23fc07ccc5cefd27e204c727f2859faeb3643e'
BEFORE_CIPHER_HASH = '18e9e3f4d1512f697ccd890f8be8acfc1eb55e7e39d10138175b2324f60cdab1'
BEFORE_MEMBER_HASH = '7368cd30e3a83e7940795cf393c834b7882caf0715d3483b9faf9a77347cc7b5'
BEFORE_FINGERPRINT = '5770f52425d2add8542ce8a36eb0dbbbc32dd92200908ffdaff009b46d4bdd14'
BEFORE_PAGES = 2148
BEFORE_ARTIFACT_ID = 12239565230
AFTER = '39440953412'
AFTER_CIPHER_HASH = 'a8753b5cb7be6ad8892446bb80ddfc1442c8b55e34b93db55a8ebc38dd8e156f'
AFTER_MEMBER_HASH = '61b4dc9186a456f8a424e186474007b7b78063ca0d062eda22e49c1a1958a73a'
AFTER_FINGERPRINT = '0120654a16cbcafb8aebee1a751e0093311639c7208f6f92ba2270e15b6e8dd9'
AFTER_PAGES = 2247
AFTER_ARTIFACT_ID = 12274468698
FAILED_JOB = 118668972994
FAILED_STEPS_HASH = '44312003d9ee5f9d9cb4175b6ad2e0c093b4321b6e14a103cc330fdc3c467e3d'
FAILED_LOG_HASH = '76451c2571ef9896fc892688537848d997a885be11527d8f53e51d096b94c294'
FAILED_RUN = {'conclusion': 'failure', 'created_at': '2026-10-20T13:56:48Z',
    'display_title': 'cloud:live:all', 'event': 'workflow_dispatch', 'head_branch': 'main',
    'head_sha': PINNED_COMMIT, 'id': int(FAILED), 'path': '.github/workflows/cloud-operations.yml',
    'run_attempt': 1, 'run_started_at': '2026-10-20T13:56:48Z', 'status': 'completed',
    'updated_at': '2026-10-20T14:35:19Z'}
DIAGNOSTIC = {'cloud_operation': 'publish_agent_index', 'error_type': 'IndexPublicationError',
              'reason': 'inspect_private_reconciliation_records', 'status': 'blocked'}
SOURCE_HASHES = {
    'config/cloud.json': '102aec4611c61704a6a0be172c1d03451bbdfea0adeae44b0ad73fb50f76f52d',
    'cloud/applications.py': '419a58c61273b131344d59fbe671f301cd3898268153eba1cbd63e15931ba8a0',
    'cloud/orchestrator.py': 'cda8e890ade51889b0ded9ec7dd4817109c0302ef0302f9fb1e23941ab879b9b',
    'cloud/outreach.py': '6ee0d1dcf85dcaba5178c63edf011b33d4e13ef2bec896fba17c2ef6b3171a61',
    'cloud/discovery.py': '772c317c0f95eb6b065834f9a2e08e89e1e0c2bfb3334debfad5ae5b91a2fd73',
    'cloud/submission.py': '3b8bf9c922cf30008393784ad996c590d319b45582157bc4276b33566ed5ffa7',
    'cloud/preparation.py': '0619f3de4da66a7763928c9324ab1cc111568e60eeb3c068ba6ef6550606ee77',
    'cloud/agent_worker.py': '6f1631765b4d93807d5896fca795247190df313d39960e867020e72f324c3438',
    'cloud/agent_dispatcher.py': '4ad8db58d923afb51dbe0d4053e7b5d2c9f3c05e5b1eb0347db993903e1a8865',
    'cloud/agent_index.py': '7a63ce93fb1fdeee5c1df41bee9e3fd4c4ad07bec9b36a9bbd9dabfafe9bf6d5',
    'cloud/index_publisher.py': 'b469b324bed90be0b40ac8af6c93ebefa1e13d39d58ceea6d0f52f912389864d',
    'cloud/notion.py': 'c50bf7822786fcf2ed77bfecb3fb9d0700d5ae2e5dfeb74e7af0b5f07fd50014',
    'cloud/reporting.py': '0a4495a6285cf4cae33fa01726dca0cadfed099762e45c99d84e574b2eba7fd8',
    'cloud/records.py': '1703a35182a91636bff29c7de0a71b33f12e592f9fd26f17736d7ebee5583103',
    'cloud/coordination_quarantine.py': '93041a5845896b0bfef81718841ea3aceebdc29d3a55995225cfcf8dd2d7ddc5',
    'docs/ground-truth.md': 'e09e1bb05ea9c164e5f543903d1d1c70bc90727361f6d5fff71fd47213add74f',
    '.github/workflows/cloud-operations.yml': '85ee5287db4846e7cb13f94c6436b24ee0dd418fe5e20cbf0e0e709075bc7dd2',
}
# Set only from the independently verified pure proof of these exact captures.
REVIEWED_DELTA_PROOF_HASH = '41592f5c5888bb27a722a489db5e12d441dd07f8ecf11711defa7feae0b651fe'
ADDED_CORES_HASH = 'f73de08a5d967a8e740813aaaabbbb884815a0f9f3ae01d7a42cd9fb669b0cfe'
BUSINESS_CORES_HASH = '08d09958c2e349c6b2e39081e8d952c83980e400b5f50b1c5c9af406693174e2'
# The independent constructor audit rebuilt all 82 exact business records.
# This digest binds the reviewed audit, rather than claiming to perform public
# job reads or replay original business execution inside this maintenance job.
REVIEWED_CONSTRUCTOR_AUDIT_HASH = '1cf9d5ef744f26673bd98d6b6205ae392471572d1b52168a01cd9d89eaf74dd8'
ADDED_COUNTS = {'agent_runs': 12, 'conversation_observations': 64, 'entity_projections': 17,
                'funnel_reports': 1, 'jobs': 4, 'sync_cursors': 1}
EXPECTED_INDEX_COUNTS = {'entity_projections': 301, 'agent_tasks': 186, 'index_changes': 263, 'index_manifests': 1}
EXPECTED_INDEX_MANIFEST_HASH = '4cf921dd5e336dc2ba9c5fc142edd417b28db5ece77cb1adf018c9b3379aa4bb'


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def require_reviewed_pins():
    require(_hash(REVIEWED_DELTA_PROOF_HASH), 'index_snapshot_review_pending')


def verify_maintenance_context(github, context):
    identity = context.get('run_id', '')
    require(re.fullmatch('[0-9]+', str(identity)) is not None, 'maintenance_run_identity_missing')
    run = github.fetch('/actions/runs/' + str(identity))
    require(str(run.get('id')) == str(identity) and run.get('head_sha') == context['commit']
            and run.get('head_branch') == 'main' and run.get('status') == 'in_progress'
            and run.get('conclusion') is None and run.get('event') == 'workflow_dispatch'
            and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and run.get('display_title') == 'history:reconcile-index'
            and run.get('actor', {}).get('login') == 'narendranathe'
            and run.get('triggering_actor', {}).get('login') == 'narendranathe'
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/history-reconciliation.yml',
            'central_maintenance_run_mismatch')
    jobs = github.fetch('/actions/runs/' + str(identity) + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'maintenance_jobs_incomplete')
    job = jobs['jobs'][0]
    require(job.get('name') == 'reconcile' and job.get('status') == 'in_progress'
            and job.get('conclusion') is None and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', [])
            and sum(step.get('name') == 'Reconcile without replaying historical effects'
                    and step.get('status') == 'in_progress' and step.get('conclusion') is None
                    for step in job.get('steps', [])) == 1, 'central_maintenance_job_mismatch')
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
            and re.search(r'(?:if|elif) \[ "\$IMPORT_MODE" = \'reconcile-index\' \]; then\n            python scripts/reconcile_index.py', text),
            'central_writer_concurrency_contract_mismatch')


def _job_matches(jobs):
    if not _complete_list(jobs, 'jobs', 1):
        return False
    job = jobs['jobs'][0]
    return (type(job.get('id')) is int and job['id'] == FAILED_JOB and job.get('name') == 'orchestrate'
            and job.get('status') == 'completed' and job.get('conclusion') == 'failure'
            and 'ubuntu-latest' in job.get('labels', []) and 'self-hosted' not in job.get('labels', [])
            and isinstance(job.get('steps'), list) and content_hash(job['steps']) == FAILED_STEPS_HASH)


def _run_matches(value):
    return (isinstance(value, dict) and type(value.get('id')) is int
            and type(value.get('run_attempt')) is int
            and {key: value.get(key) for key in RUN_FIELDS} == FAILED_RUN)


def verify_incident(github):
    sources = {}
    for path, expected in SOURCE_HASHES.items():
        source = github.fetch('/contents/' + path + '?ref=' + PINNED_COMMIT)
        try:
            body = base64.b64decode(source['content'], validate=False)
        except Exception:
            raise ReconciliationBlocked('pinned_source_unavailable') from None
        require(source.get('encoding') == 'base64' and sha(body) == expected, 'pinned_source_contract_mismatch')
        sources[path] = body
    require(json.loads(sources['config/cloud.json']).get('applications', {}).get('employers') == {},
            'submission_impossibility_contract_changed')
    path = '/actions/runs/' + FAILED
    run = github.fetch(path)
    require(_run_matches(run), 'pinned_failed_run_mismatch')
    require(_job_matches(github.fetch(path + '/jobs?filter=all&per_page=100&page=1')),
            'failed_job_history_incomplete_or_changed')
    # Parse only in memory; the record stores the closed diagnostic and digest.
    logs = github.download_path('/actions/jobs/' + str(FAILED_JOB) + '/logs')
    require(_diagnostics(logs) == [DIAGNOSTIC] and sha(logs) == FAILED_LOG_HASH, 'index_diagnostic_mismatch')
    after = github.fetch(path)
    require(_run_matches(after) and after == run, 'failed_run_changed_during_verification')
    return {'failed_run': FAILED_RUN, 'failed_job': {'id': FAILED_JOB, 'steps_sha256': FAILED_STEPS_HASH},
            'log_sha256': sha(logs), 'diagnostic': DIAGNOSTIC, 'source_contract': SOURCE_HASHES}


def read_snapshot(github, identity, key):
    require(identity in (BEFORE, AFTER), 'source_snapshot_identity_invalid')
    before = identity == BEFORE
    expected_commit = BEFORE_COMMIT if before else PINNED_COMMIT
    expected_cipher = BEFORE_CIPHER_HASH if before else AFTER_CIPHER_HASH
    expected_member = BEFORE_MEMBER_HASH if before else AFTER_MEMBER_HASH
    expected_state = BEFORE_FINGERPRINT if before else AFTER_FINGERPRINT
    expected_pages = BEFORE_PAGES if before else AFTER_PAGES
    path = '/actions/runs/' + identity
    run = github.fetch(path)
    require(str(run.get('id')) == identity and run.get('head_sha') == expected_commit
            and run.get('head_branch') == 'main' and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and run.get('display_title') == ('history:reconcile-scheduled' if before else 'history:snapshot')
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/history-reconciliation.yml',
            'source_snapshot_hosted_run_invalid')
    jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'source_snapshot_jobs_incomplete')
    job = jobs['jobs'][0]
    require(job.get('name') == 'reconcile' and job.get('status') == 'completed'
            and job.get('conclusion') == 'success' and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', []), 'source_snapshot_hosted_job_invalid')
    response = github.fetch(path + '/artifacts?per_page=100&page=1')
    require(_complete_list(response, 'artifacts', 1), 'source_snapshot_artifact_history_incomplete')
    artifact = response['artifacts'][0]
    require(artifact.get('name') == 'reconciliation-evidence-' + identity and artifact.get('expired') is False
            and type(artifact.get('id')) is int
            and artifact['id'] == (BEFORE_ARTIFACT_ID if before else AFTER_ARTIFACT_ID),
            'source_snapshot_artifact_invalid')
    zipped = github.download(artifact['id'])
    try:
        cipher_name = 'cloud-backup-' + identity + '.aesgcm'
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)) == 2 and set(names) == {'maintenance-summary.json', cipher_name}
                    and all(safe_name(name) for name in names)
                    and all(item.file_size <= 64_000_000 for item in archive.infolist()),
                    'source_snapshot_artifact_members_invalid')
            summary = json.loads(archive.read('maintenance-summary.json'))
            ciphertext = archive.read(cipher_name)
        backup = summary.get('backup', {})
        require(backup.get('restore_verified') is True and backup.get('sha256') == sha(ciphertext) == expected_cipher
                and summary.get('operation') == ('pinned_scheduled_writer_reconciliation' if before else 'encrypted_source_snapshot')
                and (not before or summary.get('verified') is True), 'source_snapshot_ciphertext_mismatch')
        with tempfile.TemporaryDirectory(prefix='apply-index-reconciliation-') as folder:
            root = Path(folder)
            key_path, cipher_path = root / 'recovery.bin', root / 'snapshot.aesgcm'
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            cipher_path.write_bytes(ciphertext)
            cipher_path.chmod(0o600)
            restored = root / 'restored'
            restored_proof = restore_archive(cipher_path, restored, key_path=key_path)
            require(restored_proof.get('hashes_verified') is True and restored_proof.get('restored_files') == 1
                    and {member.name for member in restored.iterdir()} == {'cloud-records.json'},
                    'source_snapshot_member_hashes_unverified')
            member = (restored / 'cloud-records.json').read_bytes()
            wrapper = json.loads(member)
        require(sha(member) == expected_member and wrapper.get('schema') == 'apply-cloud-snapshot/v1',
                'source_snapshot_member_or_wrapper_invalid')
        state = SnapshotState(wrapper['snapshot'])
        require(type(backup.get('records')) is int and backup['records'] == len(state.pages) == expected_pages
                and state.fingerprint() == expected_state, 'source_snapshot_content_mismatch')
        require(_time(wrapper.get('exported_at')) is not None
                and _time(wrapper['exported_at']) >= _time(state.snapshot['captured_at']),
                'source_snapshot_export_time_invalid')
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('source_snapshot_authentication_failed') from None
    require(github.fetch(path) == run, 'source_snapshot_run_changed')
    return state, {'run_id': identity, 'artifact_id': artifact['id'], 'artifact_zip_sha256': sha(zipped),
                   'archive_sha256': sha(ciphertext), 'member_sha256': sha(member),
                   'state_sha256': state.fingerprint(), 'pages': len(state.pages)}


def verify_delta_and_index(before, after, exported):
    """Bind exact reviewed effects and reconstruct the incomplete index prefix.

    No provider is called. The fixed hashes preserve the independent review of
    original outreach/reporting constructors and saved official-read job inputs.
    Index construction is repeated locally from the exact authoritative rows.
    """
    require(content_hash(exported) == BASELINE_HASH and len(exported.get('records', [])) == BASELINE_RECORDS,
            'authenticated_baseline_mismatch')
    require(before.fingerprint() == BEFORE_FINGERPRINT and after.fingerprint() == AFTER_FINGERPRINT
            and len(before.pages) == BEFORE_PAGES and len(after.pages) == AFTER_PAGES,
            'reviewed_source_fingerprint_mismatch')
    require(before.data_source_id == after.data_source_id
            and source_metadata(before.snapshot['data_source'], data_source=True)
                == source_metadata(after.snapshot['data_source'], data_source=True), 'source_schema_changed')
    require(set(before.pages) <= set(after.pages)
            and all(before.pages[identity] == after.pages[identity] for identity in before.pages), 'prior_source_page_changed')
    before_rows = {identity: validate_record(row) for identity, row in before.rows.items()}
    after_rows = {identity: validate_record(row) for identity, row in after.rows.items()}
    require(len(before_rows) == 1902 and set(before_rows) <= set(after_rows)
            and all(after_rows[identity] == row for identity, row in before_rows.items()), 'prior_source_core_changed')
    added = {identity: row for identity, row in after_rows.items() if identity not in before_rows}
    require(len(added) == 99 and dict(Counter(row['kind'] for row in added.values())) == ADDED_COUNTS
            and content_hash({identity: content_hash(row) for identity, row in added.items()}) == ADDED_CORES_HASH
            and set(after.pages) - set(before.pages) == {after.page_ids[identity] for identity in added},
            'source_delta_not_exact_reviewed_records')
    require(_effects(before) == _effects(after), 'new_attempt_receipt_or_outreach_effect')
    for state in (before, after):
        ready = state.get('deployment:cutover:v2')
        require(ready is not None and content_hash(core(ready)) == READY_CORE_HASH, 'ready_record_changed')
        validate_cutover_record(ready)
        report = validate_import(state, exported)
        require(report['records'] == report['unchanged'] == BASELINE_RECORDS
                and report['conflicted'] == report['blocked'] == 0, 'baseline_readback_changed')
    queue_kinds = ('agent_execution_queue', 'agent_proposals', 'agent_observations', 'agent_action_results',
                   'agent_action_reservations', 'agent_action_executions')
    require(not any(row['kind'] in queue_kinds for row in after_rows.values()), 'agent_admission_or_execution_present')
    generated = lambda row: (row['kind'] in GENERATED_KINDS
        or row['kind'] == 'agent_tasks' and row['payload'].get('schema') == INDEX_SCHEMA)
    business = {identity: row for identity, row in added.items() if not generated(row)}
    require(len(business) == 82 and content_hash({identity: content_hash(row) for identity, row in business.items()})
            == BUSINESS_CORES_HASH, 'reviewed_business_core_changed')
    reports = [row for row in added.values() if row['kind'] == 'funnel_reports'
               and row['payload'].get('schema') == 'qualified-funnel/v1']
    require(len(reports) == 1, 'unique_new_funnel_report_required')
    now = _time(reports[0]['payload'].get('observed_at'))
    require(now is not None and _time(FAILED_RUN['run_started_at']) <= now <= _time(FAILED_RUN['updated_at'])
            and now <= _time(after.snapshot['captured_at']), 'frozen_business_timestamp_invalid')
    local_day = now.astimezone(ZoneInfo('America/Chicago')).date().isoformat()
    cursors = [row for row in added.values() if row['kind'] == 'sync_cursors']
    require(len(cursors) == 1 and cursors[0]['status'] == 'readback_verified'
            and cursors[0]['payload'].get('complete') is True
            and cursors[0]['payload'].get('report_id') == reports[0]['id']
            and cursors[0]['payload'].get('observed_at') == reports[0]['payload']['observed_at'],
            'frozen_reporting_cursor_invalid')
    for identity, row in after_rows.items():
        if row['kind'] != 'agent_runs' or not identity.endswith(':started'):
            continue
        run_id = identity.removesuffix(':started')
        completed = after_rows.get(run_id + ':completed')
        if completed is None:
            require(run_id == SLOT and slot_resolution_matches(SLOT, after.get(SLOT + ':reconciled'),
                    after.get('writer-resolution:39135562489')), 'unresolved_started_business_slot')
            continue
        require(completed['kind'] == 'agent_runs' and completed['status'] == 'completed', 'business_slot_completion_invalid')
    require(after.get(SLOT + ':completed') is None, 'old_failed_slot_falsely_completed')
    completed_new = [row for row in business.values() if row['kind'] == 'agent_runs' and row['id'].endswith(':completed')]
    require(len(completed_new) == 6, 'exact_six_new_completed_slots_required')
    for completed in completed_new:
        payload = completed['payload']
        identity = completed['id'].removesuffix(':completed')
        started = after_rows.get(identity + ':started')
        marker = {key: payload.get(key) for key in ('date', 'lane', 'actor', 'dry_run', 'mode', 'persistent')}
        require(started is not None and started == record(identity + ':started', 'agent_runs', marker, 'started')
                and marker['actor'] == 'github-actions' and marker['mode'] == 'live'
                and marker['persistent'] is True and marker['dry_run'] is False
                and marker['lane'] in ('accepted', 'first_touch', 'applications')
                and identity == 'run:' + marker['date'] + ':' + marker['lane'], 'business_slot_marker_invalid')
        summary = payload.get('summary')
        require(isinstance(summary, dict) and summary.get('lane') == marker['lane'], 'business_lane_summary_invalid')
        if marker['date'] < local_day:
            require(summary == {'lane': marker['lane'], 'date': marker['date'], 'status': 'missed_research_only',
                                'external_effects': 0}, 'historical_slot_replayed')
        elif marker['date'] == local_day:
            if marker['lane'] == 'applications':
                require(summary.get('dry_run') is False and all(summary.get(key) == 0 for key in
                    ('submission_attempts', 'applied_verified', 'submission_unknown')), 'application_summary_effect_or_uncertainty')
            else:
                require(summary.get('sent') == 0, 'outreach_summary_has_send')
        else:
            raise ReconciliationBlocked('future_business_slot')
    # The original constructor module is part of the reviewed source contract.
    require(sha(Path('cloud/agent_index.py').read_bytes()) == SOURCE_HASHES['cloud/agent_index.py'],
            'index_builder_source_changed')
    previous_manifests = [row for row in before_rows.values() if row['kind'] == 'index_manifests']
    require(bool(previous_manifests), 'reference_index_manifest_missing')
    latest = max(_time(row['payload'].get('generated_at')) for row in previous_manifests)
    candidates = [row for row in previous_manifests if _time(row['payload'].get('generated_at')) == latest]
    require(len(candidates) == 1, 'reference_index_timestamp_conflict')
    previous = candidates[0]
    source = [row for row in after_rows.values() if not generated(row)]
    complete, conflicts = _reconciled_source_proof(source, exported, after_rows.get('deployment:cutover:v2'))
    require(complete is True and conflicts == [], 'complete_index_source_not_proven')
    source_hash = snapshot_hash(source)
    expected = build_index(source, now, previous_manifest=previous,
                          source_snapshot_hash={'hash': source_hash, 'complete': True, 'conflicts': []})
    require(dict(Counter(row['kind'] for row in expected)) == EXPECTED_INDEX_COUNTS, 'index_reconstruction_shape_changed')
    manifest = next(row for row in expected if row['kind'] == 'index_manifests')
    require(manifest['payload'].get('complete') is True and manifest['payload'].get('conflicts') == []
            and manifest['hash'] == EXPECTED_INDEX_MANIFEST_HASH and after.get(manifest['id']) is None,
            'incomplete_index_manifest_proof_changed')
    require(all(after_rows.get(row['id']) in (None, row) for row in expected), 'index_child_core_conflict')
    prefix = next((index for index, row in enumerate(expected) if after_rows.get(row['id']) != row), len(expected))
    new_generated = {identity: row for identity, row in added.items() if generated(row)}
    require(prefix == 29 and len(new_generated) == 17
            and new_generated == {row['id']: row for row in expected[:prefix] if row['id'] not in before_rows}
            and all(row['kind'] == 'entity_projections' for row in new_generated.values())
            and after_rows.get(expected[prefix]['id']) is None, 'index_prefix_not_exact_reviewed_publication')
    return {'schema': 'apply-pinned-index-source-proof/v1', 'baseline_unchanged': BASELINE_RECORDS,
            'historical_manifest_sha256': BASELINE_HASH, 'ready_core_sha256': READY_CORE_HASH,
            'prior_pages_unchanged': BEFORE_PAGES, 'prior_cores_unchanged': len(before_rows),
            'added_records': len(added), 'added_cores_sha256': ADDED_CORES_HASH,
            'reviewed_business_records': len(business), 'reviewed_business_cores_sha256': BUSINESS_CORES_HASH,
            'reviewed_constructor_audit_sha256': REVIEWED_CONSTRUCTOR_AUDIT_HASH,
            'new_attempts': 0, 'new_receipts': 0, 'new_outreach_events': 0, 'agent_admissions': 0,
            'new_completed_slots': len(completed_new), 'old_failed_slot_unchanged': True,
            'index_source_sha256': source_hash, 'previous_manifest_sha256': previous['hash'],
            'expected_manifest_sha256': manifest['hash'], 'partial_prefix_records': prefix,
            'new_partial_children': len(new_generated), 'index_complete': False}


def resolution_record(incident, before_proof, after_proof, delta):
    return record('writer-resolution:' + FAILED, 'audit_events', {
        'schema': SCHEMA, 'github_run_id': FAILED, 'reason': 'business_complete_index_publication_incomplete',
        'external_effects_reconciled': True, 'retry_external_effects': False,
        'business_lanes_completed': True, 'index_complete': False,
        'evidence': {'incident': incident, 'before': before_proof, 'after': after_proof, 'delta': delta}},
        'reconciled_business_complete_index_incomplete')


def _snapshot_proof_valid(proof, before):
    return (isinstance(proof, dict)
            and set(proof) == {'run_id', 'artifact_id', 'artifact_zip_sha256', 'archive_sha256', 'member_sha256', 'state_sha256', 'pages'}
            and proof['run_id'] == (BEFORE if before else AFTER)
            and type(proof['artifact_id']) is int
            and proof['artifact_id'] == (BEFORE_ARTIFACT_ID if before else AFTER_ARTIFACT_ID)
            and _hash(proof['artifact_zip_sha256'])
            and proof['archive_sha256'] == (BEFORE_CIPHER_HASH if before else AFTER_CIPHER_HASH)
            and proof['member_sha256'] == (BEFORE_MEMBER_HASH if before else AFTER_MEMBER_HASH)
            and proof['state_sha256'] == (BEFORE_FINGERPRINT if before else AFTER_FINGERPRINT)
            and type(proof['pages']) is int and proof['pages'] == (BEFORE_PAGES if before else AFTER_PAGES))


def _resolution_valid(identity, marker):
    try:
        require_reviewed_pins()
        row = validate_record(marker)
        payload = row['payload']
        if not (identity == FAILED and row['id'] == 'writer-resolution:' + FAILED and row['kind'] == 'audit_events'
                and row['status'] == 'reconciled_business_complete_index_incomplete'
                and set(payload) == {'schema', 'github_run_id', 'reason', 'external_effects_reconciled',
                                    'retry_external_effects', 'business_lanes_completed', 'index_complete', 'evidence'}
                and payload['schema'] == SCHEMA and payload['github_run_id'] == FAILED
                and payload['reason'] == 'business_complete_index_publication_incomplete'
                and payload['external_effects_reconciled'] is True and payload['retry_external_effects'] is False
                and payload['business_lanes_completed'] is True and payload['index_complete'] is False):
            return False
        evidence = payload['evidence']
        incident = evidence['incident']
        return (set(evidence) == {'incident', 'before', 'after', 'delta'}
                and _snapshot_proof_valid(evidence['before'], True) and _snapshot_proof_valid(evidence['after'], False)
                and content_hash(evidence['delta']) == REVIEWED_DELTA_PROOF_HASH
                and set(incident) == {'failed_run', 'failed_job', 'log_sha256', 'diagnostic', 'source_contract'}
                and incident['failed_run'] == FAILED_RUN
                and type(incident['failed_run'].get('id')) is int
                and type(incident['failed_run'].get('run_attempt')) is int
                and incident['failed_job'] == {'id': FAILED_JOB, 'steps_sha256': FAILED_STEPS_HASH}
                and incident['log_sha256'] == FAILED_LOG_HASH and incident['diagnostic'] == DIAGNOSTIC
                and incident['source_contract'] == SOURCE_HASHES)
    except Exception:
        return False


def index_resolution_matches(identity, marker, fetch, repository):
    """Only the exact original attempt/job may inherit this no-retry resolution."""
    try:
        if repository != REPOSITORY or not _resolution_valid(identity, marker):
            return False
        path = '/actions/runs/' + FAILED
        before = fetch(path)
        if not _run_matches(before):
            return False
        if not _job_matches(fetch(path + '/jobs?filter=all&per_page=100&page=1')):
            return False
        after = fetch(path)
        return _run_matches(after)
    except Exception:
        return False


def reconcile(store, exported, github, context, key):
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == REPOSITORY and context.get('actor') == 'narendranathe'
            and context.get('triggering_actor') == 'narendranathe' and context.get('event') == 'workflow_dispatch'
            and context.get('cloud_enabled') == 'false' and re.fullmatch('[0-9a-f]{40}', context.get('commit', ''))
            and isinstance(key, bytes) and len(key) == 32, 'owner_disabled_hosted_maintenance_required')
    require_reviewed_pins()
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    incident = verify_incident(github)
    before, before_proof = read_snapshot(github, BEFORE, key)
    after, after_proof = read_snapshot(github, AFTER, key)
    require(_time(before.snapshot['captured_at']) < _time(FAILED_RUN['run_started_at'])
            <= _time(FAILED_RUN['updated_at']) < _time(after.snapshot['captured_at']), 'snapshot_does_not_bracket_failure')
    delta = verify_delta_and_index(before, after, exported)
    require(content_hash(delta) == REVIEWED_DELTA_PROOF_HASH, 'reviewed_index_delta_proof_changed')
    expected = resolution_record(incident, before_proof, after_proof, delta)
    current = SnapshotState(store.snapshot_all())
    require(current.data_source_id == getattr(store, 'data_source_id', None) == after.data_source_id
            and _time(current.snapshot['captured_at']) >= _time(after.snapshot['captured_at']),
            'fresh_source_identity_or_time_mismatch')
    old = current.get(expected['id'])
    if old is not None:
        require(core(old) == core(expected), 'existing_index_resolution_conflict')
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
            raise AmbiguousWriteError('Index reconciliation readback uncertain; stop for reconciliation')
    return {'operation': 'pinned_index_writer_reconciliation', 'verified': True, 'unchanged': old is not None,
            'resolved_run_id': FAILED, 'resolution_sha256': expected['hash'], 'external_effects': 0,
            'business_lanes_completed': True, 'index_complete': False, 'baseline_unchanged': BASELINE_RECORDS,
            'cloud_enabled_changed': False, 'ready_record_changed': False}
