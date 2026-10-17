"""Durable GitHub failure witness for writes whose Notion marker is uncertain."""
import json
import os
import re
from urllib.request import Request, urlopen

ZERO_JOB_SCHEMA = 'apply-zero-job-writer-resolution/v1'
CORE = ('id', 'kind', 'payload', 'status', 'hash')


def _zero_job_cancellation(identity, listed, fetch, repository):
    """Return only independent, complete GitHub proof of no allocated job.

    ``filter=all`` includes earlier attempts of a rerun. A cancelled run with
    even a queued job remains uncertain. Re-read run metadata after jobs so a
    concurrent rerun cannot silently change the proof's attempt or state.
    """
    if not re.fullmatch('[0-9]+', identity) or listed.get('conclusion') != 'cancelled':
        return None
    run_path = '/actions/runs/' + identity
    jobs_path = run_path + '/jobs?filter=all&per_page=100&page=1'
    try:
        actual = fetch(run_path)
        if not isinstance(actual, dict):
            return None
        fields = ('id', 'display_title', 'status', 'conclusion', 'head_branch', 'head_sha',
                  'event', 'path', 'run_attempt', 'updated_at')
        proof = {name: actual.get(name) for name in fields}
        attempt = proof['run_attempt']
        if not (isinstance(proof['id'], int) and not isinstance(proof['id'], bool)
                and str(proof['id']) == identity
                and proof['status'] == 'completed' and proof['conclusion'] == 'cancelled'
                and proof['display_title'] == listed.get('display_title')
                and isinstance(proof['display_title'], str) and proof['display_title'].startswith('cloud:live:')
                and proof['head_branch'] == 'main'
                and isinstance(proof['head_sha'], str) and re.fullmatch('[0-9a-f]{40}', proof['head_sha'])
                and proof['event'] in {'schedule', 'workflow_dispatch'}
                and isinstance(proof['path'], str)
                and proof['path'].partition('@')[0] == '.github/workflows/cloud-operations.yml'
                and isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1
                and isinstance(proof['updated_at'], str) and bool(proof['updated_at'])):
            return None
        jobs = fetch(jobs_path)
        if not (isinstance(jobs, dict) and type(jobs.get('total_count')) is int
                and jobs['total_count'] == 0 and jobs.get('jobs') == []
                and not any(jobs.get(field) for field in
                            ('_next_page', 'has_more', 'next_cursor', 'incomplete_results'))):
            return None
        after = fetch(run_path)
        if not isinstance(after, dict) or any(after.get(name) != value for name, value in proof.items()):
            return None
    except Exception:
        # Unavailable or partial metadata never establishes a no-effect result.
        return None
    return {'schema': ZERO_JOB_SCHEMA, 'github_run_id': identity,
            'reason': 'cancelled_without_allocated_job', 'external_effects_reconciled': True,
            'retry_external_effects': False,
            'evidence': {'run_url': 'https://github.com/' + repository + '/actions/runs/' + identity,
                         'jobs_api_url': 'https://api.github.com/repos/' + repository + jobs_path,
                         'run': proof, 'jobs': {'filter': 'all', 'per_page': 100,
                                             'pages_checked': [1], 'total_count': 0, 'observed_jobs': 0}}}


def _persist_zero_job_resolution(store, identity, proof):
    """Append once, requiring exact immutable write and fresh readback."""
    from cloud.notion import AmbiguousWriteError, ConflictError
    from cloud.outreach import record
    expected = record('writer-resolution:' + identity, 'audit_events', proof, 'reconciled_no_job')
    previous = store.get(expected['id'])
    if previous is not None:
        if any(previous.get(key) != expected[key] for key in CORE):
            raise ConflictError('Writer resolution evidence changed; reconcile before continuing')
        return
    written = store.put(expected)
    observed = store.get(expected['id'])
    if (not isinstance(written, dict) or not isinstance(observed, dict)
            or any(written.get(key) != expected[key] or observed.get(key) != expected[key] for key in CORE)):
        raise AmbiguousWriteError('Writer resolution readback uncertain; stop for reconciliation')


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
    def request_path(path):
        url = f'https://api.github.com/repos/{repository}' + path
        req = Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'User-Agent': 'apply-cloud-writer'})
        with urlopen(req, timeout=30) as response:
            value = json.load(response)
            # The API count must agree with pagination. Do not retain/log URLs.
            if isinstance(value, dict) and 'rel="next"' in getattr(response, 'headers', {}).get('Link', ''):
                value['_next_page'] = True
            return value
    fetch = transport or (lambda page: request_path('/actions/workflows/cloud-operations.yml/runs?per_page=100&page=' + str(page)))
    for page in range(1, 101):
        response = fetch(page)
        runs = response.get('workflow_runs')
        if not isinstance(runs, list):
            raise Blocked('writer_history_unavailable')
        def resolved(identity):
            marker = store.get('writer-resolution:' + identity)
            payload = marker.get('payload', {}) if marker else {}
            # A later rerun can allocate jobs under the same GitHub run ID.
            # Automatic zero-job evidence must therefore be rechecked below.
            return bool(payload.get('schema') != ZERO_JOB_SCHEMA
                        and payload.get('external_effects_reconciled') is True and payload.get('evidence'))
        failures = unresolved_writers(runs, current_id, resolved)
        for identity in failures:
            listed = next(row for row in runs if str(row.get('id')) == identity)
            proof = _zero_job_cancellation(identity, listed, request_path, repository)
            if proof is not None:
                _persist_zero_job_resolution(store, identity, proof)
                continue
            from cloud.outreach import record
            store.put(record('writer-failure:' + identity, 'audit_events', {'github_run_id': identity, 'reason': 'failed_writer_requires_reconciliation', 'retry_external_effects': False}, 'needs_reconciliation'))
            raise Blocked('previous_failed_writer_requires_reconciliation')
        if len(runs) < 100:
            return
    raise Blocked('writer_history_pagination_budget_exhausted')
