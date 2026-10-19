"""Private outreach previews must not reuse immutable live plan identities."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cloud.outreach import record, run_outreach
from cloud.shadow import Blocked, ShadowStore


NOW = datetime(2026, 10, 19, 7, tzinfo=ZoneInfo('America/Chicago'))
LIVE_IDS = {
    'accepted': 'plan:3c66c1117d378fcf382cb2f7a0785c3364e8605a20bfb34743e517ed9b4fb21b',
    'first_touch': 'plan:575af9d720eafaecb4f13f601b6525d6f7bbb713e8f164e215df0247811a2e4d',
}


def contact(lane, **updates):
    context = 'Would the platform team be the right route?'
    return record('contact:fixture', 'contacts', {
        'name': 'Fixture Person', 'company_id': 'company:fixture',
        'state': 'connected' if lane == 'accepted' else 'uncontacted',
        'identity_verified': True, 'history_complete': True,
        'verified_at': NOW.isoformat(), 'unanswered_total': 0,
        'reply_context': context, 'routing_context': context,
        'verified_reply_draft': 'Thanks for the update.',
        'reply_draft_context_hash': hashlib.sha256(context.encode()).hexdigest(),
        **updates,
    })


class Store:
    def __init__(self, rows):
        self.records = {row['id']: row for row in rows}
        self.puts = []

    def get(self, identity):
        return self.records.get(identity)

    def query(self, kind=None):
        return [row for row in self.records.values() if kind is None or row['kind'] == kind]

    def put(self, row):
        self.puts.append(row)
        self.records[row['id']] = row
        return row


class OutreachPreviewIdentityTests(unittest.TestCase):
    def test_later_shadow_preview_preserves_existing_same_day_live_plan(self):
        for lane in LIVE_IDS:
            with self.subTest(lane=lane), patch('cloud.outreach.verified_proof', return_value='Verified proof'):
                base = Store([contact(lane)])
                run_outreach(base, {'account_history_reconciled': True}, lane, NOW, dry_run=False)
                historical = deepcopy(base.records)
                base.puts.clear()
                preview = ShadowStore(base)

                summary = run_outreach(preview, {'account_history_reconciled': True},
                                       lane, NOW + timedelta(minutes=1), dry_run=True)

                self.assertEqual(summary['drafts'], 1)
                self.assertEqual(summary['sent'], 0)
                self.assertEqual(base.puts, [])
                self.assertEqual(base.records, historical)
                self.assertEqual(list(preview.records), ['preview:' + LIVE_IDS[lane]])
                staged = next(iter(preview.records.values()))
                self.assertEqual(staged['payload']['observed_at'], (NOW + timedelta(minutes=1)).isoformat())

    def test_live_plan_identity_and_payload_remain_unchanged(self):
        for lane in LIVE_IDS:
            with self.subTest(lane=lane), patch('cloud.outreach.verified_proof', return_value='Verified proof'):
                base = Store([contact(lane)])
                run_outreach(base, {'account_history_reconciled': True}, lane, NOW, dry_run=False)

                self.assertEqual(len(base.puts), 1)
                plan = base.puts[0]
                self.assertEqual(plan['id'], LIVE_IDS[lane])
                self.assertEqual(plan['status'], 'draft')
                self.assertEqual(plan['payload'], {
                    'contact_id': 'contact:fixture', 'company_id': 'company:fixture',
                    'lane': lane, 'observed_at': '2026-10-19T07:00:00-05:00',
                    'inbox': 'not checked', 'blocker': None, 'action': 'human_execute_draft',
                    'draft': ('Thanks for the update.' if lane == 'accepted' else
                              'Hi Fixture, I’m a Data Engineer at ExponentHR. Verified proof. '
                              'Would the platform team be the right route?'),
                })

    def test_shadow_store_namespace_cannot_be_disabled_by_live_flag(self):
        base = Store([contact('accepted')])
        preview = ShadowStore(base)
        with patch('cloud.outreach.verified_proof', return_value='Verified proof'):
            run_outreach(preview, {}, 'accepted', NOW, dry_run=False)

        self.assertEqual(list(preview.records), ['preview:' + LIVE_IDS['accepted']])
        self.assertEqual(base.puts, [])

    def test_changed_preview_with_same_identity_still_fails_closed(self):
        base = Store([contact('accepted')])
        preview = ShadowStore(base)
        with patch('cloud.outreach.verified_proof', return_value='Verified proof'):
            run_outreach(preview, {}, 'accepted', NOW, dry_run=True)
            staged = deepcopy(preview.records)
            with self.assertRaisesRegex(Blocked, '^shadow_identity_conflict$'):
                run_outreach(preview, {}, 'accepted', NOW + timedelta(minutes=1), dry_run=True)

        self.assertEqual(preview.records, staged)
        self.assertEqual(base.puts, [])

    def test_preview_namespace_preserves_suppression_and_freshness_gates(self):
        cases = (
            ({'opt_out': True}, 'suppressed'),
            ({'state': 'pending'}, 'pending'),
            ({'delivery_unknown': True}, 'delivery_unknown'),
            ({'history_complete': False}, 'needs_verification'),
            ({'verified_at': (NOW - timedelta(hours=25)).isoformat()}, 'needs_verification'),
            ({'source_notion_id': 'missing-source'}, 'fresh_source_unavailable'),
        )
        for lane in LIVE_IDS:
            for updates, blocker in cases:
                with self.subTest(lane=lane, updates=updates), patch('cloud.outreach.verified_proof', return_value='Verified proof'):
                    base = Store([contact(lane, **updates)])
                    preview = ShadowStore(base)
                    summary = run_outreach(preview, {'account_history_reconciled': True}, lane, NOW, dry_run=True)

                    self.assertEqual(summary['drafts'], 0)
                    self.assertEqual(summary['sent'], 0)
                    self.assertEqual(summary['blocked'], {blocker: 1})
                    plan = next(iter(preview.records.values()))
                    self.assertEqual(plan['id'], 'preview:' + LIVE_IDS[lane])
                    self.assertNotIn('draft', plan['payload'])
                    self.assertEqual(base.puts, [])


if __name__ == '__main__':
    unittest.main()
