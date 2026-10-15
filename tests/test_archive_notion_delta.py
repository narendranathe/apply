"""Synthetic authenticated Notion preservation without cutover authority."""
from copy import deepcopy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from cloud.migration import event_record
from cloud.records import content_hash, make_record
from scripts.archive_local_state import json_bytes, sha
from scripts.encrypted_backup import restore_archive
from tests.test_cutover import event, export, prop, snapshot


def fixture(root):
    private = root / ".migration-private"
    private.mkdir(mode=0o700)
    old = event(company="Reviewed immutable source metadata")
    contact = make_record("contacts", {"source_notion_id": "contact", "suppressed": True}, "contact:old", "suppressed")
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


class NotionDeltaArchiveTests(unittest.TestCase):
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
