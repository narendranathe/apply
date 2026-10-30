"""One writer, durable immutable run markers, safe shadow and missed-run recovery."""
import argparse
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud.outreach import record, run_outreach
from cloud.runtime_budget import RuntimeBudget
from cloud.shadow import Blocked, ShadowStore

ZONE = ZoneInfo('America/Chicago')
CRONS = {'0 6 * * *': 'accepted', '30 6 * * *': 'first_touch'}
MODES = ('dry-run', 'research-only', 'live')
RUNTIME_SECONDS = 10800
RECOVERY_RESERVE_SECONDS = 1800
# Static stage names only: never derive diagnostics from records, URLs or paths.
CLOUD_OPERATIONS = frozenset({
    'restore_authenticated_baseline', 'stage_application_suppression',
    'calculate_research_share', 'check_application_coverage', 'read_run_state',
    'stage_run_marker', 'run_accepted_outreach', 'run_first_touch_outreach',
    'discover_jobs', 'run_applications', 'report_run', 'publish_agent_index',
    'backup_research_artifact', 'backup_operational_records', 'write_run_summary',
    'consume_agent_queue', 'verify_agent_worker_context',
})
APPLICATION_FAILURE_CODES = frozenset({
    'ambiguous_application_revision', 'application_pacing_timezone_unverified',
    'application_attempt_date_unverified', 'application_changed_before_transition',
    'invalid_application_transition', 'application_transition_readback_failed',
    'application_audit_readback_failed', 'application_packet_readback_failed',
    'historical_application_readback_failed', 'application_claim_changed',
})


@contextmanager
def _cloud_stage(operation):
    """Annotate the original exception with one fixed stage; never retry a call."""
    if type(operation) is not str or operation not in CLOUD_OPERATIONS:
        raise ValueError('invalid_cloud_operation')
    try:
        yield
    except Exception as error:
        error._cloud_operation = operation
        raise


class IndexPreviewStore(ShadowStore):
    """Stage generated index records locally without per-child remote probes.

    Source queries and the previous manifest are still freshly read through the
    base store. This preview never claims that generated children were persisted
    or read back from Notion, and it is never used by an application lane.
    """
    def put(self, value):
        from cloud.agent_index import GENERATED_KINDS, SCHEMA
        if value['kind'] not in GENERATED_KINDS and not (value['kind'] == 'agent_tasks' and value['payload'].get('schema') == SCHEMA):
            raise Blocked('index_preview_generated_records_only')
        old = self.records.get(value['id'])
        if old and any(old.get(key) != value.get(key) for key in ('hash', 'kind', 'status', 'payload')):
            raise Blocked('shadow_identity_conflict')
        self.records[value['id']] = value
        return value


def _publish_agent_index(working, now, dry_run, budget, *, publication_clock=None):
    from cloud.index_publisher import publish_index
    budget.check_request()
    writer = IndexPreviewStore(working) if dry_run else working
    # A complete query is not proof that every historical import and final
    # source delta was reconciled. No operational authority is inferred here.
    export = getattr(working, '_reconciled_export', None) if not dry_run else None
    cutover = working.get('deployment:cutover:v2') if export is not None else None
    publication = publish_index(writer, now, source_complete=False,
                                publication_clock=publication_clock,
                                **({'reconciled_export': export, 'cutover_record': cutover} if export is not None else {}))
    if dry_run:
        # Preserve the preview in the caller's private staging, never in Notion.
        working.records.update(writer.records)
    return {'complete': publication['complete'], 'counts': publication['counts'],
            'record_count': publication['record_count'], 'persistent': not dry_run}


