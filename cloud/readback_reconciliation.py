"""Pinned October 21 index readback recovery; never replays business work."""
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

SCHEMA = 'apply-pinned-index-readback-writer-resolution/v2'
FAILED = '39608197676'
FAILURES = (FAILED, '39629503187', '39631067173')
PINNED_COMMIT = '34856cd47a41cc9f4433208113210bc94407d373'
BEFORE = '39545315317'
BEFORE_COMMIT = 'c2d3ae6064b6c24c56041a7d0727f7eb3699b794'
BEFORE_CIPHER_HASH = '3691a777eda6b027cb5e7956cc6bee5d4967b0bc1094f1a59381621030910d8f'
BEFORE_MEMBER_HASH = '8c805006cddfaee29d1dfb4cc68490326afe47566e4f6f1483faf54813aa1acc'
BEFORE_FINGERPRINT = 'c05a58afa1d7a7b78a6c3f41e2489aed68ee16566722d9852b2059e24a0bc968'
BEFORE_PAGES = 2248
BEFORE_RECORDS = 2002
BEFORE_ARTIFACT_ID = 12322558229
INCIDENT = '39649529296'
INCIDENT_COMMIT = '2d566a271bb030747df51e79f5a8d70549df515c'
INCIDENT_CIPHER_HASH = 'c62ea1d0dc890426fb383defe45e59b0bbc7a24da96d288daf274e57f0741ea7'
INCIDENT_MEMBER_HASH = '50a49e9dad83592b3bc33ac2b735075d332f227dae1a3a7532e01095beaf513e'
INCIDENT_FINGERPRINT = '51ee7332471f1fdb64abc49bf78b944bd7e43ca49e6dbae7776abcf1b8d946d0'
INCIDENT_PAGES = 2897
INCIDENT_RECORDS = 2650
INCIDENT_ARTIFACT_ID = 12370258354
AFTER = '39676723486'
AFTER_COMMIT = 'e86c50049cdb64e1a3a53a19cdd793369dd1a56d'
# Actual complete post-stop snapshot independently authenticated and reviewed.
# No caller, environment or workflow input can supply or replace these pins.
AFTER_CIPHER_HASH = 'ae5298a92b37fc04becdbb465790891e99cbdd704a1154898aec3343810e30bd'
AFTER_MEMBER_HASH = '5fb3b865ae4718703e0540fb0534c93f9a0177b6837a2eb93006069bd6eb9c06'
AFTER_FINGERPRINT = '51ee7332471f1fdb64abc49bf78b944bd7e43ca49e6dbae7776abcf1b8d946d0'
AFTER_PAGES = 2897
AFTER_RECORDS = 2650
AFTER_ARTIFACT_ID = 12383531835
REVIEWED_DELTA_PROOF_HASH = '7ceeea32ff7e67ad76ed1ce597f543b325634f3b70c51403447f8df44749dc37'
ADDED_CORES_HASH = '2fd6865b5ee6ad96803bacf9a0b59a2f2865e43cb81fdbacdee741153acbe81a'
BUSINESS_CORES_HASH = 'b1f209860085e8fa46f6153f35e79d41456cbbdb6c19f8b08a9201172e7f5cde'
REVIEWED_CONSTRUCTOR_AUDIT_HASH = 'f4c5b90582f78527bbc265433e847bd554de7b9b692a9fc005f5c60285ae721d'
GUARD_AUDIT_CORES_HASH = 'cd5d92c5c6d1708cc6d95e05466009b877108aad009f487b4db8de367bd65609'
ADDED_COUNTS = {'agent_runs': 6,
 'agent_tasks': 99,
 'applications': 2,
 'audit_events': 4,
 'conversation_observations': 65,
 'entity_projections': 171,
 'funnel_reports': 1,
 'index_changes': 294,
 'jobs': 5,
 'sync_cursors': 1}
ADDED_RECORDS = 648
BUSINESS_RECORDS = 82
EXPECTED_INDEX_COUNTS = {'agent_tasks': 189, 'entity_projections': 310, 'index_changes': 375, 'index_manifests': 1}
EXPECTED_INDEX_MANIFEST_HASH = '0302ee6216bb7d326267be2b50022253e661cb0e1a150a911d87e8c83435610d'
EXPECTED_PREFIX_RECORDS = 793
EXPECTED_NEW_GENERATED = 564
EXPECTED_LAST_CHILD_ID_HASH = '65b0c3c52df044778e08c21ace83313512d317f597e931c4e63d811288d5a972'
EXPECTED_LAST_CHILD_PAYLOAD_HASH = '6e72e105a78bca8652638df0925c13ee2028500a8199bba972f15be72ebd9bae'
EXPECTED_INDEX_SOURCE_HASH = 'a40ada945e065ea687988c5bb08d085515e6c32376eb78eb798020cc793821c9'
EXPECTED_PREVIOUS_MANIFEST_HASH = '5852fd65601f526f2ccb20d3a6b45e67615151d083b757c6b126d84025d42e75'
REVIEWED_CONTAINMENT_PROOF_HASH = '26f16fc956a154ac98e2696ed21a0260f945ac8e0f9ad4f616fe41e7db984342'
NATIVE_LOGICAL_HASH = '593cab69f7747625fa16ce2899284336a81668a4c8f95e6640990bf018e96413'
SNAPSHOT_RUNS = {'39545315317': {'conclusion': 'success',
                 'created_at': '2026-10-21T04:40:20Z',
                 'display_title': 'history:reconcile-index',
                 'event': 'workflow_dispatch',
                 'head_branch': 'main',
                 'head_sha': 'c2d3ae6064b6c24c56041a7d0727f7eb3699b794',
                 'id': 39545315317,
                 'path': '.github/workflows/history-reconciliation.yml',
                 'run_attempt': 1,
                 'run_started_at': '2026-10-21T04:40:20Z',
                 'status': 'completed',
                 'updated_at': '2026-10-21T05:14:06Z'},
 '39649529296': {'conclusion': 'success',
                 'created_at': '2026-10-21T18:05:08Z',
                 'display_title': 'history:snapshot',
                 'event': 'workflow_dispatch',
                 'head_branch': 'main',
                 'head_sha': '2d566a271bb030747df51e79f5a8d70549df515c',
                 'id': 39649529296,
                 'path': '.github/workflows/history-reconciliation.yml',
                 'run_attempt': 1,
                 'run_started_at': '2026-10-21T18:05:08Z',
                 'status': 'completed',
                 'updated_at': '2026-10-21T18:27:08Z'},
 '39676723486': {'conclusion': 'success',
                 'created_at': '2026-10-21T21:11:21Z',
                 'display_title': 'history:snapshot',
                 'event': 'workflow_dispatch',
                 'head_branch': 'main',
                 'head_sha': 'e86c50049cdb64e1a3a53a19cdd793369dd1a56d',
                 'id': 39676723486,
                 'path': '.github/workflows/history-reconciliation.yml',
                 'run_attempt': 1,
                 'run_started_at': '2026-10-21T21:11:21Z',
                 'status': 'completed',
                 'updated_at': '2026-10-21T21:33:16Z'}}
