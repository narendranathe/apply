"""Reviewed October 24 index custody evidence; read-only native witnesses.

Static pins contain no source records, contact data, logs, credentials or keys.
All remote access is explicit through the supplied read-only GitHub evidence reader.
"""
from __future__ import annotations
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from cloud.records import content_hash
from cloud.startup_reconciliation import ReconciliationBlocked

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'narendranathe/apply'
OWNER = 'narendranathe'
WORKFLOW = '.github/workflows/index-custody-reconciliation.yml'
OPERATION_STEP = 'Reconcile managed index custody without replay'
PROOF_PATH = ROOT / 'docs/runbooks/evidence/2026-10-26-authenticated-incident-diagnosis.json'
PROOF_SHA256 = '32b14333869d3cd3e03b994c73242c9e3b976d47f459cb19af17b944b9ff6037'
VERIFIER_SHA256 = '8d3e48dfa4e384d2bc30ab8c2ddb04eb8798e991206af6a888a4974543cb209e'
PROTOCOL_FILES = (WORKFLOW, 'scripts/reconcile_index_custody.py', 'scripts/verify_index_custody.py',
    'cloud/index_custody_reconciliation.py', 'cloud/index_custody_evidence.py',
    'cloud/index_custody_receipt.py', 'cloud/runtime_guard.py', 'cloud/notion.py', 'cloud/agent_index.py')
RUN_FIELDS = ('id',
 'head_sha',
 'head_branch',
 'run_attempt',
 'path',
 'event',
 'display_title',
 'status',
 'conclusion',
 'created_at',
 'updated_at',
 'run_started_at')