def _failure_summary(error):
    """Keep API and uncertain-write diagnostics within the same safe schema."""
    from cloud.notion import AmbiguousWriteError, NotionAPIError
    from cloud.applications import StateConflict
    from cloud.index_publisher import IndexPublicationError
    failure = {'status': 'blocked', 'error_type': type(error).__name__,
               'reason': str(error) if isinstance(error, Blocked) else 'inspect_private_reconciliation_records'}
    if isinstance(error, StateConflict) and len(error.args) == 1:
        code = error.args[0]
        if type(code) is str and code in APPLICATION_FAILURE_CODES:
            failure['reason'] = code
    operation = getattr(error, '_cloud_operation', None)
    if type(operation) is str and operation in CLOUD_OPERATIONS:
        failure['cloud_operation'] = operation
    if isinstance(error, (NotionAPIError, AmbiguousWriteError)):
        failure['notion'] = error.diagnostics()
    if isinstance(error, IndexPublicationError):
        diagnostics = IndexPublicationError.diagnostics(error)
        failure['reason'] = diagnostics['error_code']
        if 'notion' in diagnostics:
            failure['notion'] = diagnostics['notion']
    return failure


def selected_lanes(cron='', requested='all'):
    if cron:
        if cron not in CRONS:
            raise Blocked('unknown_originating_cron')
        return ['accepted'] if CRONS[cron] == 'accepted' else ['first_touch', 'applications']
    if requested == 'all':
        return ['accepted', 'first_touch', 'applications']
    if requested not in ('accepted', 'first_touch', 'applications'):
        raise Blocked('unknown_lane')
    return [requested]


def due_slots(now, lanes, lookback_days=2, reconcile=False):
    local = now.astimezone(ZONE)
    slots = []
    for age in range(lookback_days if reconcile else 0, -1, -1):
        day = local.date() - timedelta(days=age)
        for lane in lanes:
            target = datetime(day.year, day.month, day.day, 6, 0 if lane == 'accepted' else 30, tzinfo=ZONE)
            if not reconcile or target <= local:
                slots.append((day.isoformat(), lane, age > 0))
    return slots


def _reconciled_slot(store, identity, resolution):
    if not resolution:
        return False
    from cloud.scheduled_reconciliation import FAILED, SLOT, slot_resolution_matches
    schema = resolution.get('payload', {}).get('schema')
    if identity == SLOT or (isinstance(schema, str)
                            and schema.startswith('apply-pinned-scheduled-slot-resolution/')):
        return slot_resolution_matches(identity, resolution, store.get('writer-resolution:' + FAILED))
    payload = resolution.get('payload', {})
    return bool(payload.get('run_id') == identity
                and payload.get('external_effects_reconciled') is True and payload.get('evidence'))


def _live_config_gate(config):
    if config.get('destination_private') is not True:
        raise Blocked('private_repository_required')
    if config.get('cloud_enabled') is not True:
        raise Blocked('cutover_not_enabled')


def live_gate(store, config):
    _live_config_gate(config)
    cutover = store.get('deployment:cutover:v2')
    if not cutover:
        raise Blocked('cutover_evidence_required')
    from cloud.cutover import validate_cutover_record
    try:
        validate_cutover_record(cutover)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise Blocked('cutover_evidence_invalid') from None
    started = {x['id'].removesuffix(':started'): x for x in store.query(kind='agent_runs') if x['id'].endswith(':started')}
    for identity in started:
        resolution = store.get(identity + ':reconciled')
        cleared = _reconciled_slot(store, identity, resolution)
        if not store.get(identity + ':completed') and not cleared:
            store.put(record(identity + ':dead_letter', 'audit_events', {'run_id': identity, 'reason': 'unfinished_run_requires_reconciliation', 'retry_external_effects': False}, 'needs_reconciliation'))
            raise Blocked('unresolved_previous_writer_run')


def research_gate(config):
    if config.get('destination_private') is not True:
        raise Blocked('private_repository_required')
    if config.get('cloud_research_enabled') is not True:
        raise Blocked('cloud_research_not_enabled')


def _attach_budget(store, budget):
    """Bound the actual Notion transport before reads, retries or writes."""
    if hasattr(store, 'transport') and getattr(store, '_orchestrator_budget', None) is not budget:
        budget.attach(store)
        store._orchestrator_budget = budget


