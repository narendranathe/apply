"""Synthetic publisher/reader integration through frozen Notion write limits."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from cloud.agent_dispatcher import DispatcherStorageError
from cloud.agent_index import build_index
from cloud.agent_protocol import AgentProtocol, ProtocolError
from cloud.index_manifest import ManifestView, PAGE_MAX_BYTES, pack_manifest
from cloud import index_publisher
from cloud.index_publisher import IndexPublicationError, publish_index, snapshot_hash
from cloud.notion import NotionStore, _text
from cloud.records import canonical_json, make_record, validate_record
from tests.test_agent_dispatcher import dispatch
from tests.test_agent_index import NOW, contact
from tests.test_agent_protocol import REGISTRY
from tests.test_index_publisher import FreshStore
from tests.test_notion import FakeNotion


class WireStore(NotionStore):
    """Keep actual NotionStore.put/request serialization; fake only remote I/O."""
    def __init__(self, sources=(), data_source_id="00000000-0000-0000-0000-000000000001"):
        self.records = {row["id"]: deepcopy(row) for row in sources}
        self.posts, self.put_calls, self.get_calls = [], [], []
        self.schema = FakeNotion().schema
        super().__init__("synthetic-test-token", data_source_id=data_source_id,
                         transport=self._transport, request_interval=0, sleeper=lambda _: None)

    def _transport(self, method, path, body, headers):
        if method == "GET" and path.startswith("/data_sources/"):
            return deepcopy(self.schema)
        if (method, path) == ("POST", "/pages"):
            self.posts.append(deepcopy(body))
            record = validate_record(json.loads(_text(body["properties"]["Cloud Envelope"])))
            self.records[record["id"]] = record
            return {"id": "synthetic-page"}
        raise AssertionError("Unexpected synthetic transport operation")

    def get(self, identity):
        self.get_calls.append(identity)
        return deepcopy(self.records.get(identity))

    def query(self, kind=None):
        return [deepcopy(row) for row in self.records.values() if kind is None or row["kind"] == kind]

    def index_source_records(self):
        return self.query()

    def latest_index_manifest(self):
        return next((deepcopy(row) for row in reversed(list(self.records.values()))
                     if row["kind"] == "index_manifests"), None)

    def put(self, row):
        self.put_calls.append(row["id"])
        return super().put(row)


class PagedIndexIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = [contact("contact:synthetic:" + str(i)) for i in range(400)]
        cls.semantic = build_index(cls.source, NOW, source_complete=True)
        cls.logical = next(row for row in cls.semantic if row["kind"] == "index_manifests")
        cls.pages, cls.root = pack_manifest(cls.logical)

    def reader_store(self, *, inline=False):
        records = [*self.source, *(row for row in self.semantic if row["kind"] != "index_manifests")]
        records += [self.logical] if inline else [*self.pages, self.root]
        return FreshStore(records)

    def client(self, store, principal="codex"):
        return AgentProtocol(store, REGISTRY, principal, now=NOW)

    def test_actual_frozen_notion_rejects_unpacked_manifest_before_post(self):
        self.assertGreater(len(canonical_json(self.logical).encode()), 200000)
        store = WireStore()
        with self.assertRaises(ValueError):
            store.put(self.logical)
        self.assertEqual(store.posts, [])

    def test_large_publication_fits_actual_notion_and_preserves_semantics(self):
        store = WireStore(self.source)
        result = publish_index(store, NOW, source_complete=True, source_snapshot_hash=snapshot_hash(self.source))
        self.assertEqual(result["manifest"], self.root)
        self.assertTrue(result["complete"])
        self.assertEqual(result["counts"], self.logical["payload"]["counts"])
        self.assertEqual(result["record_count"], len(self.semantic) + len(self.pages))
        self.assertEqual(result["created_or_verified"], result["record_count"])
        expected = [row["id"] for row in self.semantic if row["kind"] != "index_manifests"]
        expected += [row["id"] for row in self.pages] + [self.root["id"]]
        self.assertEqual(store.put_calls, expected)
        self.assertTrue(all(len(canonical_json(body).encode()) <= 490000 for body in store.posts))
        self.assertTrue(all(len(canonical_json(page).encode()) <= PAGE_MAX_BYTES for page in self.pages))
        self.assertEqual(ManifestView(result["manifest"], store.get).materialize(), self.logical)
        self.assertNotIn(self.logical["id"], store.records)
        self.assertTrue(all(store.records[row["id"]] == row for row in self.semantic
                            if row["kind"] != "index_manifests"))

    def test_small_publication_remains_exact_inline_v1(self):
        source = [contact()]
        expected = build_index(source, NOW, source_complete=True)
        logical = next(row for row in expected if row["kind"] == "index_manifests")
        store = WireStore(source)
        result = publish_index(store, NOW, source_complete=True, source_snapshot_hash=snapshot_hash(source))
        self.assertEqual(result["manifest"], logical)
        self.assertEqual(result["record_count"], len(expected))
        self.assertFalse(any(row["status"] == "manifest_page" for row in store.records.values()))

    def test_exact_pure_request_matches_actual_notion_unicode_and_parent(self):
        for parent in ("00000000-0000-0000-0000-000000000001", "ffffffff-ffff-ffff-ffff-ffffffffffff"):
            with self.subTest(parent=parent):
                row = make_record("contacts", {"name": "λ🤖", "message": "synthetic only"}, "contact:wire")
                store = WireStore(data_source_id=parent)
                request = index_publisher._notion_write_request(row, parent)
                self.assertEqual(store.posts, [])
                store.put(row)
                self.assertEqual(request, store.posts[0])

    def test_pure_capacity_matches_actual_frozen_ascii_and_unicode_limits(self):
        for text in ("a" * 201000, "🤖" * 65000):
            with self.subTest(character=text[0]):
                row = make_record("entity_projections", {"value": text}, "projection:oversize")
                store = WireStore()
                with self.assertRaises(ValueError):
                    store.put(row)
                with self.assertRaises(ValueError):
                    index_publisher._notion_write_request(row, store.data_source_id)
                self.assertEqual(store.posts, [])

    def test_every_semantic_child_preflighted_before_first_put(self):
        first = make_record("entity_projections", {"entity_id": "small"}, "projection:small")
        huge = make_record("entity_projections", {"entity_id": "large", "value": "🤖" * 65000}, "projection:large")
        logical = build_index([contact()], NOW, source_complete=True)[-1]
        store = WireStore([contact()])
        with patch.object(index_publisher, "build_index", return_value=[first, huge, logical]):
            with self.assertRaisesRegex(IndexPublicationError, "index_storage_capacity_exceeded"):
                publish_index(store, NOW)
        self.assertEqual(store.put_calls, [])
        self.assertEqual(store.posts, [])

    def test_page_and_root_capacity_preflight_prevents_all_prefix_writes(self):
        if not hasattr(index_publisher, "pack_manifest"):
            self.fail("Publisher does not yet pack a physical manifest")
        first = make_record("entity_projections", {"entity_id": "small"}, "projection:small")
        logical = next(row for row in build_index([contact()], NOW) if row["kind"] == "index_manifests")
        huge_page = make_record("index_changes", {"value": "🤖" * 65000}, "storage:huge", "manifest_page")
        huge_root = make_record("index_manifests", {"value": "🤖" * 65000}, "storage:root", "published")
        for pages, root in (([huge_page], logical), ([], huge_root)):
            with self.subTest(root=root["id"]):
                store = WireStore([contact()])
                with patch.object(index_publisher, "build_index", return_value=[first, logical]), \
                     patch.object(index_publisher, "pack_manifest", return_value=(pages, root)):
                    with self.assertRaisesRegex(IndexPublicationError, "index_storage_capacity_exceeded"):
                        publish_index(store, NOW)
                self.assertEqual(store.put_calls, [])
                self.assertEqual(store.posts, [])

    def test_wrapped_store_preflight_uses_actual_data_source_identity(self):
        if not hasattr(index_publisher, "_notion_write_request"):
            self.fail("Publisher has no pure wire-capacity preflight")
        store = WireStore([contact()], data_source_id="ffffffff-ffff-ffff-ffff-ffffffffffff")
        class Wrapper:
            def __init__(self, base):
                self.base = base
            def __getattr__(self, name):
                if name == "data_source_id":
                    raise AttributeError(name)
                return getattr(self.base, name)
        original = index_publisher._notion_write_request
        parents = []
        def inspect(row, parent):
            parents.append(parent)
            return original(row, parent)
        with patch.object(index_publisher, "_notion_write_request", side_effect=inspect):
            publish_index(Wrapper(Wrapper(store)), NOW)
        self.assertTrue(parents)
        self.assertEqual(set(parents), {store.data_source_id})

    def test_paged_previous_hydrated_before_clock_and_semantic_rebuild(self):
        store = self.reader_store()
        clock_observations = []
        def clock():
            clock_observations.append(set(store.get_calls))
            return NOW + timedelta(seconds=1)
        result = publish_index(store, NOW, source_complete=True, source_snapshot_hash=snapshot_hash(self.source),
                               publication_clock=clock)
        expected = build_index(self.source, NOW + timedelta(seconds=1), previous_manifest=self.logical,
                               source_complete=True)
        logical = next(row for row in expected if row["kind"] == "index_manifests")
        self.assertEqual(ManifestView(result["manifest"], store.get).materialize(), logical)
        self.assertEqual(len(clock_observations), 1)
        self.assertTrue({page["id"] for page in self.pages} <= clock_observations[0])

    def test_missing_previous_page_prevents_clock_and_all_puts(self):
        store = self.reader_store()
        del store.records[self.pages[0]["id"]]
        clock = unittest.mock.Mock(return_value=NOW + timedelta(seconds=1))
        with self.assertRaisesRegex(IndexPublicationError, "previous_index_unavailable"):
            publish_index(store, NOW, publication_clock=clock)
        clock.assert_not_called()
        self.assertEqual(store.put_calls, [])

    def test_paged_brief_and_selected_context_are_bounded_and_count_page_reads(self):
        store = self.reader_store()
        client = self.client(store)
        brief = client.get_campaign_brief()
        self.assertTrue(brief["data"]["health"]["complete"])
        self.assertEqual(brief["data"]["health"]["blocking_conditions"], [])
        self.assertEqual(brief["usage"]["store_calls"], 1)
        self.assertEqual(store.get_calls, [])
        for identity in ("contact:synthetic:0", "contact:synthetic:399"):
            before = len(store.get_calls)
            result = client.get_entity_context(identity, 1)
            actual_calls = len(store.get_calls) - before
            self.assertEqual(result["data"]["entity"]["entity_id"], identity)
            self.assertEqual(result["usage"]["store_calls"], actual_calls)
            self.assertLessEqual(actual_calls, 6)
            self.assertNotIn("Test Recruiter", canonical_json(result))
        self.assertEqual(store.put_calls, [])

    def test_ready_tasks_keep_first_40_window_without_full_task_map_scan(self):
        store = self.reader_store()
        result = self.client(store, "reader").get_ready_tasks(limit=20)
        self.assertEqual(result["data"]["tasks"], [])
        self.assertEqual(store.get_calls, self.logical["payload"]["tasks"][:40])
        self.assertEqual(result["usage"]["store_calls"], 41)
        selected = self.client(self.reader_store()).get_ready_tasks(limit=3)
        self.assertEqual(len(selected["data"]["tasks"]), 3)

    def test_paged_cursor_window_matches_inline_response_and_limits(self):
        paged = self.client(self.reader_store())
        inline = self.client(self.reader_store(inline=True))
        cursor = None
        for _ in range(4):
            actual = paged.get_recent_changes(cursor, limit=5)
            expected = inline.get_recent_changes(cursor, limit=5)
            self.assertLessEqual(actual["usage"]["store_calls"], 20)
            actual["data"].pop("manifest_id")
            expected["data"].pop("manifest_id")
            self.assertEqual(actual["data"], expected["data"])
            cursor = actual["data"]["next_cursor"]
        final = paged.get_recent_changes(self.logical["payload"]["source_cursor"], limit=5)
        self.assertEqual(final["data"]["changes"], [])
        with self.assertRaisesRegex(ProtocolError, "cursor_expired_refresh_brief"):
            paged.get_recent_changes("cursor:unknown", limit=5)
        with self.assertRaisesRegex(ProtocolError, "invalid_limit"):
            paged.get_recent_changes(limit=21)

    def test_paged_changes_retain_existing_context_budget(self):
        for inline in (False, True):
            with self.subTest(inline=inline):
                with self.assertRaisesRegex(ProtocolError, "context_budget_exceeded_select_smaller_scope"):
                    self.client(self.reader_store(inline=inline)).get_recent_changes(limit=20)

    def test_storage_page_cannot_masquerade_as_public_semantic_change(self):
        payload = deepcopy(self.logical["payload"])
        payload["changes"][0] = self.pages[0]["id"]
        malformed = make_record("index_manifests", payload, "logical:wrong-change-family", self.logical["status"])
        pages, root = pack_manifest(malformed)
        store = self.reader_store()
        store.records.update({row["id"]: row for row in [*pages, root]})
        with self.assertRaisesRegex(ProtocolError, "index_invalid"):
            self.client(store).get_recent_changes(limit=1)
        self.assertEqual(store.put_calls, [])

    def test_inline_changes_cannot_expose_storage_pages_after_format_shrinks(self):
        for layout in ("explicit_cursor", "legacy_string", "legacy_metadata"):
            with self.subTest(layout=layout):
                payload = deepcopy(self.logical["payload"])
                page = self.pages[0]
                payload["changes"][0] = page["id"]
                if layout != "explicit_cursor":
                    del payload["change_cursors"]
                if layout == "legacy_metadata":
                    payload["changes"][0] = {"record_id": page["id"], "cursor": "cursor:page",
                        "schema": page["payload"]["schema"], "status": page["status"]}
                root = make_record("index_manifests", payload, "logical:inline-wrong-page", self.logical["status"])
                store = self.reader_store()
                store.records[root["id"]] = root
                with self.assertRaisesRegex(ProtocolError, "index_invalid"):
                    self.client(store).get_recent_changes(limit=1)
                self.assertEqual(store.put_calls, [])

    def test_failed_storage_page_is_not_replayed_and_root_stays_unpublished(self):
        store = FreshStore(self.source)
        first_page = len(self.semantic)  # semantic children occupy len - 1
        store.fail_put_at = first_page
        with self.assertRaisesRegex(IndexPublicationError, "index_write_uncertain_stop_no_retry"):
            publish_index(store, NOW, source_complete=True, source_snapshot_hash=snapshot_hash(self.source))
        self.assertEqual(len(store.put_calls), first_page)
        self.assertEqual(store.put_calls[-1], self.pages[0]["id"])
        self.assertEqual(store.put_calls.count(self.pages[0]["id"]), 1)
        self.assertIsNone(store.latest_index_manifest())
        self.assertNotIn(self.root["id"], store.records)

    def test_fresh_intake_does_not_reuse_warm_missing_or_tampered_page(self):
        for mode in ("missing", "tampered"):
            with self.subTest(mode=mode):
                store = self.reader_store()
                client = self.client(store)
                identity = "contact:synthetic:0"
                context = client.get_entity_context(identity, 1)["data"]
                action = {"type": "research", "entity_id": identity, "idempotency_key": "fresh-" + mode,
                          "expected_manifest_id": context["manifest_id"],
                          "expected_projection_hash": context["projection_hash"]}
                evidence = context["entity"]["evidence_refs"]
                envelope = client.propose_action(action, evidence)["data"]["envelope"]
                selected = next(page_id for page_id in store.get_calls
                                if page_id.startswith("index_manifest_page:")
                                and store.records[page_id]["payload"].get("section") == "projections")
                if mode == "missing":
                    del store.records[selected]
                else:
                    store.records[selected]["hash"] = "0" * 64
                with self.assertRaisesRegex(ProtocolError, "authoritative_index_unavailable_or_conflicted"):
                    client.propose_action(action, evidence)
                with self.assertRaises(DispatcherStorageError):
                    dispatch(store, envelope)
                self.assertEqual(store.put_calls, [])

    def test_task_mapping_supports_selected_evidence_and_grants(self):
        store = self.reader_store()
        task_id, task_record_id = list(self.logical["payload"]["task_map"].items())[-1]
        task = store.records[task_record_id]["payload"]
        evidence = task["evidence_refs"][:1]
        self.assertTrue(evidence)
        self.assertNotIn(task_record_id, self.logical["payload"]["tasks"][:40])
        result = self.client(store).get_entity_context(task["entity_id"], 3,
                                                     task_id=task_id, evidence_ids=evidence)
        self.assertEqual(result["data"]["task_id"], task_id)
        self.assertEqual([row["id"] for row in result["data"]["evidence"]], evidence)
        self.assertLessEqual(result["usage"]["store_calls"], 12)
        with self.assertRaisesRegex(ProtocolError, "source_evidence_permission_denied"):
            self.client(self.reader_store(), "reader").get_entity_context(task["entity_id"], 3,
                                                                         task_id=task_id, evidence_ids=evidence)

    def test_direct_queued_ack_fallback_survives_paged_manifest(self):
        store = self.reader_store()
        ack = make_record("agent_action_results", {"action_id": "action:unpublished", "external_effect": False},
                          "action-result:action:unpublished", "queued")
        store.records[ack["id"]] = ack
        result = self.client(store).get_action_result("action:unpublished")
        self.assertEqual(result["data"]["status"], "queued")
        self.assertEqual(result["data"]["record_id"], ack["id"])

    def test_paged_missing_projection_has_no_legacy_fixed_id_fallback(self):
        store = self.reader_store()
        fallback = make_record("entity_projections", {"entity_id": "contact:unpublished"},
                               "projection:contact:unpublished")
        store.records[fallback["id"]] = fallback
        with self.assertRaisesRegex(ProtocolError, "entity_projection_missing"):
            self.client(store).get_entity_context("contact:unpublished", 1)
        self.assertNotIn(fallback["id"], store.get_calls)

    def test_unknown_root_schema_stops_with_fixed_storage_error(self):
        store = self.reader_store()
        root = make_record("index_manifests", {"schema": "agent-index-manifest/unknown",
                           "generated_at": NOW.isoformat(), "complete": True, "conflicts": []},
                           "index:unknown", "published")
        store.records[root["id"]] = root
        with self.assertRaisesRegex(ProtocolError, "authoritative_index_unavailable_or_conflicted"):
            self.client(store).get_campaign_brief()
        self.assertEqual(store.put_calls, [])


if __name__ == "__main__":
    unittest.main()
