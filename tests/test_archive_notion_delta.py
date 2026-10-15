"""Synthetic authenticated Notion preservation without cutover authority."""
from copy import deepcopy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cloud.migration import event_record
from cloud.records import content_hash, make_record
from scripts.archive_local_state import json_bytes, sha
from scripts.encrypted_backup import restore_archive
from tests.test_cutover import event, export, prop, snapshot


def fixture(root):
    private = root / ".migration-private"
    private.mkdir(mode=0o700)
    old = event(company="Reviewed immutable source metadata")
    contact = make_record("contacts", {"source_notion_id": "native-contact", "suppressed": True}, "contact:old", "suppressed")
    baseline = export([event_record(old), contact])
    baseline_body = json_bytes(baseline)
    baseline_path = private / "baseline.json"
    baseline_path.write_bytes(baseline_body)
    baseline_path.chmod(0o600)
    member = "normalized/merged-historical-records.json"
    receipt = {"normalized_member": member, "canonical_records": 2, "restore_verified": True,
               "files": [{"member_id": sha(member.encode()), "bytes": len(baseline_body), "sha256": sha(baseline_body)}]}
    receipt_path = private / "baseline.receipt.json"
    receipt_path.write_bytes(json_bytes(receipt))
    receipt_path.chmod(0o600)
    source = snapshot([old, event("new", content="Synthetic new immutable history")])
    source["pages"].append({"page": {"id": "native-contact", "properties": {
        "Type": prop("Contact", "select"), "No Contact": prop(True, "checkbox"),
        "Status": prop("Suppressed", "select")}}, "blocks": []})
    source["pages"].append({"page": {"id": "native-queue", "properties": {
        "Type": prop("Task", "select"), "Status": prop("In progress", "select")}}, "blocks": []})
    wrapper = {"schema": "apply-cloud-snapshot/v1", "exported_at": "2026-10-14T22:00:00Z", "snapshot": source}
    raw = json.dumps(wrapper, ensure_ascii=False, indent=3).encode() + b"\n"
    snapshot_path = private / "cloud-records.json"
    snapshot_path.write_bytes(raw)
    snapshot_path.chmod(0o600)
    key = root / "synthetic-recovery.bin"
    key.write_bytes(b"x" * 32)
    key.chmod(0o600)
    return {"snapshot_path": snapshot_path, "baseline_path": baseline_path, "baseline_receipt_path": receipt_path,
            "staging": private / "notion-delta", "key": key,
            "output": root / "migration-archives/pre-cutover-notion.aesgcm", "private_destination_verified": True}, baseline, raw


def rewrite_snapshot(kwargs, mutate):
    value = json.loads(kwargs["snapshot_path"].read_bytes())
    mutate(value)
    kwargs["snapshot_path"].write_bytes(json_bytes(value))


def add_new_contact(kwargs, *, no_contact=False, status="Accepted"):
    def append(wrapper):
        wrapper["snapshot"]["pages"].append({"page": {"id": "new-native-contact",
            "url": "https://app.notion.com/p/fixture-new-contact", "last_edited_time": "2026-10-14T21:00:00Z",
            "properties": {"Type": prop("Contact", "select"), "Name": prop("Synthetic candidate", "title"),
                "Contact Key": prop("https://uk.linkedin.com/in/fixture-new/?trk=public-profile"),
                "Company": prop("Unverified source label"), "Role Key": prop("Unverified source role"),
                "Status": prop(status, "select"), "No Contact": prop(no_contact, "checkbox")}},
            "blocks": [{"type": "paragraph", "has_children": False, "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": "Synthetic source claim: invitation sent and accepted."}}]}}]})
    rewrite_snapshot(kwargs, append)


def next_capture(kwargs, receipt, *, suffix="next"):
    return {**kwargs, "baseline_path": kwargs["staging"] / receipt["normalized_member"],
            "baseline_receipt_path": kwargs["output"].with_suffix(".receipt.json"),
            "staging": kwargs["staging"].parent / ("capture-" + suffix),
            "output": kwargs["output"].parent / (suffix + ".aesgcm")}


