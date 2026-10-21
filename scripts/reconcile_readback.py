#!/usr/bin/env python3
"""Hosted reconciliation of the pinned October 21 readback incident only."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.backup import backup_store, validate_key
from cloud.baseline import restore_baseline
from cloud.readback_reconciliation import ReconciliationBlocked, reconcile, require, require_reviewed_pins
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError, NotionStore
from cloud.runtime_budget import RuntimeBudget
from scripts.reconcile_startup import StartupGitHubEvidence


def main():
    context = {'actions': os.environ.get('GITHUB_ACTIONS') == 'true', 'ref': os.environ.get('GITHUB_REF'),
        'repository': os.environ.get('GITHUB_REPOSITORY'), 'actor': os.environ.get('GITHUB_ACTOR'),
        'triggering_actor': os.environ.get('GITHUB_TRIGGERING_ACTOR'), 'event': os.environ.get('GITHUB_EVENT_NAME'),
        'commit': os.environ.get('GITHUB_SHA'), 'run_id': os.environ.get('GITHUB_RUN_ID'),
        'cloud_enabled': os.environ.get('CLOUD_ENABLED')}
    require(context['actions'] and context['ref'] == 'refs/heads/main'
            and context['repository'] == 'narendranathe/apply' and context['actor'] == 'narendranathe'
            and context['triggering_actor'] == 'narendranathe' and context['event'] == 'workflow_dispatch'
            and context['cloud_enabled'] == 'false', 'owner_disabled_hosted_maintenance_required')
    require_reviewed_pins()
    key = validate_key()
    exported, _ = restore_baseline()
    config = json.loads(Path('config/cloud.json').read_text())
    budget = RuntimeBudget()
    store = budget.attach(NotionStore(token=os.environ['NOTION_TOKEN'], data_source_id=config['notion_data_source_id']))
    github = StartupGitHubEvidence(os.environ['GITHUB_TOKEN'], budget=budget)
    result = reconcile(store, exported, github, context, key)
    result['backup'] = backup_store(store)
    Path('run-output').mkdir(exist_ok=True)
    Path('run-output/maintenance-summary.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = {'status': 'blocked', 'error_type': type(error).__name__, 'operation': 'pinned_index_readback_reconciliation'}
        if isinstance(error, ReconciliationBlocked):
            failure['reason'] = str(error)
        if isinstance(error, (NotionAPIError, AmbiguousWriteError, ConflictError)):
            failure['notion'] = error.diagnostics()
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
