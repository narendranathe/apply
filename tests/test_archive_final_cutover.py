"""Synthetic final boundary capture; no live source, key or writer access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cloud.coordination_quarantine import ContactQuarantines, validate_quarantine
from cloud.migration import SCHEMA, combine_exports, event_record
from cloud.records import content_hash, make_record
from cloud.notion import DEFAULT_DATA_SOURCE_ID
from scripts.archive_final_cutover import _final_quarantines, _preserve_current_claims, capture_final_cutover
from scripts.archive_local_state import json_bytes, sha
from scripts.encrypted_backup import restore_archive
from tests.test_archive_local_state import setup_fixture
from tests.test_cutover import event, prop, snapshot

NOW = datetime(2026, 10, 15, 8, tzinfo=timezone.utc).isoformat()


def grant_fixture(folder):
    journal = Path(folder) / "final.sqlite3"
    claims = [(f"fixture-contact-token-{n}", f"fixture-contact-task-{n}",
               f"contact:https://www.linkedin.com/in/fixture-person-{n}", "published") for n in range(3)]
    claims += [(f"fixture-company-token-{n}", f"fixture-company-task-{n}", f"company:fixture-{n}", "reserved")
               for n in range(3)]
    with sqlite3.connect(journal) as db:
        db.execute("CREATE TABLE work_claims(token TEXT,task_id TEXT,resource_key TEXT,state TEXT,"
                   "active INTEGER,verification TEXT,result TEXT,release_evidence TEXT)")
        db.executemany("INSERT INTO work_claims VALUES(?,?,?,?,1,NULL,NULL,NULL)", claims)
    source = {"pages": [{"page": {"id": f"fixture-queue-{n}", "properties": {
        "Claim Token": prop(claims[n][0]), "Contact Key": prop(claims[n][2].removeprefix("contact:"))}},
        "blocks": []} for n in range(3)]}
    baseline = {"records": [make_record("contacts", {"contact_id": "contact:fixture-existing",
        "source_notion_id": "00000000-0000-4000-8000-000000000001",
        "profile_aliases": ["https://uk.linkedin.com/in/fixture-person-0/?trk=fixture"]}, "contact:fixture-existing")]}
    return journal, baseline, source, claims


def capture_fixture(root):
    original, db, journal, baseline = setup_fixture(root)
    private = root / ".migration-private"
    private.chmod(0o700)
    old = event()
    db.execute("ALTER TABLE events ADD COLUMN created REAL")
    db.execute("ALTER TABLE events ADD COLUMN legacy INTEGER")
    db.execute("DELETE FROM events")
    db.execute("INSERT INTO events VALUES(?,?,?,?,?)", (old["event_id"], json.dumps(old), "https://app.notion.com/p/abc", 1, 0))
    db.execute("ALTER TABLE work_claims ADD COLUMN worker TEXT")
    db.execute("ALTER TABLE work_claims ADD COLUMN verification TEXT")
    db.execute("DELETE FROM work_claims")
    claims = [(f"fixture-contact-token-{n}", f"fixture-contact-task-{n}",
               f"contact:https://www.linkedin.com/in/fixture-person-{n}", "published") for n in range(3)]
    claims += [(f"fixture-company-token-{n}", f"fixture-company-task-{n}", f"company:fixture-{n}", "reserved") for n in range(3)]
    for token, task, resource, state in claims:
        db.execute("INSERT INTO work_claims VALUES(?,?,?,?,?,?,?,?,?,?)", (token, task, resource, state, 1, 1, None, None, "fixture-worker", None))
    db.commit()
    baseline["records"].append(event_record(old))
    prior_claim = make_record("audit_events", {"token": claims[0][0], "state": "reserved"},
                              "legacy-claim:" + claims[0][0], "historical_reserved")
    baseline["records"].append(prior_claim)
    body = json_bytes(baseline)
    original["baseline_path"].write_bytes(body)
    original["baseline_path"].chmod(0o600)
    receipt = json.loads(original["baseline_receipt_path"].read_bytes())
    receipt["canonical_records"] = len(baseline["records"])
    receipt["files"][0].update(sha256=sha(body), bytes=len(body))
    original["baseline_receipt_path"].write_bytes(json_bytes(receipt))
    boundary_time = datetime.now(timezone.utc) - timedelta(hours=1)
    captured_time = boundary_time + timedelta(minutes=1)
    source = snapshot([old])
    source["data_source_id"] = DEFAULT_DATA_SOURCE_ID
    source["data_source"]["id"] = DEFAULT_DATA_SOURCE_ID
    source["captured_at"] = captured_time.isoformat()
    source["pages"] += [{"page": {"id": claims[n][1], "properties": {
        "Type": prop("Task", "select"), "Claim Token": prop(claims[n][0]),
        "Contact Key": prop(claims[n][2].removeprefix("contact:")),
        "Work Key": prop("wk-" + sha(claims[n][2].encode())), "Worker": prop("fixture-worker")}}, "blocks": []} for n in range(3)]
    snapshot_path = private / "cloud-records.json"
    snapshot_path.write_bytes(json_bytes({"schema": "apply-cloud-snapshot/v1", "exported_at": captured_time.isoformat(), "snapshot": source}))
    snapshot_path.chmod(0o600)
    actor_ids = ("linkedin-accepted-connection-follow-up", "linkedin-connection-request-morning")
    actors, workers = [], []
    for n, identity in enumerate(actor_ids):
        config_path = original["automation_root"] / identity / "automation.toml"
        raw = f'id="{identity}"\nstatus="PAUSED"\ntarget_thread_id="fixture-thread-{n}"\n'.encode()
        config_path.write_bytes(raw)
        actors.append({"id": identity, "member": f"deployment/automations/{identity}.toml", "sha256": sha(raw)})
        workers.append({"automation_id": identity, "thread_id": f"fixture-thread-{n}", "status": "notLoaded",
                        "latest_turn_status": "completed", "observed_at": captured_time.isoformat()})
    workers_path = private / "workers.json"
    workers_path.write_bytes(json_bytes({"schema": "apply-local-worker-boundary/v1", "observed_at": captured_time.isoformat(), "workers": workers}))
    milo_path = private / "milo.json"
    milo_path.write_bytes(json_bytes({"schema": "apply-user-confirmation/v1", "actor": "Milo", "source": "direct_user", "stopped": True,
        "question": "Where is Milo’s application workflow controlled (app, chat, or scheduler), or has it already been stopped? The audits located both Codex outreach schedules but found no Milo controller. I need that information to prove that retiring the desktop schedules leaves one writer.",
        "answer": "it's stopped", "question_item_id": '["request_user_input_async","call_gSVd3NExmiJifZUeS4KBsxhq",0]'}))
    boundary_path = private / "boundary.json"
    boundary_path.write_bytes(json_bytes({"schema": "apply-cutover-boundary/v1", "observed_at": boundary_time.isoformat(), "actors": actors,
        "milo": {"member": "deployment/milo-confirmation.json", "sha256": sha(milo_path.read_bytes())},
        "workers": {"member": "deployment/worker-readbacks.json", "sha256": sha(workers_path.read_bytes())}}))
    kwargs = {key: original[key] for key in ("baseline_path", "baseline_receipt_path", "inventory_receipt_path", "staging", "key", "private_destination_verified", "automation_root")}
    kwargs.update(snapshot_path=snapshot_path, boundary_path=boundary_path, milo_path=milo_path, workers_path=workers_path,
                  output=root / "migration-archives/final-boundary.aesgcm")
    return kwargs, db, journal, baseline


class FinalGrantDerivationTests(unittest.TestCase):
    def derive(self, journal, baseline, source):
        return _final_quarantines(journal, baseline, source, observed_at=NOW,
                                  evidence_refs=["audit:actual-boundary", "audit:fresh-source"])

    def test_six_exact_grants_create_only_permanent_scoped_uncertainty(self):
        with tempfile.TemporaryDirectory() as folder:
            journal, baseline, source, claims = grant_fixture(folder)
            before = journal.read_bytes()
            rows = self.derive(journal, baseline, source)
            self.assertEqual(len(rows), 6)
            for row in rows:
                validate_quarantine(row)
                self.assertNotIn("expires_at", row["payload"])
                self.assertNotIn("delivery_state", row["payload"])
                self.assertEqual(row["payload"]["evidence_refs"], ["audit:actual-boundary", "audit:fresh-source"])
            contact_rows = [row for row in rows if row["payload"]["classification"] == "effect_unknown"]
            self.assertEqual(len(contact_rows), 3)
            first = next(row for row in contact_rows if row["payload"]["identity"].get("contact_ids"))
            self.assertEqual(first["payload"]["identity"]["contact_ids"], ["contact:fixture-existing"])
            self.assertEqual(sum(set(row["payload"]["identity"]) == {"profile_urls"} for row in contact_rows), 2)
            for token, task, resource, _ in claims:
                provenance = {"claim_sha256": hashlib.sha256(token.encode()).hexdigest(),
                              "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
                              "resource_sha256": hashlib.sha256(resource.encode()).hexdigest()}
                self.assertEqual(sum(row["payload"]["provenance"] == provenance for row in rows), 1)
            quarantines = ContactQuarantines(rows)
            for n in range(3):
                result = quarantines.hold_for([{"id": "changed-identity", "payload": {
                    "profile_url": f"https://linkedin.com/in/fixture-person-{n}/?trk=new",
                    "revision": 99, "identity_verified": True}}])
                self.assertEqual(result["reason"], "coordination_effect_unknown")
            self.assertIsNone(quarantines.hold_for([{"id": "unrelated", "payload": {
                "profile_url": "https://www.linkedin.com/in/fixture-unrelated"}}]))
            self.assertEqual(journal.read_bytes(), before)

    def test_existing_result_verification_or_release_requires_reconciliation(self):
        for field in ("verification", "result", "release_evidence"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                journal, baseline, source, _ = grant_fixture(folder)
                with sqlite3.connect(journal) as db:
                    db.execute("UPDATE work_claims SET " + field + "=? WHERE rowid=1", ("source evidence",))
                with self.assertRaisesRegex(ValueError, "outcome evidence"):
                    self.derive(journal, baseline, source)

    def test_unknown_grant_state_or_scope_is_never_reclassified(self):
        for column, value in (("state", "flushing"), ("resource_key", "role:fixture")):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as folder:
                journal, baseline, source, _ = grant_fixture(folder)
                with sqlite3.connect(journal) as db:
                    db.execute("UPDATE work_claims SET " + column + "=? WHERE rowid=1", (value,))
                with self.assertRaisesRegex(ValueError, "scope or state"):
                    self.derive(journal, baseline, source)

    def test_fresh_native_token_and_profile_must_both_agree(self):
        for mode in ("token", "profile", "duplicate"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                journal, baseline, source, _ = grant_fixture(folder)
                if mode == "duplicate":
                    source["pages"].append(deepcopy(source["pages"][0]))
                else:
                    field = "Claim Token" if mode == "token" else "Contact Key"
                    source["pages"][0]["page"]["properties"][field] = prop("different source")
                with self.assertRaisesRegex(ValueError, "fresh native source"):
                    self.derive(journal, baseline, source)

    def test_conflicting_contact_alias_sources_stop_before_any_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            journal, baseline, source, _ = grant_fixture(folder)
            payload = {**baseline["records"][0]["payload"],
                       "source_notion_id": "00000000-0000-4000-8000-000000000002"}
            baseline["records"].append(make_record("contacts", payload, "contact:conflicting"))
            with self.assertRaisesRegex(ValueError, "aliases require reconciliation"):
                self.derive(journal, baseline, source)


class CurrentClaimHistoryTests(unittest.TestCase):
    def preserve(self, rows, baseline, **updates):
        local = {"schema": SCHEMA, "records": rows, "source_manifest": [], "blocked": [], **updates}
        return _preserve_current_claims(local, baseline, archive_reference={"archive": "migration-archives/final.aesgcm"},
                                       journal_sha256="a" * 64, observed_at=NOW)

    def test_new_claims_are_retained_and_changed_prior_claims_are_append_only(self):
        old = make_record("audit_events", {"token": "fixture-old", "active": 1, "state": "reserved"},
                          "legacy-claim:fixture-old", "historical_reserved")
        changed = make_record("audit_events", {"token": "fixture-old", "active": 0, "state": "released"},
                              old["id"], "historical_released")
        new = make_record("audit_events", {"token": "fixture-new", "active": 1, "state": "published"},
                          "legacy-claim:fixture-new", "historical_published")
        baseline = {"schema": SCHEMA, "records": [old], "source_manifest": [], "blocked": []}
        delta = self.preserve([changed, new], baseline)
        merged = combine_exports([baseline, delta])
        self.assertIn(old, merged["records"])
        self.assertIn(new, merged["records"])
        self.assertNotIn(changed, merged["records"])
        observation = next(row for row in merged["records"] if row["status"] == "historical_claim_observation")
        self.assertEqual(observation["payload"]["source_record_id"], old["id"])
        self.assertEqual(observation["payload"]["current_payload"], changed["payload"])
        self.assertEqual(observation["payload"]["current_payload_hash"], changed["hash"])
        self.assertEqual(observation["id"], "legacy-claim-observation:" + content_hash(observation["payload"]))
        self.assertFalse(observation["payload"]["claim_modified_by_capture"])
        self.assertFalse(observation["payload"]["replay_allowed"])

    def test_identical_prior_claim_is_never_rewritten_or_reclassified(self):
        old = make_record("audit_events", {"state": "reserved"}, "legacy-claim:fixture", "historical_reserved")
        self.assertEqual(self.preserve([old], {"records": [old]})["records"], [old])

    def test_blocked_source_cannot_be_filtered_into_a_success(self):
        with self.assertRaisesRegex(ValueError, "normalization contains conflicts"):
            self.preserve([], {"records": []}, blocked=[{"reason": "fixture-conflict"}])

    def test_actual_cli_failure_exits_nonzero_without_private_details(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/archive_final_cutover.py")]
        for argument in ("baseline", "baseline-receipt", "inventory-receipt", "snapshot", "boundary",
                         "milo", "workers", "staging", "key", "output"):
            command += ["--" + argument, "private-fixture-path"]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(result.stderr), {"status": "blocked", "error_type": "ValueError", "source_effect_replay": False})
        self.assertNotIn("private-fixture-path", result.stderr)


class FinalCaptureTests(unittest.TestCase):
    def test_minimal_boundary_input_derives_all_five_exact_member_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            kwargs, db, journal, _ = capture_fixture(Path(folder))
            full = json.loads(kwargs["boundary_path"].read_bytes())
            kwargs["boundary_path"].write_bytes(json_bytes({"schema": full["schema"], "observed_at": full["observed_at"]}))
            try:
                receipt = capture_final_cutover(**kwargs)
                evidence = json.loads((kwargs["staging"] / "deployment/cutover-evidence.json").read_bytes())
                self.assertTrue(receipt["boundary_validated"])
                self.assertEqual(set(evidence), {"schema", "observed_at", "actors", "milo", "workers", "claims", "snapshot"})
                for binding in evidence["actors"] + [evidence[name] for name in ("milo", "workers", "claims", "snapshot")]:
                    self.assertEqual(binding["sha256"], sha((kwargs["staging"] / binding["member"]).read_bytes()))
            finally:
                db.close()

    def test_real_boundary_validator_authenticated_restore_and_baseline_immutability(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kwargs, db, journal, baseline = capture_fixture(root)
            before = journal.read_bytes()
            try:
                receipt = capture_final_cutover(**kwargs)
                restored = root / "restored"
                restore_archive(kwargs["output"], restored, key_path=kwargs["key"])
                merged = json.loads((restored / receipt["normalized_member"]).read_bytes())
                self.assertTrue(receipt["restore_verified"])
                self.assertTrue(receipt["boundary_validated"])
                self.assertEqual(receipt["quarantine_records"], 6)
                self.assertFalse(receipt["production_activated"])
                self.assertFalse(receipt["cutover_record_created"])
                self.assertFalse(any(row["id"] == "deployment:cutover:v2" for row in merged["records"]))
                for old in baseline["records"]:
                    self.assertIn(old, merged["records"])
                self.assertEqual(sum(row["id"].startswith("legacy-claim:") for row in merged["records"]), 6)
                self.assertEqual(sum(row["status"] == "historical_claim_observation" for row in merged["records"]), 1)
                boundary = json.loads((restored / "deployment/cutover-evidence.json").read_bytes())
                self.assertEqual(boundary["claims"]["sha256"], sha((restored / boundary["claims"]["member"]).read_bytes()))
                self.assertEqual((restored / boundary["snapshot"]["member"]).read_bytes(), kwargs["snapshot_path"].read_bytes())
                self.assertEqual(journal.read_bytes(), before)
                self.assertTrue(all(set(item) == {"member_id", "sha256", "bytes"} for item in receipt["files"]))
                self.assertNotIn("fixture-person", json.dumps(receipt))
            finally:
                db.close()

    def test_active_writer_busy_worker_and_stale_snapshot_block_archive(self):
        for mode in ("active", "busy", "stale"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                kwargs, db, journal, _ = capture_fixture(Path(folder))
                try:
                    if mode == "active":
                        path = next(kwargs["automation_root"].rglob("automation.toml"))
                        path.write_bytes(path.read_bytes().replace(b'"PAUSED"', b'"ACTIVE"'))
                    elif mode == "busy":
                        value = json.loads(kwargs["workers_path"].read_bytes())
                        value["workers"][0]["status"] = "running"
                        kwargs["workers_path"].write_bytes(json_bytes(value))
                    else:
                        value = json.loads(kwargs["snapshot_path"].read_bytes())
                        value["snapshot"]["captured_at"] = "2026-10-01T00:00:00Z"
                        kwargs["snapshot_path"].write_bytes(json_bytes(value))
                    with self.assertRaises(ValueError):
                        capture_final_cutover(**kwargs)
                    self.assertFalse(kwargs["output"].exists())
                finally:
                    db.close()

    def test_source_change_during_encryption_removes_unverified_archive(self):
        from scripts.encrypted_backup import create_backup
        with tempfile.TemporaryDirectory() as folder:
            kwargs, db, journal, _ = capture_fixture(Path(folder))
            def changed(*args, **options):
                receipt = create_backup(*args, **options)
                kwargs["workers_path"].write_bytes(b"Concurrent source edit")
                return receipt
            try:
                with patch("scripts.archive_final_cutover.create_backup", side_effect=changed):
                    with self.assertRaisesRegex(ValueError, "Final source changed"):
                        capture_final_cutover(**kwargs)
                self.assertFalse(kwargs["output"].exists())
                self.assertFalse(kwargs["output"].with_suffix(".receipt.json").exists())
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
