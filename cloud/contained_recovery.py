"""Fixed V3 correction for one auxiliary-observation storage incident.

No reserved observation is replayed. A new immutable evidence bundle and three
provably unattempted resolutions remain gated on exact source and native proof.
"""
from __future__ import annotations
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile

from cloud import native_claim_reconciliation as native
from cloud import readback_reconciliation as old
from cloud.cutover import validate_cutover_record, validate_import
from cloud.notion import AmbiguousWriteError
from cloud.outreach import record
from cloud.records import content_hash, validate_record
from cloud.startup_reconciliation import SnapshotState, ReconciliationBlocked, core
from cloud.storage_reservations import RESERVED_RECORD_IDS, RESERVED_CORE_HASHES
from scripts.encrypted_backup import MAGIC, restore_archive

SCHEMA = 'apply-pinned-index-readback-writer-resolution/v3'
BUNDLE_SCHEMA = 'apply-pinned-native-claim-storage-correction/v3'
FAILURES = old.FAILURES
EPOCH_ID = 'native-claim-correction:39718240610:v3'
BUNDLE_ID = 'native-claim-correction-bundle:39718240610:v3'
HOLD_ID = 'coordination-quarantine:2b4c8427ccfc9482a7508dac42b968b3eeeb165de89aed2659f92de34814d876'
HOLD_CORE_HASH = '8b9d595b38dfcc28c00add003f12ce75f12058e25b3dccdc7bb988be817fa606'
PARTIAL_RUN = '39780427733'
PARTIAL_ARCHIVE = Path(__file__).resolve().parents[1] / 'recovery-evidence/2026-10-22-partial-contained-source.aesgcm'
TITLE = 'history:reconcile-contained'
WORKFLOW = '.github/workflows/history-reconciliation.yml'
OPERATION = 'Reconcile without replaying historical effects'
PROTOCOL_FILES = ('cloud/contained_recovery.py', 'cloud/storage_reservations.py', 'cloud/notion.py',
                  'scripts/reconcile_contained.py', WORKFLOW)
TEMPLATE_CORE_HASHES = {'native-claim-correction-bundle:39718240610:v3': 'b769081df795408e3f4dc53f2d9f02195446a5ea12b9ab36eb415d277d847eb2',
 'writer-resolution:39608197676': '462f7a7686666238d3d154a10faa8fba62e88bc27717bf55329a4e482912b268',
 'writer-resolution:39629503187': '637638994cb35a8a7b7d498b02b7a10b63bb3a6fdd93913eea5d122a5c311edd',
 'writer-resolution:39631067173': '8eabeddd5add9ed7d1c971562f6dc3c0b9229547a7c4656fe9e27dfc7f179e30'}
STATIC_PINS = {'activation_authorized': False,
 'external_effects_reconciled': False,
 'failed_maintenance_closed_diagnostic': {'error_type': 'AmbiguousWriteError',
                                          'notion': {'error_code': 'unknown',
                                                     'http_status': None,
                                                     'operation': 'notion_request'},
                                          'operation': 'pinned_index_readback_reconciliation',
                                          'status': 'blocked'},
 'failed_maintenance_job_id': 119625425868,
 'failed_maintenance_jobs_sha256': '760878eae82c95bba6566e716ad307a743458e65979d14a01088b75603a5dfa9',
 'failed_maintenance_operation_completed_at': '2026-10-22T04:05:14Z',
 'failed_maintenance_operation_started_at': '2026-10-22T03:41:56Z',
 'failed_maintenance_raw_log_sha256': 'bddd15a6b18694e8093b7f3086013cc5426c924dfd1c3e54f360f3c6a41655a3',
 'failed_maintenance_run': {'conclusion': 'failure',
                            'created_at': '2026-10-22T03:41:40Z',
                            'display_title': 'history:reconcile-readback',
                            'event': 'workflow_dispatch',
                            'head_branch': 'main',
                            'head_sha': '28b550aba1bf8b7d5be93494e3e146f68e675fe3',
                            'id': 39718240610,
                            'path': '.github/workflows/history-reconciliation.yml',
                            'run_attempt': 1,
                            'run_started_at': '2026-10-22T03:41:40Z',
                            'status': 'completed',
                            'updated_at': '2026-10-22T04:05:17Z'},
 'failed_maintenance_steps_sha256': '0b32496f37cb81e3ccd67c2138d968c6739638b5495c1e80da50f33f04fc24df',
 'fresh_actual_unknown_individual_ids_absent': True,
 'native_delivery_verified': False,
 'never_attempted_resolution_record_ids': ['writer-resolution:39629503187',
                                           'writer-resolution:39631067173',
                                           'writer-resolution:39608197676'],
 'nonattempt_proof': {'actual_failed_operation_elapsed_ms': 1398000,
                      'evidence_class': 'source_control_flow_and_pacing_lower_bound',
                      'mandatory_complete_scans_before_first_resolution_post': 2,
                      'minimum_elapsed_ms': 2317200,
                      'minimum_original_pages_per_scan': 2897,
                      'minimum_paced_request_starts': 5794,
                      'minimum_request_start_interval_ms': 400,
                      'original_resolution_post_cannot_have_been_reached': True},
 'original_history_and_source_preserved': True,
 'original_maintenance_source_commit': '28b550aba1bf8b7d5be93494e3e146f68e675fe3',
 'original_maintenance_source_sha256': {'.github/workflows/history-reconciliation.yml': 'c3240baa5943c6a0347939c5bbec7102370295faadd2ce02f9972a299038a0ec',
                                        'cloud/notion.py': '0fd0bf0f4d631cca1098279182a801f50c6b449d340a0cc49b65c4ecaaf1652c',
                                        'cloud/readback_reconciliation.py': '2669eb24e4157459e04bdd4938205abcb2b86059e664d28299b8a4f7ff4bda7c',
                                        'cloud/runtime_budget.py': 'fedfa41faa87dbe3e45f46640576068348fb90b811535b6db30b19ceae7added',
                                        'scripts/reconcile_readback.py': '71021dd91f9bfee4ac3fa7cd2489032e9999fad85cc405b2156ebb66ea132050'},
 'original_native_plan_proof_sha256': '26f16fc956a154ac98e2696ed21a0260f945ac8e0f9ad4f616fe41e7db984342',
 'partial_prefix_independent_proof_sha256': '0f57b7c076a52e31432f647be22260c53f3670c2caa8c110e199c5b22d316a7f',
 'permanently_reserved_individual_observation_ids': ['native-delivery-claim-observation:13eacd409b8c6dca076c4b1f62d9db1bf015b7759cb02360a7f5421fd8896208',
                                                     'user-controller-stop-observation:963a71822d95d8712a13e9ccec8522e2fe6046890c122eae07f94497fe0d9c6b'],
 'permanently_reserved_individual_observation_whole_core_sha256': {'native-delivery-claim-observation:13eacd409b8c6dca076c4b1f62d9db1bf015b7759cb02360a7f5421fd8896208': 'fa85910a0c7c8eb1d5a1c94e190f531848b167db331b67354e78a1965bc99876',
                                                                   'user-controller-stop-observation:963a71822d95d8712a13e9ccec8522e2fe6046890c122eae07f94497fe0d9c6b': '14bb53f9a4ee01f8c45702d03e23cfd4b6a5f80b6c8269417eb97b370ffcb344'},
 'persisted_hold_created_at': '2026-10-22T04:05:00.000Z',
 'persisted_hold_creator_object_sha256': 'ae5aec15cf34e6c97aab9a6978c2e7b524ce90048524571956cbba91d85989c7',
 'persisted_hold_record_id': 'coordination-quarantine:2b4c8427ccfc9482a7508dac42b968b3eeeb165de89aed2659f92de34814d876',
 'persisted_hold_source_page_identity_sha256': 'c3315710c90826698509e3faf622b6fc09cf4ea30dd1b60567706848858a9444',
 'persisted_hold_source_page_sha256': '8ccb2b1c0d88ed2b14bd681a8bbf2def89e7c2c35f649ed8751731e2afb09959',
 'persisted_hold_whole_core_sha256': '8b9d595b38dfcc28c00add003f12ce75f12058e25b3dccdc7bb988be817fa606',
 'post_failure_snapshot_archive_sha256': '5f7ef2937933c86dbd5decfc267495303f98b92800e0fb1488ea3e39d8eae0d7',
 'post_failure_snapshot_artifact_id': 12427048416,
 'post_failure_snapshot_artifact_zip_sha256': '272e37958cfb26cee44ece0ef94ad99b163bca275d17484e196ce0ca69de8228',
 'post_failure_snapshot_core_records': 2651,
 'post_failure_snapshot_fingerprint': '53f9cac777811faf3d27c140e4c40f92ee8dabe0827170522c61ffe3ae1ff14b',
 'post_failure_snapshot_job_id': 119829982369,
 'post_failure_snapshot_jobs_sha256': 'ee4c998a8e451e24121e4ead44b02a6bf63cde32b66bf158437ff735ae0ffb03',
 'post_failure_snapshot_member_sha256': '950f5ec2f713b94d37ab526b76be5da234b2812adbd70fecb4fb82c322cb9026',
 'post_failure_snapshot_operation_completed_at': '2026-10-22T13:02:13Z',
 'post_failure_snapshot_operation_started_at': '2026-10-22T12:41:56Z',
 'post_failure_snapshot_pages': 2898,
 'post_failure_snapshot_run': {'conclusion': 'success',
                               'created_at': '2026-10-22T12:41:29Z',
                               'display_title': 'history:snapshot',
                               'event': 'workflow_dispatch',
                               'head_branch': 'main',
                               'head_sha': '28b550aba1bf8b7d5be93494e3e146f68e675fe3',
                               'id': 39780427733,
                               'path': '.github/workflows/history-reconciliation.yml',
                               'run_attempt': 1,
                               'run_started_at': '2026-10-22T12:41:29Z',
                               'status': 'completed',
                               'updated_at': '2026-10-22T13:02:18Z'},
 'post_failure_snapshot_steps_sha256': '321d2e78c07548dd2ae10c3b311873eb3fe903053857e3533375a1048151590c',
 'reviewed_cloud_creator_reference_records': 648,
 'schema': 'apply-pinned-native-correction-source-and-nonattempt-proof/v1'}

