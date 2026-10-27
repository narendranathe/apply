"""Bounded physical manifests preserve the complete legacy logical contract."""
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import timedelta
from functools import lru_cache
import hashlib
from pathlib import Path
import random
import unittest

from cloud.agent_index import build_index
from cloud.index_manifest import (
    FANOUT, INLINE_MAX_BYTES, PAGE_MAX_BYTES, PAGE_KIND, PAGE_SCHEMA, PAGE_STATUS,
    PAGED_SCHEMA, ManifestError, ManifestView, pack_manifest,
)
from cloud.records import canonical_json, content_hash, make_record, stable_id, validate_record
from tests.test_agent_index import NOW, contact


@lru_cache(maxsize=6)
def logical_fixture(count=334):
    sources = [contact("contact:synthetic:" + str(i).zfill(5)) for i in range(count)]
    return next(row for row in build_index(sources, NOW, source_complete=True)
                if row["kind"] == "index_manifests")


class Reader:
    def __init__(self, rows):
        self.rows = {row["id"]: deepcopy(row) for row in rows}
        self.calls = []

    def get(self, identity):
        self.calls.append(identity)
        return deepcopy(self.rows.get(identity))

    def latest_index_manifest(self):
        return next(row for row in self.rows.values() if row["kind"] == "index_manifests")


def rehashed_root(payload):
    return make_record("index_manifests", payload,
        stable_id("index_manifest_root", content_hash(payload)), "published")


def replace_section_page(root, pages, section, mutate):
    """Attack a committed page with valid envelopes, not just broken hashes."""
    payload = deepcopy(root["payload"])
    ref = payload["sections"][section]
    records = {page["id"]: deepcopy(page) for page in pages}
    node = deepcopy(records[ref["id"]]["payload"])
    mutate(node)
    changed = make_record(PAGE_KIND, node,
        stable_id("index_manifest_page", content_hash(node)), PAGE_STATUS)
    ref.update(id=changed["id"], hash=changed["hash"], count=node["count"], depth=node["depth"])
    records[changed["id"]] = changed
    return rehashed_root(payload), Reader(records.values())