SNAPSHOT_JOBS = {'39545315317': {'id': 119042060079,
                 'jobs_sha256': '34be9a1c21d050f06925dfaab91a70dd583a859304b2d5f3014b042b19972317',
                 'operation_started_at': '2026-10-21T04:40:33Z',
                 'steps_sha256': 'a3950f072d0fceb13ddf5053b6daac6767e5f0ab9e07debf991b5afefbbc7e09'},
 '39649529296': {'id': 119393894957,
                 'jobs_sha256': 'c8acc41f39724904e2d0bdf0cad9730d2d3032e0fc194bab0c8c716aa850686f',
                 'operation_started_at': '2026-10-21T18:05:24Z',
                 'steps_sha256': 'c3ad9d1d3e53cf4bda237900d0a6d2693a9c1da487c80c38c93dd4c0e1d4a9ac'},
 '39676723486': {'id': 119488408820,
                 'jobs_sha256': '89cb5a0d4e886007e47e5d101000946155d8d2ad89d58074fede6cf897195d72',
                 'operation_started_at': '2026-10-21T21:11:39Z',
                 'steps_sha256': 'db38899f3a6605649b02ed52f9b38fd1e6026dc3b7d90c1d7901821e7fba017a'}}
FAILED_RUNS = {'39608197676': {'conclusion': 'failure',
                 'created_at': '2026-10-21T13:27:46Z',
                 'display_title': 'cloud:live:all',
                 'event': 'workflow_dispatch',
                 'head_branch': 'main',
                 'head_sha': '34856cd47a41cc9f4433208113210bc94407d373',
                 'id': 39608197676,
                 'path': '.github/workflows/cloud-operations.yml',
                 'run_attempt': 1,
                 'run_started_at': '2026-10-21T13:27:46Z',
                 'status': 'completed',
                 'updated_at': '2026-10-21T14:53:26Z'},
 '39629503187': {'conclusion': 'failure',
                 'created_at': '2026-10-21T15:51:44Z',
                 'display_title': 'cloud:live:0 6 * * *',
                 'event': 'schedule',
                 'head_branch': 'main',
                 'head_sha': '34856cd47a41cc9f4433208113210bc94407d373',
                 'id': 39629503187,
                 'path': '.github/workflows/cloud-operations.yml',
                 'run_attempt': 1,
                 'run_started_at': '2026-10-21T15:51:44Z',
                 'status': 'completed',
                 'updated_at': '2026-10-21T15:54:06Z'},
 '39631067173': {'conclusion': 'failure',
                 'created_at': '2026-10-21T16:01:58Z',
                 'display_title': 'cloud:live:30 6 * * *',
                 'event': 'schedule',
                 'head_branch': 'main',
                 'head_sha': '34856cd47a41cc9f4433208113210bc94407d373',
                 'id': 39631067173,
                 'path': '.github/workflows/cloud-operations.yml',
                 'run_attempt': 1,
                 'run_started_at': '2026-10-21T16:01:58Z',
                 'status': 'completed',
                 'updated_at': '2026-10-21T16:04:56Z'}}
FAILED_JOBS = {'39608197676': {'id': 119249843073,
                 'steps_sha256': 'd93e3a2f52719f7e8fe63cd2b34b24f9f7ec39635748c33f169a5520a70c699e',
                 'jobs_sha256': '80c0ffb066be5c4035b71de778caa1bfe4c2171a036c9286a05fd7b8ecc858b1',
                 'log_sha256': '65d11970725d68e6e5452294058a7f90d37405f2667ddfa18e75c55989c1f2ea'},
 '39629503187': {'id': 119323569072,
                 'steps_sha256': '0b76b49c440bcb2b90d01561616402e6d859cc327968d56df8310c1751620fd5',
                 'jobs_sha256': '87c13bd4d667a8b65c6eb6350ac1f73199d985e5fb7d7014905b7af79fbbca33',
                 'log_sha256': '24ef8afba4ce9974102f984b2f49c854d23f3727daa2ca6978b92f3782cdbfc2'},
 '39631067173': {'id': 119329055422,
                 'steps_sha256': '09b318dde61bd885cb807c21c8552bb519b5bc33d6a80ca8387e67d41bb97596',
                 'jobs_sha256': '5e1af993b8da2ed408040eee7626c3e3883cbb1b1c3382223b7591f023aa128c',
                 'log_sha256': '479eae4887c0d675066f8c721a69754cf9c1e9ed465df981c20b9b96e69263a8'}}
