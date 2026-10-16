#!/usr/bin/env python3
"""Validate authenticated cutover evidence and append only its ready record."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.activation import (ActivationBlocked, REPOSITORY, _require, activate, load_boundary_bundle, read_shadow_zip)
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError, NotionStore
from cloud.runtime_budget import RuntimeBudget


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHubEvidence:
    """Read-only GitHub verification. Bearer headers never follow redirects."""
    def __init__(self, token, *, fetch=None, download=None, budget=None):
        self.token, self.budget = token, budget
        self.fetch = fetch or self._json
        self.download = download or self._download

    def _request(self, path):
        if self.budget:
            self.budget.check_request()
        return Request('https://api.github.com/repos/' + REPOSITORY + path,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'User-Agent': 'apply-cutover-verifier'})

    def _json(self, path):
        try:
            with build_opener(NoRedirect()).open(self._request(path), timeout=30) as response:
                return json.loads(response.read(4_000_001))
        except Exception:
            raise ActivationBlocked('github_evidence_unavailable') from None

    def _download(self, artifact_id):
        try:
            request = self._request('/actions/artifacts/' + str(artifact_id) + '/zip')
            try:
                response = build_opener(NoRedirect()).open(request, timeout=30)
            except HTTPError as redirect:
                _require(redirect.code in {301, 302, 303, 307, 308}, 'shadow_artifact_download_failed')
                location = redirect.headers.get('Location', '')
                parsed = urlsplit(location)
                _require(parsed.scheme == 'https' and not parsed.username and not parsed.password
                    and parsed.port in {None, 443} and isinstance(parsed.hostname, str)
                    and parsed.hostname.endswith(('.blob.core.windows.net', '.actions.githubusercontent.com', '.githubusercontent.com')),
                    'shadow_artifact_redirect_invalid')
                # New unauthenticated request: never forward the GitHub token.
                response = build_opener(NoRedirect()).open(Request(location, headers={'User-Agent': 'apply-cutover-verifier'}), timeout=60)
            with response:
                data = response.read(64_000_001)
            _require(len(data) <= 64_000_000, 'shadow_artifact_too_large')
            return data
        except ActivationBlocked:
            raise
        except Exception:
            raise ActivationBlocked('shadow_artifact_download_failed') from None

    def verify_main(self, commit):
        repository = self.fetch('')
        reference = self.fetch('/git/ref/heads/main')
        _require(repository.get('private') is True and repository.get('full_name') == REPOSITORY
                 and repository.get('default_branch') == 'main' and reference.get('ref') == 'refs/heads/main'
                 and reference.get('object', {}).get('sha') == commit, 'deployed_private_main_changed')

    def _run(self, run_id, commit, workflow):
        run = self.fetch('/actions/runs/' + str(run_id))
        _require(run.get('head_sha') == commit and run.get('head_branch') == 'main'
                 and run.get('status') == 'completed' and run.get('conclusion') == 'success'
                 and run.get('event') == 'workflow_dispatch'
                 and str(run.get('path', '')).partition('@')[0] == '.github/workflows/' + workflow,
                 'hosted_run_not_successful_current_main')
        return run

    def verify_activation_run(self, run_id, commit):
        run = self._run(run_id, commit, 'history-reconciliation.yml')
        _require(run.get('display_title') == 'history:activate', 'original_activation_run_invalid')

    def verify_no_failed_activation(self, current_run_id):
        for page in range(1, 6):
            data = self.fetch('/actions/workflows/history-reconciliation.yml/runs?per_page=100&page=' + str(page))
            rows = data.get('workflow_runs')
            _require(isinstance(rows, list), 'activation_history_unavailable')
            _require(not any(str(row.get('id')) != str(current_run_id) and row.get('display_title') == 'history:activate'
                and row.get('conclusion') in {'failure', 'cancelled', 'timed_out', 'action_required'} for row in rows),
                'prior_uncertain_activation_requires_reconciliation')
            if len(rows) < 100:
                return
        raise ActivationBlocked('activation_history_pagination_incomplete')

    def shadow(self, commit, run_id=None, *, minimum_contacts=0):
        if run_id is None:
            for page in range(1, 6):
                data = self.fetch('/actions/workflows/cloud-operations.yml/runs?branch=main&per_page=100&page=' + str(page))
                rows = data.get('workflow_runs')
                _require(isinstance(rows, list), 'shadow_history_unavailable')
                matches = [row for row in rows if row.get('head_sha') == commit and row.get('conclusion') == 'success'
                           and row.get('display_title') in {'cloud:research-only:all', 'cloud:dry-run:all'}]
                if matches:
                    run_id = str(matches[0]['id'])
                    break
                if len(rows) < 100:
                    break
        _require(run_id is not None and re.fullmatch('[0-9]+', str(run_id)), 'successful_all_lane_shadow_required')
        self._run(run_id, commit, 'cloud-operations.yml')
        jobs = self.fetch('/actions/runs/' + str(run_id) + '/jobs?per_page=100').get('jobs', [])
        _require(any(row.get('name') == 'orchestrate' and row.get('conclusion') == 'success'
                 and 'ubuntu-latest' in row.get('labels', []) and 'self-hosted' not in row.get('labels', []) for row in jobs),
                 'github_hosted_shadow_job_required')
        artifacts = self.fetch('/actions/runs/' + str(run_id) + '/artifacts?per_page=100').get('artifacts', [])
        matches = [row for row in artifacts if row.get('name') == 'redacted-run-' + str(run_id) and row.get('expired') is False]
        _require(len(matches) == 1 and isinstance(matches[0].get('id'), int), 'shadow_artifact_missing')
        read_shadow_zip(self.download(matches[0]['id']), minimum_contacts=minimum_contacts)
        return {'hosted': True, 'dry_run': True, 'conclusion': 'success', 'commit': commit,
                'lanes': ['accepted', 'first_touch', 'applications'],
                'evidence_url': f'https://github.com/{REPOSITORY}/actions/runs/{run_id}'}


def main():
    context = {'actions': os.environ.get('GITHUB_ACTIONS') == 'true', 'ref': os.environ.get('GITHUB_REF'),
        'repository': os.environ.get('GITHUB_REPOSITORY'), 'actor': os.environ.get('GITHUB_ACTOR'),
        'event': os.environ.get('GITHUB_EVENT_NAME'), 'commit': os.environ.get('GITHUB_SHA'),
        'triggering_actor': os.environ.get('GITHUB_TRIGGERING_ACTOR', os.environ.get('GITHUB_ACTOR')),
        'run_id': os.environ.get('GITHUB_RUN_ID'), 'cloud_enabled': os.environ.get('CLOUD_ENABLED')}
    _require(context['actions'] and context['ref'] == 'refs/heads/main' and context['repository'] == REPOSITORY
             and context['actor'] == 'narendranathe' and context['event'] == 'workflow_dispatch',
             'activation_requires_owner_default_branch_hosted_writer')
    bundle = load_boundary_bundle()
    config = json.loads(Path('config/cloud.json').read_text())
    budget = RuntimeBudget()
    store = budget.attach(NotionStore(token=os.environ['NOTION_TOKEN'], data_source_id=config['notion_data_source_id']))
    github = GitHubEvidence(os.environ['GITHUB_TOKEN'], budget=budget)
    result = activate(store, bundle, github, context, budget=budget)
    Path('run-output').mkdir(exist_ok=True)
    Path('run-output/maintenance-summary.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = {'status': 'blocked', 'error_type': type(error).__name__, 'operation': 'cutover_activation'}
        if isinstance(error, ActivationBlocked):
            failure['reason'] = str(error)
        if isinstance(error, (NotionAPIError, AmbiguousWriteError, ConflictError)):
            failure['notion'] = error.diagnostics()
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