class ManifestTests(unittest.TestCase):
    def packed(self, count=334):
        logical = logical_fixture(count)
        pages, root = pack_manifest(logical)
        return logical, pages, root, Reader([*pages, root])

    def test_small_inline_is_unchanged_and_reads_no_pages(self):
        logical = logical_fixture(1)
        pages, root = pack_manifest(logical)
        self.assertEqual(pages, [])
        self.assertEqual(root, logical)
        reader = Reader([root])
        view = ManifestView(root, reader.get)
        self.assertIsInstance(view, Mapping)
        self.assertNotIsInstance(view, dict)
        self.assertIs(view.payload, view)
        self.assertEqual(view.materialize(), logical)
        self.assertEqual(reader.calls, [])

    def test_large_actual_builder_shape_round_trips_every_field(self):
        logical, pages, root, reader = self.packed()
        self.assertGreater(len(canonical_json(logical).encode()), INLINE_MAX_BYTES)
        self.assertTrue(pages)
        self.assertEqual(root["payload"]["schema"], PAGED_SCHEMA)
        self.assertNotIn("complete", root["payload"])
        self.assertNotEqual(root["id"], logical["id"])
        self.assertLessEqual(len(canonical_json(root).encode()), INLINE_MAX_BYTES)
        for page in pages:
            self.assertEqual((page["kind"], page["status"]), (PAGE_KIND, PAGE_STATUS))
            self.assertEqual(page["payload"]["schema"], PAGE_SCHEMA)
            self.assertLessEqual(len(canonical_json(page).encode()), PAGE_MAX_BYTES)
            self.assertNotIn(page["id"], logical["payload"]["changes"])
        view = ManifestView(root, reader.get)
        self.assertEqual(view.record, root)
        self.assertEqual(view.header["complete"], logical["payload"]["complete"])
        self.assertEqual(view.materialize(), logical)
        self.assertEqual(validate_record(view.materialize()), logical)
        self.assertEqual(validate_record(view.record), root)
        with self.assertRaises(ValueError):
            validate_record(view)
        self.assertEqual(set(view), set(logical["payload"]))

    def test_map_and_sequence_views_are_lazy_and_accounted(self):
        logical, pages, root, reader = self.packed(2048)
        view = ManifestView(root, reader.get)
        self.assertIsInstance(view["projections"], Mapping)
        self.assertIsInstance(view["changes"], Sequence)
        self.assertEqual(reader.calls, [])
        key = sorted(logical["payload"]["projections"])[-1]
        self.assertEqual(view.lookup("projections", key), logical["payload"]["projections"][key])
        self.assertLessEqual(len(reader.calls), 6)
        self.assertLess(len(reader.calls), len(pages))
        before = len(reader.calls)
        self.assertIsNone(view.lookup("projections", "contact:absent"))
        self.assertLessEqual(len(reader.calls) - before, 6)
        self.assertEqual(view.window("changes", 15, 20), logical["payload"]["changes"][15:35])
        self.assertEqual(view["changes"][19:22], logical["payload"]["changes"][19:22])

    def test_all_change_windows_keep_order_and_cursor_semantics(self):
        logical, pages, root, reader = self.packed()
        view = ManifestView(root, reader.get)
        cursor = None
        observed = []
        while True:
            batch = view.change_window(cursor, 20)
            observed.extend(batch["changes"])
            cursor = batch["next_cursor"]
            if not batch["has_more"]:
                break
        self.assertEqual(observed, logical["payload"]["changes"])
        self.assertEqual(cursor, logical["payload"]["source_cursor"])
        self.assertEqual(view.change_window(cursor, 20)["changes"], [])
        with self.assertRaisesRegex(ManifestError, "cursor_expired_refresh_brief"):
            view.change_window("cursor:absent", 20)

    def test_cursor_lookup_does_not_scan_all_sequence_pages(self):
        logical, pages, root, reader = self.packed(2048)
        view = ManifestView(root, reader.get)
        at = len(logical["payload"]["changes"]) // 2
        batch = view.change_window(logical["payload"]["change_cursors"][at], 20)
        self.assertEqual(batch["changes"], logical["payload"]["changes"][at + 1:at + 21])
        self.assertLessEqual(len(reader.calls), 14)
        self.assertLess(len(reader.calls), len(pages))

    def test_timestamp_only_publication_reuses_all_pages(self):
        logical, pages, root, _ = self.packed()
        payload = deepcopy(logical["payload"])
        payload["generated_at"] = (NOW + timedelta(minutes=1)).isoformat()
        changed = make_record("index_manifests", payload, "logical:later", logical["status"])
        later_pages, later_root = pack_manifest(changed)
        self.assertEqual(pages, later_pages)
        self.assertNotEqual(root["id"], later_root["id"])

    def test_unicode_and_escaped_values_are_lossless_and_bounded(self):
        logical = deepcopy(logical_fixture(334))
        payload = deepcopy(logical["payload"])
        payload["projections"]["contact:unicode"] = "資料\"\\\n" * 90
        logical = make_record("index_manifests", payload, "logical:unicode", logical["status"])
        pages, root = pack_manifest(logical)
        self.assertEqual(ManifestView(root, Reader(pages).get).materialize(), logical)
        self.assertTrue(all(len(canonical_json(row).encode()) <= PAGE_MAX_BYTES for row in pages))

    def test_missing_and_tampered_page_fail_without_source_error_text(self):
        logical, pages, root, _ = self.packed()
        ref = root["payload"]["sections"]["projections"]
        for kind in ("absent", "changed", "exception"):
            with self.subTest(kind=kind):
                reader = Reader(pages)
                if kind == "absent":
                    del reader.rows[ref["id"]]
                elif kind == "changed":
                    reader.rows[ref["id"]]["payload"]["count"] += 1
                else:
                    def unavailable(_):
                        raise RuntimeError("PRIVATE TOKEN AND SOURCE BODY")
                    reader.get = unavailable
                with self.assertRaises(ManifestError) as caught:
                    ManifestView(root, reader.get).materialize()
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertNotIn("TOKEN", str(caught.exception))

    def test_invalid_scope_limits_and_unknown_formats_fail_closed(self):
        _, pages, root, reader = self.packed()
        view = ManifestView(root, reader.get)
        for offset, limit in ((-1, 1), (0, 41), (0, True)):
            with self.subTest(offset=offset, limit=limit):
                with self.assertRaises(ManifestError):
                    view.window("changes", offset, limit)
        with self.assertRaises(ManifestError):
            view.lookup("not-a-section", "key")
        bad = make_record("index_manifests", {"schema": "unknown-future-layout"}, "unknown")
        with self.assertRaises(ManifestError):
            ManifestView(bad, reader.get)

    def test_schema_less_legacy_fixture_remains_exact(self):
        from tests.test_agent_protocol import fixture
        _, legacy = fixture()
        view = ManifestView(legacy, lambda _: self.fail("inline needs no page read"))
        self.assertEqual(view.materialize(), legacy)
        self.assertEqual(view["task_map"], legacy["payload"]["task_map"])

    def test_unmodified_old_reader_cannot_gain_new_root_authority(self):
        _, _, root, _ = self.packed()
        # Frozen v1 reader's authority predicates from AgentProtocol._health and
        # get_ready_tasks, before paged support. Keep this compatibility proof
        # independent of the reader being upgraded in the integration change.
        payload = root["payload"]
        complete = payload.get("complete") is True
        blockers = [] if complete else ["index_incomplete"]
        tasks = payload.get("tasks", []) if not blockers else []
        self.assertFalse(complete)
        self.assertIn("index_incomplete", blockers)
        self.assertEqual(tasks, [])

    def test_rehashed_branch_structure_attacks_are_rejected(self):
        _, pages, root, _ = self.packed()

        def duplicate(node):
            node["children"][1] = deepcopy(node["children"][0])
            node["count"] = sum(child["count"] for child in node["children"])

        def overlap(node):
            node["children"][1]["first"] = node["children"][0]["last"]

        def wrong_depth(node):
            node["children"][0]["depth"] = node["depth"]

        def excess_fanout(node):
            node["children"] = node["children"] * (FANOUT + 1)
            node["count"] = sum(child["count"] for child in node["children"])

        mutations = [duplicate, overlap, wrong_depth, excess_fanout,
            lambda node: node["children"].reverse(),
            lambda node: node.update(count=node["count"] + 1),
            lambda node: node.update(schema="unsupported-page"),
            lambda node: node["children"][0].update(count=-1)]
        for mutate in mutations:
            with self.subTest(mutation=mutate.__name__):
                changed, reader = replace_section_page(root, pages, "projections", mutate)
                with self.assertRaisesRegex(ManifestError, "manifest_page_invalid"):
                    ManifestView(changed, reader.get).materialize()

    def test_rehashed_leaf_order_and_capacity_attacks_are_rejected(self):
        logical = deepcopy(logical_fixture(334))
        payload = deepcopy(logical["payload"])
        # This map is one leaf while other sections force physical paging.
        payload["action_results"] = {"a": "ack:one", "b": "ack:two"}
        logical = make_record("index_manifests", payload, "logical:leaf", "complete")
        pages, root = pack_manifest(logical)

        def duplicate_key(node):
            node["entries"][1][0] = node["entries"][0][0]

        def oversized(node):
            node["entries"][0][1] = "PRIVATE" * PAGE_MAX_BYTES

        for mutate in (duplicate_key, oversized, lambda node: node["entries"].reverse(),
                       lambda node: node.update(depth=1)):
            with self.subTest(mutation=mutate.__name__):
                changed, reader = replace_section_page(root, pages, "action_results", mutate)
                with self.assertRaisesRegex(ManifestError, "manifest_page_invalid"):
                    ManifestView(changed, reader.get).materialize()

    def test_root_format_depth_and_hash_attacks_are_rejected(self):
        _, pages, root, _ = self.packed()
        mutations = [lambda payload: payload.update(complete=True),
            lambda payload: payload["sections"]["projections"].update(depth=99),
            lambda payload: payload["logical_sections"].append("projections"),
            lambda payload: payload["sections"]["action_results"].update(id="not-empty"),
            lambda payload: payload["header"].update(tasks=["task"] * 41),
            lambda payload: payload["header"].update(extra="x" * INLINE_MAX_BYTES)]
        for mutate in mutations:
            with self.subTest(mutation=mutate.__name__):
                payload = deepcopy(root["payload"])
                mutate(payload)
                with self.assertRaises(ManifestError):
                    ManifestView(rehashed_root(payload), Reader(pages).get)
        payload = deepcopy(root["payload"])
        payload["logical_hash"] = "0" * 64
        with self.assertRaisesRegex(ManifestError, "manifest_invalid"):
            ManifestView(rehashed_root(payload), Reader(pages).get).materialize()

    def test_indivisible_oversized_value_or_header_fails_before_return(self):
        for section in ("action_results", "campaign_brief"):
            with self.subTest(section=section):
                payload = deepcopy(logical_fixture(334)["payload"])
                payload[section] = {"oversize": "PRIVATE" * INLINE_MAX_BYTES}
                logical = make_record("index_manifests", payload, "logical:too-big", "complete")
                with self.assertRaisesRegex(ManifestError, "manifest_capacity_exceeded"):
                    pack_manifest(logical)

    def test_derived_cursor_map_is_verified_against_selected_sequence(self):
        logical, pages, root, _ = self.packed()

        positions = root["payload"]["sections"]["change_positions"]
        rows = {row["id"]: deepcopy(row) for row in pages}
        cursor = logical["payload"]["change_cursors"][0]

        def corrupt(ref):
            node = deepcopy(rows[ref["id"]]["payload"])
            if node["node"] == "map_leaf":
                target = next(pair for pair in node["entries"] if pair[0] == cursor)
                target[1] = 2
            else:
                index = next(i for i, child in enumerate(node["children"])
                             if child["first"] <= cursor <= child["last"])
                node["children"][index] = corrupt(node["children"][index])
            row = make_record(PAGE_KIND, node,
                stable_id("index_manifest_page", content_hash(node)), PAGE_STATUS)
            rows[row["id"]] = row
            changed = deepcopy(ref)
            changed.update(id=row["id"], hash=row["hash"])
            return changed

        payload = deepcopy(root["payload"])
        payload["sections"]["change_positions"] = corrupt(positions)
        view = ManifestView(rehashed_root(payload), Reader(rows.values()).get)
        with self.assertRaisesRegex(ManifestError, "manifest_page_invalid"):
            view.change_window(cursor, 20)
        with self.assertRaisesRegex(ManifestError, "manifest_page_invalid"):
            view.materialize()

    def test_sequence_repeated_content_is_preserved_without_false_cycle(self):
        payload = deepcopy(logical_fixture(334)["payload"])
        payload["conflicts"] = ["same-conflict"] * 8000
        logical = make_record("index_manifests", payload, "logical:repetition", "complete")
        pages, root = pack_manifest(logical)
        view = ManifestView(root, Reader(pages).get)
        self.assertEqual(view.materialize(), logical)
        self.assertEqual(view.window("conflicts", 100, 20), ["same-conflict"] * 20)

    def test_reader_values_cannot_mutate_cached_authority(self):
        logical, pages, root, reader = self.packed()
        view = ManifestView(root, reader.get)
        raw, header = view.record, view.header
        raw["payload"]["header"]["complete"] = False
        header["complete"] = False
        window = view.window("changes", 0, 20)
        window.clear()
        self.assertEqual(view.materialize(), logical)
        self.assertTrue(view["complete"])

    def test_input_order_does_not_change_content_addressed_storage(self):
        logical, pages, root, _ = self.packed()
        rng = random.Random(20261026)
        payload = deepcopy(logical["payload"])
        for section in ("projections", "entity_versions", "task_map", "action_results"):
            items = list(payload[section].items())
            rng.shuffle(items)
            payload[section] = dict(items)
        reordered = make_record("index_manifests", payload, logical["id"], logical["status"])
        later_pages, later_root = pack_manifest(reordered)
        self.assertEqual(later_pages, pages)
        self.assertEqual(later_root, root)

    def test_incomplete_and_conflicted_logical_authority_is_preserved(self):
        payload = deepcopy(logical_fixture(334)["payload"])
        payload.update(complete=False, conflicts=[{"reason": "synthetic-unresolved-state"}])
        payload["campaign_brief"]["complete"] = False
        logical = make_record("index_manifests", payload, "logical:incomplete", "incomplete")
        pages, root = pack_manifest(logical)
        view = ManifestView(root, Reader(pages).get)
        self.assertFalse(view["complete"])
        self.assertEqual(view.window("conflicts", 0, 1), payload["conflicts"])
        self.assertEqual(view.materialize(), logical)

    def test_map_branch_cannot_reference_its_own_old_parent_as_a_child(self):
        _, pages, root, _ = self.packed()
        old_ref = deepcopy(root["payload"]["sections"]["projections"])

        def self_reference(node):
            child = deepcopy(old_ref)
            child["depth"] = node["depth"] - 1
            node["children"] = [child]
            node["count"] = child["count"]

        changed, reader = replace_section_page(root, pages, "projections", self_reference)
        # A true content-addressed self-cycle would additionally require a SHA
        # fixed point. This plausible attempted parent reference fails the
        # descriptor/depth checks before it could recurse without a bound.
        with self.assertRaisesRegex(ManifestError, "manifest_page_invalid"):
            ManifestView(changed, reader.get).materialize()

    def test_frozen_semantic_builder_hash_is_unchanged(self):
        path = Path(__file__).resolve().parents[1] / "cloud/agent_index.py"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
            "7a63ce93fb1fdeee5c1df41bee9e3fd4c4ad07bec9b36a9bbd9dabfafe9bf6d5")


if __name__ == "__main__":
    unittest.main()