DIAGNOSTICS = {
    FAILED: {'status': 'blocked', 'error_type': 'IndexPublicationError',
             'reason': 'index_readback_mismatch_stop_no_retry', 'cloud_operation': 'publish_agent_index'},
    '39629503187': {'status': 'blocked', 'error_type': 'Blocked',
                   'reason': 'previous_failed_writer_requires_reconciliation'},
    '39631067173': {'status': 'blocked', 'error_type': 'Blocked',
                   'reason': 'previous_failed_writer_requires_reconciliation'},
}
SOURCE_HASHES = {'config/cloud.json': '102aec4611c61704a6a0be172c1d03451bbdfea0adeae44b0ad73fb50f76f52d',
 'cloud/applications.py': 'fa37bb1468f8b52e929df6843453574313075f09cdf5fab10edb214f5ae2244e',
 'cloud/orchestrator.py': '6043eb85e0a42ec6d282b66db042108287c090e279117dff19f0d3d55107f1dc',
 'cloud/outreach.py': '6ee0d1dcf85dcaba5178c63edf011b33d4e13ef2bec896fba17c2ef6b3171a61',
 'cloud/discovery.py': '772c317c0f95eb6b065834f9a2e08e89e1e0c2bfb3334debfad5ae5b91a2fd73',
 'cloud/submission.py': 'a8e3b213433532d87862fad71556919edae019929b30b5137a9c4cc4469f5699',
 'cloud/preparation.py': '0619f3de4da66a7763928c9324ab1cc111568e60eeb3c068ba6ef6550606ee77',
 'cloud/agent_worker.py': '6f1631765b4d93807d5896fca795247190df313d39960e867020e72f324c3438',
 'cloud/agent_dispatcher.py': '4ad8db58d923afb51dbe0d4053e7b5d2c9f3c05e5b1eb0347db993903e1a8865',
 'cloud/agent_index.py': '7a63ce93fb1fdeee5c1df41bee9e3fd4c4ad07bec9b36a9bbd9dabfafe9bf6d5',
 'cloud/index_publisher.py': '29608ad3348b2d71ebbdc3fc913e6a632e88359c9e96bfc91cc2a1953e3f2f14',
 'cloud/notion.py': 'c50bf7822786fcf2ed77bfecb3fb9d0700d5ae2e5dfeb74e7af0b5f07fd50014',
 'cloud/reporting.py': '0a4495a6285cf4cae33fa01726dca0cadfed099762e45c99d84e574b2eba7fd8',
 'cloud/records.py': '1703a35182a91636bff29c7de0a71b33f12e592f9fd26f17736d7ebee5583103',
 'cloud/coordination_quarantine.py': '93041a5845896b0bfef81718841ea3aceebdc29d3a55995225cfcf8dd2d7ddc5',
 'cloud/runtime_guard.py': '9acce54391e71af90a8780f563e9ba757ea036f5c2066f28a3e5be01f0549ea9',
 'cloud/runtime_budget.py': 'fedfa41faa87dbe3e45f46640576068348fb90b811535b6db30b19ceae7added',
 'docs/ground-truth.md': 'e09e1bb05ea9c164e5f543903d1d1c70bc90727361f6d5fff71fd47213add74f',
 '.github/workflows/cloud-operations.yml': '85ee5287db4846e7cb13f94c6436b24ee0dd418fe5e20cbf0e0e709075bc7dd2'}

def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def require_reviewed_pins():
    hashes = (AFTER_CIPHER_HASH, AFTER_MEMBER_HASH, AFTER_FINGERPRINT,
        REVIEWED_DELTA_PROOF_HASH, ADDED_CORES_HASH, BUSINESS_CORES_HASH,
        REVIEWED_CONSTRUCTOR_AUDIT_HASH, GUARD_AUDIT_CORES_HASH,
        EXPECTED_INDEX_MANIFEST_HASH, EXPECTED_LAST_CHILD_ID_HASH,
        EXPECTED_LAST_CHILD_PAYLOAD_HASH, EXPECTED_INDEX_SOURCE_HASH,
        EXPECTED_PREVIOUS_MANIFEST_HASH, REVIEWED_CONTAINMENT_PROOF_HASH)
    require(all(_hash(value) for value in hashes)
            and all(type(value) is int and value > 0 for value in
                (AFTER_PAGES, AFTER_RECORDS, AFTER_ARTIFACT_ID, ADDED_RECORDS,
                 BUSINESS_RECORDS, EXPECTED_PREFIX_RECORDS, EXPECTED_NEW_GENERATED))
            and isinstance(ADDED_COUNTS, dict) and bool(ADDED_COUNTS)
            and all(isinstance(key, str) and type(value) is int and value > 0 for key, value in ADDED_COUNTS.items())
            and sum(ADDED_COUNTS.values()) == ADDED_RECORDS
            and isinstance(EXPECTED_INDEX_COUNTS, dict) and bool(EXPECTED_INDEX_COUNTS)
            and all(isinstance(key, str) and type(value) is int and value > 0 for key, value in EXPECTED_INDEX_COUNTS.items())
            and set(SNAPSHOT_RUNS) == set(SNAPSHOT_JOBS) == {BEFORE, INCIDENT, AFTER}
            and all(isinstance(run, dict) and set(run) == set(RUN_FIELDS) for run in SNAPSHOT_RUNS.values())
            and all(isinstance(job, dict)
                    and set(job) == {'id', 'jobs_sha256', 'steps_sha256', 'operation_started_at'}
                    and type(job['id']) is int and job['id'] > 0
                    and _hash(job['jobs_sha256']) and _hash(job['steps_sha256'])
                    and _time(job['operation_started_at']) is not None for job in SNAPSHOT_JOBS.values()),
            'readback_snapshot_review_pending')


def verify_maintenance_context(github, context):
    identity = context.get('run_id', '')
    require(re.fullmatch('[0-9]+', str(identity)) is not None, 'maintenance_run_identity_missing')
    run = github.fetch('/actions/runs/' + str(identity))
    require(str(run.get('id')) == str(identity) and run.get('head_sha') == context['commit']
            and run.get('head_branch') == 'main' and run.get('status') == 'in_progress'
            and run.get('conclusion') is None and run.get('event') == 'workflow_dispatch'
            and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and run.get('display_title') == 'history:reconcile-readback'
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
            and re.search(r'(?:if|elif) \[ "\$IMPORT_MODE" = \'reconcile-readback\' \]; then\n            python scripts/reconcile_readback.py', text),
            'central_writer_concurrency_contract_mismatch')