STATIC_PINS_HASH = '3d1b225cefecc7a0696ebddf909b091de89599b2c989b59c54252fc979123ec1'
FAILED_JOB_HASH = '263c374292b9ad0e93e225b1ff9e2b5ded70298d5891827a3c6dcbc87c52733a'
PARTIAL_JOB_HASH = 'c0ac0d503001772c51b2a1187f260d8a14cec15f6abf95248e8be362b6ff5f96'
RECEIPT_SCHEMA = 'apply-contained-storage-verification-receipt/v1'
RECEIPT_PATH = Path(__file__).resolve().parents[1] / 'recovery-evidence/2026-10-22-contained-verification.json'
COMPLETE_ARCHIVE_PATH = Path(__file__).resolve().parents[1] / 'recovery-evidence/2026-10-22-contained-complete.aesgcm'
RECEIPT_FIELDS = frozenset({'schema', 'protocol_schema', 'execution_binding', 'verification_run',
    'verification_job_id', 'verification_jobs_sha256', 'verification_steps_sha256', 'artifact_id',
    'artifact_zip_sha256', 'archive_sha256', 'member_sha256', 'complete_source_fingerprint',
    'complete_pages', 'complete_core_records', 'prior_source_fingerprint', 'prior_pages_unchanged',
    'prior_core_unchanged', 'historical_records_unchanged', 'ready_core_sha256',
    'cohort_whole_core_sha256', 'independent_proof_sha256', 'independently_authenticated',
    'external_effects', 'native_delivery_verified', 'external_effects_reconciled', 'index_complete',
    'receipt_content_sha256'})


def require(value, reason):
    if not value:
        raise ReconciliationBlocked(reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def require_reviewed_pins():
    require(content_hash(STATIC_PINS) == STATIC_PINS_HASH
            and set(TEMPLATE_CORE_HASHES) == {BUNDLE_ID, *('writer-resolution:' + value for value in FAILURES)}
            and all(_hash(value) for value in TEMPLATE_CORE_HASHES.values())
            and set(RESERVED_CORE_HASHES) == set(STATIC_PINS['permanently_reserved_individual_observation_ids'])
            and dict(RESERVED_CORE_HASHES) == STATIC_PINS['permanently_reserved_individual_observation_whole_core_sha256']
            and PARTIAL_ARCHIVE.is_file() and not PARTIAL_ARCHIVE.is_symlink()
            and hashlib.sha256(PARTIAL_ARCHIVE.read_bytes()).hexdigest()
                == STATIC_PINS['post_failure_snapshot_archive_sha256'], 'contained_reviewed_pins_missing')


def _contents(fetch, path, head):
    value = fetch('/contents/' + path + '?ref=' + head)
    try:
        require(value.get('encoding') == 'base64', 'contained_source_encoding_invalid')
        return base64.b64decode(value['content'], validate=False)
    except (KeyError, ValueError, TypeError):
        raise ReconciliationBlocked('contained_source_unavailable') from None


def _local_sources():
    root = Path(__file__).resolve().parents[1]
    require(all((root / path).is_file() and not (root / path).is_symlink() for path in PROTOCOL_FILES),
            'contained_protocol_source_missing')
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in PROTOCOL_FILES}


def _verify_sources(fetch, head, claimed):
    expected = _local_sources()
    require(claimed == expected and all(hashlib.sha256(_contents(fetch, path, head)).hexdigest() == digest
                                       for path, digest in expected.items()), 'contained_protocol_source_changed')


