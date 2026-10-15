from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cloud.migration import SCHEMA, record
from cloud.agent_index import build_index
from scripts.archive_local_state import capture_local_state, json_bytes, sha
from scripts.encrypted_backup import restore_archive


NOW = datetime(2026, 10, 14, 22, tzinfo=timezone.utc)


def setup_fixture(root, *, wal=False):
    coordination = root / "coordination"
    (coordination / "sync-state").mkdir(parents=True)
    journal = coordination / "sync-state/journal.sqlite3"
    db = sqlite3.connect(journal)
    db.executescript("""
        CREATE TABLE events(event_id TEXT PRIMARY KEY,payload TEXT,remote_url TEXT);
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE work_claims(token TEXT,task_id TEXT,resource_key TEXT,state TEXT,
          lease_until REAL,active INTEGER,result TEXT,release_evidence TEXT);
    """)
    db.execute("INSERT INTO events VALUES ('first','exact private historical bytes','https://example.invalid/receipt')")
    db.execute("INSERT INTO meta VALUES ('last_complete_pull','2026-10-03T14:09:13Z')")
    claims = [("expired", "task-old", "contact:old", "published", (NOW-timedelta(days=1)).timestamp(), 1, None, None),
              ("future", "task-future", "contact:future", "reserved", (NOW+timedelta(days=1)).timestamp(), 1, None, None)]
    db.executemany("INSERT INTO work_claims VALUES (?,?,?,?,?,?,?,?)", claims)
    db.commit()
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO events VALUES ('wal-row','committed WAL bytes','https://example.invalid/second')")
        db.commit()
    log = b"Exact private historical log\n"
    (coordination / "Outreach_Log.md").write_bytes(log)
    canonical = root / "canonical/Outreach_Log.md"
    canonical.parent.mkdir()
    canonical.write_bytes(log)
    names = ["source/coordination/sync-state/journal.sqlite3", "source/coordination/Outreach_Log.md"]
    for n in range(40):
        name = f"source/coordination/source-{n:02d}.md"
        (coordination / f"source-{n:02d}.md").write_bytes(b"Historical coordination source\n")
        names.append(name)
    private = root / ".migration-private"
    private.mkdir()
    baseline = {"schema": SCHEMA, "records": [record("prior", "audit_events", {"prior": True}, "observed")],
                "source_manifest": [{"classification": "historical_outreach_log", "source_path": str(p)}
                                    for p in (coordination / "Outreach_Log.md", canonical)], "blocked": []}
    baseline_path = private / "baseline.json"
    baseline_body = json_bytes(baseline)
    baseline_path.write_bytes(baseline_body)
    receipt_path = private / "baseline.receipt.json"
    receipt_path.write_bytes(json_bytes({"normalized_member": "normalized/merged-historical-records.json",
        "canonical_records": 1, "restore_verified": True, "files": [{
            "name": "normalized/merged-historical-records.json", "bytes": len(baseline_body), "sha256": sha(baseline_body)}]}))
    inventory_path = private / "inventory.receipt.json"
    inventory_path.write_bytes(json_bytes({"files": [{"name": name} for name in names]}))
    automation_root = root / "automations"
    automations = []
    for identity in ("linkedin-accepted-connection-follow-up", "linkedin-connection-request-morning"):
        folder = automation_root / identity
        folder.mkdir(parents=True)
        body = b'status = "ACTIVE"\nrrule = "fixture-daily"\nprompt = "Synthetic prompt"\n'
        (folder / "automation.toml").write_bytes(body)
        automations.append({"id": identity, "status": "ACTIVE", "rrule": "fixture-daily",
                            "raw_toml_sha256": sha(body), "prompt_sha256": sha(b"Synthetic prompt")})
    hashes = [{"token_sha256": sha(token.encode()), "task_id_sha256": sha(task.encode()),
               "resource_key_sha256": sha(resource.encode()), "state": state,
               "lease_until_utc": datetime.fromtimestamp(lease, timezone.utc).isoformat()}
              for token, task, resource, state, lease, *_ in claims]
    audit = {"schema": "apply-local-writers-audit/v1", "observed_at": NOW.isoformat(), "read_only": True,
        "journal": {"path": str(journal), "sha256": sha(journal.read_bytes()), "events": 2 if wal else 1,
                    "pending_remote": 0, "last_complete_pull": "2026-10-03T14:09:13Z"},
        "logs": {"same": True, "bytes": len(log), "sha256": sha(log)}, "automations": automations,
        "claims": {"total": 2, "active": 2, "states": {"published": 1, "reserved": 1},
                   "expired_active": 1, "unexpired_active": 1, "active_without_result": 2,
                   "active_without_release_evidence": 2, "active_claim_hashes": hashes}}
    audit_path = private / "writer-audit.json"
    audit_path.write_bytes(json_bytes(audit))
    key = root / "recovery.bin"
    key.write_bytes(b"x" * 32)
    key.chmod(0o600)
    kwargs = dict(baseline_path=baseline_path, baseline_receipt_path=receipt_path,
                  inventory_receipt_path=inventory_path, writer_audit_path=audit_path,
                  staging=private / "staging", key=key, output=root / "provisional.aesgcm",
                  private_destination_verified=True, automation_root=automation_root, now=NOW)
    return kwargs, db, journal, baseline


