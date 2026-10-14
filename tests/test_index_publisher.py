"""Manifest-last publication through one immutable writer, with fault injection."""
from copy import deepcopy
from datetime import timedelta
import unittest

from cloud.index_publisher import IndexPublicationError, publish_index, snapshot_hash
from cloud.orchestrator import ShadowStore
from cloud.records import make_record
from tests.test_agent_index import NOW, app, contact, event, job, receipt


class FreshStore:
    """Notion's cloud-only query(None), plus its separate legacy event query."""
    def __init__(self, sources=(), legacy=()):
        self.records = {row["id"]: deepcopy(row) for row in sources}
        self.legacy = {row["id"]: deepcopy(row) for row in legacy}
        self.query_calls, self.get_calls, self.put_calls = [], [], []
        self.fail_query, self.fail_put_at, self.bad_readback_at = None, None, None
        self.failed_read_id = None
        self.latest_calls = 0

    def query(self, kind=None):
        self.query_calls.append(kind)
        if kind == self.fail_query and self.fail_query is not None:
            raise RuntimeError("private network diagnostic must not leak")
        cloud = [dict(row, notion_page_id="page:" + row["id"]) for row in self.records.values() if kind is None or row["kind"] == kind]
        return cloud + list(self.legacy.values()) if kind == "outreach_events" else cloud

    def get(self, identity):
        self.get_calls.append(identity)
        if identity == self.failed_read_id:
            return None
        return self.records.get(identity) or self.legacy.get(identity)

    def put(self, row):
        self.put_calls.append(row["id"])
        self.records[row["id"]] = deepcopy(row)
        if len(self.put_calls) == self.fail_put_at:
            raise RuntimeError("private write diagnostic must not leak")
        if len(self.put_calls) == self.bad_readback_at:
            self.failed_read_id = row["id"]
        return dict(row, notion_page_id="page:" + row["id"])

    def latest_index_manifest(self):
        self.latest_calls += 1
        return next((row for row in reversed(list(self.records.values())) if row["kind"] == "index_manifests"), None)


