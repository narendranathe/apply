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
    assert value.count('concurrency:') == 1
    assert re.search(r'^concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n',
                     value, re.MULTILINE)
    assert 'vars.CLOUD_ENABLED' in value
    assert '--cron "$ORIGINATING_CRON"' in value
    assert 'contents: write' not in value
    assert 'persist-credentials: false' in value
    fallback_match = re.search(r'^  research_fallback:\n(.*?)(?=^  [a-z_]+:\n|\Z)',
                               value, re.MULTILINE | re.DOTALL)
    assert fallback_match, 'scheduled_research_fallback_required'
    fallback = fallback_match.group(1)
    condition = ("${{ always() && !cancelled() && github.event_name == 'schedule' && "
                 "github.ref == 'refs/heads/main' && vars.CLOUD_ENABLED == 'true' && "
                 "vars.CLOUD_RESEARCH_ENABLED == 'true' && needs.orchestrate.result == 'failure' }}")
    fallback_condition = re.search(r'^    if: (.+)$', fallback, re.MULTILINE)
    assert fallback_condition and fallback_condition.group(1) == condition
    assert '    needs: orchestrate\n' in fallback
    assert '    runs-on: ubuntu-latest\n' in fallback
    assert '    timeout-minutes: 150\n' in fallback
    assert "          CLOUD_ENABLED: 'false'\n" in fallback
    assert '          CLOUD_RESEARCH_ENABLED: ${{ vars.CLOUD_RESEARCH_ENABLED }}\n' in fallback
    assert '          ref: ${{ github.sha }}\n' in fallback
    assert '          persist-credentials: false\n' in fallback
    assert 'test "$PRIVATE" = \'true\'' in fallback
    assert re.findall(r'--mode\s+(\S+)', fallback) == ['research-only']
    assert '--cron "$ORIGINATING_CRON" --lane all' in fallback
    assert '--reconcile --output run-output/research-fallback-summary.json' in fallback
    assert 'name: research-fallback-${{ github.run_id }}-${{ github.run_attempt }}' in fallback
    assert 'retention-days: 14' in fallback
    assert 'run-output/summary.json' not in fallback
    assert 'download-artifact' not in fallback and 'overwrite:' not in fallback
    assert 'continue-on-error:' not in value
    assert not re.search(r'^\s+[\w-]+: write\s*$', value, re.MULTILINE)
    maintenance = (ROOT / '.github/workflows/history-reconciliation.yml').read_text()
    assert re.search(r'^concurrency:\n  group: hiring-outreach-single-writer\n  cancel-in-progress: false\n',
                     maintenance, re.MULTILINE)
    assert "reconcile-readback]" in maintenance
    assert "elif [ \"$IMPORT_MODE\" = 'reconcile-readback' ]; then\n            python scripts/reconcile_readback.py" in maintenance
    assert 'CLOUD_ENABLED: ${{ vars.CLOUD_ENABLED }}' in maintenance
    for workflow in (ROOT / '.github/workflows').glob('*.yml'):
        content = workflow.read_text()
        for use in re.findall(r'uses: ([^\s]+)', content):
            assert re.fullmatch(r'[A-Za-z0-9_/-]+@[0-9a-f]{40}', use), (workflow.name, 'unpinned_action')
        if 'cloud.orchestrator' in content:
            assert 'hiring-outreach-single-writer' in content and 'cancel-in-progress: false' in content
    print('PASS: 06:00/06:30 America/Chicago schedules, pinned actions, one writer, guarded activation and research fallback')


if __name__ == '__main__':
    check()
