"""One writer, durable immutable run markers, safe shadow and missed-run recovery."""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from cloud.outreach import record, run_outreach

ZONE = ZoneInfo('America/Chicago')
CRONS = {'0 6 * * *': 'accepted', '30 6 * * *': 'first_touch'}


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
    if not config.get('destination_private'):
        raise Blocked('private_repository_required')
    if not config.get('cloud_enabled'):
        raise Blocked('cutover_not_enabled')
    cutover = store.get('deployment:cutover:v2')
    required = ('history_reconciled', 'backup_restore_verified', 'local_actors_retired', 'shadow_verified')
    if not cutover or not all(cutover.get('payload', {}).get(x) is True for x in required):
        raise Blocked('cutover_evidence_required')
    started = {x['id'].removesuffix(':started'): x for x in store.query(kind='agent_runs') if x['id'].endswith(':started')}
    for identity in started:
        resolution = store.get(identity + ':reconciled')
        cleared = bool(resolution and resolution.get('payload', {}).get('run_id') == identity
                       and resolution.get('payload', {}).get('external_effects_reconciled') is True
                       and resolution.get('payload', {}).get('evidence'))
        if not store.get(identity + ':completed') and not cleared:
            store.put(record(identity + ':dead_letter', 'audit_events', {'run_id': identity, 'reason': 'unfinished_run_requires_reconciliation', 'retry_external_effects': False}, 'needs_reconciliation'))
            raise Blocked('unresolved_previous_writer_run')


def execute(store, config, now=None, cron='', requested='all', dry_run=True, reconcile=False):
    now = now or datetime.now(timezone.utc)
    if not dry_run:
        live_gate(store, config)
    working = ShadowStore(store) if dry_run else store
    lanes = selected_lanes(cron, requested)
    # Every production wake checks all due lanes: GitHub can replace a pending run.
    if not dry_run:
        if cron or requested == 'all':
            lanes = ['accepted', 'first_touch', 'applications']
        reconcile = True
    results = []
    for day, lane, missed in due_slots(now, lanes, config.get('reconcile_lookback_days', 2), reconcile):
        run_id = f'run:{day}:{lane}'
        if working.get(run_id + ':completed'):
            results.append({'lane': lane, 'date': day, 'status': 'already_completed'})
            continue
        if working.get(run_id + ':started'):
            # A crashed run could have external effects. Do not recycle its lease.
            payload = {'run_id': run_id, 'reason': 'unfinished_run_requires_reconciliation', 'retry_external_effects': False}
            working.put(record(run_id + ':dead_letter', 'audit_events', payload, 'needs_reconciliation'))
            results.append({'lane': lane, 'date': day, 'status': 'needs_reconciliation'})
            continue
        working.put(record(run_id + ':started', 'agent_runs', {'date': day, 'lane': lane, 'actor': 'github-actions', 'dry_run': dry_run}, 'started'))
        # Historical gaps create durable skipped slots; never replay old applications/outreach.
        if missed:
            summary = {'lane': lane, 'date': day, 'status': 'missed_research_only', 'external_effects': 0}
        elif lane == 'applications':
            from cloud.applications import run_applications
            summary = run_applications(working, config, dry_run=dry_run)
        else:
            summary = run_outreach(working, config.get('outreach', {}), lane, now.astimezone(ZONE), dry_run)
            if lane == 'first_touch':
                from cloud.discovery import run_discovery
                summary['discovery'] = run_discovery(working, config, now)
        working.put(record(run_id + ':completed', 'agent_runs', {'date': day, 'lane': lane, 'summary': summary}, 'completed'))
        results.append(summary)
    return {'dry_run': dry_run, 'timezone': str(ZONE), 'results': results, 'cloud_hosted': os.environ.get('GITHUB_ACTIONS') == 'true',
            'linkedin_inbox': 'not checked', 'linkedin_sent': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='config/cloud.json')
    parser.add_argument('--cron', default='')
    parser.add_argument('--lane', default='all')
    parser.add_argument('--mode', choices=('dry-run', 'live'), default='dry-run')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('--output', default='run-output/summary.json')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    config['destination_private'] = os.environ.get('DESTINATION_PRIVATE') == 'true'
    config['cloud_enabled'] = os.environ.get('CLOUD_ENABLED') == 'true'
    token = os.environ.get('NOTION_TOKEN')
    if not token:
        raise Blocked('NOTION_TOKEN_required_for_live_shadow_validation')
    from cloud.notion import NotionStore
    store = NotionStore(token=token, data_source_id=config['notion_data_source_id'])
    if args.mode == 'live':
        from cloud.runtime_guard import verify_previous_writers
        verify_previous_writers(store)
        from cloud.backup import validate_key
        validate_key()
    result = execute(store, config, cron=args.cron, requested=args.lane, dry_run=args.mode != 'live', reconcile=args.reconcile)
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
        # Upstream errors may contain URLs or payloads; emit class only to runner logs.
        print(json.dumps({'status': 'blocked', 'error_type': type(error).__name__, 'reason': str(error) if isinstance(error, Blocked) else 'inspect_private_reconciliation_records'}))
        raise SystemExit(1)
