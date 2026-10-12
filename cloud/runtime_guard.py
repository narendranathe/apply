"""Durable GitHub failure witness for writes whose Notion marker is uncertain."""
import json
import os
from urllib.request import Request, urlopen


def unresolved_writers(runs, current_id, resolved):
    return [str(run['id']) for run in runs
            if str(run['id']) != str(current_id)
            and run.get('display_title', '').startswith('cloud:live:')
            and run.get('conclusion') in ('failure', 'cancelled', 'timed_out', 'action_required')
            and not resolved(str(run['id']))]


def verify_previous_writers(store, transport=None):
    from cloud.orchestrator import Blocked
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    token = os.environ.get('CLOUD_GITHUB_TOKEN', '')
    current_id = os.environ.get('GITHUB_RUN_ID', '')
    if not repository or not token or not current_id:
        raise Blocked('github_hosted_writer_witness_required')
    def request(page):
        url = f'https://api.github.com/repos/{repository}/actions/workflows/cloud-operations.yml/runs?per_page=100&page={page}'
        req = Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'User-Agent': 'apply-cloud-writer'})
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    fetch = transport or request
    for page in range(1, 101):
        response = fetch(page)
        runs = response.get('workflow_runs')
        if not isinstance(runs, list):
            raise Blocked('writer_history_unavailable')
        def resolved(identity):
            marker = store.get('writer-resolution:' + identity)
            return bool(marker and marker.get('payload', {}).get('external_effects_reconciled') is True and marker.get('payload', {}).get('evidence'))
        failures = unresolved_writers(runs, current_id, resolved)
        if failures:
            from cloud.outreach import record
            identity = failures[0]
            store.put(record('writer-failure:' + identity, 'audit_events', {'github_run_id': identity, 'reason': 'failed_writer_requires_reconciliation', 'retry_external_effects': False}, 'needs_reconciliation'))
            raise Blocked('previous_failed_writer_requires_reconciliation')
        if len(runs) < 100:
            return
    raise Blocked('writer_history_pagination_budget_exhausted')