def _binding_valid(value):
    return (isinstance(value, dict)
        and set(value) == {'run_id', 'head_sha', 'run_attempt', 'repository', 'workflow_path',
                          'actor', 'triggering_actor', 'event', 'head_branch', 'source_sha256'}
        and isinstance(value['run_id'], str) and re.fullmatch('[0-9]+', value['run_id']) is not None
        and isinstance(value['head_sha'], str) and re.fullmatch('[0-9a-f]{40}', value['head_sha']) is not None
        and type(value['run_attempt']) is int and value['run_attempt'] == 1
        and value['repository'] == old.REPOSITORY and value['workflow_path'] == WORKFLOW
        and value['actor'] == value['triggering_actor'] == 'narendranathe'
        and value['event'] == 'workflow_dispatch' and value['head_branch'] == 'main'
        and isinstance(value['source_sha256'], dict) and set(value['source_sha256']) == set(PROTOCOL_FILES)
        and all(_hash(digest) for digest in value['source_sha256'].values()))


def _run_matches(run, identity, head):
    return (isinstance(run, dict) and type(run.get('id')) is int and str(run['id']) == str(identity)
        and run.get('head_sha') == head and run.get('head_branch') == 'main'
        and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
        and run.get('display_title') == TITLE and run.get('event') == 'workflow_dispatch'
        and run.get('actor', {}).get('login') == run.get('triggering_actor', {}).get('login') == 'narendranathe'
        and str(run.get('path', '')).partition('@')[0] == WORKFLOW)


def _jobs(fetch, identity, head, *, active=False, success=False):
    jobs = fetch('/actions/runs/' + str(identity) + '/jobs?filter=all&per_page=100&page=1')
    require(old._complete_list(jobs, 'jobs', 1), 'contained_complete_job_history_required')
    job = jobs['jobs'][0]
    operation = [step for step in job.get('steps', []) if step.get('name') == OPERATION]
    require(str(job.get('run_id')) == str(identity) and job.get('head_sha') == head
            and type(job.get('run_attempt')) is int and job['run_attempt'] == 1
            and job.get('name') == 'reconcile' and 'ubuntu-latest' in job.get('labels', [])
            and 'self-hosted' not in job.get('labels', []) and len(operation) == 1,
            'contained_hosted_job_invalid')
    if active:
        require(job.get('status') == operation[0].get('status') == 'in_progress'
                and job.get('conclusion') is None and operation[0].get('conclusion') is None,
                'contained_current_operation_not_active')
    else:
        require(job.get('status') == operation[0].get('status') == 'completed'
                and old._time(operation[0].get('started_at')) is not None,
                'contained_executor_job_not_terminal')
    if success:
        names = {step.get('name'): step for step in job.get('steps', [])}
        require(job.get('conclusion') == operation[0].get('conclusion') == 'success'
                and all(step.get('status') == 'completed'
                        and step.get('conclusion') in {'success', 'skipped'} for step in job['steps'])
                and all(names.get(name, {}).get('conclusion') == 'success' for name in
                    (OPERATION, 'Encrypted source snapshot and aggregate evidence', 'Remove plaintext staging')),
                'contained_successful_backup_job_required')
    return jobs


def _current(github, context):
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == old.REPOSITORY
            and context.get('actor') == context.get('triggering_actor') == 'narendranathe'
            and context.get('event') == 'workflow_dispatch' and context.get('cloud_enabled') == 'false'
            and re.fullmatch('[0-9a-f]{40}', str(context.get('commit', ''))) is not None
            and re.fullmatch('[0-9]+', str(context.get('run_id', ''))) is not None,
            'contained_owner_disabled_hosted_maintenance_required')
    github.verify_main(context['commit'])
    run = github.fetch('/actions/runs/' + str(context['run_id']))
    require(_run_matches(run, context['run_id'], context['commit']) and run.get('status') == 'in_progress'
            and run.get('conclusion') is None, 'contained_current_run_mismatch')
    _jobs(github.fetch, context['run_id'], context['commit'], active=True)
    binding = {'run_id': str(run['id']), 'head_sha': run['head_sha'], 'run_attempt': run['run_attempt'],
        'repository': old.REPOSITORY, 'workflow_path': WORKFLOW, 'actor': run['actor']['login'],
        'triggering_actor': run['triggering_actor']['login'], 'event': run['event'],
        'head_branch': run['head_branch'], 'source_sha256': _local_sources()}
    _verify_sources(github.fetch, binding['head_sha'], binding['source_sha256'])
    return binding, run


def _history(fetch):
    values, total, seen = [], None, set()
    for page in range(1, 1001):
        result = fetch('/actions/workflows/history-reconciliation.yml/runs?event=workflow_dispatch&per_page=100&page=' + str(page))
        require(isinstance(result, dict) and type(result.get('total_count')) is int
                and result['total_count'] >= 0 and isinstance(result.get('workflow_runs'), list),
                'contained_attempt_history_incomplete')
        if total is None:
            total = result['total_count']
        require(result['total_count'] == total and len(result['workflow_runs']) <= 100,
                'contained_attempt_history_changed')
        require(result.get('incomplete_results') in (None, False)
                and result.get('request_status', {}).get('type') != 'incomplete',
                'contained_attempt_history_incomplete')
        for run in result['workflow_runs']:
            require(isinstance(run, dict) and type(run.get('id')) is int and run['id'] not in seen,
                    'contained_attempt_history_duplicate_or_invalid')
            seen.add(run['id'])
            values.append(run)
        require(len(values) <= total, 'contained_attempt_history_count_invalid')
        if len(values) == total:
            require(not any(result.get(name) for name in ('_next_page', 'next_page', 'has_more', 'next_cursor')),
                    'contained_attempt_history_terminal_marker_invalid')
            return values
        require(len(result['workflow_runs']) == 100, 'contained_attempt_history_incomplete')
    raise ReconciliationBlocked('contained_attempt_history_incomplete')


def _prior_attempts(fetch, current):
    history = _history(fetch)
    require(sum(run.get('id') == current['id'] for run in history) == 1,
            'contained_current_attempt_missing_from_history')
    start = old._time(current.get('created_at'))
    require(start is not None, 'contained_current_attempt_time_invalid')
    for run in history:
        if not str(run.get('display_title', '')).startswith(TITLE):
            continue
        require(run.get('display_title') == TITLE and old._time(run.get('created_at')) is not None,
                'contained_unknown_protocol_attempt')
        if (old._time(run['created_at']), run['id']) < (start, current['id']):
            raise ReconciliationBlocked('contained_prior_attempt_forbids_new_posts')


def _source_capture_metadata(fetch):
    for identity in (old.BEFORE, old.INCIDENT, old.AFTER):
        path = '/actions/runs/' + identity
        run, jobs = fetch(path), fetch(path + '/jobs?filter=all&per_page=100&page=1')
        require({key: run.get(key) for key in old.RUN_FIELDS} == old.SNAPSHOT_RUNS[identity]
                and run.get('actor', {}).get('login') == run.get('triggering_actor', {}).get('login') == 'narendranathe'
                and old._complete_list(jobs, 'jobs', 1)
                and content_hash(jobs) == old.SNAPSHOT_JOBS[identity]['jobs_sha256']
                and content_hash(jobs['jobs'][0]['steps']) == old.SNAPSHOT_JOBS[identity]['steps_sha256'],
                'contained_original_source_capture_changed')
        require(fetch(path + '/jobs?filter=all&per_page=100&page=1') == jobs and fetch(path) == run,
                'contained_original_source_capture_rerun_or_change')


