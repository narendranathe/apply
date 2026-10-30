"""Technical storage ambiguity fences all live lanes before their first write."""
import copy
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from cloud.cutover import make_cutover_record
from cloud.orchestrator import Blocked, ShadowStore, execute
from cloud.technical_admission import _records
from cloud.outreach import record
from tests.test_cutover import evidence
from tests.test_technical_storage_integration import technical


class TechnicalOrchestratorFenceTests(unittest.TestCase):
    def test_partial_admission_fences_dead_letter_before_live_gate_writes(self):
        store = ShadowStore()
        store.put(make_cutover_record(evidence()))
        store.put(record("run:synthetic:started", "agent_runs", {}))
        store.put(_records(technical("technical_items"))[0])
        before = copy.deepcopy(store.records)
        config = {"destination_private": True, "cloud_enabled": True}
        with self.assertRaisesRegex(Blocked, "technical_admission_requires_reconciliation"):
            execute(store, config, datetime(2026, 10, 20, 13, tzinfo=timezone.utc), mode="live")
        self.assertEqual(store.records, before)

    def test_unverified_cursor_also_fences_live_business_lanes(self):
        store = ShadowStore()
        store.put(make_cutover_record(evidence()))
        store.put(technical("source_cursors"))
        before = copy.deepcopy(store.records)
        config = {"destination_private": True, "cloud_enabled": True, "reconcile_lookback_days": 0}
        now = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)
        with patch("cloud.orchestrator.run_outreach", return_value={}) as outreach, \
             patch("cloud.reporting.run_reporting", return_value={}), \
             patch("cloud.orchestrator._publish_agent_index", return_value={}), \
             self.assertRaisesRegex(Blocked, "technical_admission_requires_reconciliation"):
            execute(store, config, now, requested="accepted", mode="live")
        outreach.assert_not_called()
        self.assertEqual(store.records, before)

    def test_partial_admission_blocks_live_lanes_even_without_agent_consumer(self):
        store = ShadowStore()
        store.put(make_cutover_record(evidence()))
        store.put(_records(technical("technical_items"))[0])
        before = copy.deepcopy(store.records)
        config = {"destination_private": True, "cloud_enabled": True, "reconcile_lookback_days": 0}
        now = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)
        with patch("cloud.orchestrator.run_outreach", return_value={}) as outreach, \
             patch("cloud.reporting.run_reporting", return_value={}), \
             patch("cloud.orchestrator._publish_agent_index", return_value={}), \
             self.assertRaisesRegex(Blocked, "technical_admission_requires_reconciliation"):
            execute(store, config, now, requested="accepted", mode="live")
        outreach.assert_not_called()
        self.assertEqual(store.records, before)
