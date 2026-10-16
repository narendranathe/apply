"""Guarded one-writer cutover from authenticated boundary evidence.

The ready record is constructed at runtime and never enters the historical
export whose hash it binds. No provider or repository-variable API exists here.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import tomllib
import zipfile

from .coordination_quarantine import PREFIX, validate_quarantine
from .cutover import LOCAL_ACTORS, build_source_delta, make_cutover_record, validate_cutover_record, validate_import
from .linkedin_ingestion import _contact_urls, profile_url
from .notion import _text
from .records import content_hash
from scripts.encrypted_backup import safe_name

EVIDENCE_MEMBER = 'deployment/cutover-evidence.json'
REPOSITORY = 'narendranathe/apply'
TERMINAL = {'completed', 'failed', 'interrupted', 'cancelled'}


class ActivationBlocked(ValueError):
    """Only closed, non-sensitive reason labels may reach runner output."""


def _require(condition, reason):
    if not condition:
        raise ActivationBlocked(reason)


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _time(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _updated_at(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 100_000_000_000 else value, timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    return _time(value)


def _member(reference, members):
    _require(isinstance(reference, dict) and set(reference) == {'member', 'sha256'}, 'boundary_member_invalid')
    name = reference['member']
    _require(isinstance(name, str) and safe_name(name) and name in members, 'boundary_member_missing')
    body = members[name]
    _require(isinstance(body, bytes) and _sha(body) == reference['sha256'], 'boundary_member_hash_mismatch')
    return body


def _native_profile(page):
    props = page.get('properties', {})
    value = _text(props.get('Contact Key')) or props.get('Link', {}).get('url')
    try:
        return profile_url(value) if isinstance(value, str) else None
    except ValueError:
        return None


def validate_boundary_members(evidence, members, exported, *, now=None):
    """Validate source bytes only; no Notion, GitHub, claims or source writes.

    The caller separately authenticates the archive. SQLite is opened only from
    a disposable private copy. Expiration is never a no-effect assertion.
    """
    now = now or datetime.now(timezone.utc)
    _require(isinstance(evidence, dict) and set(evidence) ==
             {'schema', 'observed_at', 'actors', 'milo', 'workers', 'claims', 'snapshot'}
             and evidence.get('schema') == 'apply-cutover-boundary/v1', 'boundary_schema_invalid')
    observed = _time(evidence['observed_at'])
    _require(observed is not None and observed <= now, 'boundary_timestamp_invalid')
    actors = evidence['actors']
    _require(isinstance(actors, list) and len(actors) == 2
             and {row.get('id') for row in actors if isinstance(row, dict)} == LOCAL_ACTORS, 'desktop_actor_evidence_missing')
    actor_facts, threads, paused_times = [], {}, []
    for actor in actors:
        _require(set(actor) == {'id', 'member', 'sha256'}, 'desktop_actor_evidence_invalid')
        body = _member({k: actor[k] for k in ('member', 'sha256')}, members)
        try:
            config = tomllib.loads(body.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise ActivationBlocked('desktop_readback_invalid') from None
        _require(config.get('status') == 'PAUSED' and isinstance(config.get('target_thread_id'), str)
                 and bool(config['target_thread_id']), 'desktop_writer_not_retired')
        threads[actor['id']] = config['target_thread_id']
        updated = _updated_at(config.get('updated_at'))
        if updated is not None:
            _require(updated <= now, 'desktop_readback_timestamp_invalid')
            paused_times.append(updated)
        actor_facts.append({'id': actor['id'], 'status': 'PAUSED', 'persisted': True, 'readback_sha256': _sha(body)})
    workers = json.loads(_member(evidence['workers'], members))
    _require(isinstance(workers, dict) and workers.get('schema') == 'apply-local-worker-boundary/v1'
             and _time(workers.get('observed_at')) is not None
             and observed <= _time(workers['observed_at']) <= now, 'worker_boundary_invalid')
    rows = workers.get('workers')
    _require(isinstance(rows, list) and len(rows) == 2
             and {row.get('automation_id') for row in rows if isinstance(row, dict)} == LOCAL_ACTORS, 'worker_readbacks_missing')
    for row in rows:
        _require(row.get('thread_id') == threads[row['automation_id']]
                 and row.get('status') in {'idle', 'notLoaded'} and row.get('latest_turn_status') in TERMINAL
                 and _time(row.get('observed_at')) is not None
                 and max([observed, *paused_times]) <= _time(row['observed_at']) <= now, 'worker_not_terminal_at_boundary')
    last_worker = max(_time(row['observed_at']) for row in rows)
    _require(_time(workers['observed_at']) >= last_worker, 'worker_boundary_timestamp_invalid')
    last_worker = max(last_worker, _time(workers['observed_at']))
    milo_body = _member(evidence['milo'], members)
    milo = json.loads(milo_body)
    _require(isinstance(milo, dict) and milo.get('schema') == 'apply-user-confirmation/v1'
             and milo.get('actor') == 'Milo' and milo.get('source') == 'direct_user' and milo.get('stopped') is True
             and isinstance(milo.get('question'), str) and 'milo' in milo['question'].lower()
             and milo.get('answer') == "it's stopped"
             and isinstance(milo.get('question_item_id'), str) and bool(milo['question_item_id']), 'milo_direct_confirmation_missing')
    snapshot_body = _member(evidence['snapshot'], members)
    wrapper = json.loads(snapshot_body)
    source = wrapper.get('snapshot') if isinstance(wrapper, dict) else None
    _require(isinstance(wrapper, dict) and wrapper.get('schema') == 'apply-cloud-snapshot/v1' and isinstance(source, dict)
             and _time(source.get('captured_at')) is not None
             and last_worker <= _time(source['captured_at']) <= now, 'final_source_snapshot_predates_boundary')
    snapshot_hash = content_hash({k: v for k, v in source.items() if k != 'hash'})
    _require(str(source.get('data_source', {}).get('id', '')).replace('-', '').lower()
             == str(source.get('data_source_id', '')).replace('-', '').lower(), 'snapshot_source_identity_mismatch')
    # Reuse the offline full-source decoder; the transient audit is not stored.
    build_source_delta(source, exported, archive_reference={'archive': 'migration-archives/validation-only.aesgcm',
        'member': evidence['snapshot']['member'], 'member_sha256': _sha(snapshot_body), 'snapshot_sha256': snapshot_hash})
    quarantine_rows = [row for row in exported['records'] if row['id'].startswith(PREFIX)]
    quarantines = {}
    for row in quarantine_rows:
        try:
            canonical = validate_quarantine(row)
        except ValueError:
            raise ActivationBlocked('claim_quarantine_invalid') from None
        provenance = canonical['payload']['provenance']
        quarantines[provenance['claim_sha256']] = canonical
    db_body = _member(evidence['claims'], members)
    try:
        with tempfile.TemporaryDirectory(prefix='apply-boundary-claims-') as folder:
            path = Path(folder) / 'claims.sqlite3'
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(db_body)
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                _require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'claims_snapshot_invalid')
                claims = [dict(row) for row in db.execute('SELECT * FROM work_claims WHERE active=1')]
    except sqlite3.Error:
        raise ActivationBlocked('claims_snapshot_invalid') from None
    _require(len(claims) == len(quarantines) == 6, 'active_claim_coverage_incomplete')
    classes = Counter()
    for claim in claims:
        hashes = {field: _sha(claim[column].encode('utf-8')) for field, column in
                  (('claim_sha256', 'token'), ('task_sha256', 'task_id'), ('resource_sha256', 'resource_key'))}
        row = quarantines.get(hashes['claim_sha256'])
        _require(row is not None and row['payload']['provenance'] == hashes
                 and not any(claim.get(key) for key in ('verification', 'result', 'release_evidence')),
                 'active_claim_quarantine_mismatch')
        payload, resource = row['payload'], claim['resource_key']
        if resource.startswith('contact:'):
            try:
                canonical_profile = profile_url(resource.split(':', 1)[1])
                aliases = {profile_url(value) for value in payload['identity'].get('profile_urls', [])}
            except ValueError:
                raise ActivationBlocked('contact_quarantine_scope_invalid') from None
            _require(claim['state'] == 'published' and payload['classification'] == 'effect_unknown'
                     and aliases == {canonical_profile} and payload['identity'].get('profile_urls') == [canonical_profile],
                     'contact_quarantine_scope_invalid')
            native = [entry['page'] for entry in source['pages']
                      if not _text(entry['page']['properties'].get('Cloud ID'))
                      and not _text(entry['page']['properties'].get('Cloud Envelope'))]
            queue = [page for page in native if _text(page['properties'].get('Claim Token')) == claim['token']]
            _require(len(queue) == 1 and _text(queue[0]['properties'].get('Type')) == 'Task'
                     and queue[0]['id'].replace('-', '').lower() == claim['task_id'].replace('-', '').lower()
                     and _native_profile(queue[0]) == canonical_profile
                     and _text(queue[0]['properties'].get('Work Key')) == 'wk-' + hashes['resource_sha256']
                     and _text(queue[0]['properties'].get('Worker')) == claim['worker'], 'native_claim_grant_mismatch')
            source_ids = {page['id'].replace('-', '').lower() for page in native
                          if _text(page['properties'].get('Type')) == 'Contact' and _native_profile(page) == canonical_profile}
            matching_contacts = [item for item in exported['records'] if item['kind'] == 'contacts' and
                (canonical_profile in _contact_urls(item['payload'])
                 or str(item['payload'].get('source_notion_id', '')).replace('-', '').lower() in source_ids)]
            contact_ids = {identity for item in matching_contacts for identity in
                           (item['id'], item['payload'].get('contact_id')) if isinstance(identity, str)}
            source_ids |= {str(item['payload']['source_notion_id']).replace('-', '').lower()
                           for item in matching_contacts if item['payload'].get('source_notion_id')}
            _require(set(payload['identity'].get('contact_ids', [])) <= contact_ids
                     and {value.replace('-', '').lower() for value in payload['identity'].get('source_notion_ids', [])} <= source_ids,
                     'contact_quarantine_extra_identity_mismatch')
            classes['effect_unknown'] += 1
        elif resource.startswith('company:'):
            _require(claim['state'] == 'reserved' and payload['classification'] == 'unstarted_unverified'
                     and payload['identity'].get('company_ids') == [resource],
                     'company_coordination_scope_invalid')
            classes['unstarted_unverified'] += 1
        else:
            raise ActivationBlocked('unrecognized_active_claim')
    _require(classes == {'effect_unknown': 3, 'unstarted_unverified': 3}, 'claim_classification_incomplete')
    return {'observed_at': evidence['observed_at'], 'data_source_id': source['data_source_id'],
            'actors': sorted(actor_facts, key=lambda row: row['id']),
            'milo_confirmation_sha256': _sha(milo_body), 'raw_snapshot_sha256': snapshot_hash,
            'worker_readbacks_sha256': evidence['workers']['sha256'], 'claims_snapshot_sha256': _sha(db_body),
            'quarantine_record_ids': sorted(row['id'] for row in quarantines.values()),
            'quarantine_classes': dict(classes)}


def load_boundary_bundle(directory='migration-archives', key_value=None):
    """Authenticate every archive, selecting one non-normalized evidence member."""
    from scripts.verify_migration_archive import restore_all, sources
    try:
        pairs = sources(Path(directory))
        member_id = _sha(EVIDENCE_MEMBER.encode())
        candidates = [(index, archive, json.loads(receipt.read_bytes())) for index, (archive, receipt) in enumerate(pairs)
                      if any(row.get('member_id', _sha(row.get('name', '').encode())) == member_id
                             for row in json.loads(receipt.read_bytes()).get('files', []))]
        _require(len(candidates) == 1, 'authenticated_boundary_member_missing')
        key = base64.b64decode(key_value if key_value is not None else os.environ.get('MIGRATION_BACKUP_KEY', ''), validate=True)
        _require(len(key) == 32, 'authenticated_boundary_unavailable')
        with tempfile.TemporaryDirectory(prefix='apply-activation-restore-') as folder:
            root = Path(folder)
            key_path = root / 'recovery.bin'
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(key)
            report = restore_all(root / 'restored', key_path, Path(directory))
            _require(report.get('hashes_verified') is True, 'authenticated_boundary_unavailable')
            index, archive, receipt = candidates[0]
            _require(isinstance(receipt.get('archive'), str)
                     and re.fullmatch(r'migration-archives/[A-Za-z0-9][A-Za-z0-9._-]*\.aesgcm', receipt['archive']),
                     'authenticated_boundary_archive_path_invalid')
            archived = root / 'restored' / f'archive-{index:04d}'
            members = {str(path.relative_to(archived)): path.read_bytes() for path in archived.rglob('*') if path.is_file()}
            exported = json.loads((root / 'restored/combined-historical-records.json').read_bytes())
            _require(not any(row['id'] == 'deployment:cutover:v2' for row in exported['records']), 'ready_record_in_historical_manifest')
            return {'exported': exported, 'evidence': json.loads(members[EVIDENCE_MEMBER]), 'members': members,
                    'archive': receipt['archive'], 'archive_sha256': _sha(archive.read_bytes())}
    except ActivationBlocked:
        raise
    except Exception:
        raise ActivationBlocked('authenticated_boundary_unavailable') from None


def _run_id(url):
    match = re.fullmatch(r'https://github\.com/narendranathe/apply/actions/runs/([0-9]+)', str(url))
    _require(match is not None, 'github_evidence_url_invalid')
    return match.group(1)


def validate_shadow_summary(summary, *, minimum_contacts=0):
    _require(isinstance(summary, dict) and summary.get('dry_run') is True and summary.get('cloud_hosted') is True
             and summary.get('persistent') is False and summary.get('mode') in {'dry-run', 'research-only'}
             and summary.get('timezone') == 'America/Chicago' and summary.get('linkedin_sent') == 0, 'hosted_shadow_summary_invalid')
    results = summary.get('results')
    _require(isinstance(results, list), 'hosted_shadow_summary_invalid')
    actual = [row for row in results if isinstance(row, dict) and row.get('status') not in
              {'missed_research_only', 'already_completed', 'needs_reconciliation'}]
    _require(Counter(row.get('lane') for row in actual) == {'accepted': 1, 'first_touch': 1, 'applications': 1},
             'hosted_shadow_lanes_incomplete')
    for row in actual:
        if summary['mode'] == 'research-only':
            _require(row.get('persistent') is False and row.get('external_effects') == 0,
                     'hosted_shadow_effects_not_zero')
        if row['lane'] == 'applications':
            _require(row.get('dry_run') is True and row.get('feed_checked') is True
                     and row.get('submission_attempts') == 0 and row.get('applied_verified') == 0
                     and row.get('submission_unknown') == 0,
                     'hosted_shadow_effects_not_zero')
        else:
            _require(row.get('sent') == 0 and row.get('inbox') == 'not checked'
                     and all(isinstance(row.get(key), int) and not isinstance(row[key], bool)
                     and row[key] >= 0 for key in ('contacts_available', 'contacts_reviewed', 'drafts', 'research')),
                     'hosted_shadow_effects_not_zero')
            _require(row['contacts_available'] >= minimum_contacts
                     and min(1, minimum_contacts) <= row['contacts_reviewed'] <= row['contacts_available'],
                     'hosted_shadow_contacts_unpopulated')
            if row['lane'] == 'first_touch':
                discovery = row.get('discovery')
                _require(isinstance(discovery, dict) and isinstance(discovery.get('exceptions'), dict)
                     and all(isinstance(discovery.get(key), int) and not isinstance(discovery[key], bool) and discovery[key] >= 0
                             for key in ('roles_checked', 'official_open', 'research_tasks', 'duplicates')),
                     'hosted_discovery_evidence_missing')


def read_shadow_zip(data, *, minimum_contacts=0):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            _require(len(names) == len(set(names)) and all(safe_name(name) for name in names), 'shadow_artifact_invalid')
            matches = [item for item in archive.infolist() if Path(item.filename).name == 'summary.json']
            _require(len(matches) == 1 and matches[0].file_size <= 2_000_000, 'shadow_artifact_invalid')
            summary = json.loads(archive.read(matches[0]))
        validate_shadow_summary(summary, minimum_contacts=minimum_contacts)
        return summary
    except ActivationBlocked:
        raise
    except Exception:
        raise ActivationBlocked('shadow_artifact_invalid') from None


def activate(store, bundle, github, context, *, now=None, budget=None):
    """Append at most one ready row, after independent checks; never enable it."""
    now = now or datetime.now(timezone.utc)
    _require(context.get('actions') is True and context.get('ref') == 'refs/heads/main'
             and context.get('repository') == REPOSITORY and context.get('actor') == 'narendranathe'
             and context.get('triggering_actor', context.get('actor')) == 'narendranathe'
             and context.get('event') == 'workflow_dispatch' and re.fullmatch('[0-9a-f]{40}', context.get('commit', ''))
             and re.fullmatch('[0-9]+', str(context.get('run_id', ''))),
             'activation_requires_owner_default_branch_hosted_writer')
    _require(context.get('cloud_enabled') in {'false', 'true'}, 'cloud_enabled_state_unknown')
    exported, boundary = bundle['exported'], bundle['evidence']
    facts = validate_boundary_members(boundary, bundle['members'], exported, now=now)
    _require(str(getattr(store, 'data_source_id', '')).replace('-', '').lower()
             == str(facts['data_source_id']).replace('-', '').lower(), 'activation_target_source_mismatch')
    github.verify_main(context['commit'])
    existing = store.get('deployment:cutover:v2')
    contact_count = sum(row['kind'] == 'contacts' for row in exported['records'])
    if existing:
        try:
            validate_cutover_record(existing)
        except (ValueError, TypeError, KeyError):
            raise ActivationBlocked('existing_cutover_record_invalid') from None
        history_url = existing['payload']['history']['evidence_url']
        github.verify_activation_run(_run_id(history_url), context['commit'])
        shadow = github.shadow(context['commit'], run_id=_run_id(existing['payload']['shadow']['evidence_url']), minimum_contacts=contact_count)
    else:
        _require(context['cloud_enabled'] == 'false', 'disable_cloud_before_cutover_record')
        github.verify_no_failed_activation(context['run_id'])
        history_url = f'https://github.com/{REPOSITORY}/actions/runs/{context["run_id"]}'
        shadow = github.shadow(context['commit'], minimum_contacts=contact_count)
    readback = validate_import(store, exported)
    _require(readback['records'] == len(exported['records']) == readback['unchanged']
             and readback['conflicted'] == readback['blocked'] == 0, 'history_readback_incomplete')
    manifest_hash = content_hash(exported)
    archive_url = f'https://github.com/{REPOSITORY}/blob/{context["commit"]}/{bundle["archive"]}'
    actors = [{**row, 'evidence_url': archive_url} for row in facts['actors']]
    evidence = {'history_reconciled': True, 'backup_restore_verified': True, 'local_actors_retired': True, 'shadow_verified': True,
        'commit': context['commit'], 'observed_at': facts['observed_at'], 'historical_manifest_sha256': manifest_hash,
        'evidence_urls': [history_url, archive_url, shadow['evidence_url']],
        'history': {'historical_manifest_sha256': manifest_hash, 'evidence_url': history_url, 'readback': readback},
        'archive': {'restore_verified': True, 'hashes_verified': True, 'archive_sha256': bundle['archive_sha256'],
                    'raw_snapshot_sha256': facts['raw_snapshot_sha256'], 'historical_manifest_sha256': manifest_hash, 'evidence_url': archive_url},
        'local_actors': {'readbacks': actors, 'worker_readbacks_sha256': facts['worker_readbacks_sha256'],
            'application_writer': {'actor': 'Milo', 'stopped': True, 'source': 'direct_user',
                'confirmation_sha256': facts['milo_confirmation_sha256'], 'evidence_url': archive_url}},
        'coordination': {'claims_snapshot_sha256': facts['claims_snapshot_sha256'],
                         'quarantine_record_ids': facts['quarantine_record_ids'], 'classes': facts['quarantine_classes']},
        'boundary_evidence_sha256': _sha(bundle['members'][EVIDENCE_MEMBER]),
        'shadow': shadow}
    ready = make_cutover_record(evidence)
    if budget:
        budget.check_request()
    github.verify_main(context['commit'])  # The branch may have moved during full readback.
    core = ('id', 'kind', 'payload', 'status', 'hash')
    if existing:
        _require(all(existing.get(key) == ready[key] for key in core), 'existing_cutover_evidence_conflict')
        return {'operation': 'cutover_ready_record', 'verified': True, 'unchanged': True,
                'ready_record_sha256': ready['hash'], 'cloud_enabled_changed': False, 'external_effects': 0}
    if budget:
        budget.require_effect_capacity()
    from .notion import AmbiguousWriteError
    written = store.put(ready)
    observed = store.get(ready['id'])
    if not written or not observed or any(written.get(k) != ready[k] or observed.get(k) != ready[k] for k in core):
        raise AmbiguousWriteError('Cutover ready record readback uncertain; stop for reconciliation')
    return {'operation': 'cutover_ready_record', 'verified': True, 'unchanged': False,
            'ready_record_sha256': ready['hash'], 'cloud_enabled_changed': False, 'external_effects': 0}