def _native_fixed(fetch, *, artifact_required=False):
    pins = STATIC_PINS
    identity = str(pins['failed_maintenance_run']['id'])
    path = '/actions/runs/' + identity
    run, jobs = fetch(path), fetch(path + '/jobs?filter=all&per_page=100&page=1')
    require({key: run.get(key) for key in old.RUN_FIELDS} == pins['failed_maintenance_run']
            and run.get('actor', {}).get('login') == run.get('triggering_actor', {}).get('login') == 'narendranathe'
            and old._complete_list(jobs, 'jobs', 1)
            and content_hash(jobs) == pins['failed_maintenance_jobs_sha256']
            and content_hash(jobs['jobs'][0]) == FAILED_JOB_HASH
            and content_hash(jobs['jobs'][0]['steps']) == pins['failed_maintenance_steps_sha256'],
            'contained_failed_maintenance_metadata_changed')
    require(fetch(path + '/jobs?filter=all&per_page=100&page=1') == jobs and fetch(path) == run,
            'contained_failed_maintenance_rerun_or_change')
    head = pins['original_maintenance_source_commit']
    require(all(hashlib.sha256(_contents(fetch, name, head)).hexdigest() == digest
                for name, digest in pins['original_maintenance_source_sha256'].items()),
            'contained_failed_maintenance_source_changed')
    path = '/actions/runs/' + PARTIAL_RUN
    run, jobs = fetch(path), fetch(path + '/jobs?filter=all&per_page=100&page=1')
    require({key: run.get(key) for key in old.RUN_FIELDS} == pins['post_failure_snapshot_run']
            and run.get('actor', {}).get('login') == run.get('triggering_actor', {}).get('login') == 'narendranathe'
            and old._complete_list(jobs, 'jobs', 1)
            and content_hash(jobs) == pins['post_failure_snapshot_jobs_sha256']
            and content_hash(jobs['jobs'][0]) == PARTIAL_JOB_HASH
            and content_hash(jobs['jobs'][0]['steps']) == pins['post_failure_snapshot_steps_sha256'],
            'contained_partial_snapshot_native_metadata_changed')
    if artifact_required:
        artifacts = fetch(path + '/artifacts?per_page=100&page=1')
        require(old._complete_list(artifacts, 'artifacts', 1), 'contained_partial_snapshot_artifact_incomplete')
        item = artifacts['artifacts'][0]
        require(item.get('id') == pins['post_failure_snapshot_artifact_id']
                and item.get('digest') == 'sha256:' + pins['post_failure_snapshot_artifact_zip_sha256']
                and item.get('name') == 'reconciliation-evidence-' + PARTIAL_RUN and item.get('expired') is False
                and str(item.get('workflow_run', {}).get('id')) == PARTIAL_RUN
                and item['workflow_run'].get('head_sha') == pins['post_failure_snapshot_run']['head_sha'],
                'contained_partial_snapshot_artifact_changed')
    require(fetch(path + '/jobs?filter=all&per_page=100&page=1') == jobs and fetch(path) == run,
            'contained_partial_snapshot_rerun_or_change')
    _source_capture_metadata(fetch)


def _partial(key):
    require(isinstance(key, bytes) and len(key) == 32, 'contained_backup_key_invalid')
    with tempfile.TemporaryDirectory(prefix='apply-contained-source-') as folder:
        root = Path(folder)
        root.chmod(0o700)
        key_path = root / 'archive.key'
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        restored = root / 'restored'
        proof = restore_archive(PARTIAL_ARCHIVE, restored, key_path=key_path)
        require(proof.get('hashes_verified') is True and proof.get('restored_files') == 1
                and {path.name for path in restored.iterdir()} == {'cloud-records.json'},
                'contained_partial_archive_member_set_invalid')
        member = (restored / 'cloud-records.json').read_bytes()
        require(hashlib.sha256(member).hexdigest() == STATIC_PINS['post_failure_snapshot_member_sha256'],
                'contained_partial_archive_member_changed')
        wrapper = json.loads(member)
    require(wrapper.get('schema') == 'apply-cloud-snapshot/v1', 'contained_partial_wrapper_invalid')
    state = SnapshotState(wrapper['snapshot'])
    require(state.fingerprint() == STATIC_PINS['post_failure_snapshot_fingerprint']
            and len(state.pages) == STATIC_PINS['post_failure_snapshot_pages']
            and len(state.rows) == STATIC_PINS['post_failure_snapshot_core_records'],
            'contained_partial_source_pin_changed')
    return state


def _prepare(github, exported, key):
    incident = old.verify_incidents(github)
    before, before_proof = old.read_snapshot(github, old.BEFORE, key)
    original_incident, incident_proof = old.read_snapshot(github, old.INCIDENT, key)
    post_stop, post_proof = old.read_snapshot(github, old.AFTER, key)
    plan = native.load_plan(original_incident, post_stop, key, post_stop_started_at=post_proof['operation_started_at'])
    delta = old.verify_delta_and_index(before, post_stop, exported, plan)
    require(content_hash(delta) == old.REVIEWED_DELTA_PROOF_HASH
            and content_hash(plan.proof) == old.REVIEWED_CONTAINMENT_PROOF_HASH,
            'contained_original_constructor_proof_changed')
    _native_fixed(github.fetch, artifact_required=True)
    logs = github.download_path('/actions/jobs/' + str(STATIC_PINS['failed_maintenance_job_id']) + '/logs')
    require(hashlib.sha256(logs).hexdigest() == STATIC_PINS['failed_maintenance_raw_log_sha256']
            and old._diagnostics(logs) == [STATIC_PINS['failed_maintenance_closed_diagnostic']],
            'contained_failed_maintenance_diagnostic_changed')
    partial = _partial(key)
    hold = plan.records[0]
    require(partial.get(HOLD_ID) is not None and core(partial.get(HOLD_ID)) == core(hold)
            and content_hash(core(hold)) == HOLD_CORE_HASH
            and all(partial.get(identity) is None for identity in RESERVED_RECORD_IDS)
            and all(partial.get('writer-resolution:' + identity) is None for identity in FAILURES),
            'contained_actual_partial_cohort_changed')
    trimmed = deepcopy(partial.snapshot)
    hold_page = partial.page_ids[HOLD_ID]
    trimmed['pages'] = [item for item in trimmed['pages'] if item['page']['id'] != hold_page]
    require(SnapshotState(trimmed).fingerprint() == post_stop.fingerprint()
            and content_hash(partial.pages[hold_page]) == STATIC_PINS['persisted_hold_source_page_sha256'],
            'contained_original_source_or_hold_metadata_changed')
    report = validate_import(partial, exported)
    require(report['records'] == report['unchanged'] == old.BASELINE_RECORDS
            and report['blocked'] == report['conflicted'] == 0, 'contained_historical_baseline_changed')
    ready = partial.get('deployment:cutover:v2')
    require(ready is not None and content_hash(core(ready)) == old.READY_CORE_HASH,
            'contained_whole_ready_record_changed')
    validate_cutover_record(ready)
    timing = STATIC_PINS['nonattempt_proof']
    require(timing['minimum_elapsed_ms'] == (2 * old.AFTER_PAGES - 1) * 400
            and timing['minimum_elapsed_ms'] > timing['actual_failed_operation_elapsed_ms'] == 1398000
            and (old._time(STATIC_PINS['failed_maintenance_operation_completed_at'])
                 - old._time(STATIC_PINS['failed_maintenance_operation_started_at'])).total_seconds() * 1000
                == timing['actual_failed_operation_elapsed_ms'],
            'contained_resolution_nonattempt_proof_invalid')
    original_resolutions = [old.resolution_record(identity, incident, before_proof, post_proof, delta,
        incident_after_proof=incident_proof) for identity in (*FAILURES[1:], old.FAILED)]
    return partial, plan, original_resolutions