def _job_matches(identity, jobs):
    if identity not in FAILURES or not _complete_list(jobs, 'jobs', 1):
        return False
    expected = FAILED_JOBS[identity]
    job = jobs['jobs'][0]
    return (type(job.get('id')) is int and job['id'] == expected['id']
            and job.get('name') == 'orchestrate' and job.get('status') == 'completed'
            and job.get('conclusion') == 'failure' and type(job.get('run_attempt')) is int
            and job['run_attempt'] == 1 and type(job.get('run_id')) is int
            and job['run_id'] == int(identity) and job.get('head_sha') == PINNED_COMMIT
            and 'ubuntu-latest' in job.get('labels', []) and 'self-hosted' not in job.get('labels', [])
            and isinstance(job.get('steps'), list) and content_hash(job['steps']) == expected['steps_sha256']
            and content_hash(jobs) == expected['jobs_sha256'])


def _run_matches(identity, value):
    return (identity in FAILURES and isinstance(value, dict) and type(value.get('id')) is int
            and type(value.get('run_attempt')) is int
            and {key: value.get(key) for key in RUN_FIELDS} == FAILED_RUNS[identity])


def verify_incidents(github):
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
    incidents = {}
    for identity in FAILURES:
        path = '/actions/runs/' + identity
        run = github.fetch(path)
        jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
        require(_run_matches(identity, run), 'pinned_failed_run_mismatch')
        require(_job_matches(identity, jobs), 'failed_job_history_incomplete_or_changed')
        # Sensitive raw log bytes are examined in memory and never retained.
        logs = github.download_path('/actions/jobs/' + str(FAILED_JOBS[identity]['id']) + '/logs')
        require(_diagnostics(logs) == [DIAGNOSTICS[identity]]
                and sha(logs) == FAILED_JOBS[identity]['log_sha256'], 'readback_diagnostic_mismatch')
        after_jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
        after_run = github.fetch(path)
        require(_run_matches(identity, after_run) and after_run == run
                and _job_matches(identity, after_jobs) and after_jobs == jobs,
                'failed_run_changed_during_verification')
        incidents[identity] = {'failed_run': FAILED_RUNS[identity],
            'failed_job': FAILED_JOBS[identity], 'diagnostic': DIAGNOSTICS[identity]}
    return {'failed_runs': incidents, 'source_contract': SOURCE_HASHES}


def read_snapshot(github, identity, key):
    specifications = {
        BEFORE: (BEFORE_COMMIT, BEFORE_CIPHER_HASH, BEFORE_MEMBER_HASH, BEFORE_FINGERPRINT,
                 BEFORE_PAGES, BEFORE_RECORDS, BEFORE_ARTIFACT_ID),
        INCIDENT: (INCIDENT_COMMIT, INCIDENT_CIPHER_HASH, INCIDENT_MEMBER_HASH, INCIDENT_FINGERPRINT,
                   INCIDENT_PAGES, INCIDENT_RECORDS, INCIDENT_ARTIFACT_ID),
        AFTER: (AFTER_COMMIT, AFTER_CIPHER_HASH, AFTER_MEMBER_HASH, AFTER_FINGERPRINT,
                AFTER_PAGES, AFTER_RECORDS, AFTER_ARTIFACT_ID),
    }
    require(identity in specifications and identity in SNAPSHOT_RUNS and identity in SNAPSHOT_JOBS,
            'source_snapshot_identity_invalid')
    before = identity == BEFORE
    (expected_commit, expected_cipher, expected_member, expected_state,
     expected_pages, expected_records, expected_artifact) = specifications[identity]
    path = '/actions/runs/' + identity
    run = github.fetch(path)
    require(str(run.get('id')) == identity and run.get('head_sha') == expected_commit
            and run.get('head_branch') == 'main' and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and run.get('event') == 'workflow_dispatch' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and all(run.get(key) == value for key, value in SNAPSHOT_RUNS[identity].items())
            and run.get('actor', {}).get('login') == 'narendranathe'
            and run.get('triggering_actor', {}).get('login') == 'narendranathe'
            and run.get('display_title') == ('history:reconcile-index' if before else 'history:snapshot')
            and str(run.get('path', '')).partition('@')[0] == '.github/workflows/history-reconciliation.yml',
            'source_snapshot_hosted_run_invalid')
    jobs = github.fetch(path + '/jobs?filter=all&per_page=100&page=1')
    require(_complete_list(jobs, 'jobs', 1), 'source_snapshot_jobs_incomplete')
    job = jobs['jobs'][0]
    require(job.get('name') == 'reconcile' and job.get('status') == 'completed'
            and job.get('conclusion') == 'success' and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', [])
            and job.get('id') == SNAPSHOT_JOBS[identity]['id']
            and content_hash(jobs) == SNAPSHOT_JOBS[identity]['jobs_sha256']
            and content_hash(job.get('steps')) == SNAPSHOT_JOBS[identity]['steps_sha256'],
            'source_snapshot_hosted_job_invalid')
    operation = [step for step in job.get('steps', [])
                 if step.get('name') == 'Reconcile without replaying historical effects']
    require(len(operation) == 1 and operation[0].get('conclusion') == 'success'
            and _time(operation[0].get('started_at')) is not None
            and operation[0]['started_at'] == SNAPSHOT_JOBS[identity]['operation_started_at'],
            'source_snapshot_operation_start_invalid')
    response = github.fetch(path + '/artifacts?per_page=100&page=1')
    require(_complete_list(response, 'artifacts', 1), 'source_snapshot_artifact_history_incomplete')
    artifact = response['artifacts'][0]
    require(artifact.get('name') == 'reconciliation-evidence-' + identity and artifact.get('expired') is False
            and type(artifact.get('id')) is int
            and artifact['id'] == expected_artifact,
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
                and summary.get('operation') == ('pinned_index_writer_reconciliation' if before else 'encrypted_source_snapshot')
                and (not before or (summary.get('verified') is True and summary.get('resolved_run_id') == '39434825788')), 'source_snapshot_ciphertext_mismatch')
        with tempfile.TemporaryDirectory(prefix='apply-index-reconciliation-') as folder:
            root = Path(folder)
            root.chmod(0o700)
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
                and len(state.rows) == expected_records and state.fingerprint() == expected_state,
                'source_snapshot_content_mismatch')
        require(_time(wrapper.get('exported_at')) is not None
                and _time(wrapper['exported_at']) >= _time(state.snapshot['captured_at']),
                'source_snapshot_export_time_invalid')
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked('source_snapshot_authentication_failed') from None
    require(github.fetch(path + '/jobs?filter=all&per_page=100&page=1') == jobs
            and github.fetch(path) == run, 'source_snapshot_run_changed')
    return state, {'run_id': identity, 'artifact_id': artifact['id'], 'artifact_zip_sha256': sha(zipped),
                   'archive_sha256': sha(ciphertext), 'member_sha256': sha(member),
                   'state_sha256': state.fingerprint(), 'pages': len(state.pages),
                   'operation_started_at': operation[0]['started_at']}


