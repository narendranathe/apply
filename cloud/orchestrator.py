"""One writer, durable immutable run markers, safe shadow and missed-run recovery."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud.outreach import record, run_outreach
from cloud.runtime_budget import RuntimeBudget

ZONE = ZoneInfo('America/Chicago')
CRONS = {'0 6 * * *': 'accepted', '30 6 * * *': 'first_touch'}
MODES = ('dry-run', 'research-only', 'live')
RUNTIME_SECONDS = 4500
RECOVERY_RESERVE_SECONDS = 1200


class Blocked(RuntimeError):
    pass


class ShadowStore:
    """Reads live state, stages writes in memory; shadow cannot compete with locals."""
    def __init__(self, base=None):
        self.base = base
        self.records = {}

    def get(self, identity):
        return self.records.get(identity) or (self.base.get(identity) if self.base else None)

    def put(self, value):
        old = self.get(value['id'])
        if old and any(old.get(k) != value.get(k) for k in ('hash', 'kind', 'status', 'payload')):
            raise Blocked('shadow_identity_conflict')
        self.records[value['id']] = value
        return value

    def query(self, kind=None):
        merged = {x['id']: x for x in (self.base.query(kind=kind) if self.base else [])}
        merged.update({k: v for k, v in self.records.items() if kind is None or v['kind'] == kind})
        return list(merged.values())

    def current_contact_observations(self):
        return self.base.current_contact_observations() if self.base and hasattr(self.base, 'current_contact_observations') else []

    def index_source_records(self):
        from cloud.agent_index import GENERATED_KINDS, SCHEMA
        if self.base and hasattr(self.base, 'index_source_records'):
            rows = self.base.index_source_records()
        else:
            rows = self.base.query() if self.base else []
        merged = {row['id']: row for row in rows}
        merged.update(self.records)
        return [row for row in merged.values() if row['kind'] not in GENERATED_KINDS
                and not (row['kind'] == 'agent_tasks' and row['payload'].get('schema') == SCHEMA)]

    def latest_index_manifest(self):
        staged = [row for row in self.records.values() if row['kind'] == 'index_manifests']
        if self.base and hasattr(self.base, 'latest_index_manifest'):
            previous = self.base.latest_index_manifest()
        else:
            previous = max((row for row in (self.base.query(kind='index_manifests') if self.base else [])),
                           key=lambda row: (row['payload'].get('generated_at', ''), row['id']), default=None)
        if previous is not None:
            staged.append(previous)
        return max(staged, key=lambda row: (row['payload'].get('generated_at', ''), row['id']), default=None)


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


def _publish_agent_index(working, now, dry_run, budget):
    from cloud.index_publisher import publish_index
    budget.check_request()
    writer = IndexPreviewStore(working) if dry_run else working
    # A complete query is not proof that every historical import and final
    # source delta was reconciled. No operational authority is inferred here.
    export = getattr(working, '_reconciled_export', None) if not dry_run else None
    cutover = working.get('deployment:cutover:v2') if export is not None else None
    publication = publish_index(writer, now, source_complete=False,
                                **({'reconciled_export': export, 'cutover_record': cutover} if export is not None else {}))
    if dry_run:
        # Preserve the preview in the caller's private staging, never in Notion.
        working.records.update(writer.records)
    return {'complete': publication['complete'], 'counts': publication['counts'],
            'record_count': publication['record_count'], 'persistent': not dry_run}


def _failure_summary(error):
    """Keep API and uncertain-write diagnostics within the same safe schema."""
    from cloud.notion import AmbiguousWriteError, NotionAPIError
    failure = {'status': 'blocked', 'error_type': type(error).__name__,
               'reason': str(error) if isinstance(error, Blocked) else 'inspect_private_reconciliation_records'}
    if isinstance(error, (NotionAPIError, AmbiguousWriteError)):
        failure['notion'] = error.diagnostics()
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


def live_gate(store, config):
    if config.get('destination_private') is not True:
        raise Blocked('private_repository_required')
    if config.get('cloud_enabled') is not True:
        raise Blocked('cutover_not_enabled')
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
        cleared = bool(resolution and resolution.get('payload', {}).get('run_id') == identity
                       and resolution.get('payload', {}).get('external_effects_reconciled') is True
                       and resolution.get('payload', {}).get('evidence'))
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


def execute(store, config, now=None, cron='', requested='all', dry_run=True, reconcile=False,
            *, mode=None, budget=None, preview_sink=None):
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
    now = now or datetime.now(timezone.utc)
    if not dry_run:
        live_gate(store, config)
    working = ShadowStore(store) if dry_run else store
    baseline = config.get('_reconciled_export') if not dry_run else None
    if baseline is not None:
        working._reconciled_export = baseline

    def require_effect_capacity():
        budget.require_effect_capacity()
        if baseline is not None:
            from cloud.baseline_guard import verify_application_coverage
            verify_application_coverage(working, baseline)
    from cloud.reporting import effective_research_share
    config = {**config, 'outreach': dict(config.get('outreach', {}))}
    config['outreach']['research_ai_share'] = effective_research_share(working, config, now)
    lanes = selected_lanes(cron, requested)
    # Every production wake checks all due lanes: GitHub can replace a pending run.
    if not dry_run:
        if cron or requested == 'all':
            lanes = ['accepted', 'first_touch', 'applications']
        reconcile = True
    if baseline is not None and 'applications' in lanes:
        from cloud.baseline_guard import verify_application_coverage
        verify_application_coverage(working, baseline)  # Before any preparation.
    results = []
    for day, lane, missed in due_slots(now, lanes, config.get('reconcile_lookback_days', 2), reconcile):
        budget.check_request()
        run_id = f'{"research:" if research_only else ""}run:{day}:{lane}'
        if working.get(run_id + ':completed'):
            results.append({'lane': lane, 'date': day, 'status': 'already_completed'})
            continue
        if working.get(run_id + ':started'):
            # A crashed run could have external effects. Do not recycle its lease.
            payload = {'run_id': run_id, 'reason': 'unfinished_run_requires_reconciliation', 'retry_external_effects': False}
            working.put(record(run_id + ':dead_letter', 'audit_events', payload, 'needs_reconciliation'))
            results.append({'lane': lane, 'date': day, 'status': 'needs_reconciliation'})
            continue
        marker = {'date': day, 'lane': lane, 'actor': 'github-actions', 'dry_run': dry_run,
                  'mode': mode, 'persistent': not dry_run}
        working.put(record(run_id + ':started', 'agent_runs', marker, 'research_started' if research_only else 'started'))
        # Historical gaps create durable skipped slots; never replay old applications/outreach.
        if missed:
            summary = {'lane': lane, 'date': day, 'status': 'missed_research_only', 'external_effects': 0}
        elif lane == 'applications':
            from cloud.applications import run_applications
            summary = run_applications(working, config, dry_run=dry_run,
                                       effect_guard=require_effect_capacity if not dry_run else None)
        else:
            summary = run_outreach(working, config.get('outreach', {}), lane, now.astimezone(ZONE), dry_run)
            if lane == 'first_touch':
                from cloud.discovery import run_discovery
                summary['discovery'] = run_discovery(working, config, now)
        if research_only:
            summary = {**summary, 'mode': mode, 'persistent': False, 'external_effects': 0}
        working.put(record(run_id + ':completed', 'agent_runs', {**marker, 'summary': summary},
                           'research_completed' if research_only else 'completed'))
        results.append(summary)
    from cloud.reporting import run_reporting
    budget.check_request()
    reporting = run_reporting(working, config, now, dry_run=dry_run)
    index = _publish_agent_index(working, now, dry_run, budget)
    if dry_run and preview_sink is not None:
        preview_sink(list(working.records.values()))
    return {'mode': mode, 'persistent': not dry_run, 'dry_run': dry_run, 'timezone': str(ZONE), 'results': results,
            'reporting': {**reporting, 'persistent': not dry_run},
            'agent_index': index,
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
    if args.mode == 'live':
        from cloud.backup import validate_key
        validate_key()
        # Validate activation before a failure witness may append an audit row.
        live_gate(store, config)
        from cloud.baseline import restore_baseline
        baseline, manifest_hash = restore_baseline()
        cutover = store.get('deployment:cutover:v2')
        if not cutover or cutover['payload'].get('historical_manifest_sha256') != manifest_hash:
            raise Blocked('authenticated_baseline_differs_from_cutover')
        config['_reconciled_export'] = baseline
        from cloud.runtime_guard import verify_previous_writers
        verify_previous_writers(store)
    preview = []
    result = execute(store, config, cron=args.cron, requested=args.lane, mode=args.mode,
                     reconcile=args.reconcile, budget=budget,
                     preview_sink=preview.extend if args.mode == 'research-only' else None)
    if args.mode == 'research-only':
        from cloud.backup import backup_store
        staged = ShadowStore()
        staged.records = {row['id']: row for row in preview}
        result['research_artifact'] = backup_store(staged)
        result['research_artifact']['operational_source_of_truth'] = False
    if args.mode == 'live':
        from cloud.backup import backup_store
        result['backup'] = backup_store(store)
    output = Path(args.output)
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