def _bundle(plan, original_resolutions, binding):
    observations = tuple(validate_record(value) for value in plan.records[1:])
    require(len(observations) == 2 and {value['id']: content_hash(value) for value in observations}
            == dict(RESERVED_CORE_HASHES), 'contained_embedded_observations_changed')
    reservations = [{'record_id': value['id'], 'whole_core_sha256': content_hash(value),
        'payload_sha256': value['hash'], 'storage_state': 'storage_unknown', 'attempted': None,
        'persisted': None, 'permanent': True, 'individual_record_absent_in_authenticated_snapshot': True,
        'may_create': False, 'may_retry': False, 'may_alias': False,
        'late_visibility_action': 'stop_for_reconciliation'} for value in observations]
    return record(BUNDLE_ID, 'audit_events', {'schema': BUNDLE_SCHEMA, 'correction_epoch_id': EPOCH_ID,
        'failed_maintenance_run_id': str(STATIC_PINS['failed_maintenance_run']['id']),
        'existing_hold': {'record_id': HOLD_ID, 'whole_core_sha256': HOLD_CORE_HASH},
        'embedded_original_observation_records': list(observations),
        'permanent_storage_unknown_reservations': reservations,
        'original_observations_individually_verified': False, 'original_observations_replayed': False,
        'embedded_original_core_values_unchanged': True, 'native_delivery_verified': False,
        'native_history_complete': False, 'user_to_native_row_linkage_verified': False,
        'actual_sender_verified': False, 'actual_recipient_verified': False,
        'external_effects_reconciled': False, 'canonical_contact_created': False,
        'activation_authorized': False, 'writer_resolution_record_ids': [value['id'] for value in original_resolutions],
        'all_original_resolution_posts_never_attempted': True, 'source_and_timing_evidence': deepcopy(STATIC_PINS),
        'authenticated_original_native_plan_proof': plan.proof,
        'encrypted_original_evidence': {'repository_path': str(native.PACKET_PATH.relative_to(Path(__file__).resolve().parents[1])),
            'packet_ciphertext_sha256': native.PACKET_SHA256,
            'packet_member_hashes_sha256': content_hash(native.MEMBER_SHA256),
            'trusted_user_attestation_ciphertext_sha256': native.MEMBER_SHA256['source-evidence/trusted-user-stop-attestation.aesgcm'],
            'attestation_member_hashes_sha256': content_hash(native.ATTESTATION_MEMBER_SHA256)},
        'verified_correction_run_binding': deepcopy(binding),
        'requires_actual_verified_run_binding_before_apply': True,
        'requires_common_write_boundary_reservation_enforcement': True,
        'requires_fresh_original_unknown_id_absence_checks': True,
        'requires_successful_hosted_correction_and_encrypted_backup': True,
        'requires_explicit_bundle_adapter_for_future_coordination': True,
        'partial_new_cohort_action': 'stop_no_retry_no_new_epoch'}, 'storage_unknown_contained')


def _records(plan, original_resolutions, binding):
    bundle = _bundle(plan, original_resolutions, binding)
    base = original_resolutions[0]['payload']['evidence']
    require(all(value['payload']['evidence'] == base for value in original_resolutions),
            'contained_original_peer_evidence_changed')
    evidence = {'original_readback_incident_evidence': deepcopy(base),
        'correction_epoch': {'schema': BUNDLE_SCHEMA, 'id': EPOCH_ID,
            'bundle_record_id': BUNDLE_ID, 'bundle_whole_core_sha256': content_hash(bundle),
            'existing_hold_record_id': HOLD_ID, 'existing_hold_whole_core_sha256': HOLD_CORE_HASH,
            'source_and_timing_evidence_sha256': STATIC_PINS_HASH,
            'verified_correction_run_binding': deepcopy(binding),
            'requires_actual_verified_run_binding_before_apply': True}}
    resolutions = []
    for original in original_resolutions:
        payload = deepcopy(original['payload'])
        payload.update(schema=SCHEMA, evidence=deepcopy(evidence),
            original_individual_observation_cohort_complete=False,
            native_observations_losslessly_embedded_in_correction_bundle=True,
            original_auxiliary_observation_storage_unknown_permanently_reserved=True,
            new_correction_epoch_id=EPOCH_ID)
        resolutions.append(record(original['id'], original['kind'], payload, original['status']))
    return [bundle, *resolutions]


def _template_valid(rows):
    try:
        bundle, *resolutions = [validate_record(value) for value in rows]
        binding = bundle['payload']['verified_correction_run_binding']
        if not _binding_valid(binding) or bundle['id'] != BUNDLE_ID:
            return False
        normalized_bundle = deepcopy(bundle['payload'])
        normalized_bundle['verified_correction_run_binding'] = None
        normalized = record(BUNDLE_ID, bundle['kind'], normalized_bundle, bundle['status'])
        if content_hash(normalized) != TEMPLATE_CORE_HASHES[BUNDLE_ID]:
            return False
        if len(resolutions) != 3 or {value['id'] for value in resolutions} != set(TEMPLATE_CORE_HASHES) - {BUNDLE_ID}:
            return False
        evidence = resolutions[0]['payload']['evidence']
        for row in resolutions:
            p = deepcopy(row['payload'])
            correction = p['evidence']['correction_epoch']
            if (row['payload']['evidence'] != evidence or correction['verified_correction_run_binding'] != binding
                    or correction['bundle_whole_core_sha256'] != content_hash(bundle)):
                return False
            correction['verified_correction_run_binding'] = None
            correction['bundle_whole_core_sha256'] = TEMPLATE_CORE_HASHES[BUNDLE_ID]
            if content_hash(record(row['id'], row['kind'], p, row['status'])) != TEMPLATE_CORE_HASHES[row['id']]:
                return False
        return True
    except Exception:
        return False