FAILURES = ('40050463437', '40101990960', '40103832704', '40204362589', '40204819102')
INCIDENTS = {'40050463437': {'artifact_count': 0,
                 'artifacts': {},
                 'artifacts_sha256': '0f35eb972ace40e4bbe366b75979113ffe59e9a09ef713d1756c9e3de7639d44',
                 'diagnostic': [{'cloud_operation': 'publish_agent_index',
                                 'error_type': 'IndexPublicationError',
                                 'reason': 'index_write_uncertain_stop_no_retry',
                                 'status': 'blocked'}],
                 'job_count': 2,
                 'jobs_sha256': '9286fd469e9a9bf24e037289909e32725f2141d56eff9c49dd00c123158b028f',
                 'logs_sha256': {'120741768877': '3d398be88621ebd55e8866183044fa3d5d1f42d9ad391d8a69aa9137a53cfab1'},
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'failure',
                         'created_at': '2026-10-24T01:35:09Z',
                         'display_title': 'cloud:live:all',
                         'event': 'workflow_dispatch',
                         'head_branch': 'main',
                         'head_sha': 'a0412bc8eb4faa5d8b97dfa18f66e2c4970235cb',
                         'id': 40050463437,
                         'path': '.github/workflows/cloud-operations.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-24T01:35:09Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-24T03:16:29Z'}},
 '40101990960': {'artifact_count': 1,
                 'artifacts': {'12563608946': {'members': {'cloud-backup-40101990960.aesgcm': '2a6dcd8e91c97a0a89128086e8bc3e2a4ed57f36912c51b7a2c457291ac92ad4',
                                                           'research-fallback-summary.json': '7a1a2967abddfc9345cbd318365c67c6d926b91500eb948675dee3f58867fb16'},
                                               'zip_sha256': '272e520527c0f0964c0493b41fa5472eeb99ed6776e8900df7f7883c48bd3b9b'}},
                 'artifacts_sha256': '0e70b594d82ac90563452a3084f8d32906eae02286e356857187ab93e968f897',
                 'diagnostic': [{'error_type': 'Blocked',
                                 'reason': 'previous_failed_writer_requires_reconciliation',
                                 'status': 'blocked'}],
                 'job_count': 2,
                 'jobs_sha256': 'e80331fc37503ad3ce7db202676f1d2af0302f710c60a75c639f0b3355cb6210',
                 'logs_sha256': {'120895871519': '0c09a15c4ecfa866b19f6b2d34b95e2a01342d6fcdd3aa670bac829fbd2f2c7c',
                                 '120896736672': '8b50ea6cb21558fa4eee01d22d07a5b9ba5ff58ba098c87eb9e3c6b9188ad35c'},
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'failure',
                         'created_at': '2026-10-24T14:19:38Z',
                         'display_title': 'cloud:live:0 6 * * *',
                         'event': 'schedule',
                         'head_branch': 'main',
                         'head_sha': 'a0412bc8eb4faa5d8b97dfa18f66e2c4970235cb',
                         'id': 40101990960,
                         'path': '.github/workflows/cloud-operations.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-24T14:19:38Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-24T14:47:45Z'}},
 '40103832704': {'artifact_count': 1,
                 'artifacts': {'12564797596': {'members': {'cloud-backup-40103832704.aesgcm': '0ab0b17e5cad54ffd1e2e4142a794d497f8e46190c93d830e20b1d0d045400c1',
                                                           'research-fallback-summary.json': 'dc0584cab53798c78bf00f129caeece71044b230ba77d74bab7cd24bfd408faf'},
                                               'zip_sha256': 'ffade0030a57e2e53310e0344ed19a1f1158a8fd5133c94cbb92a0682f7ff982'}},
                 'artifacts_sha256': '0e037ef5267dc9bfd794486ef8a260321dc88af323f5eb6112e2e43007c3fc3e',
                 'diagnostic': [{'error_type': 'Blocked',
                                 'reason': 'previous_failed_writer_requires_reconciliation',
                                 'status': 'blocked'}],
                 'job_count': 2,
                 'jobs_sha256': '70b74ae0a593f981cdd7f859ba3a7f1ba2ee5a5c94c7bd67737b4faed9f939a5',
                 'logs_sha256': {'120902046515': '4546915732343aa67490e80771e6026ab24738ed2245dffcb50880ab8a3a9ca8',
                                 '120902799837': '0a945e99ea64426d61b92ec3fe41cb78298b01e3628a5fc1bf7c5122e5b564a2'},
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'failure',
                         'created_at': '2026-10-24T14:44:16Z',
                         'display_title': 'cloud:live:30 6 * * *',
                         'event': 'schedule',
                         'head_branch': 'main',
                         'head_sha': 'a0412bc8eb4faa5d8b97dfa18f66e2c4970235cb',
                         'id': 40103832704,
                         'path': '.github/workflows/cloud-operations.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-24T14:44:16Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-24T15:19:35Z'}},
 '40204362589': {'artifact_count': 1,
                 'artifacts': {'12605040502': {'members': {'cloud-backup-40204362589.aesgcm': '555c10d61dd143a7faacebe5fa1bf1148a83a80c42f476201eed67d9354c6d58',
                                                           'research-fallback-summary.json': '9e09f5d7464e63eae2c2e3329e1eb938fee2a275c8ec32f3f0332f24d006596e'},
                                               'zip_sha256': 'c81e44b0836b4bf5f10cb21de75f3fc68222881c1952278f6a45cc44967b2f49'}},
                 'artifacts_sha256': 'a4e463c225fc7053d6c48a9a0a9a408fe85b954d7b7681ad9fe9a8da66f309d6',
                 'diagnostic': [{'error_type': 'Blocked',
                                 'reason': 'previous_failed_writer_requires_reconciliation',
                                 'status': 'blocked'}],
                 'job_count': 2,
                 'jobs_sha256': '245b6c918078bb3c33997be1e9aa39f5e4f16a21d8254d5faaecdf81beff80fa',
                 'logs_sha256': {'121202016284': 'd75efec5e118697ca6e40c8e236f0ed6d38b6d5fd99081d387ce604035886bf0',
                                 '121202863770': 'b69d6a2463584c5ae01cd2780bc3ef85939064c92549018d4f43e99b30529b53'},
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'failure',
                         'created_at': '2026-10-25T14:53:37Z',
                         'display_title': 'cloud:live:0 6 * * *',
                         'event': 'schedule',
                         'head_branch': 'main',
                         'head_sha': 'a0412bc8eb4faa5d8b97dfa18f66e2c4970235cb',
                         'id': 40204362589,
                         'path': '.github/workflows/cloud-operations.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-25T14:53:37Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-25T15:34:54Z'}},
 '40204819102': {'artifact_count': 1,
                 'artifacts': {'12605877004': {'members': {'cloud-backup-40204819102.aesgcm': '636fce6e934c5289893d442e46aca97710a2cbe02c7019cfbf9338b2ebad4866',
                                                           'research-fallback-summary.json': '0d67555d0842d7399bc6ceca2445dbfe8a41d1e930668a1c9c12cb9d11cb9c0b'},
                                               'zip_sha256': '2edf92648b6b4504b63337fcc236ff42213517560c5394323d0d8d896fc1e270'}},
                 'artifacts_sha256': '92cc1715297945eea5f4b6f256f26005cc41c810b2c566379f3f19bb1925f576',
                 'diagnostic': [{'error_type': 'Blocked',
                                 'reason': 'previous_failed_writer_requires_reconciliation',
                                 'status': 'blocked'}],
                 'job_count': 2,
                 'jobs_sha256': '84240ce4081ea95782c015903993770536bbb40d579f7d0eb8a8831789f09481',
                 'logs_sha256': {'121211836630': 'c193cd8615df85d55046620c8f87c78c58191b1934b561364aec46ed0a3afbe7',
                                 '121212650328': 'f0b55e075328176cd295a86a949d7de649a39838d358f3058e680e45ac184eec'},
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'failure',
                         'created_at': '2026-10-25T14:59:45Z',
                         'display_title': 'cloud:live:30 6 * * *',
                         'event': 'schedule',
                         'head_branch': 'main',
                         'head_sha': 'a0412bc8eb4faa5d8b97dfa18f66e2c4970235cb',
                         'id': 40204819102,
                         'path': '.github/workflows/cloud-operations.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-25T14:59:45Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-25T16:29:14Z'}}}