def _agent_worker_authority(store=None):
    """Use deployment-owned grants inside the reserved hosted orchestrator only.

    This is called after activation, baseline and previous-writer verification.
    Neither an intake envelope nor the cloud configuration supplies authority.
    """
    from cloud.agent_dispatcher import CONCURRENCY_GROUP
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_REPOSITORY') != 'narendranathe/apply'
            or os.environ.get('GITHUB_JOB') != 'orchestrate'
            or os.environ.get('GITHUB_EVENT_NAME') not in {'schedule', 'workflow_dispatch'}
            or os.environ.get('GITHUB_WORKFLOW_REF') !=
                'narendranathe/apply/.github/workflows/cloud-operations.yml@refs/heads/main'
            or not re.fullmatch('[0-9]+', os.environ.get('GITHUB_RUN_ID', ''))):
        raise Blocked('trusted_central_agent_worker_required')
    workflow = Path('.github/workflows/cloud-operations.yml').read_text()
    if not re.search(r'^concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n',
                     workflow, re.MULTILINE):
        raise Blocked('agent_worker_concurrency_contract_required')
    registry = json.loads(Path('config/agents.json').read_text())
    if (registry.get('schema') != 'apply-github-agent-registry/v1'
            or not isinstance(registry.get('actors'), dict)):
        raise Blocked('trusted_agent_registry_required')
    context = {'concurrency_group': CONCURRENCY_GROUP,
        'reservation_verified': True, 'orchestrator_verified': True,
        'run_id': os.environ['GITHUB_RUN_ID']}
    if store is not None:
        from cloud.runtime_guard import verify_previous_writers
        verify_previous_writers(store)
        context['previous_writer_reconciled'] = True
    return registry['actors'], context


