"""Synthetic hosted-entry ordering: technical uncertainty must stop all writes."""
import copy
import sys
import unittest
from unittest.mock import patch

from cloud.cutover import make_cutover_record
from cloud.orchestrator import Blocked, ShadowStore, main
from cloud.outreach import record
from cloud.technical_admission import _records
from tests.test_agent_worker_orchestration import HOST
from tests.test_cutover import evidence
from tests.test_technical_storage_integration import technical


class TechnicalMainFenceTests(unittest.TestCase):
    def test_live_main_keeps_disabled_production_gate_before_preflight(self):
        store = ShadowStore()
        env = {**HOST, "DESTINATION_PRIVATE": "true", "CLOUD_ENABLED": "false", "NOTION_TOKEN": "fixture"}
        with patch.dict("os.environ", env, clear=True), patch.object(sys, "argv", ["orchestrator", "--mode", "live"]), \
             patch("cloud.notion.NotionStore", return_value=store), patch("cloud.backup.validate_key"), \
             patch.object(store, "index_source_records", side_effect=AssertionError("disabled must not scan")) as scan, \
             self.assertRaisesRegex(Blocked, "cutover_not_enabled"):
            main()
        scan.assert_not_called()

    def test_live_main_fences_partial_technical_state_before_dead_letter(self):
        store = ShadowStore()
        store.put(make_cutover_record(evidence()))
        store.put(record("run:synthetic:started", "agent_runs", {}))
        store.put(_records(technical("technical_items"))[0])
        before = copy.deepcopy(store.records)
        env = {**HOST, "DESTINATION_PRIVATE": "true", "CLOUD_ENABLED": "true", "NOTION_TOKEN": "fixture"}
        with patch.dict("os.environ", env, clear=True), patch.object(sys, "argv", ["orchestrator", "--mode", "live"]), \
             patch("cloud.notion.NotionStore", return_value=store), patch("cloud.backup.validate_key"), \
             self.assertRaisesRegex(Blocked, "technical_admission_requires_reconciliation"):
            main()
        self.assertEqual(store.records, before)