SNAPSHOTS = {'observed': {'artifact_id': 12658569365,
              'ciphertext_sha256': 'f5aec527011c981139e1fae404069f552fd889c9288a8258b20ba7c1f0d0e239',
              'cores': 3384,
              'native_zip_sha256': 'e093f34c3794b6e659a98b8101362b9ea57f246dde3c388fa7dff99379e82b0f',
              'pages': 3631,
              'path': 'recovery-evidence/2026-10-26-index-custody-snapshot.aesgcm',
              'plaintext_member_sha256': '668f90cf81d7eb9261ad1bd2eae5fe7669ff8e3eac2ff078c77403f9d7b8fe76',
              'run_id': '40337456562',
              'source_fingerprint': '939a653f34825e337625a32f9925f8bf63e43819dc6e327f98b708857320effe'},
 'trusted': {'artifact_id': 12507566994,
             'ciphertext_sha256': '017f00cfa78cc9d8acf17a7332dbaa847f8eed56aa0296b6e09f584c91ef883e',
             'cores': 2655,
             'native_zip_sha256': '92a0a315a71a77d225c0dd537a3911109e1192cc623cf8aab6f1e521a94ad97f',
             'pages': 2902,
             'path': 'recovery-evidence/2026-10-22-contained-complete.aesgcm',
             'plaintext_member_sha256': 'd693ed3f26e7d64849b13f13eff22661186fddf7131bac33fea8dbb79904e723',
             'run_id': '39958792936',
             'source_fingerprint': '51998fcc1656ed2a77f69a6e62688442b53279158f595b1c608be6c40b94764b'}}