def _cohort(read_record):
    hold = read_record(HOLD_ID)
    require(hold is not None and content_hash(core(hold)) == HOLD_CORE_HASH, 'contained_hold_missing_or_changed')
    require(all(read_record(identity) is None for identity in RESERVED_RECORD_IDS), 'contained_reserved_id_appeared')
    rows = [read_record(BUNDLE_ID), *[read_record('writer-resolution:' + value) for value in (*FAILURES[1:], old.FAILED)]]
    require(_template_valid(rows), 'contained_complete_exact_peer_cohort_required')
    return rows, rows[0]['payload']['verified_correction_run_binding']


def _receipt(rows, binding):
    require(RECEIPT_PATH.is_file() and not RECEIPT_PATH.is_symlink()
            and COMPLETE_ARCHIVE_PATH.is_file() and not COMPLETE_ARCHIVE_PATH.is_symlink(),
            'contained_durable_verification_receipt_required')
    require(RECEIPT_PATH.stat().st_size <= 100000, 'contained_durable_receipt_size_invalid')
    require(COMPLETE_ARCHIVE_PATH.stat().st_size <= 128000000, 'contained_durable_archive_size_invalid')
    cipher = COMPLETE_ARCHIVE_PATH.read_bytes()
    require(cipher.startswith(MAGIC) and len(cipher) > len(MAGIC) + 28,
            'contained_durable_archive_format_invalid')
    try:
        value = json.loads(RECEIPT_PATH.read_text())
    except (ValueError, UnicodeError):
        raise ReconciliationBlocked('contained_durable_receipt_invalid') from None
    require(isinstance(value, dict) and set(value) == RECEIPT_FIELDS, 'contained_durable_receipt_fields_invalid')
    normalized = {key: item for key, item in value.items() if key != 'receipt_content_sha256'}
    require(content_hash(normalized) == value['receipt_content_sha256']
            and value['schema'] == RECEIPT_SCHEMA and value['protocol_schema'] == SCHEMA
            and value['execution_binding'] == binding and value['independently_authenticated'] is True
            and value['external_effects'] == 0 and type(value['external_effects']) is int
            and value['native_delivery_verified'] is False and value['external_effects_reconciled'] is False
            and value['index_complete'] is False
            and type(value['complete_pages']) is int and value['complete_pages'] == 2902
            and type(value['complete_core_records']) is int and value['complete_core_records'] == 2655
            and all(type(value[key]) is int for key in
                    ('prior_pages_unchanged', 'prior_core_unchanged', 'historical_records_unchanged'))
            and value['prior_source_fingerprint'] == STATIC_PINS['post_failure_snapshot_fingerprint']
            and value['prior_pages_unchanged'] == 2898 and value['prior_core_unchanged'] == 2651
            and value['historical_records_unchanged'] == old.BASELINE_RECORDS
            and value['ready_core_sha256'] == old.READY_CORE_HASH
            and value['cohort_whole_core_sha256'] == {HOLD_ID: HOLD_CORE_HASH,
                    **{row['id']: content_hash(core(row)) for row in rows}}
            and all(_hash(value[key]) for key in ('archive_sha256', 'member_sha256', 'artifact_zip_sha256',
                'complete_source_fingerprint', 'verification_jobs_sha256', 'verification_steps_sha256',
                'independent_proof_sha256'))
            and type(value['artifact_id']) is int and value['artifact_id'] > 0
            and type(value['verification_job_id']) is int and value['verification_job_id'] > 0
            and isinstance(value['verification_run'], dict) and set(value['verification_run']) == set(old.RUN_FIELDS)
            and type(value['verification_run'].get('id')) is int
            and type(value['verification_run'].get('run_attempt')) is int
            and value['verification_run']['run_attempt'] == 1
            and value['verification_run'].get('status') == 'completed'
            and value['verification_run'].get('conclusion') == 'success'
            and value['verification_run'].get('head_sha') == binding['head_sha']
            and value['verification_run'].get('head_branch') == 'main'
            and value['verification_run'].get('event') == 'workflow_dispatch'
            and value['verification_run'].get('display_title') == TITLE
            and str(value['verification_run'].get('path', '')).partition('@')[0] == WORKFLOW
            and all(old._time(value['verification_run'].get(key)) is not None
                    for key in ('created_at', 'run_started_at', 'updated_at'))
            and hashlib.sha256(cipher).hexdigest() == value['archive_sha256'],
            'contained_durable_receipt_proof_changed')
    return value


def _successful_verification(fetch, binding, rows):
    _verify_sources(fetch, binding['head_sha'], binding['source_sha256'])
    receipt = _receipt(rows, binding)
    executor = _executor(fetch, binding)
    history = _history(fetch)
    require(any(run.get('id') == executor['id'] for run in history), 'contained_executor_missing_from_history')
    selected = receipt['verification_run']
    require(type(selected.get('id')) is int and selected['id'] >= executor['id']
            and sum(run.get('id') == selected['id'] for run in history) == 1,
            'contained_successful_verification_missing_from_history')
    identity = str(selected['id'])
    run = fetch('/actions/runs/' + identity)
    require(_run_matches(run, identity, binding['head_sha']) and run.get('status') == 'completed'
            and run.get('conclusion') == 'success'
            and {key: run.get(key) for key in old.RUN_FIELDS} == selected,
            'contained_successful_verification_run_changed')
    jobs = _jobs(fetch, identity, binding['head_sha'], success=True)
    require(content_hash(jobs) == receipt['verification_jobs_sha256']
            and jobs['jobs'][0]['id'] == receipt['verification_job_id']
            and content_hash(jobs['jobs'][0]['steps']) == receipt['verification_steps_sha256'],
            'contained_successful_verification_jobs_changed')
    require(fetch('/actions/runs/' + identity + '/jobs?filter=all&per_page=100&page=1') == jobs
            and fetch('/actions/runs/' + identity) == run,
            'contained_successful_verification_rerun_or_change')
    return True


def _executor(fetch, binding):
    executor_path = '/actions/runs/' + binding['run_id']
    executor = fetch(executor_path)
    require(_run_matches(executor, binding['run_id'], binding['head_sha']) and executor.get('status') == 'completed',
            'contained_executor_native_attempt_changed')
    jobs = _jobs(fetch, binding['run_id'], binding['head_sha'])
    require(fetch(executor_path + '/jobs?filter=all&per_page=100&page=1') == jobs and fetch(executor_path) == executor,
            'contained_executor_native_rerun_or_change')
    return executor


def _original_attempts(fetch):
    for failed in FAILURES:
        path = '/actions/runs/' + failed
        run, jobs = fetch(path), fetch(path + '/jobs?filter=all&per_page=100&page=1')
        require(old._run_matches(failed, run) and old._job_matches(failed, jobs),
                'contained_original_failed_attempt_changed')
        require(fetch(path + '/jobs?filter=all&per_page=100&page=1') == jobs and fetch(path) == run,
                'contained_original_failed_attempt_rerun_or_change')


def contained_resolution_matches(identity, marker, fetch, repository, *, read_record=None):
    try:
        require_reviewed_pins()
        if identity not in FAILURES or repository != old.REPOSITORY or not callable(read_record):
            return False
        rows, binding = _cohort(read_record)
        peer = next(row for row in rows[1:] if row['id'] == 'writer-resolution:' + identity)
        if core(peer) != core(validate_record(marker)):
            return False
        _original_attempts(fetch)
        _native_fixed(fetch)
        return _successful_verification(fetch, binding, rows)
    except Exception:
        return False


