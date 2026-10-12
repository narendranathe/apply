from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from cloud.orchestrator import Blocked, ShadowStore, due_slots, execute, selected_lanes
from cloud.outreach import gate, record
from scripts.security_check import unsafe


class OrchestrationTests(unittest.TestCase):
    def test_originating_cron_is_dispatch_key(self):
        self.assertEqual(selected_lanes('0 6 * * *'), ['accepted'])
        self.assertEqual(selected_lanes('30 6 * * *'), ['first_touch', 'applications'])
        with self.assertRaises(Blocked):
            selected_lanes('0 9 * * *')

    def test_dst_dates(self):
        winter = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        summer = datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc)
        self.assertEqual(due_slots(winter, ['accepted'], 0, True), [('2026-11-02', 'accepted', False)])
        self.assertEqual(due_slots(summer, ['accepted'], 0, True), [('2026-07-01', 'accepted', False)])
        self.assertEqual(due_slots(winter.replace(hour=11), ['accepted'], 0, True), [])

    def test_dry_run_has_no_remote_mutations(self):
        base = ShadowStore()
        execute(base, {}, datetime(2026, 10, 12, 12, tzinfo=timezone.utc), requested='accepted')
        self.assertEqual(base.records, {})

    def test_private_and_cutover_gate(self):
        base = ShadowStore()
        for config in ({}, {'destination_private': True}, {'destination_private': True, 'cloud_enabled': True}):
            with self.assertRaises(Blocked):
                execute(base, config, requested='accepted', dry_run=False)

    def test_incomplete_slot_blocks_all_future_live_effects(self):
        store = ShadowStore()
        store.put(record('deployment:cutover:v2', 'audit_events', {k: True for k in ('history_reconciled', 'backup_restore_verified', 'local_actors_retired', 'shadow_verified')}))
        store.put(record('run:2026-10-12:accepted:started', 'agent_runs', {'date': '2026-10-12'}))
        config = {'destination_private': True, 'cloud_enabled': True, 'reconcile_lookback_days': 0}
        with patch('cloud.applications.run_applications', return_value={'verified': 0}), patch('cloud.orchestrator.run_outreach') as outreach:
            with self.assertRaises(Blocked):
                execute(store, config, datetime(2026, 10, 12, 11, 10, tzinfo=timezone.utc), dry_run=False)
            outreach.assert_not_called()

    def test_github_failure_witness_needs_explicit_resolution(self):
        from cloud.runtime_guard import unresolved_writers
        rows = [{'id': 1, 'display_title': 'cloud:live:accepted', 'conclusion': 'failure'},
                {'id': 2, 'display_title': 'cloud:dry-run:all', 'conclusion': 'failure'},
                {'id': 3, 'display_title': 'cloud:live:applications', 'conclusion': 'success'}]
        self.assertEqual(unresolved_writers(rows, 4, lambda identity: False), ['1'])
        self.assertEqual(unresolved_writers(rows, 4, lambda identity: identity == '1'), [])

    def test_manual_live_lane_does_not_expand_into_applications(self):
        store = ShadowStore()
        store.put(record('deployment:cutover:v2', 'audit_events', {k: True for k in ('history_reconciled', 'backup_restore_verified', 'local_actors_retired', 'shadow_verified')}))
        config = {'destination_private': True, 'cloud_enabled': True, 'reconcile_lookback_days': 0}
        with patch('cloud.applications.run_applications') as applications, patch('cloud.orchestrator.run_outreach', return_value={'sent': 0}) as outreach:
            execute(store, config, datetime(2026, 10, 12, 13, tzinfo=timezone.utc), requested='accepted', dry_run=False)
        applications.assert_not_called()
        self.assertEqual(outreach.call_count, 1)

    def test_completed_slot_suppresses_rerun(self):
        store = ShadowStore()
        store.put(record('run:2026-10-12:accepted:completed', 'agent_runs', {}))
        with patch('cloud.orchestrator.run_outreach') as outreach:
            result = execute(store, {}, datetime(2026, 10, 12, 11, tzinfo=timezone.utc), requested='accepted')
        outreach.assert_not_called()
        self.assertEqual(result['results'][0]['status'], 'already_completed')

    def test_missed_runs_never_replay_side_effects(self):
        store = ShadowStore()
        with patch('cloud.orchestrator.run_outreach', return_value={'sent': 0}) as outreach:
            result = execute(store, {'reconcile_lookback_days': 2}, datetime(2026, 10, 12, 11, tzinfo=timezone.utc), requested='accepted', reconcile=True)
        self.assertEqual(outreach.call_count, 1)
        self.assertEqual(sum(x.get('status') == 'missed_research_only' for x in result['results']), 2)


class OutreachSafetyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        self.contact = {'state': 'connected', 'identity_verified': True, 'history_complete': True, 'verified_at': self.now.isoformat(), 'unanswered_total': 0}

    def test_pending_and_unknown_block(self):
        for state in ('pending', 'delivery_unknown', 'suppressed', 'ambiguous'):
            self.assertEqual(gate(dict(self.contact, state=state), self.now, 'accepted'), state)

    def test_fresh_original_suppression_matches_compact_notion_id(self):
        from cloud.outreach import run_outreach
        class Store(ShadowStore):
            def current_contact_observations(inner):
                return [{'source_notion_id': '12345678-1234-1234-1234-123456789abc', 'observed_at': self.now.isoformat(), 'no_contact': True, 'suppressed': True}]
        store = Store()
        payload = dict(self.contact, name='Example', source_notion_id='12345678123412341234123456789abc')
        store.put(record('contact:test', 'contacts', payload))
        result = run_outreach(store, {}, 'accepted', self.now)
        self.assertEqual(result['blocked'], {'suppressed': 1})
        self.assertEqual(result['drafts'], 0)

    def test_absent_fresh_source_blocks_verified_snapshot(self):
        from cloud.outreach import run_outreach
        store = ShadowStore()
        store.put(record('contact:test', 'contacts', dict(self.contact, source_notion_id='missing')))
        result = run_outreach(store, {}, 'accepted', self.now)
        self.assertEqual(result['blocked'], {'fresh_source_unavailable': 1})

    def test_changed_original_identity_invalidates_verified_snapshot(self):
        from cloud.outreach import run_outreach
        class Store(ShadowStore):
            def current_contact_observations(inner):
                return [{'source_notion_id': 'source', 'observed_at': self.now.isoformat(), 'properties': {'Company': 'Changed employer'}, 'body': [], 'no_contact': False}]
        store = Store()
        store.put(record('contact:test', 'contacts', dict(self.contact, source_notion_id='source', verified_source_hash='previous-hash')))
        result = run_outreach(store, {}, 'accepted', self.now)
        self.assertEqual(result['blocked'], {'needs_verification': 1})

    def test_research_rotation_and_reply_priority(self):
        from cloud.outreach import select_contacts
        contacts = [record('a', 'contacts', {}), record('b', 'contacts', {}), record('c', 'contacts', {'genuine_reply': True})]
        observations = [record('old', 'conversation_observations', {'contact_id': 'a', 'lane': 'accepted', 'observed_at': self.now.isoformat()})]
        selected = select_contacts(contacts, observations, 'accepted', 2)
        self.assertEqual([x['id'] for x in selected], ['c', 'b'])

    def test_two_unanswered_and_24_hour_gap(self):
        self.assertEqual(gate(dict(self.contact, unanswered_total=2), self.now, 'accepted'), 'unanswered_limit')
        self.assertEqual(gate(dict(self.contact, last_touch_at=self.now.isoformat()), self.now, 'accepted'), 'minimum_gap')

    def test_unknown_account_counts_prevent_first_touch(self):
        contact = dict(self.contact, state='uncontacted')
        self.assertEqual(gate(contact, self.now, 'first_touch'), 'account_counts_unknown')
        self.assertEqual(gate(contact, self.now, 'first_touch', 3), 'company_cap')

    def test_friday_sunday_reply_only(self):
        friday = self.now.replace(day=16)
        contact = dict(self.contact, verified_at=friday.isoformat())
        self.assertEqual(gate(contact, friday, 'accepted'), 'research_only_day')
        self.assertIsNone(gate(dict(contact, genuine_reply=True), friday, 'accepted'))

    def test_secret_and_database_scanning(self):
        self.assertTrue(unsafe('other.bin', b'SQLite format 3\0'))
        self.assertTrue(unsafe('thing.txt', ('gh' + 'p_' + 'a' * 40).encode()))
        self.assertTrue(unsafe('export/Outreach_Log.md', b'message'))
        self.assertTrue(unsafe('config.json', ('{"api_key":"' + 'a' * 32 + '"}').encode()))
        self.assertTrue(unsafe('.env.production', ('NOTION_TOKEN=' + 'a' * 32).encode()))
        self.assertFalse(unsafe('migration-archives/snapshot.aesgcm', b'ciphertext'))


if __name__ == '__main__':
    unittest.main()
