"""Offline handoff through the paged index and technical projections together."""
from datetime import timedelta
import unittest

from cloud.agent_index import build_index
from cloud.agent_protocol import AgentProtocol, ProtocolError
from cloud.index_manifest import pack_manifest
from cloud.records import canonical_json, make_record
from tests.test_agent_index import contact
from tests.test_agent_protocol import REGISTRY
from tests.test_index_publisher import FreshStore
from tests.test_technical_agent_index import NOW, admitted, technical


class TechnicalPagedIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        people = [contact("contact:synthetic:" + str(i)) for i in range(300)]
        company = make_record("companies", {"company_id": "company:fixture"}, "company:fixture-row")
        opted_out = make_record("contacts", {"contact_id": "contact:opted-out", "opt_out": True}, "contact:opted-out-row")
        initial = build_index([*people, company, opted_out], NOW, source_complete=True)
        previous = next(row for row in initial if row["kind"] == "index_manifests")
        cls.item = technical(company_id="company:fixture", contact_id="contact:opted-out",
                             original_text="PRIVATE SYNTHETIC SOURCE BODY")
        cls.sources = admitted([*people, company, opted_out, cls.item])
        cls.semantic = build_index(cls.sources, NOW + timedelta(seconds=1), previous, source_complete=True)
        cls.logical = next(row for row in cls.semantic if row["kind"] == "index_manifests")
        cls.pages, cls.root = pack_manifest(cls.logical)
        cls.cursor = previous["payload"]["source_cursor"]

    def store(self, paged=True):
        records = [*self.sources, *(row for row in self.semantic if row["kind"] != "index_manifests")]
        records += [*self.pages, self.root] if paged else [self.logical]
        return FreshStore(records)

    def test_paged_agent_handoff_preserves_links_suppression_and_cursor(self):
        self.assertTrue(self.pages, "fixture must actually use paged storage")
        paged, inline = self.store(), self.store(False)
        codex = AgentProtocol(paged, REGISTRY, "codex", now=NOW + timedelta(seconds=1))
        muse = AgentProtocol(paged, REGISTRY, "muse", now=NOW + timedelta(seconds=1))
        baseline = AgentProtocol(inline, REGISTRY, "codex", now=NOW + timedelta(seconds=1))
        item_id = self.item["payload"]["entity_id"]
        company = codex.get_entity_context("company:fixture")
        self.assertIn(item_id, company["data"]["entity"]["related_ids"]["technical_item_ids"])
        other = muse.get_entity_context("company:fixture")
        self.assertEqual(company["data"]["entity"], other["data"]["entity"])
        self.assertTrue(codex.get_entity_context("contact:opted-out")["data"]["entity"]["suppression"]["suppressed"])
        current, expected = codex.get_recent_changes(self.cursor, limit=5), baseline.get_recent_changes(self.cursor, limit=5)
        current["data"].pop("manifest_id")
        expected["data"].pop("manifest_id")
        self.assertEqual(current["data"], expected["data"])
        self.assertEqual({row["entity_id"] for row in current["data"]["changes"]},
                         {"company:fixture", "contact:opted-out", item_id})
        self.assertNotEqual(current["data"]["next_cursor"], self.cursor)
        self.assertLessEqual(len(canonical_json(company).encode()), 8000)
        self.assertNotIn("PRIVATE SYNTHETIC SOURCE BODY", canonical_json([company, other, current]))
        self.assertNotIn("manifest_page", canonical_json(current["data"]))
        self.assertEqual(paged.put_calls, [])

    def test_selected_missing_projection_page_fails_closed(self):
        store = self.store()
        client = AgentProtocol(store, REGISTRY, "codex", now=NOW + timedelta(seconds=1))
        client.get_entity_context("company:fixture")
        page_id = next(identity for identity in store.get_calls
                       if identity.startswith("index_manifest_page:")
                       and store.records[identity]["payload"].get("section") == "projections")
        del store.records[page_id]
        fresh = AgentProtocol(store, REGISTRY, "codex", now=NOW + timedelta(seconds=1))
        with self.assertRaises(ProtocolError):
            fresh.get_entity_context("company:fixture")
        self.assertEqual(store.put_calls, [])


if __name__ == "__main__":
    unittest.main()