class NotionDeltaArchiveTests(unittest.TestCase):
    def test_new_contact_is_exact_unverified_source_without_inferred_outcomes(self):
        from scripts.archive_notion_delta import capture_notion_delta
        with tempfile.TemporaryDirectory() as folder:
            kwargs, baseline, _ = fixture(Path(folder))
            add_new_contact(kwargs)
            source = json.loads(kwargs["snapshot_path"].read_bytes())["snapshot"]["pages"][-1]
            receipt = capture_notion_delta(**kwargs)
            merged = json.loads((kwargs["staging"] / receipt["normalized_member"]).read_bytes())
            added = [row for row in merged["records"] if row["kind"] == "contacts" and row["id"] != "contact:old"]
            self.assertEqual(len(added), 1)
            self.assertEqual(receipt["new_contacts"], 1)
            row = added[0]
            payload = row["payload"]
            self.assertEqual(row["status"], "Needs verification")
            self.assertEqual(payload["state"], "needs_verification")
            self.assertEqual(payload["properties"], source["page"]["properties"])
            self.assertEqual(payload["body"], source["blocks"])
            self.assertEqual(payload["name"], "Synthetic candidate")
            self.assertEqual(payload["profile_url"], "https://www.linkedin.com/in/fixture-new")
            self.assertEqual(payload["source_notion_id"], source["page"]["id"])
            for flag in ("identity_verified", "history_complete", "live_state_verified"):
                self.assertIs(payload[flag], False)
            self.assertTrue(payload["requires_refresh"])
            self.assertFalse(payload["suppressed"])
            for field in ("company_id", "role_id", "genuine_reply", "receipt", "confirmation_evidence", "last_touch_at"):
                self.assertNotIn(field, payload)
            self.assertEqual(payload["provenance"]["archive_reference"]["archive"], "migration-archives/pre-cutover-notion.aesgcm")
            for old in baseline["records"]:
                self.assertIn(old, merged["records"])

    def test_duplicate_profile_alias_in_baseline_or_native_candidates_is_blocked(self):
        from scripts.archive_notion_delta import capture_notion_delta
        for mode in ("baseline", "native"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                kwargs, baseline, _ = fixture(Path(folder))
                add_new_contact(kwargs)
                if mode == "baseline":
                    baseline["records"][1] = make_record("contacts", {**baseline["records"][1]["payload"],
                        "profile_aliases": ["http://www.linkedin.com/in/fixture-new/#experience"]}, "contact:old", "suppressed")
                    body = json_bytes(baseline)
                    kwargs["baseline_path"].write_bytes(body)
                    old_receipt = json.loads(kwargs["baseline_receipt_path"].read_bytes())
                    old_receipt["files"][0].update(sha256=sha(body), bytes=len(body))
                    kwargs["baseline_receipt_path"].write_bytes(json_bytes(old_receipt))
                else:
                    rewrite_snapshot(kwargs, lambda w: w["snapshot"]["pages"][-2]["page"]["properties"].update(
                        {"Type": prop("Contact", "select"), "Contact Key": prop("https://www.linkedin.com/in/fixture-new/")}))
                with self.assertRaisesRegex(ValueError, "contact.*alias"):
                    capture_notion_delta(**kwargs)
                self.assertFalse(kwargs["output"].exists())

    def test_new_suppression_and_old_opt_out_cannot_be_cleared(self):
        from scripts.archive_notion_delta import capture_notion_delta
        for no_contact, status in ((True, "Accepted"), (False, "Suppressed")):
            with self.subTest(no_contact=no_contact, status=status), tempfile.TemporaryDirectory() as folder:
                kwargs, baseline, _ = fixture(Path(folder))
                add_new_contact(kwargs, no_contact=no_contact, status=status)
                rewrite_snapshot(kwargs, lambda w: w["snapshot"]["pages"][2]["page"]["properties"].update(
                    {"No Contact": prop(False, "checkbox"), "Status": prop("Accepted", "select")}))
                receipt = capture_notion_delta(**kwargs)
                merged = json.loads((kwargs["staging"] / receipt["normalized_member"]).read_bytes())
                contacts = [row for row in merged["records"] if row["kind"] == "contacts"]
                self.assertIn(baseline["records"][1], contacts)
                new = next(row for row in contacts if row["id"] != "contact:old")
                self.assertEqual(new["status"], "Suppressed")
                self.assertEqual(new["payload"]["state"], "suppressed")
                self.assertTrue(new["payload"]["suppressed"])
                self.assertFalse(new["payload"]["identity_verified"])

    def test_same_snapshot_reuses_original_audit_and_never_overwrites_archive(self):
        from scripts.archive_notion_delta import capture_notion_delta
        with tempfile.TemporaryDirectory() as folder:
            kwargs, _, _ = fixture(Path(folder))
            add_new_contact(kwargs)
            # The existing archive format preserved raw contacts but did not
            # normalize new contacts. Its authenticated output is immutable.
            with patch("scripts.archive_notion_delta._new_contact_records", return_value=[], create=True):
                first = capture_notion_delta(**kwargs)
            old_ciphertext = kwargs["output"].read_bytes()
            old_export = json.loads((kwargs["staging"] / first["normalized_member"]).read_bytes())
            old_audit = next(row for row in old_export["records"] if row["status"] == "pre_cutover_capture")
            second_kwargs = next_capture(kwargs, first)
            second = capture_notion_delta(**second_kwargs)
            merged = json.loads((second_kwargs["staging"] / second["normalized_member"]).read_bytes())
            self.assertTrue(second["source_audit_reused"])
            self.assertEqual(second["new_contacts"], 1)
            self.assertEqual(second["delta_records"], 1)
            self.assertEqual(second["new_outreach_events"], 0)
            self.assertEqual(len(merged["records"]), len(old_export["records"]) + 1)
            self.assertEqual([row for row in merged["records"] if row["status"] == "pre_cutover_capture"], [old_audit])
            for old in old_export["records"]:
                self.assertIn(old, merged["records"])
            self.assertEqual(kwargs["output"].read_bytes(), old_ciphertext)
            third_kwargs = next_capture(second_kwargs, second, suffix="third")
            third = capture_notion_delta(**third_kwargs)
            self.assertEqual(third["delta_records"], 0)
            self.assertEqual(third["canonical_records"], second["canonical_records"])

    def test_conflicting_prior_audit_cannot_be_reused_or_rewritten(self):
        from scripts.archive_notion_delta import capture_notion_delta
        for mutate in (lambda p: p.update(boundary_complete=True), lambda p: p.update(local_actors_retired=True),
                       lambda p: p.update(capture_purpose="final"), lambda p: p.update(schema="other"),
                       lambda p: p["archive_reference"].update(member_sha256="0" * 64),
                       lambda p: p.update(source_member_hashes={}), lambda p: p.update(source_counts=[])):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as folder:
                kwargs, _, _ = fixture(Path(folder))
                first = capture_notion_delta(**kwargs)
                second_kwargs = next_capture(kwargs, first)
                baseline = json.loads(second_kwargs["baseline_path"].read_bytes())
                old = next(row for row in baseline["records"] if row["status"] == "pre_cutover_capture")
                payload = deepcopy(old["payload"])
                mutate(payload)
                baseline["records"][baseline["records"].index(old)] = make_record(old["kind"], payload, old["id"], old["status"])
                body = json_bytes(baseline)
                second_kwargs["baseline_path"].write_bytes(body)
                receipt = json.loads(second_kwargs["baseline_receipt_path"].read_bytes())
                member_id = sha(receipt["normalized_member"].encode())
                next(row for row in receipt["files"] if row["member_id"] == member_id).update(sha256=sha(body), bytes=len(body))
                second_kwargs["baseline_receipt_path"].write_bytes(json_bytes(receipt))
                with self.assertRaisesRegex(ValueError, "prior source audit"):
                    capture_notion_delta(**second_kwargs)
                self.assertFalse(second_kwargs["output"].exists())

    def test_same_inner_snapshot_with_changed_raw_bytes_cannot_reuse_audit(self):
        from scripts.archive_notion_delta import capture_notion_delta
        with tempfile.TemporaryDirectory() as folder:
            kwargs, _, _ = fixture(Path(folder))
            first = capture_notion_delta(**kwargs)
            second_kwargs = next_capture(kwargs, first)
            kwargs["snapshot_path"].write_bytes(json_bytes(json.loads(kwargs["snapshot_path"].read_bytes())))
            with self.assertRaisesRegex(ValueError, "prior source audit"):
                capture_notion_delta(**second_kwargs)
            self.assertFalse(second_kwargs["output"].exists())

    def test_exact_raw_restore_and_new_history_preserve_every_baseline_core(self):
        from scripts.archive_notion_delta import capture_notion_delta
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kwargs, baseline, raw = fixture(root)
            originals = {field: kwargs[field].read_bytes() for field in ("snapshot_path", "baseline_path", "baseline_receipt_path")}
            receipt = capture_notion_delta(**kwargs)
            restored = root / "restored"
            restored_report = restore_archive(kwargs["output"], restored, key_path=kwargs["key"])
            self.assertTrue(receipt["restore_verified"])
            self.assertTrue(restored_report["hashes_verified"])
            self.assertEqual((restored / "source/notion/cloud-records.json").read_bytes(), raw)
            merged = json.loads((restored / receipt["normalized_member"]).read_bytes())
            self.assertEqual(receipt["baseline_records"], 2)
            self.assertEqual(receipt["canonical_records"], 4)
            self.assertEqual(receipt["new_outreach_events"], 1)
            self.assertEqual(receipt["delta_records"], 2)
            for row in baseline["records"]:
                self.assertIn(row, merged["records"])
            audit = next(row for row in merged["records"] if row["status"] == "pre_cutover_capture")
            self.assertEqual(audit["payload"]["capture_purpose"], "pre_cutover")
            self.assertFalse(audit["payload"]["boundary_complete"])
            self.assertFalse(audit["payload"]["local_actors_retired"])
            self.assertFalse(audit["payload"]["cloud_authoritative"])
            self.assertTrue(audit["id"].startswith("source-pre-cutover-snapshot:"))
            reference = audit["payload"]["archive_reference"]
            self.assertEqual(reference["archive"], "migration-archives/pre-cutover-notion.aesgcm")
            self.assertEqual(reference["member_sha256"], sha(raw))
            self.assertEqual(reference["snapshot_sha256"], content_hash(json.loads(raw)["snapshot"]))
            self.assertEqual(audit["payload"]["source_counts"]["suppressed_contacts"], 1)
            self.assertFalse(any(row["id"] == "deployment:cutover:v2" for row in merged["records"]))
            self.assertEqual([row for row in merged["records"] if row["kind"] == "contacts"], [baseline["records"][1]])
            self.assertEqual(receipt["archive_sha256"], sha(kwargs["output"].read_bytes()))
            self.assertTrue(all(set(row) == {"member_id", "sha256", "bytes"} for row in receipt["files"]))
            self.assertNotIn("Reviewed immutable source", json.dumps(receipt))
            self.assertNotIn("native-contact", json.dumps(receipt))
            for field, before in originals.items():
                self.assertEqual(kwargs[field].read_bytes(), before)
            for path in [kwargs["staging"], *kwargs["staging"].rglob("*")]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_changed_or_missing_old_history_blocks_archive(self):
        from scripts.archive_notion_delta import capture_notion_delta
        for change in (lambda wrapper: wrapper["snapshot"]["pages"].pop(0),
                       lambda wrapper: wrapper["snapshot"]["pages"][0]["blocks"][0]["code"]["rich_text"][0]["text"].update(content=json.dumps(event(content="Changed old history")))):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                kwargs, _, _ = fixture(Path(folder))
                rewrite_snapshot(kwargs, change)
                with self.assertRaises(ValueError):
                    capture_notion_delta(**kwargs)
                self.assertFalse(kwargs["output"].exists())

    def test_opaque_receipt_hash_count_or_restore_mismatch_blocks(self):
        from scripts.archive_notion_delta import capture_notion_delta
        for mutate in (lambda r: r.update(restore_verified=False), lambda r: r.update(canonical_records=3),
                       lambda r: r["files"][0].update(sha256="0" * 64), lambda r: r["files"][0].update(bytes=0)):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as folder:
                kwargs, _, _ = fixture(Path(folder))
                receipt = json.loads(kwargs["baseline_receipt_path"].read_bytes())
                mutate(receipt)
                kwargs["baseline_receipt_path"].write_bytes(json_bytes(receipt))
                with self.assertRaises(ValueError):
                    capture_notion_delta(**kwargs)
                self.assertFalse(kwargs["staging"].exists())

    def test_snapshot_wrapper_completeness_or_structure_fails_closed(self):
        from scripts.archive_notion_delta import capture_notion_delta
        for mutate in (lambda w: w.update(schema="other"), lambda w: w.update(exported_at="2026-10-14"),
                       lambda w: w["snapshot"].update(complete=False), lambda w: w["snapshot"]["pages"][0].pop("blocks"),
                       lambda w: w["snapshot"]["pages"].append(deepcopy(w["snapshot"]["pages"][0]))):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as folder:
                kwargs, _, _ = fixture(Path(folder))
                rewrite_snapshot(kwargs, mutate)
                with self.assertRaises(ValueError):
                    capture_notion_delta(**kwargs)
                self.assertFalse(kwargs["output"].exists())

    def test_source_credentials_are_rejected_before_staging(self):
        from scripts.archive_notion_delta import capture_notion_delta
        with tempfile.TemporaryDirectory() as folder:
            kwargs, _, _ = fixture(Path(folder))
            rewrite_snapshot(kwargs, lambda w: w.update(unsafe="ghp_" + "x" * 30))
            with self.assertRaisesRegex(ValueError, "Credential-pattern"):
                capture_notion_delta(**kwargs)
            self.assertFalse(kwargs["staging"].exists())
            self.assertFalse(kwargs["output"].exists())

    def test_cli_failure_emits_only_safe_error_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            kwargs, _, _ = fixture(Path(folder))
            credential = "ghp_" + "x" * 30
            rewrite_snapshot(kwargs, lambda w: w.update(unsafe=credential))
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/archive_notion_delta.py")]
            for option, field in (("snapshot", "snapshot_path"), ("baseline", "baseline_path"),
                                  ("baseline-receipt", "baseline_receipt_path"), ("staging", "staging"),
                                  ("key", "key"), ("output", "output")):
                command.extend(["--" + option, str(kwargs[field])])
            result = subprocess.run(command + ["--private-destination-verified"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertEqual(json.loads(result.stderr), {"status": "blocked", "error_type": "ValueError", "source_effect_replay": False})
            self.assertNotIn(credential, result.stderr)

    def test_private_staging_destination_and_overwrite_guards(self):
        from scripts.archive_notion_delta import capture_notion_delta
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kwargs, _, _ = fixture(root)
            for updates in ({"private_destination_verified": False}, {"staging": root / "public-staging"},
                            {"output": root / "other-place/archive.aesgcm"}):
                with self.subTest(updates=updates), self.assertRaises(ValueError):
                    capture_notion_delta(**{**kwargs, **updates})
            capture_notion_delta(**kwargs)
            ciphertext = kwargs["output"].read_bytes()
            with self.assertRaises(ValueError):
                capture_notion_delta(**kwargs)
            self.assertEqual(kwargs["output"].read_bytes(), ciphertext)


if __name__ == "__main__":
    unittest.main()
