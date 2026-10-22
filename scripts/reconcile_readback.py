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


# Only fixed deployment gate names may enter the downloadable failure artifact.
# Unrecognized/new exception text falls back to a non-sensitive classification.
SAFE_BLOCK_REASONS = frozenset({
    'owner_disabled_hosted_maintenance_required', 'readback_snapshot_review_pending',
    'partial_resolution_batch_requires_reconciliation', 'existing_readback_resolution_conflict',
    'fresh_source_changed_since_snapshot', 'fresh_source_changed_after_containment',
    'fresh_source_identity_or_time_mismatch', 'containment_cohort_missing_before_clearance',
    'incident_evidence_changed_before_append', 'reviewed_containment_proof_changed',
    'reviewed_readback_delta_proof_changed', 'source_snapshot_authentication_failed',
    'source_snapshot_ciphertext_mismatch', 'source_snapshot_content_mismatch',
    'source_delta_not_exact_reviewed_records', 'prior_source_page_changed',
    'prior_source_core_changed', 'ready_record_changed', 'baseline_readback_changed',
    'native_full_source_changed', 'native_fresh_post_stop_snapshot_required',
    'native_containment_evidence_invalid', 'new_attempt_receipt_or_outreach_effect',
})


def _failure_summary(error):
    failure = {'status': 'blocked', 'error_type': 'UnexpectedError',
               'operation': 'pinned_index_readback_reconciliation'}
    if isinstance(error, ReconciliationBlocked):
        failure['error_type'] = 'ReconciliationBlocked'
        reason = error.args[0] if len(error.args) == 1 else None
        failure['reason'] = reason if isinstance(reason, str) and reason in SAFE_BLOCK_REASONS else 'reconciliation_requirements_not_verified'
    # Call the sanitizing base implementations, never a custom exception's
    # diagnostic override. No upstream text, body, header or record is retained.
    for kind in (NotionAPIError, AmbiguousWriteError, ConflictError):
        if isinstance(error, kind):
            failure['error_type'] = kind.__name__
            try:
                failure['notion'] = kind.diagnostics(error)
            except Exception:
                failure['notion'] = {'http_status': None, 'error_code': 'unknown',
                                     'operation': 'notion_request'}
            break
    return failure


def _persist_failure_summary(failure):
    destination = Path('run-output/maintenance-summary.json')
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(destination, flags, 0o600), 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(failure, sort_keys=True) + '\n')


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
        failure = _failure_summary(error)
        try:
            _persist_failure_summary(failure)
        except OSError:
            pass  # Preserve exit 2 even when the runner cannot persist an artifact.
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
