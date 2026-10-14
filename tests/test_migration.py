import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from cloud.migration import digest, event_record, export_history, export_jobscout_history, marked_log_events, merge_history, precision, reconcile_records
from scripts.encrypted_backup import build_archive, create_backup, private_key, restore_archive, sqlite_snapshot
from scripts.migrate_history import main as migrate_main


class MemoryStore:
    def __init__(self):
        self.records, self.writes = {}, 0

    def get(self, key):
        return copy.deepcopy(self.records.get(key))

    def put(self, value):
        self.writes += 1
        self.records[value["id"]] = copy.deepcopy(value)

    def query(self, kind=None):
        return [copy.deepcopy(r) for r in self.records.values() if kind is None or r["kind"] == kind]


def example(**changes):
    value = {"event_id": "original-1", "content": "Verbatim Unicode café.\nSecond line.",
             "actor": "Historical import", "kind": "Message", "occurred_at": "2026-09-22",
             "delivery_state": "delivery_unknown", "evidence": "Source observation only"}
    value.update(changes)
    value["content_hash"] = hashlib.sha256(value["content"].encode()).hexdigest()
    return value


class MigrationTests(unittest.TestCase):
    def test_uncertain_readback_stops_all_later_imports(self):
        store = MemoryStore()
        store.get = lambda key: None
        rows = [event_record(example(event_id='first')), event_record(example(event_id='second'))]
        result = reconcile_records(rows, store, dry_run=False, private_destination=True)
        self.assertEqual(store.writes, 1)
        self.assertEqual(result['blocked'], 1)
        self.assertEqual(len(result['results']), 1)

    def test_bounded_import_uses_validated_stable_order(self):
        rows = [event_record(example(event_id='b')), event_record(example(event_id='a'))]
        store, reads = MemoryStore(), []
        def read(key):
            reads.append(key)
            return None
        store.get = read
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.json'
            path.write_text(json.dumps({'schema': 'apply-historical-export/v1', 'records': rows, 'blocked': []}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(migrate_main(['--input', str(path), '--limit', '1'], store=store), 0)
        self.assertEqual(reads, ['a'])
        result = json.loads(output.getvalue())
        self.assertEqual((result['processed_records'], result['total_records'], result['next_offset']), (1, 2, 1))
        self.assertEqual(store.writes, 0)

    def test_precision_and_source_actor_are_not_invented(self):
        self.assertEqual(precision("2026-09-22"), "date")
        self.assertEqual(precision("2026-09-22T12:00:01.123Z"), "fractional_seconds:3")
        row = event_record(example())
        self.assertEqual(row["payload"]["actor"], "Historical import")
        self.assertEqual(row["status"], "delivery_unknown")
        self.assertEqual(row["hash"], digest(row["payload"]))

    def test_idempotent_import_dry_run_and_private_gate(self):
        row, store = event_record(example()), MemoryStore()
        self.assertEqual(reconcile_records([row], store)["would_import"], 1)
        self.assertEqual(store.writes, 0)
        self.assertEqual(reconcile_records([row], store, dry_run=False)["blocked"], 1)
        self.assertEqual(store.writes, 0)
        first = reconcile_records([row], store, dry_run=False, private_destination=True)
        second = reconcile_records([row], store, dry_run=False, private_destination=True)
        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(store.writes, 1)

    def test_human_changed_status_and_changed_payload_conflict(self):
        row, store = event_record(example()), MemoryStore()
        store.records[row["id"]] = dict(row, status="human_changed")
        self.assertEqual(reconcile_records([row], store)["conflicted"], 1)
        self.assertEqual(store.writes, 0)
        changed = event_record(example(content="Changed history"))
        self.assertEqual(reconcile_records([row, changed], MemoryStore())["conflicted"], 1)
        collision_store = MemoryStore()
        self.assertEqual(reconcile_records([row, changed], collision_store, dry_run=False,
                                          private_destination=True)["conflicted"], 1)
        self.assertEqual(collision_store.writes, 0)

    def test_failed_readback_never_retries(self):
        class Uncertain(MemoryStore):
            def put(self, value):
                self.writes += 1
        store = Uncertain()
        result = reconcile_records([event_record(example())], store, dry_run=False, private_destination=True)
        self.assertEqual(result["blocked"], 1)
        self.assertEqual(result["results"][0]["reason"], "write_readback_uncertain_no_retry")
        self.assertEqual(store.writes, 1)

    def test_bad_content_hash_cannot_import(self):
        event = example()
        event["content_hash"] = "bad"
        with self.assertRaises(ValueError):
            event_record(event)

    def test_nested_marker_in_correction_is_verbatim_content(self):
        body = "Proof:\n<!-- outreach-event:old-id -->\nold content\n<!-- /outreach-event:old-id -->"
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        block = ("<!-- outreach-event:correction-id -->\n## 2026-09-22: Proof\n\n"
                 "Event ID: correction-id\nActor: Codex\nKind: Correction\nDelivery state: Not applicable\n"
                 f"Content SHA-256: {content_hash}\n\n{body}\n<!-- /outreach-event:correction-id -->")
        rows = marked_log_events(block)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], body)

    def test_jobscout_applied_is_unknown_and_proven_fixture_is_audit_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp) / "history.sqlite3"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE applications(id INTEGER,external_id TEXT,company TEXT,title TEXT,url TEXT,status TEXT,applied_at TEXT,user_id TEXT,updated_at TEXT)")
                db.execute("INSERT INTO applications VALUES (1,'real-id','Acme','Engineer','https://acme.test/job/1','applied','2026-09-22','u','2026-09-22')")
                db.execute("INSERT INTO applications VALUES (2,'fixture-id','Fixture','Engineer','https://fixture.test/job/1','removed','2026-09-22','u','2026-09-22')")
            rows = export_jobscout_history(dbpath, proven_test_identities={("fixture-id", "Fixture", "https://fixture.test/job/1")})
            self.assertEqual(rows[0]["kind"], "applications")
            self.assertEqual(rows[0]["status"], "submission_unknown")
            self.assertEqual(rows[0]["payload"]["source_payload"]["status"], "applied")
            self.assertFalse(rows[0]["payload"]["receipt_verified"])
            self.assertEqual(rows[1]["kind"], "audit_events")
            self.assertEqual(rows[1]["status"], "source_test_artifact")

    def test_cli_missing_credential_is_sanitized_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps({"schema": "apply-historical-export/v1", "records": [event_record(example())], "blocked": []}))
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                self.assertEqual(migrate_main(["--input", str(path)]), 2)
            self.assertEqual(json.loads(output.getvalue())["reason"], "notion_runner_credential_missing")
            store, output = MemoryStore(), io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(migrate_main(["--input", str(path)], store=store), 0)
            self.assertEqual(store.writes, 0)
            self.assertEqual(json.loads(output.getvalue())["would_import"], 1)
            with contextlib.redirect_stdout(io.StringIO()):
                result = migrate_main(["--input", str(path), "--write", "--private-destination-verified"],
                                      store=store, private_verifier=lambda: False)
            self.assertEqual(result, 2)
            self.assertEqual(store.writes, 0)

    def test_merge_exact_sources_and_reject_unapproved_variants(self):
        row = event_record(example())
        local = {"schema": "apply-historical-export/v1", "records": [row], "blocked": []}
        remote = [{"payload": row["payload"], "notion_url": "https://app.notion.com/p/11111111111111111111111111111111"}]
        merged = merge_history(local, [], remote)
        self.assertEqual(merged["remote_event_comparison"]["exact"], 1)
        self.assertFalse(merged["cloud_authoritative"])
        bad = copy.deepcopy(remote)
        bad[0]["payload"]["company"] = "Extra metadata without approval"
        with self.assertRaises(ValueError):
            merge_history(local, [], bad)

    def test_cli_conflict_and_uncertain_readback_fail_run(self):
        row = event_record(example())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps({"schema": "apply-historical-export/v1", "records": [row], "blocked": []}))
            changed = MemoryStore()
            changed.records[row["id"]] = event_record(example(content="A different historical payload"))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = migrate_main(["--input", str(path), "--write", "--private-destination-verified"],
                                      store=changed, private_verifier=lambda: True)
            self.assertEqual(result, 2)
            self.assertEqual(changed.writes, 0)
            self.assertEqual(json.loads(output.getvalue())["conflicted"], 1)

            class Uncertain(MemoryStore):
                def put(self, value):
                    self.writes += 1
            uncertain = Uncertain()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = migrate_main(["--input", str(path), "--write", "--private-destination-verified"],
                                      store=uncertain, private_verifier=lambda: True)
            self.assertEqual(result, 2)
            self.assertEqual(uncertain.writes, 1)
            self.assertEqual(json.loads(output.getvalue())["blocked"], 1)

    def test_journal_and_marker_mirrors_preserve_full_optional_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event = example(occurred_at="2026-09-22T12:00:01.123Z", contact_key="https://example.test/person")
            journal = root / "journal.sqlite3"
            with sqlite3.connect(journal) as db:
                db.execute("CREATE TABLE events(event_id TEXT PRIMARY KEY,payload TEXT,remote_url TEXT,created TEXT,legacy INTEGER)")
                db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (event["event_id"], json.dumps(event), None, "2026-09-23", 0))
            text = (f"<!-- outreach-event:{event['event_id']} -->\n## {event['occurred_at']}: Exact title\n\n"
                    f"Event ID: {event['event_id']}\nActor: {event['actor']}\nKind: {event['kind']}\n"
                    f"Delivery state: {event['delivery_state']}\nContent SHA-256: {event['content_hash']}\n\n"
                    f"{event['content']}\n<!-- /outreach-event:{event['event_id']} -->\n")
            event["title"] = "Exact title"
            with sqlite3.connect(journal) as db:
                db.execute("UPDATE events SET payload=?", (json.dumps(event),))
            logs = [root / "a.md", root / "b.md"]
            for path in logs:
                path.write_text(text)
            exported = export_history(journal, logs)
            self.assertEqual(exported["blocked"], [])
            self.assertEqual(len(exported["records"]), 1)
            self.assertEqual(exported["records"][0]["payload"], event)
            provenance = exported["provenance"][event["event_id"]]
            self.assertEqual(len(provenance), 3)
            self.assertEqual(provenance[0]["timestamp_precision"], "fractional_seconds:3")
            self.assertTrue(provenance[0]["actor_ambiguous"])


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / "key.bin"
        private_key(self.key, create=True)
        self.source = self.root / "source.md"
        self.source.write_text("Exact historical content\n", encoding="utf-8")

    def test_authenticated_backup_restore_and_hashes(self):
        archive = self.root / "backup.aesgcm"
        result = create_backup({"outreach/source.md": self.source}, archive, key_path=self.key,
                               private_destination_verified=True)
        self.assertTrue(result["restore_verified"])
        restored = self.root / "restored"
        restore_archive(archive, restored, key_path=self.key)
        self.assertEqual((restored / "outreach/source.md").read_bytes(), self.source.read_bytes())
        self.assertEqual(self.key.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.key.read_bytes(), archive.read_bytes())

    def test_tampering_and_wrong_key_fail_without_plaintext(self):
        archive = self.root / "backup.aesgcm"
        create_backup({"source.md": self.source}, archive, key_path=self.key, private_destination_verified=True)
        wrong = self.root / "other-key.bin"
        private_key(wrong, create=True)
        with self.assertRaises(Exception):
            restore_archive(archive, self.root / "wrong", key_path=wrong)
        data = bytearray(archive.read_bytes())
        data[-1] ^= 1
        archive.write_bytes(data)
        with self.assertRaises(Exception):
            restore_archive(archive, self.root / "tampered", key_path=self.key)
        self.assertFalse((self.root / "wrong").exists())
        self.assertFalse((self.root / "tampered").exists())

    def test_private_gate_and_credential_files_rejected(self):
        with self.assertRaises(ValueError):
            create_backup({"source.md": self.source}, self.root / "archive", key_path=self.key)
        for name in ("../escape", "cookies.json", "credentials.json", "api-secret.json"):
            with self.assertRaises(ValueError):
                build_archive({name: self.source})

    def test_sqlite_backup_includes_committed_wal(self):
        source, snapshot = self.root / "live.sqlite3", self.root / "snapshot.sqlite3"
        db = sqlite3.connect(source)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE events(id TEXT)")
        db.execute("INSERT INTO events VALUES ('committed')")
        db.commit()
        sqlite_snapshot(source, snapshot)
        with sqlite3.connect(snapshot) as restored:
            self.assertEqual(restored.execute("SELECT id FROM events").fetchone()[0], "committed")
            self.assertEqual(restored.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
