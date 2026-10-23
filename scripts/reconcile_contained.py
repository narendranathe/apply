#!/usr/bin/env python3
"""Owner-only correction of one pinned evidence-storage incident, without replay."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.backup import backup_store, validate_key
from cloud.baseline import restore_baseline
from cloud.contained_recovery import SAFE_REASONS, reconcile, require_reviewed_pins
from cloud.notion import NotionStore
from cloud.runtime_budget import RuntimeBudget
from cloud.startup_reconciliation import ReconciliationBlocked, require
from scripts.reconcile_readback import _failure_summary, _persist_failure_summary
from scripts.reconcile_startup import StartupGitHubEvidence

OPERATION = 'pinned_contained_storage_reconciliation'


def main():
    context = {'actions': os.environ.get('GITHUB_ACTIONS') == 'true',
        'ref': os.environ.get('GITHUB_REF'), 'repository': os.environ.get('GITHUB_REPOSITORY'),
        'actor': os.environ.get('GITHUB_ACTOR'), 'triggering_actor': os.environ.get('GITHUB_TRIGGERING_ACTOR'),
        'event': os.environ.get('GITHUB_EVENT_NAME'), 'commit': os.environ.get('GITHUB_SHA'),
        'run_id': os.environ.get('GITHUB_RUN_ID'), 'cloud_enabled': os.environ.get('CLOUD_ENABLED'),
        'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')}
    require(context['actions'] and context['ref'] == 'refs/heads/main'
            and context['repository'] == 'narendranathe/apply'
            and context['actor'] == context['triggering_actor'] == 'narendranathe'
            and context['event'] == 'workflow_dispatch' and context['cloud_enabled'] == 'false'
            and context['run_attempt'] == '1', 'owner_disabled_hosted_maintenance_required')
    require_reviewed_pins()
    key = validate_key()
    exported, _ = restore_baseline()
    config = json.loads(Path('config/cloud.json').read_text())
    budget = RuntimeBudget()
    store = budget.attach(NotionStore(token=os.environ['NOTION_TOKEN'],
                                    data_source_id=config['notion_data_source_id']))
    github = StartupGitHubEvidence(os.environ['GITHUB_TOKEN'], budget=budget)
    result = reconcile(store, exported, github, context, key)
    result['backup'] = backup_store(store)
    destination = Path('run-output/maintenance-summary.json')
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    with os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                           | getattr(os, 'O_NOFOLLOW', 0), 0o600), 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = _failure_summary(error)
        failure['operation'] = OPERATION
        if isinstance(error, ReconciliationBlocked):
            reason = error.args[0] if len(error.args) == 1 else None
            if isinstance(reason, str) and reason in SAFE_REASONS:
                failure['reason'] = reason
        try:
            _persist_failure_summary(failure)
        except OSError:
            pass
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