def _generated(row):
    return (row['kind'] in GENERATED_KINDS
            or row['kind'] == 'agent_tasks' and row['payload'].get('schema') == INDEX_SCHEMA)


def _guard_audits(before_rows, after_rows, after):
    """Exact later guard audits are storage writes, not business execution."""
    result = {}
    for guard_run, unresolved in zip(FAILURES[1:], FAILURES[:-1]):
        identity = 'writer-failure:' + unresolved
        expected = record(identity, 'audit_events', {'github_run_id': unresolved,
            'reason': 'failed_writer_requires_reconciliation', 'retry_external_effects': False},
            'needs_reconciliation')
        require(identity not in before_rows and after_rows.get(identity) == expected,
                'later_guard_audit_constructor_changed')
        created = _time(after.pages[after.page_ids[identity]]['page'].get('created_time'))
        run = FAILED_RUNS[guard_run]
        require(created is not None and _time(run['run_started_at']) <= created <= _time(run['updated_at'])
                and created > _time(FAILED_RUNS[FAILED]['updated_at']), 'later_guard_audit_time_invalid')
        result[identity] = expected
    require(content_hash({identity: content_hash(row) for identity, row in result.items()}) == GUARD_AUDIT_CORES_HASH,
            'reviewed_guard_audit_core_changed')
    return result


