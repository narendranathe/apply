#!/usr/bin/env python3
"""Encrypt a fresh Notion snapshot and append history without claiming cutover.

All reads are local. Source contacts, queue rows and bodies are preserved as
exact archived bytes; only new immutable history and a pre-cutover audit are
normalized. This tool does not contact Notion or retire/activate any writer.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.cutover import CORE, build_source_delta
from cloud.migration import combine_exports
from cloud.records import content_hash, make_record, validate_record
from scripts.archive_local_state import json_bytes, read_source, sha, validate_baseline, write_private
from scripts.encrypted_backup import create_backup

RAW_MEMBER = "source/notion/cloud-records.json"
NORMALIZED_MEMBER = "normalized/merged-historical-records.json"


def _private_input(path):
    path = Path(path)
    if ".migration-private" not in path.resolve().parts or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("Plaintext input must be private and in .migration-private")
    return read_source(path)


def _private_directories(path):
    missing = []
    current = Path(path)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def capture_notion_delta(*, snapshot_path, baseline_path, baseline_receipt_path,
                         staging, key, output, private_destination_verified):
    staging, output = Path(staging), Path(output)
    if not private_destination_verified:
        raise ValueError("Actual private destination must be verified before capture")
    if ".migration-private" not in staging.resolve().parts:
        raise ValueError("Plaintext staging must be in .migration-private")
    if (output.parent.name != "migration-archives" or output.is_symlink()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.aesgcm", output.name)):
        raise ValueError("Output must be a migration-archives authenticated archive")
    receipt_path = output.with_suffix(".receipt.json")
    if staging.exists() or staging.is_symlink() or output.exists() or receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("Never overwrite a historical capture")
    baseline_body = _private_input(baseline_path)
    receipt_body = read_source(baseline_receipt_path)
    baseline = validate_baseline(baseline_body, json.loads(receipt_body))
    for row in baseline["records"]:
        validate_record(row)
    canonical_baseline = combine_exports([baseline])
    if len(canonical_baseline["records"]) != len(baseline["records"]):
        raise ValueError("Baseline contains duplicate immutable identities")
    raw = _private_input(snapshot_path)
    wrapper = json.loads(raw)
    if (not isinstance(wrapper, dict) or set(wrapper) != {"schema", "snapshot", "exported_at"}
            or wrapper.get("schema") != "apply-cloud-snapshot/v1" or not isinstance(wrapper.get("snapshot"), dict)):
        raise ValueError("Unsupported hosted snapshot wrapper")
    try:
        exported_at = datetime.fromisoformat(wrapper["exported_at"].replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Hosted snapshot timestamp must be timezone aware") from None
    if exported_at.tzinfo is None:
        raise ValueError("Hosted snapshot timestamp must be timezone aware")
    source = wrapper["snapshot"]
    snapshot_hash = content_hash({name: value for name, value in source.items() if name != "hash"})
    reference = {"archive": "migration-archives/" + output.name, "member": RAW_MEMBER,
                 "member_sha256": sha(raw), "snapshot_sha256": snapshot_hash}
    delta = build_source_delta(source, baseline, archive_reference=reference)
    audits = [row for row in delta["records"] if row["kind"] == "audit_events"]
    if len(audits) != 1 or audits[0]["payload"].get("schema") != "apply-source-cutover-audit/v1":
        raise ValueError("Expected one source snapshot audit")
    audit = audits[0]
    payload = {**audit["payload"], "capture_purpose": "pre_cutover", "boundary_complete": False,
               "local_actors_retired": False, "cloud_authoritative": False,
               "history_reconciliation_pending": True, "external_effects": False}
    audit = make_record("audit_events", payload, "source-pre-cutover-snapshot:" + snapshot_hash, "pre_cutover_capture")
    delta["records"] = [row for row in delta["records"] if row["kind"] != "audit_events"] + [audit]
    merged = combine_exports([baseline, delta])
    merged_by_id = {row["id"]: row for row in merged["records"]}
    if any(any(merged_by_id[row["id"]][field] != row[field] for field in CORE) for row in baseline["records"]):
        raise ValueError("An immutable baseline record changed")
    new_history = sum(row["kind"] == "outreach_events" for row in delta["records"])
    bodies = {RAW_MEMBER: raw, "source/notion/baseline-input.json": baseline_body,
              "source/notion/baseline-input.receipt.json": receipt_body,
              "normalized/notion-source-delta.json": json_bytes(delta), NORMALIZED_MEMBER: json_bytes(merged)}
    # Reuse the production credential patterns for generated data as well.
    from scripts.security_check import PATTERNS
    if any(pattern.search(body) for body in bodies.values() for pattern in PATTERNS):
        raise ValueError("Credential-pattern scan blocked Notion capture")
    for path, before in ((snapshot_path, raw), (baseline_path, baseline_body), (baseline_receipt_path, receipt_body)):
        if read_source(path) != before:
            raise ValueError("Capture input changed; obtain a stable source")
    _private_directories(staging)
    members = {}
    for name, body in bodies.items():
        target = staging / name
        _private_directories(target.parent)
        write_private(target, body)
        members[name] = target
    receipt = create_backup(members, output, key_path=Path(key), private_destination_verified=True)
    receipt.update({"archive": reference["archive"], "normalized_member": NORMALIZED_MEMBER,
                    "canonical_records": len(merged["records"]), "baseline_records": len(baseline["records"]),
                    "delta_records": len(delta["records"]), "new_outreach_events": new_history,
                    "snapshot_pages": len(source["pages"]), "snapshot_sha256": snapshot_hash,
                    "raw_snapshot_member_sha256": sha(raw), "baseline_sha256": sha(baseline_body),
                    "historical_manifest_sha256": content_hash(merged), "capture_purpose": "pre_cutover",
                    "boundary_complete": False, "local_actors_retired": False,
                    "history_reconciliation_pending": True, "cloud_authoritative": False,
                    "source_effect_replay": False, "credential_pattern_scan": "passed"})
    receipt["files"] = [{"member_id": sha(row["name"].encode()), "sha256": row["sha256"], "bytes": row["bytes"]}
                        for row in receipt["files"]]
    write_private(receipt_path, json_bytes(receipt) + b"\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--baseline-receipt", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-destination-verified", action="store_true")
    args = parser.parse_args()
    receipt = capture_notion_delta(snapshot_path=args.snapshot, baseline_path=args.baseline,
        baseline_receipt_path=args.baseline_receipt, staging=args.staging, key=args.key,
        output=args.output, private_destination_verified=args.private_destination_verified)
    fields = ("archive_sha256", "archive_bytes", "canonical_records", "baseline_records", "delta_records",
              "new_outreach_events", "snapshot_pages", "restore_verified", "capture_purpose",
              "boundary_complete", "local_actors_retired", "cloud_authoritative")
    print(json.dumps({field: receipt[field] for field in fields}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__,
                          "source_effect_replay": False}), file=sys.stderr)
        raise SystemExit(2)
