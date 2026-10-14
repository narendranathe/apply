#!/usr/bin/env python3
"""Hosted source capture and readback verification; never submits or activates."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.cutover import make_readiness_record, validate_import
from cloud.notion import AmbiguousWriteError, ConflictError, NotionAPIError, NotionStore
from cloud.records import content_hash
from cloud.runtime_budget import RuntimeBudget
from scripts.migrate_history import github_private_destination


def run(mode, store, exported, *, output='run-output', private_verified=False):
    if not private_verified:
        raise ValueError('private_destination_required')
    if mode == 'snapshot':
        from cloud.backup import backup_store
        return {'operation': 'encrypted_source_snapshot', 'backup': backup_store(store, output)}
    if mode == 'verify':
        report = validate_import(store, exported)
        return {'operation': 'full_history_readback', **report}
    if mode == 'readiness':
        # This record is explicitly blocked. It cannot satisfy live_gate or
        # turn repository variables on, even if a caller mislabels evidence.
        evidence = {'observed_at': datetime.now(timezone.utc).isoformat(),
                    'commit': os.environ.get('GITHUB_SHA', ''),
                    'run_url': 'https://github.com/' + os.environ.get('GITHUB_REPOSITORY', '')
                               + '/actions/runs/' + os.environ.get('GITHUB_RUN_ID', ''),
                    'historical_manifest_sha256': content_hash(exported),
                    'historical_records': len(exported['records']),
                    'external_effects': 0}
        row = make_readiness_record(evidence, ['history_reconciliation_pending',
            'final_local_delta_pending', 'local_writer_retirement_pending',
            'populated_hosted_shadow_pending'])
        written = store.put(row)
        readback = store.get(row['id'])
        core = ('id', 'kind', 'payload', 'status', 'hash')
        if not written or not readback or any(written.get(k) != row[k] or readback.get(k) != row[k] for k in core):
            raise AmbiguousWriteError('Readiness readback uncertain; reconcile before replay')
        return {'operation': 'blocked_readiness_record', 'verified': True,
                'activation_authorized': False, 'external_effects': 0}
    raise ValueError('unsupported_maintenance_mode')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('snapshot', 'verify', 'readiness'), required=True)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    if not github_private_destination():
        raise ValueError('private_destination_required')
    store = RuntimeBudget().attach(NotionStore.from_env())
    result = run(args.mode, store, json.loads(args.input.read_text()), private_verified=True)
    Path('run-output').mkdir(exist_ok=True)
    Path('run-output/maintenance-summary.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))
    if result.get('blocked') or result.get('conflicted'):
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        failure = {'status': 'blocked', 'error_type': type(error).__name__, 'operation': 'cloud_maintenance'}
        if isinstance(error, (NotionAPIError, AmbiguousWriteError, ConflictError)):
            failure['notion'] = error.diagnostics()
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
