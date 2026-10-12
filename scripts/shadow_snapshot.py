#!/usr/bin/env python3
"""All-lane shadow from the verified local export; no cloud write or submission."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.orchestrator import ShadowStore, execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--config', default='config/cloud.json')
    parser.add_argument('--output', default='.migration-private/local-shadow-summary.json')
    args = parser.parse_args()
    export = json.loads(Path(args.input).read_text())
    if export.get('blocked'):
        raise ValueError('Unresolved source conflicts')
    store = ShadowStore()
    for value in export['records']:
        store.put(value)
    before = json.dumps(store.records, sort_keys=True)
    result = execute(store, json.loads(Path(args.config).read_text()), datetime.now(timezone.utc), dry_run=True)
    if json.dumps(store.records, sort_keys=True) != before:
        raise ValueError('Shadow mutated baseline')
    result.update(validation_environment='local_snapshot', baseline_records=len(export['records']),
                  live_notion_credential_verified=False, production_submission_count=0, cloud_hosted=False)
    Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