SNAPSHOT_NATIVE = {'39958792936': {'artifact_count': 1,
                 'artifacts': {'12507566994': {'members': {'cloud-backup-39958792936.aesgcm': '017f00cfa78cc9d8acf17a7332dbaa847f8eed56aa0296b6e09f584c91ef883e',
                                                           'maintenance-summary.json': '94d6aeb0df5afe9f1236735cfdc99712777765c4a801969fcb213c4025bc57f0'},
                                               'zip_sha256': '92a0a315a71a77d225c0dd537a3911109e1192cc623cf8aab6f1e521a94ad97f'}},
                 'artifacts_sha256': '2748c736897c3e635d815ea753fd86f97bc95c2d9895a38b4ffbe0be9f84c7ef',
                 'job_count': 1,
                 'jobs_sha256': '34d93b6ce29da2fd5885ce8fb671dfd2dfdab70835c81776bc380aa139cf708c',
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'success',
                         'created_at': '2026-10-23T13:08:44Z',
                         'display_title': 'history:reconcile-contained',
                         'event': 'workflow_dispatch',
                         'head_branch': 'main',
                         'head_sha': '4a38ebbdf64d8ccd2039c648b993dcb2857a58a4',
                         'id': 39958792936,
                         'path': '.github/workflows/history-reconciliation.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-23T13:08:44Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-23T14:12:01Z'}},
 '40337456562': {'artifact_count': 1,
                 'artifacts': {'12658569365': {'members': {'cloud-backup-40337456562.aesgcm': 'f5aec527011c981139e1fae404069f552fd889c9288a8258b20ba7c1f0d0e239',
                                                           'maintenance-summary.json': '55dd347e623ba92f279f1d553f27d4c78b23b2d0bc27ec311de3cb77f60ce2a4'},
                                               'zip_sha256': 'e093f34c3794b6e659a98b8101362b9ea57f246dde3c388fa7dff99379e82b0f'}},
                 'artifacts_sha256': '96450bd315f6882b5bf0df49b5f77e38a46b6ab5b544ef7e0f676f4aaf04cd0c',
                 'job_count': 1,
                 'jobs_sha256': '30f2769f0cb34f9c1b88e12bcf11bac084d8e81a936fc00350c858ea2ea626e8',
                 'run': {'actor': 'narendranathe',
                         'conclusion': 'success',
                         'created_at': '2026-10-26T14:12:19Z',
                         'display_title': 'history:snapshot',
                         'event': 'workflow_dispatch',
                         'head_branch': 'main',
                         'head_sha': 'a0412bc8eb4faa5d8b97dfa18f66e2c4970235cb',
                         'id': 40337456562,
                         'path': '.github/workflows/history-reconciliation.yml',
                         'repository': {'default_branch': None,
                                        'full_name': 'narendranathe/apply',
                                        'private': True},
                         'run_attempt': 1,
                         'run_started_at': '2026-10-26T14:12:19Z',
                         'status': 'completed',
                         'triggering_actor': 'narendranathe',
                         'updated_at': '2026-10-26T14:38:08Z'}}}
HISTORICAL_SOURCES = {'.github/workflows/cloud-operations.yml': 'a0ef374fcbce916560c0bfb4f8801d75ed7305e0dbed817713ed68e32665bcc3',
 'cloud/agent_index.py': '7a63ce93fb1fdeee5c1df41bee9e3fd4c4ad07bec9b36a9bbd9dabfafe9bf6d5',
 'cloud/agent_worker.py': '6f1631765b4d93807d5896fca795247190df313d39960e867020e72f324c3438',
 'cloud/applications.py': '50f6ea1c66dfaa58794d838824c662b5c9483ba10cf44c2bea37d6f75776ab65',
 'cloud/index_publisher.py': 'b7337fe42dfe7ba5d4ab37b0544d6b6608faa41dc92f59820f2a2e752397e552',
 'cloud/notion.py': 'e58ecb647e5e1fba08e7135aa1c115168f0ce9386fddbce0fc89404a57c3098b',
 'cloud/orchestrator.py': '4262bd979c19f872f8c9ea0e79b7bf91db71f09071d73ed25f1e465875e94a82',
 'cloud/outreach.py': '6ee0d1dcf85dcaba5178c63edf011b33d4e13ef2bec896fba17c2ef6b3171a61',
 'cloud/records.py': '1703a35182a91636bff29c7de0a71b33f12e592f9fd26f17736d7ebee5583103',
 'cloud/runtime_guard.py': 'ec9b8eadfaeeee9c27b5fa2555fcd2e0334cf571c303123387bfbaa3c06eeac9',
 'cloud/startup_reconciliation.py': '195ad08830e9898a00a32be14117097ab2eb04a25f6533c930ea0fd09f2129f7',
 'config/cloud.json': '102aec4611c61704a6a0be172c1d03451bbdfea0adeae44b0ad73fb50f76f52d'}
