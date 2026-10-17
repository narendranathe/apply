#!/usr/bin/env python3
"""Resolve only the pinned October 17 startup failure, without business effects."""
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.backup import backup_store, validate_key
from cloud.baseline import restore_baseline
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError, NotionStore
from cloud.runtime_budget import RuntimeBudget
from cloud.startup_reconciliation import ReconciliationBlocked, reconcile, require
from scripts.activate_cloud import GitHubEvidence, NoRedirect


class StartupGitHubEvidence(GitHubEvidence):
    def _json(self, path):
        try:
            with build_opener(NoRedirect()).open(self._request(path), timeout=30) as response:
                body = response.read(4_000_001)
                require(len(body) <= 4_000_000, 'github_metadata_too_large')
                value = json.loads(body)
                if isinstance(value, dict) and 'rel="next"' in response.headers.get('Link', ''):
                    value['_next_page'] = True
                return value
        except ReconciliationBlocked:
            raise
        except Exception:
            raise ReconciliationBlocked('github_metadata_unavailable') from None

    def download_path(self, path):
        """Read private job logs; credentials never follow a signed redirect."""
        try:
            try:
                response = build_opener(NoRedirect()).open(self._request(path), timeout=30)
            except HTTPError as redirect:
                require(redirect.code in {301, 302, 303, 307, 308}, 'github_log_download_failed')
                location = redirect.headers.get('Location', '')
                parsed = urlsplit(location)
                require(parsed.scheme == 'https' and not parsed.username and not parsed.password
                        and parsed.port in {None, 443} and isinstance(parsed.hostname, str)
                        and parsed.hostname.endswith(('.blob.core.windows.net', '.actions.githubusercontent.com',
                                                      '.githubusercontent.com')),
                        'github_log_redirect_invalid')
                response = build_opener(NoRedirect()).open(
                    Request(location, headers={'User-Agent': 'apply-startup-reconciliation'}), timeout=60)
            with response:
                body = response.read(2_000_001)
            require(len(body) <= 2_000_000, 'github_log_too_large')
            return body
        except ReconciliationBlocked:
            raise
        except Exception:
            raise ReconciliationBlocked('github_log_download_failed') from None


def main():
    context = {'actions': os.environ.get('GITHUB_ACTIONS') == 'true', 'ref': os.environ.get('GITHUB_REF'),
               'repository': os.environ.get('GITHUB_REPOSITORY'), 'actor': os.environ.get('GITHUB_ACTOR'),
               'triggering_actor': os.environ.get('GITHUB_TRIGGERING_ACTOR'),
               'event': os.environ.get('GITHUB_EVENT_NAME'), 'commit': os.environ.get('GITHUB_SHA'),
               'run_id': os.environ.get('GITHUB_RUN_ID'),
               'cloud_enabled': os.environ.get('CLOUD_ENABLED')}
    # Reject local invocation before credentials, archives, or network reads.
    require(context['actions'] and context['ref'] == 'refs/heads/main'
            and context['actor'] == 'narendranathe' and context['triggering_actor'] == 'narendranathe'
            and context['repository'] == 'narendranathe/apply' and context['event'] == 'workflow_dispatch'
            and context['cloud_enabled'] == 'false', 'owner_disabled_hosted_maintenance_required')
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
        failure = {'status': 'blocked', 'error_type': type(error).__name__, 'operation': 'pinned_startup_reconciliation'}
        if isinstance(error, ReconciliationBlocked):
            failure['reason'] = str(error)
        if isinstance(error, (NotionAPIError, AmbiguousWriteError, ConflictError)):
            failure['notion'] = error.diagnostics()
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
