#!/usr/bin/env python3
"""Private Actions intake; identity and grants come from the trusted host."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.agent_dispatcher import CONCURRENCY_GROUP, dispatch_intake
from cloud.intake_transport import MAX_CIPHERTEXT, unseal
from cloud.notion import NotionStore
from cloud.runtime_budget import RuntimeBudget
from scripts.migrate_history import github_private_destination


def main():
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or repository != 'narendranathe/apply' or not github_private_destination()):
        raise ValueError('trusted_default_branch_writer_required')
    raw = os.environ.get('AGENT_INTAKE_CIPHERTEXT', '')
    if not raw or len(raw.encode('utf-8')) > MAX_CIPHERTEXT:
        raise ValueError('invalid_intake_size')
    config = json.loads(Path('config/agents.json').read_text())
    if config.get('schema') != 'apply-github-agent-registry/v1':
        raise ValueError('trusted_registry_schema_invalid')
    store = RuntimeBudget(seconds=600, reserve_seconds=60).attach(NotionStore.from_env())
    envelope = unseal(json.loads(raw), os.environ.get('AGENT_INTAKE_PRIVATE_KEY', ''))
    result = dispatch_intake(store, envelope, github_actor=os.environ.get('GITHUB_ACTOR'),
        trusted_registry=config['actors'], writer_context={'concurrency_group': CONCURRENCY_GROUP,
        'run_id': os.environ.get('GITHUB_RUN_ID', ''), 'reservation_verified': True})
    # Never echo the envelope, evidence refs, actor-supplied data or upstream error.
    print(json.dumps({'status': result['status'], 'acknowledgement_verified': True, 'external_effects': 0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'error_type': type(error).__name__, 'operation': 'agent_intake'}))
        raise SystemExit(2)
