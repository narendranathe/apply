#!/usr/bin/env python3
"""Preserve a provisional local recovery snapshot without claiming cutover.

This deliberately does not normalize new outreach events, reconcile claims,
contact Notion, retire writers, or modify the source directories. The single
new normalized row is an audit event; raw source bytes remain encrypted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.migration import SCHEMA, combine_exports
from cloud.records import make_record
from scripts.encrypted_backup import create_backup, DENIED_NAMES, safe_name, sqlite_snapshot
from scripts.security_check import PATTERNS

AUTOMATION_IDS = {"linkedin-accepted-connection-follow-up", "linkedin-connection-request-morning"}


def sha(body):
    return hashlib.sha256(body).hexdigest()


def read_source(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Source must be an existing regular file")
    body = path.read_bytes()
    if any(pattern.search(body) for pattern in PATTERNS):
        raise ValueError("Credential-pattern scan blocked provisional capture")
    return body


def write_private(path, body):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def validate_baseline(body, receipt):
    member = receipt.get("normalized_member", "normalized/merged-historical-records.json")
    expected_id = sha(member.encode())
    entries = [row for row in receipt.get("files", [])
               if row.get("name") == member or row.get("member_id") == expected_id]
    if (receipt.get("restore_verified") is not True or len(entries) != 1
            or entries[0].get("sha256") != sha(body) or entries[0].get("bytes") != len(body)):
        raise ValueError("Baseline differs from authenticated archive receipt")
    baseline = json.loads(body)
    combine_exports([baseline])
    if len(baseline["records"]) != receipt.get("canonical_records"):
        raise ValueError("Baseline record count differs from authenticated receipt")
    return baseline


def source_inventory(baseline, receipt):
    logs = list(dict.fromkeys(Path(item["source_path"]) for item in baseline["source_manifest"]
                             if item.get("classification") == "historical_outreach_log"))
    if len(logs) != 2:
        raise ValueError("Exactly two original outreach log paths are required")
    roots = [path.parent for path in logs if (path.parent / "sync-state/journal.sqlite3").is_file()]
    if len(roots) != 1:
        raise ValueError("Current coordination source root is ambiguous")
    coordination = roots[0]
    entries = [item["name"] for item in receipt.get("files", [])
               if item.get("name", "").startswith("source/coordination/")]
    if len(entries) != 42 or len(set(entries)) != 42:
        raise ValueError("Expected exactly 42 original coordination inventory members")
    sources = {}
    for name in entries:
        if not safe_name(name) or any(term in name.lower() for term in DENIED_NAMES):
            raise ValueError("Unsafe or credential-class source member")
        sources[name] = coordination / name.removeprefix("source/coordination/")
    journal_name = "source/coordination/sync-state/journal.sqlite3"
    if sources.get(journal_name) != coordination / "sync-state/journal.sqlite3":
        raise ValueError("Original inventory must include the coordination journal")
    if sources.get("source/coordination/Outreach_Log.md") != coordination / "Outreach_Log.md":
        raise ValueError("Original inventory must include the coordination log")
    sources["source/canonical/Outreach_Log.md"] = next(path for path in logs if path.parent != coordination)
    return sources, journal_name


def validate_writer_audit(audit, originals, journal, automation_paths, *, now):
    """Verify audit observations against captured bytes, never claim resolution."""
    if audit.get("schema") != "apply-local-writers-audit/v1" or audit.get("read_only") is not True:
        raise ValueError("A read-only local writer audit is required")
    observed = datetime.fromisoformat(audit["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= 86400:
        raise ValueError("Writer audit timestamp is stale or invalid")
    source_journal = audit.get("journal", {})
    journal_name = "source/coordination/sync-state/journal.sqlite3"
    if sha(originals[journal_name]) != source_journal.get("sha256"):
        raise ValueError("Writer audit journal source hash is stale")
    log = originals["source/coordination/Outreach_Log.md"]
    if (originals["source/canonical/Outreach_Log.md"] != log
            or audit.get("logs", {}).get("same") is not True
            or audit["logs"].get("sha256") != sha(log)
            or audit["logs"].get("bytes") != len(log)):
        raise ValueError("Writer audit log source hashes are stale or mirrors differ")
    with sqlite3.connect(Path(journal).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        count = db.execute("SELECT count(*) FROM events").fetchone()[0]
        pending = db.execute("SELECT count(*) FROM events WHERE remote_url IS NULL OR remote_url=''").fetchone()[0]
        pull = db.execute("SELECT value FROM meta WHERE key='last_complete_pull'").fetchone()
        pull = pull[0] if pull else None
        if (count != source_journal.get("events") or pending != source_journal.get("pending_remote")
                or pull != source_journal.get("last_complete_pull")):
            raise ValueError("Writer audit event observations are stale")
        claims = list(db.execute("SELECT * FROM work_claims"))
    active = [row for row in claims if row["active"]]
    states = dict(Counter(row["state"] for row in claims))
    expected = audit.get("claims", {})
    expiry = sum(row["lease_until"] <= now.timestamp() for row in active)
    active_hashes = [{"token_sha256": sha(row["token"].encode()),
                      "task_id_sha256": sha(row["task_id"].encode()),
                      "resource_key_sha256": sha(row["resource_key"].encode()),
                      "state": row["state"],
                      "lease_until_utc": datetime.fromtimestamp(row["lease_until"], timezone.utc).isoformat()}
                     for row in active]
    sort_hashes = lambda rows: sorted(rows, key=lambda row: row["token_sha256"])
    if (expected.get("total") != len(claims) or expected.get("active") != len(active)
            or expected.get("states") != states or expected.get("expired_active") != expiry
            or expected.get("unexpired_active") != len(active) - expiry
            or expected.get("active_without_result") != sum(not row["result"] for row in active)
            or expected.get("active_without_release_evidence") != sum(not row["release_evidence"] for row in active)
            or sort_hashes(expected.get("active_claim_hashes", [])) != sort_hashes(active_hashes)):
        raise ValueError("Writer audit claim observations are stale")
    actors = audit.get("automations", [])
    if len(actors) != 2 or {row.get("id") for row in actors} != AUTOMATION_IDS:
        raise ValueError("Writer audit must identify both legacy automations")
    for actor in actors:
        raw = originals[automation_paths[actor["id"]]]
        value = tomllib.loads(raw.decode())
        if (sha(raw) != actor.get("raw_toml_sha256") or value.get("status") != actor.get("status")
                or value.get("rrule") != actor.get("rrule")
                or sha(value.get("prompt", "").encode()) != actor.get("prompt_sha256")):
            raise ValueError("Writer audit automation source hash is stale")
    return {"journal_events": count, "pending_remote": pending, "active_claims": len(active),
            "expired_active_claims": expiry, "last_complete_pull": pull,
            "desktop_schedules_active": sum(row["status"] == "ACTIVE" for row in actors)}


def capture_local_state(*, baseline_path, baseline_receipt_path, inventory_receipt_path,
                        writer_audit_path, staging, key, output, private_destination_verified,
                        automation_root=None, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Capture timestamp must be timezone aware")
    staging, output = Path(staging), Path(output)
    if ".migration-private" not in staging.resolve().parts:
        raise ValueError("Provisional plaintext staging must be in .migration-private")
    if not private_destination_verified:
        raise ValueError("Actual private destination must be verified before capture")
    if staging.exists() or output.exists() or output.with_suffix(".receipt.json").exists():
        raise ValueError("Never overwrite a provisional source capture")
    baseline_body = read_source(baseline_path)
    baseline_receipt_body = read_source(baseline_receipt_path)
    inventory_receipt_body = read_source(inventory_receipt_path)
    baseline = validate_baseline(baseline_body, json.loads(baseline_receipt_body))
    sources, journal_name = source_inventory(baseline, json.loads(inventory_receipt_body))
    audit_body = read_source(writer_audit_path)
    audit = json.loads(audit_body)
    if Path(audit.get("journal", {}).get("path", "")).resolve() != sources[journal_name].resolve():
        raise ValueError("Writer audit identifies a different source journal")
    sources["source/provisional/writer-audit.json"] = Path(writer_audit_path)
    automation_root = Path(automation_root or Path.home() / ".codex/automations")
    automation_paths = {}
    for identity in sorted(AUTOMATION_IDS):
        name = "source/provisional/actor-configurations/" + identity + ".toml"
        sources[name] = automation_root / identity / "automation.toml"
        automation_paths[identity] = name
    originals = {}
    for name, source in sources.items():
        if not safe_name(name) or any(term in name.lower() for term in DENIED_NAMES):
            raise ValueError("Unsafe or credential-class archive member")
        originals[name] = read_source(source)
    if originals["source/provisional/writer-audit.json"] != audit_body:
        raise ValueError("Writer audit changed while preparing capture")
    journal = sources[journal_name]
    wal = Path(str(journal) + "-wal")
    wal_body = read_source(wal) if wal.exists() else None
    staging.mkdir(mode=0o700, parents=True)
    scratch = staging / "sqlite-working-copy"
    scratch.mkdir(mode=0o700)
    scratch_journal = scratch / "journal.sqlite3"
    members, provenance = {}, []
    try:
        write_private(scratch_journal, originals[journal_name])
        if wal_body is not None:
            write_private(Path(str(scratch_journal) + "-wal"), wal_body)
        for name, source in sources.items():
            target = staging / name
            if name == journal_name:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                sqlite_snapshot(scratch_journal, target)
                read_source(target)
            else:
                write_private(target, originals[name])
            members[name] = target
            provenance.append({"member": name, "original_source_path": str(source.resolve()),
                               "original_source_sha256": sha(originals[name]),
                               "captured_sha256": sha(target.read_bytes()), "bytes": target.stat().st_size,
                               "capture_method": "stable_private_copy_then_readonly_sqlite_backup_api"
                               if name == journal_name else "exact_bytes"})
        observations = validate_writer_audit(audit, originals, members[journal_name], automation_paths, now=now)
        for name, source in sources.items():
            if read_source(source) != originals[name]:
                raise ValueError("A source changed during provisional capture; recapture from fresh audit")
        if (read_source(wal) if wal.exists() else None) != wal_body:
            raise ValueError("Source WAL changed during provisional capture; recapture from fresh audit")
        if read_source(baseline_path) != baseline_body or read_source(writer_audit_path) != audit_body:
            raise ValueError("Capture evidence changed during provisional capture")
    finally:
        shutil.rmtree(scratch)
    opaque = [{"member_id": sha(item["member"].encode()), "sha256": item["captured_sha256"],
               "bytes": item["bytes"], "original_source_sha256": item["original_source_sha256"],
               "capture_method": item["capture_method"]} for item in provenance]
    payload = {"schema": "apply-provisional-local-capture/v1", "captured_at": now.isoformat(),
               "capture_purpose": "encrypted_recovery_only_before_reconciliation",
               "boundary_complete": False, "local_actors_retired": False,
               "history_reconciliation_pending": True, "raw_outreach_normalized": False,
               "external_effects": False, "replay_allowed": False,
               "claims_reconciled": False, "claims_modified": False,
               "baseline_sha256": sha(baseline_body), "baseline_records": len(baseline["records"]),
               "writer_audit_sha256": sha(audit_body), "raw_sources": opaque,
               "source_stability_verified": True, "credential_pattern_scan": "passed",
               "observations": observations}
    audit_record = make_record("audit_events", payload, status="pre_cutover_capture")
    delta = {"schema": SCHEMA, "records": [audit_record], "provenance": {
        audit_record["id"]: [{"historical_only": True, "provisional": True, "replay_allowed": False}]},
        "source_manifest": [], "blocked": [], "historical_only": True,
        "cloud_authoritative": False, "replay_allowed": False}
    merged = combine_exports([baseline, delta])
    additions = {"source/provisional/capture-provenance.json": json_bytes(provenance),
                 "source/provisional/baseline-input.json": baseline_body,
                 "source/provisional/baseline-input.receipt.json": baseline_receipt_body,
                 "source/provisional/inventory-input.receipt.json": inventory_receipt_body,
                 "normalized/provisional-local-state-audit.json": json_bytes(delta),
                 "normalized/merged-historical-records.json": json_bytes(merged)}
    for name, body in additions.items():
        if any(pattern.search(body) for pattern in PATTERNS):
            raise ValueError("Credential-pattern scan blocked normalized capture evidence")
        target = staging / name
        write_private(target, body)
        members[name] = target
    receipt = create_backup(members, output, key_path=Path(key), private_destination_verified=True)
    receipt.update({"archive": str(output), "normalized_member": "normalized/merged-historical-records.json",
                    "canonical_records": len(merged["records"]), "baseline_records": len(baseline["records"]),
                    "delta_records": 1, "source_files": len(sources), "coordination_inventory_files": 42,
                    "source_effect_replay": False, "provisional": True,
                    "boundary_complete": False, "local_actors_retired": False,
                    "history_reconciliation_pending": True, "raw_outreach_normalized": False,
                    "source_stability_verified": True, "credential_pattern_scan": "passed",
                    "journal_events": observations["journal_events"],
                    "baseline_sha256": sha(baseline_body), "writer_audit_sha256": sha(audit_body)})
    receipt["files"] = [{"member_id": sha(row["name"].encode()), "sha256": row["sha256"], "bytes": row["bytes"]}
                        for row in receipt["files"]]
    write_private(output.with_suffix(".receipt.json"), json_bytes(receipt) + b"\n")
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--baseline-receipt", type=Path, required=True)
    p.add_argument("--inventory-receipt", type=Path, required=True)
    p.add_argument("--writer-audit", type=Path, required=True)
    p.add_argument("--staging", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--private-destination-verified", action="store_true")
    args = p.parse_args()
    receipt = capture_local_state(baseline_path=args.baseline, baseline_receipt_path=args.baseline_receipt,
                                  inventory_receipt_path=args.inventory_receipt, writer_audit_path=args.writer_audit,
                                  staging=args.staging, key=args.key, output=args.output,
                                  private_destination_verified=args.private_destination_verified)
    print(json.dumps({field: receipt[field] for field in ("archive_sha256", "archive_bytes", "canonical_records",
                     "baseline_records", "delta_records", "source_files", "journal_events", "provisional",
                     "boundary_complete", "local_actors_retired", "restore_verified", "source_stability_verified")},
                     sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "source_effect_replay": False}), file=sys.stderr)
        raise SystemExit(2)
