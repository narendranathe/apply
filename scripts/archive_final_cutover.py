#!/usr/bin/env python3
"""Archive a stopped local boundary and fresh Notion state without activation.

Only local source reads and private archive writes occur here. The hosted
single writer must still import, read back, validate and activate the result.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.coordination_quarantine import make_quarantine
from cloud.cutover import CORE, _CapturedStore, build_source_delta
from cloud.linkedin_ingestion import profile_url
from cloud.migration import SCHEMA, combine_exports, export_history, merge_history
from cloud.notion import _text
from cloud.records import content_hash, make_record, validate_record
from scripts.archive_local_state import AUTOMATION_IDS, json_bytes, read_source, sha, source_inventory, validate_baseline, write_private
from scripts.archive_notion_delta import _contact_aliases, _new_contact_records, _private_directories, _private_input, _source_identity
from scripts.encrypted_backup import create_backup, sqlite_snapshot
from scripts.security_check import PATTERNS

JOURNAL = "source/coordination/sync-state/journal.sqlite3"
SNAPSHOT = "source/notion-final/cloud-records.json"
NORMALIZED = "normalized/merged-historical-records.json"


def _remote_events(source):
    decoder = _CapturedStore({entry["page"]["id"]: entry["blocks"] for entry in source["pages"]})
    result = []
    for entry in source["pages"]:
        page, props = entry["page"], entry["page"]["properties"]
        if _text(props.get("Cloud ID")) or _text(props.get("Cloud Envelope")):
            row = decoder._decode_cloud(page)
            if row["kind"] != "outreach_events":
                continue
        elif _text(props.get("Event ID")):
            row = decoder._decode_legacy(page)
        else:
            continue
        result.append({"payload": row["payload"], "notion_url": page.get("url", "")})
    return result


def _final_quarantines(journal, baseline, source, *, observed_at, evidence_refs):
    """Derive exact scopes from final grants; outcomes remain unverified."""
    with sqlite3.connect(Path(journal).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        claims = [dict(row) for row in db.execute("SELECT * FROM work_claims WHERE active=1")]
    contacts = [row for row in baseline["records"] if row["kind"] == "contacts"]
    result = []
    for claim in claims:
        if claim["verification"] or claim["result"] or claim["release_evidence"]:
            raise ValueError("Final claim has outcome evidence requiring reconciliation")
        resource = claim["resource_key"]
        if claim["state"] == "published" and resource.startswith("contact:"):
            profile = profile_url(resource.removeprefix("contact:"))
            matching = [row for row in contacts if profile in _contact_aliases(row["payload"])]
            sources = {_source_identity(row["payload"].get("source_notion_id")) for row in matching}
            if len(sources) > 1 or None in sources:
                raise ValueError("Claim contact aliases require reconciliation")
            identity = {"profile_urls": [profile]}
            if matching:
                identity["contact_ids"] = sorted({row["id"] for row in matching}
                    | {row["payload"]["contact_id"] for row in matching if row["payload"].get("contact_id")})
                identity["source_notion_ids"] = sorted({row["payload"]["source_notion_id"] for row in matching})
            queues = [entry["page"] for entry in source["pages"]
                      if _text(entry["page"]["properties"].get("Claim Token")) == claim["token"]]
            if len(queues) != 1 or profile not in _contact_aliases({"properties": queues[0]["properties"]}):
                raise ValueError("Final contact grant differs from fresh native source")
            classification = "effect_unknown"
        elif claim["state"] == "reserved" and resource.startswith("company:"):
            identity, classification = {"company_ids": [resource]}, "unstarted_unverified"
        else:
            raise ValueError("Final claim scope or state requires reconciliation")
        payload = {"schema": "apply-coordination-quarantine/v1", "active": True,
                   "classification": classification, "identity": identity,
                   "provenance": {"claim_sha256": sha(claim["token"].encode()),
                       "task_sha256": sha(claim["task_id"].encode()),
                       "resource_sha256": sha(resource.encode())},
                   "observed_at": observed_at, "evidence_refs": list(evidence_refs)}
        result.append(make_quarantine(payload))
    return result


def _preserve_current_claims(local, baseline, *, archive_reference, journal_sha256, observed_at):
    """Keep new claims and append observations of changed historical claims."""
    if local.get("blocked"):
        raise ValueError("Final local history normalization contains conflicts")
    previous = {row["id"]: row for row in baseline["records"]}
    result = []
    for row in local["records"]:
        old = previous.get(row["id"])
        if row["kind"] != "audit_events" or old is None or all(old[field] == row[field] for field in CORE):
            result.append(row)
            continue
        if not row["id"].startswith("legacy-claim:"):
            raise ValueError("Changed local audit requires reconciliation")
        payload = {"schema": "apply-legacy-claim-observation/v1", "source_record_id": row["id"],
                   "baseline_record_hash": old["hash"], "current_payload": deepcopy(row["payload"]),
                   "current_payload_hash": row["hash"], "observed_at": observed_at,
                   "source_member": JOURNAL, "source_member_sha256": journal_sha256,
                   "archive_reference": deepcopy(archive_reference), "historical_only": True,
                   "replay_allowed": False, "claim_modified_by_capture": False}
        result.append(make_record("audit_events", payload,
            "legacy-claim-observation:" + content_hash(payload), "historical_claim_observation"))
    return {**local, "records": result}


def capture_final_cutover(*, baseline_path, baseline_receipt_path, inventory_receipt_path,
                          snapshot_path, boundary_path, milo_path, workers_path,
                          staging, key, output, private_destination_verified,
                          automation_root=None):
    if private_destination_verified is not True:
        raise ValueError("Actual private destination must be verified")
    staging, output = Path(staging), Path(output)
    if (".migration-private" not in staging.resolve().parts or staging.exists() or staging.is_symlink()
            or output.parent.name != "migration-archives" or output.exists() or output.is_symlink()
            or output.with_suffix(".receipt.json").exists() or output.with_suffix(".receipt.json").is_symlink()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.aesgcm", output.name)):
        raise ValueError("Private fresh staging and immutable archive targets are required")
    baseline_body, raw = _private_input(baseline_path), _private_input(snapshot_path)
    baseline_receipt, inventory = read_source(baseline_receipt_path), read_source(inventory_receipt_path)
    baseline = validate_baseline(baseline_body, json.loads(baseline_receipt))
    for row in baseline["records"]:
        validate_record(row)
    if len(combine_exports([baseline])["records"]) != len(baseline["records"]):
        raise ValueError("Duplicate baseline identities")
    wrapper = json.loads(raw)
    if (not isinstance(wrapper, dict) or set(wrapper) != {"schema", "exported_at", "snapshot"}
            or wrapper["schema"] != "apply-cloud-snapshot/v1"):
        raise ValueError("Complete hosted raw snapshot required")
    source = wrapper["snapshot"]
    snapshot_hash = content_hash({name: value for name, value in source.items() if name != "hash"})
    reference = {"archive": "migration-archives/" + output.name, "member": SNAPSHOT,
                 "member_sha256": sha(raw), "snapshot_sha256": snapshot_hash}
    remote_delta = build_source_delta(source, baseline, archive_reference=reference)
    boundary_body = read_source(boundary_path)
    evidence = json.loads(boundary_body)
    observed_at = evidence["observed_at"]
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    captured = datetime.fromisoformat(source["captured_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or captured < observed:
        raise ValueError("Fresh full snapshot must follow stopped boundary")
    sources, _ = source_inventory(baseline, json.loads(inventory))
    root = Path(automation_root or Path.home() / ".codex/automations")
    sources.update({"deployment/automations/" + identity + ".toml": root / identity / "automation.toml"
                    for identity in sorted(AUTOMATION_IDS)})
    sources.update({SNAPSHOT: Path(snapshot_path), "deployment/milo-confirmation.json": Path(milo_path),
                    "deployment/worker-readbacks.json": Path(workers_path),
                    "source/final/boundary-input.json": Path(boundary_path),
                    "source/final/baseline-input.json": Path(baseline_path),
                    "source/final/baseline-input.receipt.json": Path(baseline_receipt_path),
                    "source/final/inventory-input.receipt.json": Path(inventory_receipt_path)})
    originals = {name: read_source(path) for name, path in sources.items()}
    if (originals[SNAPSHOT] != raw or originals["source/final/boundary-input.json"] != boundary_body
            or originals["source/final/baseline-input.json"] != baseline_body
            or originals["source/final/baseline-input.receipt.json"] != baseline_receipt
            or originals["source/final/inventory-input.receipt.json"] != inventory):
        raise ValueError("Boundary inputs changed during preparation")
    journal = sources[JOURNAL]
    wal = Path(str(journal) + "-wal")
    wal_body = read_source(wal) if wal.exists() else None
    _private_directories(staging)
    scratch = staging / "sqlite-working-copy"
    scratch.mkdir(mode=0o700)
    members = {}
    try:
        scratch_db = scratch / "journal.sqlite3"
        write_private(scratch_db, originals[JOURNAL])
        if wal_body is not None:
            write_private(Path(str(scratch_db) + "-wal"), wal_body)
        for name, body in originals.items():
            target = staging / name
            _private_directories(target.parent)
            if name == JOURNAL:
                sqlite_snapshot(scratch_db, target)
                read_source(target)
            else:
                write_private(target, body)
            members[name] = target
    finally:
        shutil.rmtree(scratch)
    if members["source/canonical/Outreach_Log.md"].read_bytes() != members["source/coordination/Outreach_Log.md"].read_bytes():
        raise ValueError("Final outreach log mirrors differ")
    # Raw current grants and newly seen claims survive. Changed historical
    # grants become additional observations without replacing earlier records.
    local = export_history(members[JOURNAL], [members["source/coordination/Outreach_Log.md"],
                           members["source/canonical/Outreach_Log.md"]])
    local = _preserve_current_claims(local, baseline, archive_reference=reference,
                                     journal_sha256=sha(members[JOURNAL].read_bytes()), observed_at=observed_at)
    reconciled = merge_history(local, [], _remote_events(source))
    final_id = "final-cutover-capture:" + content_hash({"journal": sha(members[JOURNAL].read_bytes()),
                 "snapshot": snapshot_hash, "boundary": sha(boundary_body)})
    source_audits = [row for row in remote_delta["records"] if row["kind"] == "audit_events"]
    if len(source_audits) != 1:
        raise ValueError("Expected exact final source audit")
    quarantines = _final_quarantines(members[JOURNAL], baseline, source, observed_at=observed_at,
                                    evidence_refs=[final_id, source_audits[0]["id"]])
    contacts = _new_contact_records(source, baseline, reference)
    final_audit = make_record("audit_events", {"schema": "apply-final-local-capture/v1",
        "observed_at": observed_at, "source_effect_replay": False, "cloud_authoritative": False,
        "history_readback_pending": True, "production_activated": False, "claims_modified": False,
        "quarantine_records": [row["id"] for row in quarantines], "baseline_sha256": sha(baseline_body),
        "raw_snapshot_sha256": snapshot_hash, "archive_reference": reference,
        "source_members": {name: sha(path.read_bytes()) for name, path in members.items()}}, final_id, "boundary_captured")
    delta = {"schema": SCHEMA, "records": reconciled["records"] + remote_delta["records"] + contacts + quarantines + [final_audit],
             "source_manifest": [], "blocked": [], "historical_only": True,
             "cloud_authoritative": False, "replay_allowed": False}
    merged = combine_exports([baseline, delta])
    indexed = {row["id"]: row for row in merged["records"]}
    if any(any(indexed[row["id"]][field] != row[field] for field in CORE) for row in baseline["records"]):
        raise ValueError("Immutable baseline differs from final source")
    evidence = deepcopy(evidence)
    actor_bindings = [{"id": identity, "member": "deployment/automations/" + identity + ".toml",
                       "sha256": sha(members["deployment/automations/" + identity + ".toml"].read_bytes())}
                      for identity in sorted(AUTOMATION_IDS)]
    for field, binding in (("actors", actor_bindings),
                           ("milo", {"member": "deployment/milo-confirmation.json", "sha256": sha(members["deployment/milo-confirmation.json"].read_bytes())}),
                           ("workers", {"member": "deployment/worker-readbacks.json", "sha256": sha(members["deployment/worker-readbacks.json"].read_bytes())}),
                           ("claims", {"member": JOURNAL, "sha256": sha(members[JOURNAL].read_bytes())}),
                           ("snapshot", {"member": SNAPSHOT, "sha256": sha(raw)})):
        if field in evidence and evidence[field] != binding:
            raise ValueError("Caller boundary binding differs from captured bytes")
        evidence[field] = binding
    generated = {"deployment/cutover-evidence.json": json_bytes(evidence),
                 "normalized/final-local-history.json": json_bytes(local),
                 "normalized/final-source-delta.json": json_bytes(delta), NORMALIZED: json_bytes(merged)}
    for name, body in generated.items():
        if any(pattern.search(body) for pattern in PATTERNS):
            raise ValueError("Credential-pattern scan blocked final capture")
        target = staging / name
        _private_directories(target.parent)
        write_private(target, body)
        members[name] = target
    from cloud.activation import validate_boundary_members
    facts = validate_boundary_members(evidence, {name: path.read_bytes() for name, path in members.items()}, merged)

    def verify_stable():
        if (any(read_source(sources[name]) != body for name, body in originals.items())
                or (read_source(wal) if wal.exists() else None) != wal_body):
            raise ValueError("Final source changed; stop and obtain fresh boundary evidence")
    verify_stable()
    receipt = create_backup(members, output, key_path=Path(key), private_destination_verified=True)
    try:
        verify_stable()
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    receipt.update({"archive": reference["archive"], "normalized_member": NORMALIZED,
        "canonical_records": len(merged["records"]), "baseline_records": len(baseline["records"]),
        "delta_records": len(merged["records"]) - len(baseline["records"]),
        "quarantine_records": len(quarantines), "source_effect_replay": False,
        "source_stability_verified": True, "credential_pattern_scan": "passed",
        "production_activated": False, "cutover_record_created": False,
        "historical_manifest_sha256": content_hash(merged), "raw_snapshot_sha256": snapshot_hash,
        "boundary_validated": True, "boundary_facts_sha256": content_hash(facts)})
    receipt["files"] = [{"member_id": sha(row["name"].encode()), "sha256": row["sha256"], "bytes": row["bytes"]}
                        for row in receipt["files"]]
    write_private(output.with_suffix(".receipt.json"), json_bytes(receipt) + b"\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "baseline-receipt", "inventory-receipt", "snapshot", "boundary", "milo", "workers", "staging", "key", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--private-destination-verified", action="store_true")
    args = parser.parse_args()
    result = capture_final_cutover(baseline_path=args.baseline, baseline_receipt_path=args.baseline_receipt,
        inventory_receipt_path=args.inventory_receipt, snapshot_path=args.snapshot, boundary_path=args.boundary,
        milo_path=args.milo, workers_path=args.workers, staging=args.staging, key=args.key, output=args.output,
        private_destination_verified=args.private_destination_verified)
    fields = ("archive_sha256", "archive_bytes", "canonical_records", "baseline_records", "delta_records",
              "quarantine_records", "source_stability_verified", "restore_verified", "production_activated")
    print(json.dumps({name: result[name] for name in fields}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__, "source_effect_replay": False}), file=sys.stderr)
        raise SystemExit(2)
