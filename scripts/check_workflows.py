#!/usr/bin/env python3
"""Validate security and orchestration invariants without YAML 1.1 `on` coercion."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def check():
    path = ROOT / '.github/workflows/cloud-operations.yml'
    value = path.read_text()
    schedules = re.findall(r"cron: '([^']+)'\s+timezone: '([^']+)'", value)
    assert schedules == [('0 6 * * *', 'America/Chicago'), ('30 6 * * *', 'America/Chicago')]
    assert 'cancel-in-progress: false' in value
    assert 'group: hiring-outreach-single-writer' in value
    assert 'vars.CLOUD_ENABLED' in value
    assert '--cron "$ORIGINATING_CRON"' in value
    assert 'contents: write' not in value
    assert 'persist-credentials: false' in value
    for workflow in (ROOT / '.github/workflows').glob('*.yml'):
        content = workflow.read_text()
        for use in re.findall(r'uses: ([^\s]+)', content):
            assert re.fullmatch(r'[A-Za-z0-9_/-]+@[0-9a-f]{40}', use), (workflow.name, 'unpinned_action')
        if 'cloud.orchestrator' in content:
            assert 'hiring-outreach-single-writer' in content and 'cancel-in-progress: false' in content
    print('PASS: 06:00/06:30 America/Chicago schedules, pinned actions, one writer, guarded activation')


if __name__ == '__main__':
    check()