UNMANAGED_DRIFT = {'after_raw_page_sha256': 'c4bac9cd7159ebce795c7920568dc1d95db62cb68dc37d225f66ff31f89d4ef8',
 'all_other_raw_fields_unchanged': True,
 'before_raw_page_sha256': '3bf1cbb1fe8b6cf26e139648609a0e023004f63cd5f1f4e652d13730503bcc4d',
 'changed_raw_pages': 1,
 'exact_changed_path': ['blocks', 3, 'paragraph', 'rich_text', 1, 'plain_text'],
 'managed_core_changes': 0,
 'normalization_performed': False,
 'page_id_sha256': '3acc6134251396efd419abae468149908a21310f98c18b8fbf5ebcc81b9be062',
 'plain_text_equals_text_content_after': None,
 'plain_text_equals_text_content_before': None,
 'rich_text_type': 'mention',
 'text_content_present_after': False,
 'text_content_present_before': False,
 'unmanaged_raw_pages_changed': 1}
STATIC_PINS_SHA256 = 'f054595b02d209432e222d9015a51fe6afcee0a6061ba4732394be518ba7f331'


def require(value, reason):
    if not value:
        raise ReconciliationBlocked(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def reviewed_proof():
    require(PROOF_PATH.is_file() and not PROOF_PATH.is_symlink()
            and _sha(PROOF_PATH.read_bytes()) == PROOF_SHA256, 'custody_reviewed_proof_changed')
    try:
        value = json.loads(PROOF_PATH.read_bytes())
        unsigned = {k: v for k, v in value.items() if k != 'proof_content_sha256'}
        require(content_hash(unsigned) == value['proof_content_sha256'], 'custody_reviewed_proof_changed')
        return value
    except (ValueError, KeyError, TypeError):
        raise ReconciliationBlocked('custody_reviewed_proof_changed') from None


def require_reviewed_pins():
    require(content_hash({'incidents': INCIDENTS, 'snapshots': SNAPSHOTS,
        'snapshot_native': SNAPSHOT_NATIVE, 'historical_sources': HISTORICAL_SOURCES,
        'drift': UNMANAGED_DRIFT}) == STATIC_PINS_SHA256,
        'custody_reviewed_pins_changed')
    reviewed_proof()
    for item in SNAPSHOTS.values():
        path = ROOT / item['path']
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 64_000_000
                and _sha(path.read_bytes()) == item['ciphertext_sha256'], 'custody_reviewed_archive_changed')


def _fetch(fetch, path):
    try:
        return fetch(path)
    except Exception:
        raise ReconciliationBlocked('custody_native_metadata_unavailable') from None


def _projection(run):
    require(isinstance(run, dict), 'custody_native_run_invalid')
    return {**{key: run.get(key) for key in RUN_FIELDS},
        'actor': run.get('actor', {}).get('login'),
        'triggering_actor': run.get('triggering_actor', {}).get('login'),
        'repository': {key: run.get('repository', {}).get(key)
                       for key in ('full_name', 'private', 'default_branch')}}


def _complete(value, name, count):
    require(isinstance(value, dict) and type(value.get('total_count')) is int
            and value['total_count'] == count and isinstance(value.get(name), list)
            and len(value[name]) == count and count < 100
            and value.get('incomplete_results') in (None, False)
            and value.get('request_status', {}).get('type') != 'incomplete'
            and not any(value.get(key) for key in ('_next_page', 'next_page', 'has_more', 'next_cursor')),
            'custody_complete_native_list_required')
    return value


def _contents(fetch, path, head):
    value = _fetch(fetch, '/contents/' + path + '?ref=' + head)
    try:
        require(isinstance(value, dict) and value.get('encoding') == 'base64'
                and value.get('type') == 'file', 'custody_source_encoding_invalid')
        raw = base64.b64decode(value['content'], validate=False)
        require(0 < len(raw) <= 2_000_000, 'custody_source_size_invalid')
        return raw
    except (KeyError, ValueError, TypeError):
        raise ReconciliationBlocked('custody_source_unavailable') from None


def _verify_sources(fetch, head, pins):
    require(isinstance(pins, dict) and pins and all(_sha(_contents(fetch, path, head)) == digest
            for path, digest in pins.items()), 'custody_protocol_source_changed')