class IndexPublisherTests(unittest.TestCase):
    def test_default_shadow_publication_is_incomplete_with_no_ready_actions(self):
        store = ShadowStore()
        store.put(contact())
        result = publish_index(store, NOW)
        self.assertFalse(result["complete"])
        self.assertEqual(result["manifest"]["payload"]["campaign_brief"]["ready_tasks"], 0)
        self.assertTrue(all(row["status"] == "blocked" for row in store.query(kind="agent_tasks")))
        self.assertIsNotNone(store.get(result["manifest_id"]))

    def test_true_flag_without_actual_snapshot_evidence_remains_incomplete(self):
        store = FreshStore([contact()])
        result = publish_index(store, NOW, source_complete=True)
        self.assertFalse(result["complete"])
        self.assertTrue(result["manifest"]["payload"]["conflicts"])

    def test_matching_reconciliation_hash_allows_complete_manifest(self):
        source = [contact(), job(), app(receipt=receipt())]
        store = FreshStore(source)
        result = publish_index(store, NOW, source_complete=True, source_snapshot_hash=snapshot_hash(source))
        self.assertTrue(result["complete"])
        self.assertEqual(result["source_snapshot_hash"], snapshot_hash(source))
        self.assertTrue(store.put_calls[-1].startswith("index_manifest:"))
        self.assertEqual(store.put_calls, store.get_calls)

    def test_snapshot_envelope_cannot_override_source_complete_argument(self):
        source = [contact()]
        result = publish_index(FreshStore(source), NOW, source_snapshot_hash={"hash": snapshot_hash(source), "complete": True, "conflicts": []})
        self.assertFalse(result["complete"])

    def test_changed_snapshot_or_conflict_evidence_never_yields_ready_tasks(self):
        source = [contact()]
        for evidence in ("old-snapshot", {"hash": snapshot_hash(source), "complete": True, "conflicts": ["reconciliation_pending"]}):
            result = publish_index(FreshStore(source), NOW, source_complete=True, source_snapshot_hash=evidence)
            self.assertFalse(result["complete"])
            self.assertEqual(result["manifest"]["payload"]["campaign_brief"]["ready_tasks"], 0)

    def test_empty_snapshot_does_not_claim_history_reconciliation(self):
        result = publish_index(FreshStore(), NOW, source_complete=True, source_snapshot_hash=snapshot_hash([]))
        self.assertFalse(result["complete"])
        self.assertTrue(result["manifest"]["payload"]["conflicts"])

    def test_explicit_fresh_legacy_query_preserves_historical_evidence(self):
        legacy = event("invitation_pending_confirmed", "legacy-pending", kind="outreach_events")
        source = [contact()]
        store = FreshStore(source, [legacy])
        result = publish_index(store, NOW, source_complete=True, source_snapshot_hash=snapshot_hash(source + [legacy]))
        self.assertEqual(store.query_calls, [None, "outreach_events"])
        projection_id = result["manifest"]["payload"]["projections"]["contact:one"]
        self.assertEqual(store.get(projection_id)["payload"]["state"], "pending")
        self.assertIn(legacy["id"], store.get(projection_id)["payload"]["history_ids"])

    def test_duplicate_cloud_legacy_core_ignores_notion_presentation_metadata(self):
        shared = event("invitation_pending_confirmed", "shared", kind="outreach_events")
        store = FreshStore([contact(), shared], [dict(shared, notion_page_id="legacy-page")])
        result = publish_index(store, NOW)
        self.assertEqual(result["source_snapshot_hash"], snapshot_hash([contact(), shared]))

    def test_core_collision_stops_before_any_write(self):
        first = event("invitation_pending_confirmed", "shared", kind="outreach_events")
        payload = deepcopy(first["payload"])
        payload["actor"] = "another-agent"
        conflicting = make_record("outreach_events", payload, first["id"], first["status"])
        store = FreshStore([contact(), first], [conflicting])
        with self.assertRaisesRegex(IndexPublicationError, "authoritative_source_identity_conflict"):
            publish_index(store, NOW)
        self.assertEqual(store.put_calls, [])

    def test_invalid_source_hash_stops_before_any_write(self):
        invalid = dict(contact(), hash="bad")
        store = FreshStore([invalid])
        with self.assertRaisesRegex(IndexPublicationError, "authoritative_source_invalid"):
            publish_index(store, NOW)
        self.assertEqual(store.put_calls, [])

    def test_failed_source_query_stops_before_any_write(self):
        store = FreshStore([contact()])
        store.fail_query = "outreach_events"
        with self.assertRaisesRegex(IndexPublicationError, "authoritative_snapshot_unavailable") as caught:
            publish_index(store, NOW)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(store.put_calls, [])

    def test_conflicting_application_revisions_publish_only_incomplete_index(self):
        first = app(status="prepared")
        second = make_record("applications", first["payload"], "different-revision-record", "qualified")
        source = [first, second]
        result = publish_index(FreshStore(source), NOW, source_complete=True, source_snapshot_hash=snapshot_hash(source))
        self.assertFalse(result["complete"])
        self.assertTrue(result["manifest"]["payload"]["conflicts"])

    def test_partial_child_write_keeps_old_manifest_visible_and_stops(self):
        store = FreshStore([contact()])
        old = publish_index(store, NOW - timedelta(minutes=1))
        store.records["contact:one:r2"] = contact(revision=2)
        store.put_calls.clear()
        store.fail_put_at = 2
        with self.assertRaisesRegex(IndexPublicationError, "index_write_uncertain_stop_no_retry"):
            publish_index(store, NOW)
        self.assertEqual(len(store.put_calls), 2)
        self.assertEqual(store.latest_index_manifest()["id"], old["manifest_id"])
        self.assertFalse(any(identity.startswith("index_manifest:") for identity in store.put_calls))

    def test_failed_child_readback_keeps_old_manifest_visible(self):
        store = FreshStore([contact()])
        old = publish_index(store, NOW - timedelta(minutes=1))
        store.put_calls.clear()
        store.bad_readback_at = 1
        with self.assertRaisesRegex(IndexPublicationError, "index_readback_mismatch_stop_no_retry"):
            publish_index(store, NOW)
        self.assertEqual(len(store.put_calls), 1)
        self.assertEqual(store.latest_index_manifest()["id"], old["manifest_id"])

    def test_exact_core_put_result_is_required_before_any_later_write(self):
        class BadPut(FreshStore):
            def put(self, row):
                super().put(row)
                return dict(row, status="different")
        store = BadPut([contact()])
        with self.assertRaisesRegex(IndexPublicationError, "index_write_readback_mismatch_stop_no_retry"):
            publish_index(store, NOW)
        self.assertEqual(len(store.put_calls), 1)
        self.assertIsNone(store.latest_index_manifest())

    def test_all_manifest_references_exist_before_manifest_put(self):
        class VisibilityStore(FreshStore):
            def put(self, row):
                if row["kind"] == "index_manifests":
                    references = [*row["payload"]["projections"].values(), *row["payload"]["tasks"], *row["payload"]["changes"]]
                    if not all(reference in self.records for reference in references):
                        raise AssertionError("Manifest was published before its children")
                return super().put(row)
        result = publish_index(VisibilityStore([contact(), app(receipt=receipt())]), NOW)
        self.assertTrue(result["manifest_id"].startswith("index_manifest:"))

    def test_replay_preserves_semantic_ids_and_hashes_without_overwrites(self):
        source = [contact(), app(receipt=receipt())]
        store = FreshStore(source)
        evidence = snapshot_hash(source)
        first = publish_index(store, NOW, source_complete=True, source_snapshot_hash=evidence)
        existing = deepcopy(store.records)
        second = publish_index(store, NOW + timedelta(minutes=1), source_complete=True, source_snapshot_hash=evidence)
        self.assertEqual(first["manifest"]["payload"]["projections"], second["manifest"]["payload"]["projections"])
        self.assertEqual(first["manifest"]["payload"]["tasks"], second["manifest"]["payload"]["tasks"])
        self.assertEqual(second["manifest"]["payload"]["changes"], [])
        self.assertEqual(first["source_snapshot_hash"], second["source_snapshot_hash"])
        self.assertTrue(all(store.records[identity] == row for identity, row in existing.items()))

    def test_fresh_read_uses_new_observations_without_cached_action_state(self):
        store = FreshStore([contact()])
        first = publish_index(store, NOW)
        reply = event("recruiter_reply_confirmed", "new-reply")
        store.records[reply["id"]] = reply
        second = publish_index(store, NOW + timedelta(minutes=1))
        first_id = first["manifest"]["payload"]["projections"]["contact:one"]
        second_id = second["manifest"]["payload"]["projections"]["contact:one"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(store.get(second_id)["payload"]["state"], "reply_received")
        self.assertEqual(store.query_calls, [None, "outreach_events", None, "outreach_events"])

    def test_snapshot_hash_rejects_conflicting_duplicate_core(self):
        with self.assertRaisesRegex(IndexPublicationError, "authoritative_source_identity_conflict"):
            snapshot_hash([contact(), contact(name="Other")])


if __name__ == "__main__":
    unittest.main()
