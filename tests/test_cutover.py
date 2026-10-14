import copy
import hashlib
import json
import unittest

from cloud.cutover import (LOCAL_ACTORS, REQUIRED_FLAGS, build_source_delta, make_cutover_record,
                           make_readiness_record, validate_cutover_record, validate_import)
from cloud.migration import SCHEMA, combine_exports, event_record
from cloud.notion import _managed_hash
from cloud.records import content_hash, display_title, make_record


def prop(value, kind="rich_text"):
    if kind == "select":
        return {"type": kind, kind: {"name": value}}
    if kind == "checkbox":
        return {"type": kind, kind: value}
    return {"type": kind, kind: [{"type": "text", "text": {"content": value}}]}


def event(identity="old", content="Exact source content", **extra):
    return {"event_id": identity, "content": content, "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "actor": "Historical import", "kind": "Historical record", "occurred_at": "2026-10-12",
            "delivery_state": "Historical observation", "evidence": "Source only", **extra}


def event_page(value):
    page = {"id": "page-" + value["event_id"], "object": "page", "url": "https://app.notion.com/p/abc",
            "properties": {"Event ID": prop(value["event_id"]), "Content Hash": prop(value["content_hash"]),
                           "Payload JSON": prop("payload-v1"), "Delivery State": prop("Delivery unknown", "select")}}
    block = {"id": "block-" + value["event_id"], "type": "code", "has_children": False,
             "code": {"rich_text": [{"type": "text", "text": {"content": json.dumps(value)}}]}}
    return {"page": page, "blocks": [block]}


def cloud_page(row):
    props = {name: prop(value) for name, value in {
        "Cloud ID": row["id"], "Cloud Kind": row["kind"], "Cloud Hash": row["hash"],
        "Cloud Status": row["status"], "Cloud Envelope": json.dumps(row)}.items()}
    props.update({"Name": prop(display_title(row), "title"), "Section": prop("Cloud", "select"),
                  "Type": prop("Reference", "select"), "Status": prop("Done", "select")})
    props["Cloud Managed Hash"] = prop(_managed_hash(props, []))
    return {"page": {"id": "cloud-page", "properties": props, "url": "https://app.notion.com/p/def"}, "blocks": []}


def snapshot(events):
    return {"schema": "notion-data-source-snapshot/v1", "complete": True, "data_source_id": "abc123",
            "data_source": {"id": "abc123", "properties": {}}, "captured_at": "2026-10-12T23:00:00Z",
            "pages": [event_page(value) for value in events]}


def reference(source):
    return {"archive": "migration-archives/final.aesgcm", "member": "source/native-snapshot.json",
            "member_sha256": hashlib.sha256(json.dumps(source).encode()).hexdigest(), "snapshot_sha256": content_hash(source)}


def export(rows):
    return {"schema": SCHEMA, "records": rows, "blocked": [], "source_manifest": []}


def evidence():
    commit = "a" * 40
    url = "https://github.com/narendranathe/apply/actions/runs/12345"
    manifest = "b" * 64
    return {**{flag: True for flag in REQUIRED_FLAGS}, "commit": commit, "observed_at": "2026-10-12T23:00:00Z",
            "historical_manifest_sha256": manifest, "evidence_urls": [url],
            "history": {"historical_manifest_sha256": manifest, "evidence_url": url,
                        "readback": {"records": 4, "families": {"outreach_events": 3, "audit_events": 1},
                                     "unchanged": 4, "conflicted": 0, "blocked": 0}},
            "archive": {"restore_verified": True, "hashes_verified": True, "archive_sha256": "c" * 64,
                        "raw_snapshot_sha256": "d" * 64, "historical_manifest_sha256": manifest, "evidence_url": url},
            "local_actors": {"readbacks": [{"id": identity, "status": "PAUSED", "persisted": True,
                                            "readback_sha256": "e" * 64, "evidence_url": url} for identity in sorted(LOCAL_ACTORS)],
                             "application_writer": {"actor": "Milo", "stopped": True, "source": "direct_user",
                                                    "confirmation_sha256": "f" * 64, "evidence_url": url}},
            "shadow": {"hosted": True, "dry_run": True, "conclusion": "success", "commit": commit,
                       "lanes": ["accepted", "first_touch", "applications"], "evidence_url": url}}


class ReadStore:
    def __init__(self, rows):
        self.rows = {row["id"]: copy.deepcopy(row) for row in rows}
        self.reads = []

    def get(self, identity):
        self.reads.append(identity)
        return copy.deepcopy(self.rows.get(identity))

    def put(self, row):
        raise AssertionError("cutover validation must never write")