class ProvisionalLocalCaptureTests(unittest.TestCase):
    def test_authenticated_capture_preserves_claims_and_baseline_without_finality(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kwargs, db, journal, baseline = setup_fixture(root)
            before = journal.read_bytes()
            try:
                receipt = capture_local_state(**kwargs)
                self.assertTrue(receipt["restore_verified"])
                self.assertEqual(receipt["coordination_inventory_files"], 42)
                self.assertEqual(receipt["canonical_records"], 2)
                self.assertFalse(receipt["boundary_complete"])
                self.assertFalse(receipt["local_actors_retired"])
                self.assertTrue(receipt["provisional"])
                restored = root / "restored"
                restore_archive(kwargs["output"], restored, key_path=kwargs["key"])
                merged = json.loads((restored / "normalized/merged-historical-records.json").read_bytes())
                self.assertIn(baseline["records"][0], merged["records"])
                audit = next(row for row in merged["records"] if row["id"] != "prior")
                self.assertEqual(audit["kind"], "audit_events")
                self.assertEqual(audit["status"], "pre_cutover_capture")
                self.assertFalse(audit["payload"]["raw_outreach_normalized"])
                self.assertFalse(audit["payload"]["claims_modified"])
                self.assertEqual(audit["payload"]["observations"]["active_claims"], 2)
                self.assertEqual(audit["payload"]["observations"]["expired_active_claims"], 1)
                self.assertNotIn("complete", audit["payload"])
                index = build_index([audit], NOW, source_snapshot_hash={
                    "hash": "f" * 64, "complete": True, "conflicts": []})
                index_manifest = next(row for row in index if row["kind"] == "index_manifests")
                self.assertTrue(index_manifest["payload"]["complete"])
                self.assertEqual(index_manifest["payload"]["conflicts"], [])
                self.assertEqual(journal.read_bytes(), before)
                self.assertEqual(db.execute("SELECT active FROM work_claims WHERE token='future'").fetchone(), (1,))
                self.assertTrue(all(set(item) == {"member_id", "sha256", "bytes"} for item in receipt["files"]))
            finally:
                db.close()

    def test_committed_wal_rows_are_backed_up_without_opening_live_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kwargs, db, journal, _ = setup_fixture(root, wal=True)
            wal = Path(str(journal) + "-wal")
            before, wal_before = journal.read_bytes(), wal.read_bytes()
            try:
                receipt = capture_local_state(**kwargs)
                self.assertEqual(receipt["journal_events"], 2)
                self.assertEqual(journal.read_bytes(), before)
                self.assertEqual(wal.read_bytes(), wal_before)
                captured = kwargs["staging"] / "source/coordination/sync-state/journal.sqlite3"
                with sqlite3.connect(captured) as copied:
                    self.assertEqual(copied.execute("SELECT count(*) FROM events").fetchone()[0], 2)
                self.assertFalse((kwargs["staging"] / "sqlite-working-copy").exists())
            finally:
                db.close()

    def test_stale_audit_or_source_change_prevents_archive_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            kwargs, db, journal, _ = setup_fixture(Path(folder))
            try:
                original = __import__("scripts.archive_local_state", fromlist=["sqlite_snapshot"]).sqlite_snapshot
                def changed(source, target):
                    original(source, target)
                    (journal.parent.parent / "source-00.md").write_bytes(b"Concurrent source edit")
                with patch("scripts.archive_local_state.sqlite_snapshot", side_effect=changed):
                    with self.assertRaisesRegex(ValueError, "source changed"):
                        capture_local_state(**kwargs)
                self.assertFalse(kwargs["output"].exists())
            finally:
                db.close()

    def test_stale_audit_and_credentials_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            kwargs, db, journal, _ = setup_fixture(Path(folder))
            try:
                audit = json.loads(kwargs["writer_audit_path"].read_bytes())
                audit["journal"]["sha256"] = "0" * 64
                kwargs["writer_audit_path"].write_bytes(json_bytes(audit))
                with self.assertRaisesRegex(ValueError, "hash is stale"):
                    capture_local_state(**kwargs)
                self.assertFalse(kwargs["output"].exists())
            finally:
                db.close()
        with tempfile.TemporaryDirectory() as folder:
            kwargs, db, journal, _ = setup_fixture(Path(folder))
            try:
                (journal.parent.parent / "source-00.md").write_bytes(b"ghp_" + b"x" * 30)
                with self.assertRaisesRegex(ValueError, "Credential-pattern"):
                    capture_local_state(**kwargs)
                self.assertFalse(kwargs["staging"].exists())
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