def _download(github, path, *, artifact=False):
    try:
        raw = github.download(int(path)) if artifact else github.download_path(path)
    except Exception:
        raise ReconciliationBlocked('custody_native_binary_unavailable') from None
    require(isinstance(raw, bytes) and 0 < len(raw) <= (64_000_000 if artifact else 2_000_000),
            'custody_native_binary_invalid')
    return raw


def _zip_members(raw, expected):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            require(len(infos) == len(expected) and {i.filename for i in infos} == set(expected)
                    and all(not i.is_dir() and i.file_size <= 64_000_000
                            and i.compress_size > 0 and not i.flag_bits & 1 for i in infos),
                    'custody_artifact_members_changed')
            require(all(_sha(archive.read(name)) == digest for name, digest in expected.items()),
                    'custody_artifact_members_changed')
    except (zipfile.BadZipFile, RuntimeError, KeyError, OSError):
        raise ReconciliationBlocked('custody_artifact_members_changed') from None


def _native_witness(fetch, identity, pins, github=None, *, include_artifacts=True):
    run_path = '/actions/runs/' + identity
    job_path = run_path + '/jobs?filter=all&per_page=100&page=1'
    artifact_path = run_path + '/artifacts?per_page=100&page=1'
    run = _fetch(fetch, run_path)
    require(_projection(run) == pins['run'], 'custody_native_run_changed')
    jobs = _complete(_fetch(fetch, job_path), 'jobs', pins['job_count'])
    require(content_hash(jobs) == pins['jobs_sha256'], 'custody_native_jobs_changed')
    artifacts = None
    if include_artifacts:
        artifacts = _complete(_fetch(fetch, artifact_path), 'artifacts', pins['artifact_count'])
        require(content_hash(artifacts) == pins['artifacts_sha256'], 'custody_native_artifacts_changed')
    if github is not None:
        for job_id, digest in pins.get('logs_sha256', {}).items():
            require(_sha(_download(github, '/actions/jobs/' + job_id + '/logs')) == digest,
                    'custody_native_log_changed')
        for artifact_id, item in pins['artifacts'].items():
            raw = _download(github, artifact_id, artifact=True)
            require(_sha(raw) == item['zip_sha256'], 'custody_artifact_zip_changed')
            _zip_members(raw, item['members'])
    # Reread all mutable native surfaces after the log/archive observations.
    require(_projection(_fetch(fetch, run_path)) == pins['run'], 'custody_native_run_changed')
    require(_complete(_fetch(fetch, job_path), 'jobs', pins['job_count']) == jobs,
            'custody_native_jobs_changed')
    if include_artifacts:
        require(_complete(_fetch(fetch, artifact_path), 'artifacts', pins['artifact_count']) == artifacts,
                'custody_native_artifacts_changed')
    require(_projection(_fetch(fetch, run_path)) == pins['run'], 'custody_native_run_changed')


def native_incidents(fetch):
    """Run/jobs/source revalidation after durable full artifact admission.

    Artifact retention is deliberately irrelevant here: pinned local ciphertext
    and the authenticated committed receipt retain the admitted archive witness.
    """
    for identity in FAILURES:
        _native_witness(fetch, identity, INCIDENTS[identity], include_artifacts=False)
    for identity, pins in SNAPSHOT_NATIVE.items():
        _native_witness(fetch, identity, pins, include_artifacts=False)
    _verify_sources(fetch, INCIDENTS[FAILURES[0]]['run']['head_sha'], HISTORICAL_SOURCES)
    return incident_proof()


def incident_proof():
    return {'schema': 'apply-reviewed-index-custody-native-proof/v1',
        'reviewed_proof_sha256': PROOF_SHA256, 'reviewed_verifier_sha256': VERIFIER_SHA256,
        'native_pins_sha256': STATIC_PINS_SHA256, 'failed_run_ids': list(FAILURES),
        'original_clock_verified': False, 'original_failure_cause_verified': False,
        'original_index_publication_complete': False, 'external_effects_reconciled': False}


def verify_incidents(github, *, include_downloads=True):
    require_reviewed_pins()
    for identity, pins in {**INCIDENTS, **SNAPSHOT_NATIVE}.items():
        _native_witness(github.fetch, identity, pins, github if include_downloads else None)
    _verify_sources(github.fetch, INCIDENTS[FAILURES[0]]['run']['head_sha'], HISTORICAL_SOURCES)
    return incident_proof()