class SourceDeltaTests(unittest.TestCase):
    def test_three_new_events_and_one_audit_do_not_replace_source_contacts(self):
        old = event()
        events = [old, event("new-1"), event("new-2"), event("new-3")]
        contact = make_record("contacts", {"source_notion_id": "contact", "suppressed": True}, "contact:old", "suppressed")
        baseline = export([event_record(old), contact])
        source = snapshot(events)
        source["pages"].append({"page": {"id": "contact", "properties": {"Type": prop("Contact", "select"),
            "No Contact": prop(True, "checkbox"), "Status": prop("Suppressed", "select")}}, "blocks": []})
        before = copy.deepcopy((source, baseline))
        delta = build_source_delta(source, baseline, archive_reference=reference(source))
        self.assertEqual(len(delta["records"]), 4)
        self.assertEqual(sum(row["kind"] == "outreach_events" for row in delta["records"]), 3)
        self.assertFalse(any(row["kind"] == "contacts" for row in delta["records"]))
        merged = combine_exports([baseline, delta])
        self.assertEqual(next(row for row in merged["records"] if row["id"] == contact["id"]), contact)
        audit = next(row for row in delta["records"] if row["kind"] == "audit_events")["payload"]
        self.assertEqual(audit["source_counts"]["suppressed_contacts"], 1)
        expected = content_hash({"properties": source["pages"][-1]["page"]["properties"], "body": []})
        self.assertEqual(audit["source_member_hashes"]["contact"], expected)
        self.assertNotIn("pages", audit)
        self.assertEqual((source, baseline), before)

    def test_changed_or_missing_baseline_history_is_rejected(self):
        baseline = export([event_record(event())])
        for events in ([event(content="Changed text")], [], [event(actor="Other actor")]):
            with self.subTest(events=events):
                source = snapshot(events)
                with self.assertRaises(ValueError):
                    build_source_delta(source, baseline, archive_reference=reference(source))

    def test_existing_additional_metadata_remains_exact(self):
        original = event(company="Reviewed metadata", contact_key="exact alias")
        baseline = export([event_record(original)])
        source = snapshot([original, event("new")])
        delta = build_source_delta(source, baseline, archive_reference=reference(source))
        self.assertEqual(sum(row["kind"] == "outreach_events" for row in delta["records"]), 1)
        source["pages"][0] = event_page(event(company="Changed metadata", contact_key="exact alias"))
        with self.assertRaises(ValueError):
            build_source_delta(source, baseline, archive_reference=reference(source))

    def test_false_complete_missing_body_unsupported_and_truncated_fail(self):
        for mutation in (lambda value: value.update(complete=False),
                         lambda value: value.update(schema="wrong"),
                         lambda value: value["pages"][0].pop("blocks"),
                         lambda value: value["pages"][0]["blocks"][0].update(type="unsupported", unsupported={}),
                         lambda value: value["pages"][0]["blocks"][0].update(has_children=True),
                         lambda value: value["pages"][0]["page"]["properties"]["Payload JSON"].update(has_more=True)):
            source = snapshot([event()])
            mutation(source)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_source_delta(source, export([event_record(event())]), archive_reference=reference(source))

    def test_duplicate_page_or_event_identity_and_bad_content_hash_fail(self):
        for mutate in (lambda value: value["pages"].append(copy.deepcopy(value["pages"][0])),
                       lambda value: value["pages"].append({**copy.deepcopy(value["pages"][0]),
                           "page": {**copy.deepcopy(value["pages"][0]["page"]), "id": "another-page"}}),
                       lambda value: value["pages"][0]["page"]["properties"].update({"Content Hash": prop("bad")})):
            source = snapshot([event()])
            mutate(source)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                build_source_delta(source, export([event_record(event())]), archive_reference=reference(source))

    def test_managed_cloud_page_is_validated_without_reimport(self):
        source = snapshot([event()])
        source["pages"].append(cloud_page(make_record("contacts", {"value": "preserved"}, "cloud:contact")))
        delta = build_source_delta(source, export([event_record(event())]), archive_reference=reference(source))
        self.assertEqual(len(delta["records"]), 1)
        self.assertEqual(delta["records"][0]["payload"]["source_counts"]["cloud_pages"], 1)
        source["pages"][-1]["page"]["properties"]["Cloud Managed Hash"] = prop("tampered")
        with self.assertRaises(ValueError):
            build_source_delta(source, export([event_record(event())]), archive_reference=reference(source))

    def test_snapshot_and_archive_reference_hash_binding(self):
        source = snapshot([event()])
        wrong = reference(source)
        wrong["snapshot_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            build_source_delta(source, export([event_record(event())]), archive_reference=wrong)
        source["hash"] = "0" * 64
        with self.assertRaises(ValueError):
            build_source_delta(source, export([event_record(event())]), archive_reference=reference(source))


class ImportReadbackTests(unittest.TestCase):
    def test_fresh_readbacks_only_return_aggregates(self):
        rows = [event_record(event()), make_record("audit_events", {"private": "body"}, "audit")]
        store = ReadStore(rows)
        self.assertEqual(validate_import(store, export(rows)), {"records": 2, "families": {"audit_events": 1, "outreach_events": 1},
                         "unchanged": 2, "conflicted": 0, "blocked": 0})
        self.assertEqual(store.reads, ["old", "audit"])
        store.rows["old"]["status"] = "different"
        self.assertEqual(validate_import(store, export(rows))["conflicted"], 1)
        self.assertEqual(len(store.reads), 4)
        del store.rows["audit"]
        self.assertEqual(validate_import(store, export(rows))["blocked"], 1)
        self.assertNotIn("private", json.dumps(validate_import(store, export(rows))))

    def test_duplicate_conflicts_bad_hash_and_read_errors_fail_closed(self):
        row = event_record(event())
        duplicate = event_record(event(content="Different"))
        self.assertEqual(validate_import(ReadStore([row]), export([row, duplicate]))["conflicted"], 1)
        self.assertEqual(validate_import(ReadStore([row]), export([{**row, "hash": "bad"}]))["blocked"], 1)
        class Broken(ReadStore):
            def get(self, identity):
                raise RuntimeError("private service response")
        result = validate_import(Broken([row]), export([row]))
        self.assertEqual(result["blocked"], 1)
        self.assertNotIn("private service", json.dumps(result))


class CutoverEvidenceTests(unittest.TestCase):
    def test_complete_evidence_builds_and_validates_activation_record(self):
        value = evidence()
        before = copy.deepcopy(value)
        row = make_cutover_record(value)
        self.assertEqual(row["id"], "deployment:cutover:v2")
        self.assertEqual(validate_cutover_record(row), row)
        self.assertEqual(value, before)

    def test_flags_alone_or_false_flags_never_establish_cutover(self):
        with self.assertRaises(ValueError):
            make_cutover_record({flag: True for flag in REQUIRED_FLAGS})
        for flag in REQUIRED_FLAGS:
            for value in (False, "true", 1):
                source = evidence()
                source[flag] = value
                with self.subTest(flag=flag, value=value), self.assertRaises(ValueError):
                    make_cutover_record(source)

    def test_real_restore_paused_readback_user_stop_and_shadow_proofs_required(self):
        mutations = [lambda value: value["archive"].update(restore_verified=False),
                     lambda value: value["archive"].update(hashes_verified=False),
                     lambda value: value["archive"].update(historical_manifest_sha256="0" * 64),
                     lambda value: value["local_actors"]["readbacks"][0].update(status="ACTIVE"),
                     lambda value: value["local_actors"]["readbacks"][0].update(persisted=False),
                     lambda value: value["local_actors"]["application_writer"].update(source="commit_author"),
                     lambda value: value["local_actors"]["application_writer"].update(stopped=False),
                     lambda value: value["shadow"].update(hosted=False),
                     lambda value: value["shadow"].update(commit="0" * 40),
                     lambda value: value["shadow"].update(lanes=["accepted"]),
                     lambda value: value["history"]["readback"].update(unchanged=3),
                     lambda value: value["history"]["readback"].update(blocked=1),
                     lambda value: value.update(evidence_urls=["https://example.com/placeholder"])]
        for mutation in mutations:
            value = evidence()
            mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                make_cutover_record(value)

    def test_stored_forged_record_and_blocked_readiness_cannot_activate(self):
        forged = make_record("audit_events", {flag: True for flag in REQUIRED_FLAGS}, "deployment:cutover:v2", "ready")
        with self.assertRaises(ValueError):
            validate_cutover_record(forged)
        ready = make_readiness_record({flag: True for flag in REQUIRED_FLAGS}, ["application_actor_stop_unconfirmed"])
        self.assertNotEqual(ready["id"], "deployment:cutover:v2")
        self.assertFalse(ready["payload"]["activation_authorized"])
        self.assertTrue(all(ready["payload"][flag] is False for flag in REQUIRED_FLAGS))
        with self.assertRaises(ValueError):
            validate_cutover_record(ready)


if __name__ == "__main__":
    unittest.main()