def _write_verified(store, row):
    written = store.put(row)
    observed = store.get(row['id'])
    for delay in (1, 2, 4):
        if observed is not None:
            break
        sleeper = getattr(store, 'sleeper', None)
        if not callable(sleeper):
            break
        sleeper(delay)
        observed = store.get(row['id'])
    try:
        exact = isinstance(written, dict) and isinstance(observed, dict) and core(written) == core(row) == core(observed)
    except (KeyError, TypeError):
        exact = False
    if not exact:
        raise AmbiguousWriteError('Contained correction readback uncertain; stop without retry',
                                  diagnostics={'operation': 'verify_create_readback'})


def reconcile(store, exported, github, context, key):
    # This check precedes credentialed source/native reads and every mutation.
    require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
            and context.get('repository') == old.REPOSITORY
            and context.get('actor') == context.get('triggering_actor') == 'narendranathe'
            and context.get('event') == 'workflow_dispatch' and context.get('cloud_enabled') == 'false',
            'contained_owner_disabled_hosted_maintenance_required')
    require_reviewed_pins()
    binding, current_run = _current(github, context)
    partial, plan, originals = _prepare(github, exported, key)
    current = SnapshotState(store.snapshot_all())
    require(current.data_source_id == getattr(store, 'data_source_id', None) == partial.data_source_id
            and old._time(current.snapshot['captured_at']) >= old._time(partial.snapshot['captured_at']),
            'contained_fresh_source_identity_or_time_changed')
    require(all(current.get(identity) is None for identity in RESERVED_RECORD_IDS), 'contained_reserved_id_appeared')
    ids = [BUNDLE_ID, *['writer-resolution:' + value for value in (*FAILURES[1:], old.FAILED)]]
    existing = [current.get(identity) for identity in ids]
    require(not any(value is not None for value in existing) or all(value is not None for value in existing),
            'contained_partial_new_batch_requires_reconciliation')
    unchanged = all(value is not None for value in existing)
    if unchanged:
        require(_template_valid(existing), 'contained_existing_complete_batch_changed')
        original_binding = existing[0]['payload']['verified_correction_run_binding']
        require(original_binding['head_sha'] == binding['head_sha']
                and original_binding['source_sha256'] == binding['source_sha256'],
                'contained_readonly_verification_source_changed')
        _executor(github.fetch, original_binding)
        expected = _records(plan, originals, original_binding)
    else:
        _prior_attempts(github.fetch, current_run)
        expected = _records(plan, originals, binding)
    require(_template_valid(expected), 'contained_reviewed_constructor_template_changed')
    for row, observed in zip(expected, existing):
        if observed is not None:
            require(core(observed) == core(row), 'contained_existing_batch_conflict')
            current.pages.pop(current.page_ids[row['id']])
    require(current.fingerprint() == partial.fingerprint(), 'contained_fresh_source_changed')
    fresh_binding, _ = _current(github, context)
    require(fresh_binding == binding, 'contained_current_execution_changed')
    if not unchanged:
        _prior_attempts(github.fetch, current_run)
        for position, row in enumerate(expected):
            if position == 1:
                contained = SnapshotState(store.snapshot_all())
                require(all(contained.get(identity) is None for identity in RESERVED_RECORD_IDS),
                        'contained_reserved_id_appeared')
                bundle = contained.get(BUNDLE_ID)
                require(bundle is not None and core(bundle) == core(expected[0]),
                        'contained_bundle_missing_before_resolution')
                contained.pages.pop(contained.page_ids[BUNDLE_ID])
                require(contained.fingerprint() == partial.fingerprint(), 'contained_source_changed_after_bundle')
                require(_current(github, context)[0] == binding, 'contained_current_execution_changed')
                _original_attempts(github.fetch)
                _native_fixed(github.fetch)
                _prior_attempts(github.fetch, current_run)
            _write_verified(store, row)
    return {'operation': 'pinned_contained_storage_reconciliation', 'verified': True, 'unchanged': unchanged,
        'new_records': 0 if unchanged else 4, 'cohort_records': 5,
        'resolved_run_ids': list(FAILURES), 'record_sha256': {row['id']: row['hash'] for row in expected},
        'whole_core_sha256': {row['id']: content_hash(row) for row in expected},
        'existing_hold_unchanged': True, 'reserved_individual_observation_records': 2,
        'embedded_original_observation_records': 2, 'reserved_individual_observation_posts': 0,
        'external_effects': 0, 'external_effects_reconciled': False, 'managed_cloud_effects_reconciled': True,
        'native_delivery_verified': False, 'native_history_incomplete': True, 'index_complete': False,
        'baseline_unchanged': old.BASELINE_RECORDS, 'ready_record_changed': False, 'cloud_enabled_changed': False,
        'execution_run_id': expected[0]['payload']['verified_correction_run_binding']['run_id'],
        'verification_run_id': str(context['run_id'])}