def execute(store, config, now=None, cron='', requested='all', dry_run=True, reconcile=False,
            *, mode=None, budget=None, preview_sink=None,
            agent_registry=None, writer_context=None, publication_clock=None):
    mode = mode or ('dry-run' if dry_run else 'live')
    if mode not in MODES:
        raise Blocked('unknown_mode')
    research_only = mode == 'research-only'
    dry_run = mode != 'live'
    if research_only:
        research_gate(config)
    budget = budget or RuntimeBudget(RUNTIME_SECONDS, RECOVERY_RESERVE_SECONDS)
    _attach_budget(store, budget)
    budget.check_request()
    if now is None:
        now = datetime.now(timezone.utc)
        if publication_clock is None:
            # The publisher samples after its fresh source reads. Explicit
            # logical-time calls keep their deterministic replay behavior.
            publication_clock = lambda: datetime.now(timezone.utc)
    if not dry_run:
        # Check disabled/private settings without reads or writes first.
        _live_config_gate(config)
        # A partial technical admission is a storage ambiguity even when the
        # agent consumer is not configured. Fence every live business lane
        # before any run marker, application, outreach, or other append.
        try:
            from cloud.technical_admission import _check_admission_ledger
            snapshot = store.index_source_records() if hasattr(store, 'index_source_records') else store.query()
            if not isinstance(snapshot, list):
                raise ValueError('incomplete_source_snapshot')
            _check_admission_ledger(snapshot)
        except Exception:
            raise Blocked('technical_admission_requires_reconciliation') from None
        live_gate(store, config)
    working = ShadowStore(store) if dry_run else store
    if dry_run and config.get('_suppression_baseline') is not None:
        # Historical applications suppress preview preparation before import;
        # an archived observation never grants live execution authority.
        from cloud.records import validate_record
        with _cloud_stage('stage_application_suppression'):
            for row in config['_suppression_baseline']['records']:
                if row['kind'] == 'applications':
                    working.put(validate_record(row))
    baseline = config.get('_reconciled_export') if not dry_run else None
    if baseline is not None:
        working._reconciled_export = baseline

    def require_effect_capacity():
        budget.require_effect_capacity()
        if baseline is not None:
            from cloud.baseline_guard import verify_application_coverage
            with _cloud_stage('check_application_coverage'):
                verify_application_coverage(working, baseline)
        budget.require_effect_capacity()  # Fresh coverage reads can consume the reserve.
    from cloud.reporting import effective_research_share
    config = {**config, 'outreach': dict(config.get('outreach', {}))}
    with _cloud_stage('calculate_research_share'):
        config['outreach']['research_ai_share'] = effective_research_share(working, config, now)
    lanes = selected_lanes(cron, requested)
    # Every production wake checks all due lanes: GitHub can replace a pending run.
    if not dry_run:
        if cron or requested == 'all':
            lanes = ['accepted', 'first_touch', 'applications']
        reconcile = True
    if baseline is not None and 'applications' in lanes:
        from cloud.baseline_guard import verify_application_coverage
        with _cloud_stage('check_application_coverage'):
            verify_application_coverage(working, baseline)  # Before any preparation.
    worker = {'status': 'skipped_nonpersistent' if dry_run else 'not_configured',
              'external_effects': 0}
    if not dry_run and (agent_registry is not None or writer_context is not None):
        from cloud.agent_worker import run_agent_worker
        with _cloud_stage('consume_agent_queue'):
            require_effect_capacity()
            worker = run_agent_worker(working, config, trusted_registry=agent_registry,
                writer_context=writer_context, now=now, effect_guard=require_effect_capacity)
    results = []
    for day, lane, missed in due_slots(now, lanes, config.get('reconcile_lookback_days', 2), reconcile):
        budget.check_request()
        run_id = f'{"research:" if research_only else ""}run:{day}:{lane}'
        with _cloud_stage('read_run_state'):
            completed = working.get(run_id + ':completed')
        if completed:
            results.append({'lane': lane, 'date': day, 'status': 'already_completed'})
            continue
        with _cloud_stage('read_run_state'):
            started = working.get(run_id + ':started')
        if started:
            resolution = working.get(run_id + ':reconciled')
            if _reconciled_slot(working, run_id, resolution):
                results.append({'lane': lane, 'date': day, 'status': resolution['status'],
                                'external_effects': 0, 'retry_external_effects': False})
                continue
            # A crashed run could have external effects. Do not recycle its lease.
            payload = {'run_id': run_id, 'reason': 'unfinished_run_requires_reconciliation', 'retry_external_effects': False}
            with _cloud_stage('stage_run_marker'):
                working.put(record(run_id + ':dead_letter', 'audit_events', payload, 'needs_reconciliation'))
            results.append({'lane': lane, 'date': day, 'status': 'needs_reconciliation'})
            continue
        marker = {'date': day, 'lane': lane, 'actor': 'github-actions', 'dry_run': dry_run,
                  'mode': mode, 'persistent': not dry_run}
        with _cloud_stage('stage_run_marker'):
            working.put(record(run_id + ':started', 'agent_runs', marker, 'research_started' if research_only else 'started'))
        # Historical gaps create durable skipped slots; never replay old applications/outreach.
        if missed:
            summary = {'lane': lane, 'date': day, 'status': 'missed_research_only', 'external_effects': 0}
        elif lane == 'applications':
            from cloud.applications import run_applications
            with _cloud_stage('run_applications'):
                summary = run_applications(working, config, dry_run=dry_run,
                                           effect_guard=require_effect_capacity if not dry_run else None,
                                           **({'preview_packets': True} if research_only else {}),
                                           **({'provider_budget_guard': budget.require_effect_capacity} if not dry_run else {}))
        else:
            with _cloud_stage('run_accepted_outreach' if lane == 'accepted' else 'run_first_touch_outreach'):
                summary = run_outreach(working, config.get('outreach', {}), lane, now.astimezone(ZONE), dry_run)
            if lane == 'first_touch':
                from cloud.discovery import run_discovery
                with _cloud_stage('discover_jobs'):
                    summary['discovery'] = run_discovery(working, config, now)
        if research_only:
            summary = {**summary, 'mode': mode, 'persistent': False, 'external_effects': 0}
        with _cloud_stage('stage_run_marker'):
            working.put(record(run_id + ':completed', 'agent_runs', {**marker, 'summary': summary},
                               'research_completed' if research_only else 'completed'))
        results.append(summary)
    from cloud.reporting import run_reporting
    budget.check_request()
    with _cloud_stage('report_run'):
        reporting = run_reporting(working, config, now, dry_run=dry_run)
    with _cloud_stage('publish_agent_index'):
        index = _publish_agent_index(working, now, dry_run, budget,
                                     publication_clock=publication_clock)
    if dry_run and preview_sink is not None:
        preview_sink(list(working.records.values()))
    return {'mode': mode, 'persistent': not dry_run, 'dry_run': dry_run, 'timezone': str(ZONE), 'results': results,
            'reporting': {**reporting, 'persistent': not dry_run},
            'agent_index': index,
            'agent_worker': worker,
            'runtime_budget': {'seconds': RUNTIME_SECONDS, 'receipt_backup_reserve_seconds': RECOVERY_RESERVE_SECONDS},
            'staged_record_counts': {kind: sum(row['kind'] == kind for row in working.records.values())
                                     for kind in sorted({row['kind'] for row in working.records.values()})} if dry_run else {},
            'cloud_hosted': os.environ.get('GITHUB_ACTIONS') == 'true',
            'linkedin_inbox': 'not checked', 'linkedin_sent': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config/cloud.json')
    parser.add_argument('--cron', default='')
    parser.add_argument('--lane', default='all')
    parser.add_argument('--mode', choices=MODES, default='dry-run')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('--output', default='run-output/summary.json')
    args = parser.parse_args()
    if args.mode == 'live' and (os.environ.get('GITHUB_ACTIONS') != 'true'
                                or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
        raise Blocked('live_requires_default_branch_hosted_writer')
    config = json.loads(Path(args.config).read_text())
    config['destination_private'] = os.environ.get('DESTINATION_PRIVATE') == 'true'
    config['cloud_enabled'] = os.environ.get('CLOUD_ENABLED') == 'true'
    config['cloud_research_enabled'] = os.environ.get('CLOUD_RESEARCH_ENABLED') == 'true'
    if args.mode == 'research-only':
        research_gate(config)
    token = os.environ.get('NOTION_TOKEN')
    if not token:
        raise Blocked('NOTION_TOKEN_required_for_live_shadow_validation')
    from cloud.notion import NotionStore
    store = NotionStore(token=token, data_source_id=config['notion_data_source_id'])
    budget = RuntimeBudget(RUNTIME_SECONDS, RECOVERY_RESERVE_SECONDS)
    _attach_budget(store, budget)
    if args.mode != 'live':
        from cloud.baseline import restore_baseline
        with _cloud_stage('restore_authenticated_baseline'):
            config['_suppression_baseline'], _ = restore_baseline()
    agent_registry, writer_context = None, None
    if args.mode == 'live':
        from cloud.backup import validate_key
        validate_key()
        # Validate activation before a failure witness may append an audit row.
        live_gate(store, config)
        from cloud.baseline import restore_baseline
        with _cloud_stage('restore_authenticated_baseline'):
            baseline, manifest_hash = restore_baseline()
        cutover = store.get('deployment:cutover:v2')
        if not cutover or cutover['payload'].get('historical_manifest_sha256') != manifest_hash:
            raise Blocked('authenticated_baseline_differs_from_cutover')
        config['_reconciled_export'] = baseline
        with _cloud_stage('verify_agent_worker_context'):
            agent_registry, writer_context = _agent_worker_authority(store)
    preview = []
    result = execute(store, config, cron=args.cron, requested=args.lane, mode=args.mode,
                     reconcile=args.reconcile, budget=budget,
                     agent_registry=agent_registry, writer_context=writer_context,
                     preview_sink=preview.extend if args.mode == 'research-only' else None)
    if args.mode == 'research-only':
        from cloud.backup import backup_store
        staged = ShadowStore()
        staged.records = {row['id']: row for row in preview}
        with _cloud_stage('backup_research_artifact'):
            result['research_artifact'] = backup_store(staged)
        result['research_artifact']['operational_source_of_truth'] = False
    if args.mode == 'live':
        from cloud.backup import backup_store
        with _cloud_stage('backup_operational_records'):
            result['backup'] = backup_store(store)
    output = Path(args.output)
    with _cloud_stage('write_run_summary'):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never serialize upstream messages, bodies, headers, or request values.
        print(json.dumps(_failure_summary(error)))
        raise SystemExit(1)