def _local_sources():
    require(all((ROOT / path).is_file() and not (ROOT / path).is_symlink() for path in PROTOCOL_FILES),
            'custody_protocol_source_missing')
    return {path: _sha((ROOT / path).read_bytes()) for path in PROTOCOL_FILES}


BINDING_FIELDS = frozenset({'run_id', 'head_sha', 'run_attempt', 'repository', 'workflow_path',
    'actor', 'triggering_actor', 'event', 'head_branch', 'source_sha256', 'mode'})


def binding_valid(value, *, executor=True):
    return (isinstance(value, dict) and set(value) == BINDING_FIELDS
        and isinstance(value['run_id'], str) and re.fullmatch('[0-9]+', value['run_id']) is not None
        and isinstance(value['head_sha'], str) and re.fullmatch('[0-9a-f]{40}', value['head_sha']) is not None
        and type(value['run_attempt']) is int and value['run_attempt'] == 1
        and value['repository'] == REPOSITORY and value['workflow_path'] == WORKFLOW
        and value['actor'] == value['triggering_actor'] == OWNER
        and value['event'] == 'workflow_dispatch' and value['head_branch'] == 'main'
        and value['mode'] in ({'reconcile'} if executor else {'reconcile', 'verify'})
        and isinstance(value['source_sha256'], dict) and set(value['source_sha256']) == set(PROTOCOL_FILES)
        and all(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest) is not None
                for digest in value['source_sha256'].values()))


def _binding_run(run, binding):
    return (isinstance(run, dict) and type(run.get('id')) is int
        and str(run['id']) == binding['run_id'] and run.get('head_sha') == binding['head_sha']
        and type(run.get('run_attempt')) is int and run['run_attempt'] == 1
        and run.get('head_branch') == 'main' and run.get('event') == 'workflow_dispatch'
        and run.get('display_title') == 'index-custody:' + binding['mode']
        and str(run.get('path', '')).partition('@')[0] == WORKFLOW
        and run.get('actor', {}).get('login') == run.get('triggering_actor', {}).get('login') == OWNER
        and run.get('repository', {}).get('private') is True
        and run.get('repository', {}).get('full_name') == REPOSITORY)


def maintenance_jobs(fetch, binding, *, active=False, success=False):
    result = _complete(_fetch(fetch, '/actions/runs/' + binding['run_id']
        + '/jobs?filter=all&per_page=100&page=1'), 'jobs', 1)
    job = result['jobs'][0]
    steps = job.get('steps', [])
    operation = [step for step in steps if step.get('name') == OPERATION_STEP]
    require(job.get('run_id') == int(binding['run_id']) and job.get('head_sha') == binding['head_sha']
            and type(job.get('run_attempt')) is int and job['run_attempt'] == 1
            and job.get('name') == 'reconcile' and job.get('runner_group_name') == 'GitHub Actions'
            and 'ubuntu-latest' in job.get('labels', []) and 'self-hosted' not in job.get('labels', [])
            and len(operation) == 1, 'custody_hosted_job_invalid')
    step = operation[0]
    if active:
        require(job.get('status') == step.get('status') == 'in_progress'
                and job.get('conclusion') is step.get('conclusion') is None,
                'custody_current_operation_not_active')
    else:
        require(job.get('status') == step.get('status') == 'completed'
                and job.get('conclusion') in {'success', 'failure', 'cancelled', 'timed_out'}
                and step.get('conclusion') in {'success', 'failure', 'cancelled', 'timed_out'},
                'custody_executor_job_not_terminal')
    if success:
        require(job.get('conclusion') == step.get('conclusion') == 'success'
                and all(s.get('status') == 'completed' and s.get('conclusion') in {'success', 'skipped'}
                        for s in steps), 'custody_successful_job_required')
    return result