SAFE_REASONS = frozenset(('agent_admission_or_execution_present',
 'all_current_business_lanes_required',
 'application_summary_effect_or_uncertainty',
 'authenticated_baseline_mismatch',
 'baseline_readback_changed',
 'business_lane_summary_invalid',
 'business_slot_completion_invalid',
 'business_slot_marker_invalid',
 'central_maintenance_job_mismatch',
 'central_maintenance_run_mismatch',
 'central_writer_concurrency_contract_mismatch',
 'complete_index_source_not_proven',
 'completed_lane_evidence_missing',
 'contained_actual_partial_cohort_changed',
 'contained_attempt_history_changed',
 'contained_attempt_history_count_invalid',
 'contained_attempt_history_duplicate_or_invalid',
 'contained_attempt_history_incomplete',
 'contained_attempt_history_terminal_marker_invalid',
 'contained_backup_key_invalid',
 'contained_bundle_missing_before_resolution',
 'contained_complete_exact_peer_cohort_required',
 'contained_complete_job_history_required',
 'contained_current_attempt_missing_from_history',
 'contained_current_attempt_time_invalid',
 'contained_current_execution_changed',
 'contained_current_operation_not_active',
 'contained_current_run_mismatch',
 'contained_durable_archive_format_invalid',
 'contained_durable_archive_size_invalid',
 'contained_durable_receipt_fields_invalid',
 'contained_durable_receipt_invalid',
 'contained_durable_receipt_proof_changed',
 'contained_durable_receipt_size_invalid',
 'contained_durable_verification_receipt_required',
 'contained_embedded_observations_changed',
 'contained_executor_job_not_terminal',
 'contained_executor_missing_from_history',
 'contained_executor_native_attempt_changed',
 'contained_executor_native_rerun_or_change',
 'contained_existing_batch_conflict',
 'contained_existing_complete_batch_changed',
 'contained_failed_maintenance_diagnostic_changed',
 'contained_failed_maintenance_metadata_changed',
 'contained_failed_maintenance_rerun_or_change',
 'contained_failed_maintenance_source_changed',
 'contained_fresh_source_changed',
 'contained_fresh_source_identity_or_time_changed',
 'contained_historical_baseline_changed',
 'contained_hold_missing_or_changed',
 'contained_hosted_job_invalid',
 'contained_original_constructor_proof_changed',
 'contained_original_failed_attempt_changed',
 'contained_original_failed_attempt_rerun_or_change',
 'contained_original_peer_evidence_changed',
 'contained_original_source_capture_changed',
 'contained_original_source_capture_rerun_or_change',
 'contained_original_source_or_hold_metadata_changed',
 'contained_owner_disabled_hosted_maintenance_required',
 'contained_partial_archive_member_changed',
 'contained_partial_archive_member_set_invalid',
 'contained_partial_new_batch_requires_reconciliation',
 'contained_partial_snapshot_artifact_changed',
 'contained_partial_snapshot_artifact_incomplete',
 'contained_partial_snapshot_native_metadata_changed',
 'contained_partial_snapshot_rerun_or_change',
 'contained_partial_source_pin_changed',
 'contained_partial_wrapper_invalid',
 'contained_prior_attempt_forbids_new_posts',
 'contained_protocol_source_changed',
 'contained_protocol_source_missing',
 'contained_readonly_verification_source_changed',
 'contained_reserved_id_appeared',
 'contained_resolution_nonattempt_proof_invalid',
 'contained_reviewed_constructor_template_changed',
 'contained_reviewed_pins_missing',
 'contained_source_changed_after_bundle',
 'contained_source_encoding_invalid',
 'contained_source_unavailable',
 'contained_successful_backup_job_required',
 'contained_successful_verification_jobs_changed',
 'contained_successful_verification_missing_from_history',
 'contained_successful_verification_rerun_or_change',
 'contained_successful_verification_run_changed',
 'contained_unknown_protocol_attempt',
 'contained_whole_ready_record_changed',
 'containment_cohort_missing_before_clearance',
 'exact_native_claim_containment_unverified',
 'exact_three_new_completed_slots_required',
 'existing_readback_resolution_conflict',
 'existing_scheduled_resolution_conflict',
 'existing_startup_resolution_conflict',
 'failed_hosted_job_mismatch',
 'failed_job_history_incomplete',
 'failed_job_history_incomplete_or_changed',
 'failed_operation_steps_mismatch',
 'failed_run_changed_during_verification',
 'failed_slot_marker_mismatch',
 'failed_steps_incomplete',
 'failure_not_at_startup_operation',
 'fresh_source_capture_predates_snapshot',
 'fresh_source_changed_after_containment',
 'fresh_source_changed_since_snapshot',
 'fresh_source_identity_mismatch',
 'fresh_source_identity_or_time_mismatch',
 'frozen_business_timestamp_invalid',
 'frozen_reporting_cursor_invalid',
 'guard_witness_record_mismatch',
 'incident_evidence_changed_before_append',
 'incomplete_index_manifest_proof_changed',
 'index_builder_source_changed',
 'index_child_core_conflict',
 'index_prefix_not_exact_reviewed_publication',
 'index_reconstruction_shape_changed',
 'last_child_create_time_outside_original_run',
 'last_child_later_exact_readback_changed',
 'later_guard_audit_constructor_changed',
 'later_guard_audit_time_invalid',
 'maintenance_jobs_incomplete',
 'maintenance_run_identity_missing',
 'maintenance_workflow_source_unavailable',
 'native_claim_honesty_invalid',
 'native_cohort_core_changed',
 'native_cohort_invalid',
 'native_contact_scope_mapping_changed',
 'native_containment_evidence_invalid',
 'native_exact_source_page_changed',
 'native_fresh_post_stop_snapshot_required',
 'native_full_source_changed',
 'native_packet_authentication_failed',
 'native_packet_ciphertext_changed',
 'native_packet_manifest_honesty_invalid',
 'native_packet_manifest_invalid',
 'native_packet_member_hash_mismatch',
 'native_packet_members_invalid',
 'native_packet_unavailable',
 'native_proposal_applied_or_changed',
 'native_quarantine_scope_invalid',
 'native_record_source_binding_changed',
 'native_source_fields_changed',
 'native_stop_honesty_invalid',
 'native_trusted_stop_attestation_changed',
 'native_unmanaged_claim_shape_changed',
 'new_attempt_receipt_or_outreach_effect',
 'old_failed_slot_falsely_completed',
 'original_business_day_changed',
 'original_index_source_or_previous_manifest_changed',
 'outreach_summary_has_send',
 'owner_disabled_hosted_maintenance_required',
 'partial_resolution_batch_requires_reconciliation',
 'pinned_failed_run_mismatch',
 'pinned_source_contract_mismatch',
 'pinned_source_unavailable',
 'predecessor_no_job_proof_missing',
 'prior_source_changed',
 'prior_source_core_changed',
 'prior_source_page_changed',
 'readback_diagnostic_mismatch',
 'readback_snapshot_review_pending',
 'ready_record_changed',
 'reference_index_manifest_missing',
 'reference_index_timestamp_conflict',
 'reference_index_timestamp_invalid',
 'resolution_identity_invalid',
 'reviewed_business_core_changed',
 'reviewed_containment_proof_changed',
 'reviewed_guard_audit_core_changed',
 'reviewed_readback_delta_proof_changed',
 'reviewed_source_fingerprint_mismatch',
 'scheduled_diagnostic_mismatch',
 'scheduled_log_invalid',
 'scheduled_log_unavailable',
 'scheduled_snapshot_review_pending',
 'snapshot_does_not_bracket_failure',
 'snapshot_does_not_bracket_failures',
 'source_delta_not_exact_guard_witness',
 'source_delta_not_exact_reviewed_records',
 'source_identity_invalid',
 'source_page_invalid',
 'source_record_identity_conflict',
 'source_schema_changed',
 'source_snapshot_artifact_history_incomplete',
 'source_snapshot_artifact_invalid',
 'source_snapshot_artifact_members_invalid',
 'source_snapshot_authentication_failed',
 'source_snapshot_ciphertext_mismatch',
 'source_snapshot_content_mismatch',
 'source_snapshot_export_time_invalid',
 'source_snapshot_hosted_job_invalid',
 'source_snapshot_hosted_run_invalid',
 'source_snapshot_identity_invalid',
 'source_snapshot_incomplete',
 'source_snapshot_jobs_incomplete',
 'source_snapshot_member_hashes_unverified',
 'source_snapshot_member_or_wrapper_invalid',
 'source_snapshot_member_set_invalid',
 'source_snapshot_operation_start_invalid',
 'source_snapshot_order_invalid',
 'source_snapshot_page_count_mismatch',
 'source_snapshot_run_changed',
 'source_snapshot_wrapper_invalid',
 'startup_diagnostic_mismatch',
 'startup_log_invalid',
 'startup_log_unavailable',
 'submission_impossibility_contract_changed',
 'unfinished_slot_scope_changed',
 'unique_new_funnel_report_required',
 'unresolved_started_business_slot'))