def verify_delta_and_index(before, after, exported, native_plan=None):
    """Rebuild the exact reviewed index source before the two later guard audits.

    Every prior logical page and CORE remains compared. The static constructor
    audit binds original business construction without replaying public reads.
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
            and all(before.pages[identity] == after.pages[identity] for identity in before.pages),
            'prior_source_page_changed')
    before_rows = {identity: validate_record(row) for identity, row in before.rows.items()}
    after_rows = {identity: validate_record(row) for identity, row in after.rows.items()}
    require(len(before_rows) == BEFORE_RECORDS and len(after_rows) == AFTER_RECORDS
            and set(before_rows) <= set(after_rows)
            and all(after_rows[identity] == row for identity, row in before_rows.items()), 'prior_source_core_changed')
    added = {identity: row for identity, row in after_rows.items() if identity not in before_rows}
    native_pages = set()
    if native_plan is not None:
        require(_hash(REVIEWED_CONTAINMENT_PROOF_HASH)
                and content_hash(native_plan.proof) == REVIEWED_CONTAINMENT_PROOF_HASH
                and native_plan.native_page_id not in before.pages
                and native_plan.native_page_id in after.pages
                and content_hash(after.pages[native_plan.native_page_id]) == NATIVE_LOGICAL_HASH
                and native_plan.native_page_id not in after.page_ids.values(),
                'exact_native_claim_containment_unverified')
        native_pages.add(native_plan.native_page_id)
    require(len(added) == ADDED_RECORDS and dict(Counter(row['kind'] for row in added.values())) == ADDED_COUNTS
            and content_hash({identity: content_hash(row) for identity, row in added.items()}) == ADDED_CORES_HASH
            and set(after.pages) - set(before.pages) == {after.page_ids[identity] for identity in added} | native_pages,
            'source_delta_not_exact_reviewed_records')
    require(_effects(before) == _effects(after), 'new_attempt_receipt_or_outreach_effect')
    for state in (before, after):
        ready = state.get('deployment:cutover:v2')
        require(ready is not None and content_hash(core(ready)) == READY_CORE_HASH, 'ready_record_changed')
        validate_cutover_record(ready)
        report = validate_import(state, exported)
        require(report['records'] == report['unchanged'] == BASELINE_RECORDS
                and report['conflicted'] == report['blocked'] == 0, 'baseline_readback_changed')
    queues = ('agent_execution_queue', 'agent_proposals', 'agent_observations', 'agent_action_results',
              'agent_action_reservations', 'agent_action_executions')
    require(not any(row['kind'] in queues for row in after_rows.values()), 'agent_admission_or_execution_present')
    guard_audits = _guard_audits(before_rows, after_rows, after)
    business = {identity: row for identity, row in added.items() if not _generated(row) and identity not in guard_audits}
    require(len(business) == BUSINESS_RECORDS
            and content_hash({identity: content_hash(row) for identity, row in business.items()}) == BUSINESS_CORES_HASH,
            'reviewed_business_core_changed')
    reports = [row for row in business.values() if row['kind'] == 'funnel_reports'
               and row['payload'].get('schema') == 'qualified-funnel/v1']
    require(len(reports) == 1, 'unique_new_funnel_report_required')
    now = _time(reports[0]['payload'].get('observed_at'))
    require(now is not None and _time(FAILED_RUNS[FAILED]['run_started_at']) <= now
            <= _time(FAILED_RUNS[FAILED]['updated_at']) <= _time(after.snapshot['captured_at']),
            'frozen_business_timestamp_invalid')
    day = now.astimezone(ZoneInfo('America/Chicago')).date().isoformat()
    require(day == '2026-10-21', 'original_business_day_changed')
    cursors = [row for row in business.values() if row['kind'] == 'sync_cursors']
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
    require(len(completed_new) == 3, 'exact_three_new_completed_slots_required')
    require({row['payload'].get('lane') for row in completed_new} == {'accepted', 'first_touch', 'applications'},
            'all_current_business_lanes_required')
    for completed in completed_new:
        payload = completed['payload']
        identity = completed['id'].removesuffix(':completed')
        started = after_rows.get(identity + ':started')
        marker = {key: payload.get(key) for key in ('date', 'lane', 'actor', 'dry_run', 'mode', 'persistent')}
        require(started is not None and started == record(identity + ':started', 'agent_runs', marker, 'started')
                and marker['actor'] == 'github-actions' and marker['mode'] == 'live'
                and marker['persistent'] is True and marker['dry_run'] is False
                and marker['lane'] in ('accepted', 'first_touch', 'applications') and marker['date'] == day
                and identity == 'run:' + day + ':' + marker['lane'], 'business_slot_marker_invalid')
        summary = payload.get('summary')
        require(isinstance(summary, dict) and summary.get('lane') == marker['lane'], 'business_lane_summary_invalid')
        if marker['lane'] == 'applications':
            require(summary.get('dry_run') is False and all(summary.get(key) == 0 for key in
                ('submission_attempts', 'applied_verified', 'submission_unknown')), 'application_summary_effect_or_uncertainty')
        else:
            require(summary.get('sent') == 0, 'outreach_summary_has_send')
    require(sha(Path('cloud/agent_index.py').read_bytes()) == SOURCE_HASHES['cloud/agent_index.py'],
            'index_builder_source_changed')
    previous_manifests = [row for row in before_rows.values() if row['kind'] == 'index_manifests']
    require(bool(previous_manifests), 'reference_index_manifest_missing')
    times = [_time(row['payload'].get('generated_at')) for row in previous_manifests]
    require(all(value is not None for value in times), 'reference_index_timestamp_invalid')
    latest = max(times)
    candidates = [row for row in previous_manifests if _time(row['payload'].get('generated_at')) == latest]
    require(len(candidates) == 1, 'reference_index_timestamp_conflict')
    previous = candidates[0]
    # Guard wakes append audits later. Including them here would manufacture a
    # source that the original failed publisher could never have observed.
    source = [row for identity, row in after_rows.items() if not _generated(row) and identity not in guard_audits]
    complete, conflicts = _reconciled_source_proof(source, exported, after_rows.get('deployment:cutover:v2'))
    require(complete is True and conflicts == [], 'complete_index_source_not_proven')
    source_hash = snapshot_hash(source)
    require(source_hash == EXPECTED_INDEX_SOURCE_HASH and previous['hash'] == EXPECTED_PREVIOUS_MANIFEST_HASH,
            'original_index_source_or_previous_manifest_changed')
    expected = build_index(source, now, previous_manifest=previous,
                          source_snapshot_hash={'hash': source_hash, 'complete': True, 'conflicts': []})
    require(dict(Counter(row['kind'] for row in expected)) == EXPECTED_INDEX_COUNTS, 'index_reconstruction_shape_changed')
    manifest = next(row for row in expected if row['kind'] == 'index_manifests')
    require(manifest['payload'].get('complete') is True and manifest['payload'].get('conflicts') == []
            and manifest['hash'] == EXPECTED_INDEX_MANIFEST_HASH and after.get(manifest['id']) is None,
            'incomplete_index_manifest_proof_changed')
    require(all(after_rows.get(row['id']) in (None, row) for row in expected), 'index_child_core_conflict')
    new_generated = {identity: row for identity, row in added.items() if _generated(row)}
    prefix = EXPECTED_PREFIX_RECORDS
    require(prefix <= len(expected) - 1 and len(new_generated) == EXPECTED_NEW_GENERATED
            and new_generated == {row['id']: row for row in expected[:prefix] if row['id'] not in before_rows}
            and all(after_rows.get(row['id']) == row for row in expected[:prefix]),
            'index_prefix_not_exact_reviewed_publication')
    last = expected[prefix - 1]
    require(last['id'] in new_generated and sha(last['id'].encode()) == EXPECTED_LAST_CHILD_ID_HASH
            and last['hash'] == EXPECTED_LAST_CHILD_PAYLOAD_HASH and after_rows.get(last['id']) == last,
            'last_child_later_exact_readback_changed')
    created = _time(after.pages[after.page_ids[last['id']]]['page'].get('created_time'))
    require(created is not None and now <= created <= _time(FAILED_RUNS[FAILED]['updated_at']),
            'last_child_create_time_outside_original_run')
    return {'schema': 'apply-pinned-index-readback-source-proof/v1', 'baseline_unchanged': BASELINE_RECORDS,
        'historical_manifest_sha256': BASELINE_HASH, 'ready_core_sha256': READY_CORE_HASH,
        'prior_pages_unchanged': BEFORE_PAGES, 'prior_cores_unchanged': len(before_rows),
        'added_records': len(added), 'added_cores_sha256': ADDED_CORES_HASH,
        'reviewed_business_records': len(business), 'reviewed_business_cores_sha256': BUSINESS_CORES_HASH,
        'reviewed_constructor_audit_sha256': REVIEWED_CONSTRUCTOR_AUDIT_HASH,
        'later_guard_audit_records': len(guard_audits), 'later_guard_audit_cores_sha256': GUARD_AUDIT_CORES_HASH,
        'new_attempts': 0, 'new_receipts': 0, 'new_outreach_events': 0, 'agent_admissions': 0,
        'new_completed_slots': len(completed_new), 'old_failed_slot_unchanged': True,
        'index_source_sha256': source_hash, 'previous_manifest_sha256': previous['hash'],
        'expected_manifest_sha256': manifest['hash'], 'partial_prefix_records': prefix,
        'new_partial_children': len(new_generated), 'last_child_identity_sha256': EXPECTED_LAST_CHILD_ID_HASH,
        'last_child_payload_sha256': EXPECTED_LAST_CHILD_PAYLOAD_HASH,
        'last_child_later_exact_readback': True, 'original_mismatch_cause': 'unverified', 'index_complete': False,
        'native_claim_containment': native_plan.proof if native_plan is not None else None}


def resolution_record(identity, incident, before_proof, after_proof, delta, *, incident_after_proof=None):
    require(identity in FAILURES, 'resolution_identity_invalid')
    lead = identity == FAILED
    return record('writer-resolution:' + identity, 'audit_events', {
        'schema': SCHEMA, 'github_run_id': identity,
        'reason': 'business_complete_index_publication_incomplete' if lead else 'guard_stopped_no_business_operation',
        'external_effects_reconciled': False, 'managed_cloud_effects_reconciled': True,
        'native_effect_unknown_contained': True, 'native_delivery_verified': False,
        'native_history_incomplete': True, 'retry_external_effects': False,
        'business_operation_entered': lead, 'business_lanes_completed': lead, 'index_complete': False,
        'evidence': {'incident': incident, 'before': before_proof, 'incident_snapshot': incident_after_proof,
                     'after': after_proof, 'delta': delta}},
        'reconciled_business_complete_index_incomplete' if lead else 'reconciled_guard_stopped_no_business_operation')


def _snapshot_proof_valid(proof, before):
    phase = INCIDENT if before == 'incident' else BEFORE if before else AFTER
    expected = {
        BEFORE: (BEFORE_ARTIFACT_ID, BEFORE_CIPHER_HASH, BEFORE_MEMBER_HASH, BEFORE_FINGERPRINT, BEFORE_PAGES),
        INCIDENT: (INCIDENT_ARTIFACT_ID, INCIDENT_CIPHER_HASH, INCIDENT_MEMBER_HASH, INCIDENT_FINGERPRINT, INCIDENT_PAGES),
        AFTER: (AFTER_ARTIFACT_ID, AFTER_CIPHER_HASH, AFTER_MEMBER_HASH, AFTER_FINGERPRINT, AFTER_PAGES),
    }[phase]
    return (isinstance(proof, dict)
            and set(proof) == {'run_id', 'artifact_id', 'artifact_zip_sha256', 'archive_sha256', 'member_sha256', 'state_sha256', 'pages', 'operation_started_at'}
            and proof['run_id'] == phase
            and type(proof['artifact_id']) is int
            and proof['artifact_id'] == expected[0]
            and _hash(proof['artifact_zip_sha256'])
            and proof['archive_sha256'] == expected[1]
            and proof['member_sha256'] == expected[2]
            and proof['state_sha256'] == expected[3]
            and type(proof['pages']) is int and proof['pages'] == expected[4]
            and proof['operation_started_at'] == SNAPSHOT_JOBS[phase]['operation_started_at'])


def _incident_report_valid(value):
    if not (isinstance(value, dict) and set(value) == {'failed_runs', 'source_contract'}
            and value['source_contract'] == SOURCE_HASHES and isinstance(value['failed_runs'], dict)
            and set(value['failed_runs']) == set(FAILURES)):
        return False
    for identity in FAILURES:
        incident = value['failed_runs'][identity]
        if not (isinstance(incident, dict) and set(incident) == {'failed_run', 'failed_job', 'diagnostic'}
                and incident['failed_run'] == FAILED_RUNS[identity]
                and _run_matches(identity, incident['failed_run'])
                and incident['failed_job'] == FAILED_JOBS[identity]
                and type(incident['failed_job'].get('id')) is int
                and incident['diagnostic'] == DIAGNOSTICS[identity]):
            return False
    return True


def _resolution_valid(identity, marker):
    try:
        require_reviewed_pins()
        if identity not in FAILURES:
            return False
        row = validate_record(marker)
        payload = row['payload']
        lead = identity == FAILED
        return (row['id'] == 'writer-resolution:' + identity and row['kind'] == 'audit_events'
            and row['status'] == ('reconciled_business_complete_index_incomplete' if lead
                                 else 'reconciled_guard_stopped_no_business_operation')
            and set(payload) == {'schema', 'github_run_id', 'reason', 'external_effects_reconciled',
                'managed_cloud_effects_reconciled', 'native_effect_unknown_contained',
                'native_delivery_verified', 'native_history_incomplete',
                'retry_external_effects', 'business_operation_entered', 'business_lanes_completed', 'index_complete', 'evidence'}
            and payload['schema'] == SCHEMA and payload['github_run_id'] == identity
            and payload['reason'] == ('business_complete_index_publication_incomplete' if lead
                                      else 'guard_stopped_no_business_operation')
            and payload['external_effects_reconciled'] is False
            and payload['managed_cloud_effects_reconciled'] is True
            and payload['native_effect_unknown_contained'] is True
            and payload['native_delivery_verified'] is False and payload['native_history_incomplete'] is True
            and payload['retry_external_effects'] is False
            and payload['business_operation_entered'] is lead and payload['business_lanes_completed'] is lead
            and payload['index_complete'] is False and isinstance(payload['evidence'], dict)
            and set(payload['evidence']) == {'incident', 'before', 'incident_snapshot', 'after', 'delta'}
            and _snapshot_proof_valid(payload['evidence']['before'], True)
            and _snapshot_proof_valid(payload['evidence']['incident_snapshot'], 'incident')
            and _snapshot_proof_valid(payload['evidence']['after'], False)
            and content_hash(payload['evidence']['delta']) == REVIEWED_DELTA_PROOF_HASH
            and _incident_report_valid(payload['evidence']['incident']))
    except Exception:
        return False


def readback_resolution_matches(identity, marker, fetch, repository, *, read_record=None):
    """A marker cannot resolve a future attempt, partial jobs, or changed run."""
    try:
        if repository != REPOSITORY or not _resolution_valid(identity, marker):
            return False
        from cloud.native_claim_reconciliation import cohort_matches
        if not callable(read_record) or not cohort_matches(read_record):
            return False
        for resolved_id in FAILURES:
            peer = read_record('writer-resolution:' + resolved_id)
            if not (_resolution_valid(resolved_id, peer)
                    and peer['payload']['evidence'] == marker['payload']['evidence']):
                return False
        # A peer rerun may disappear from GitHub's failed-run list. The full
        # reviewed cohort must still witness each original attempt directly.
        for failed_id in FAILURES:
            path = '/actions/runs/' + failed_id
            before = fetch(path)
            jobs = fetch(path + '/jobs?filter=all&per_page=100&page=1')
            if not _run_matches(failed_id, before) or not _job_matches(failed_id, jobs):
                return False
            after_jobs = fetch(path + '/jobs?filter=all&per_page=100&page=1')
            after = fetch(path)
            if not (_run_matches(failed_id, after) and after == before
                    and _job_matches(failed_id, after_jobs) and after_jobs == jobs):
                return False
        return True
    except Exception:
        return False


def reconcile(store, exported, github, context, key):
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == REPOSITORY and context.get('actor') == 'narendranathe'
            and context.get('triggering_actor') == 'narendranathe' and context.get('event') == 'workflow_dispatch'
            and context.get('cloud_enabled') == 'false' and re.fullmatch('[0-9a-f]{40}', context.get('commit', ''))
            and isinstance(key, bytes) and len(key) == 32, 'owner_disabled_hosted_maintenance_required')
    # Missing future snapshot/audit pins reject before any credentialed fetch or
    # mutation. Environment variables and workflow inputs cannot fill them.
    require_reviewed_pins()
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    incident = verify_incidents(github)
    before, before_proof = read_snapshot(github, BEFORE, key)
    incident_after, incident_after_proof = read_snapshot(github, INCIDENT, key)
    after, after_proof = read_snapshot(github, AFTER, key)
    require(_time(before.snapshot['captured_at']) < _time(FAILED_RUNS[FAILED]['run_started_at'])
            and all(_time(run['updated_at']) < _time(after.snapshot['captured_at']) for run in FAILED_RUNS.values()),
            'snapshot_does_not_bracket_failures')
    from cloud.native_claim_reconciliation import load_plan
    plan = load_plan(incident_after, after, key,
                     post_stop_started_at=after_proof['operation_started_at'])
    require(content_hash(plan.proof) == REVIEWED_CONTAINMENT_PROOF_HASH,
            'reviewed_containment_proof_changed')
    delta = verify_delta_and_index(before, after, exported, plan)
    require(content_hash(delta) == REVIEWED_DELTA_PROOF_HASH, 'reviewed_readback_delta_proof_changed')
    resolutions = [resolution_record(identity, incident, before_proof, after_proof, delta,
                                     incident_after_proof=incident_after_proof)
                   for identity in (*FAILURES[1:], FAILED)]
    expected = [*plan.records, *resolutions]
    current = SnapshotState(store.snapshot_all())
    require(current.data_source_id == getattr(store, 'data_source_id', None) == after.data_source_id
            and _time(current.snapshot['captured_at']) >= _time(after.snapshot['captured_at']),
            'fresh_source_identity_or_time_mismatch')
    existing = []
    for row in expected:
        old = current.get(row['id'])
        if old is not None:
            require(core(old) == core(row), 'existing_readback_resolution_conflict')
            current.pages.pop(current.page_ids[row['id']])
        existing.append(old is not None)
    # The six appends are one nontransactional cohort. Any proper subset is
    # a new incident, never an automatic append retry.
    require(not any(existing) or all(existing), 'partial_resolution_batch_requires_reconciliation')
    require(current.fingerprint() == after.fingerprint(), 'fresh_source_changed_since_snapshot')
    github.verify_main(context['commit'])
    verify_maintenance_context(github, context)
    require(verify_incidents(github) == incident, 'incident_evidence_changed_before_append')
    if not all(existing):
        for position, row in enumerate(expected):
            if position == len(plan.records):
                contained = SnapshotState(store.snapshot_all())
                for containment in plan.records:
                    observed = contained.get(containment['id'])
                    require(observed is not None and core(observed) == core(containment),
                            'containment_cohort_missing_before_clearance')
                    contained.pages.pop(contained.page_ids[containment['id']])
                require(contained.fingerprint() == after.fingerprint(),
                        'fresh_source_changed_after_containment')
                github.verify_main(context['commit'])
                verify_maintenance_context(github, context)
            written = store.put(row)
            observed = store.get(row['id'])
            try:
                exact = (isinstance(written, dict) and isinstance(observed, dict)
                         and core(written) == core(row) and core(observed) == core(row))
            except (KeyError, TypeError):
                exact = False
            if not exact:
                raise AmbiguousWriteError('Readback reconciliation append uncertain; stop for reconciliation')
    return {'operation': 'pinned_index_readback_writer_reconciliation', 'verified': True,
        'unchanged': all(existing), 'resolved_run_ids': list(FAILURES),
        'resolution_sha256': {row['id']: row['hash'] for row in resolutions},
        'containment_sha256': {row['id']: row['hash'] for row in plan.records}, 'external_effects': 0,
        'native_effect_unknown_contained': True, 'native_delivery_verified': False,
        'external_effects_reconciled': False, 'managed_cloud_effects_reconciled': True,
        'business_lanes_completed': True, 'index_complete': False, 'guard_storage_audits_preserved': 2,
        'baseline_unchanged': BASELINE_RECORDS, 'cloud_enabled_changed': False, 'ready_record_changed': False}