def _workflow_history(fetch):
    rows, total, seen = [], None, set()
    for page in range(1, 1001):
        value = _fetch(fetch, '/actions/workflows/index-custody-reconciliation.yml/runs'
                       '?per_page=100&page=' + str(page))
        require(isinstance(value, dict) and type(value.get('total_count')) is int
                and 0 <= value['total_count'] <= 100_000 and isinstance(value.get('workflow_runs'), list)
                and len(value['workflow_runs']) <= 100
                and value.get('incomplete_results') in (None, False)
                and value.get('request_status', {}).get('type') != 'incomplete',
                'custody_maintenance_history_incomplete')
        if total is None:
            total = value['total_count']
        require(value['total_count'] == total, 'custody_maintenance_history_changed')
        for run in value['workflow_runs']:
            require(isinstance(run, dict) and type(run.get('id')) is int and run['id'] not in seen,
                    'custody_maintenance_history_invalid')
            seen.add(run['id']); rows.append(run)
        require(len(rows) <= total, 'custody_maintenance_history_invalid')
        if len(rows) == total:
            require(not any(value.get(key) for key in ('_next_page', 'next_page', 'has_more', 'next_cursor')),
                    'custody_maintenance_history_incomplete')
            return rows
        require(len(value['workflow_runs']) == 100, 'custody_maintenance_history_incomplete')
    raise ReconciliationBlocked('custody_maintenance_history_incomplete')


def verify_current_context(github, context):
    require(isinstance(context, dict) and context.get('actions') is True
            and context.get('ref') == 'refs/heads/main' and context.get('repository') == REPOSITORY
            and context.get('actor') == context.get('triggering_actor') == OWNER
            and context.get('event') == 'workflow_dispatch' and context.get('cloud_enabled') == 'false'
            and context.get('cloud_research_enabled') == 'true' and context.get('run_attempt') in ('1', 1)
            and context.get('mode') in {'reconcile', 'verify'}, 'custody_owner_disabled_hosted_context_required')
    binding = {'run_id': str(context.get('run_id', '')), 'head_sha': context.get('commit'),
        'run_attempt': 1, 'repository': REPOSITORY, 'workflow_path': WORKFLOW, 'actor': OWNER,
        'triggering_actor': OWNER, 'event': 'workflow_dispatch', 'head_branch': 'main',
        'source_sha256': _local_sources(), 'mode': context['mode']}
    require(binding_valid(binding, executor=False), 'custody_current_binding_invalid')
    try:
        github.verify_main(binding['head_sha'])
    except Exception:
        raise ReconciliationBlocked('custody_current_private_main_changed') from None
    path = '/actions/runs/' + binding['run_id']
    run = _fetch(github.fetch, path)
    require(_binding_run(run, binding) and run.get('status') == 'in_progress'
            and run.get('conclusion') is None, 'custody_current_run_changed')
    jobs = maintenance_jobs(github.fetch, binding, active=True)
    _verify_sources(github.fetch, binding['head_sha'], binding['source_sha256'])
    workflow = _contents(github.fetch, WORKFLOW, binding['head_sha']).decode('utf-8')
    require(re.search(r'(?m)^concurrency:\s*\n\s+group: hiring-outreach-single-writer\s*\n\s+cancel-in-progress: false\s*$', workflow)
            is not None, 'custody_shared_lock_required')
    history = _workflow_history(github.fetch)
    require(sum(str(row.get('id')) == binding['run_id'] for row in history) == 1,
            'custody_current_history_missing')
    require(_projection(_fetch(github.fetch, path)) == _projection(run)
            and maintenance_jobs(github.fetch, binding, active=True) == jobs,
            'custody_current_native_changed')
    return binding


def validate_binding(github, binding):
    require(binding_valid(binding), 'custody_executor_binding_invalid')
    path = '/actions/runs/' + binding['run_id']
    run = _fetch(github.fetch, path)
    require(_binding_run(run, binding) and run.get('status') == 'completed'
            and run.get('conclusion') in {'success', 'failure', 'cancelled', 'timed_out'},
            'custody_executor_run_changed')
    jobs = maintenance_jobs(github.fetch, binding)
    require(binding['source_sha256'] == _local_sources(), 'custody_executor_protocol_changed')
    _verify_sources(github.fetch, binding['head_sha'], binding['source_sha256'])
    require(_fetch(github.fetch, path) == run and maintenance_jobs(github.fetch, binding) == jobs,
            'custody_executor_native_changed')
    return deepcopy(binding)
