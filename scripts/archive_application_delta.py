#!/usr/bin/env python3
"""Capture immutable Git source packets into a separate authenticated delta.

An ignored compatibility registry is evidence, not the operational registry.
Explicit orphan observations may identify newly tracked folders; their exact
JD/status hashes bind the observation without upgrading any readiness gate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.migration import application_delta, combine_exports
from scripts.encrypted_backup import create_backup
from scripts.security_check import PATTERNS


def git_bytes(repository, *args):
    return subprocess.check_output(["git", *args], cwd=repository)


def _private_compatibility_registry(repository, path, tracked_files):
    """Read only the explicitly selected ignored, retired compatibility view."""
    path = Path(path)
    expected = repository.resolve() / "applications" / "registry.json"
    if path.is_symlink() or path.resolve() != expected or not path.is_file():
        raise ValueError("Compatibility input must be the private local registry view")
    if "applications/registry.json" in tracked_files:
        raise ValueError("Tracked and private compatibility registries are ambiguous")
    ignored = subprocess.run(["git", "check-ignore", "-q", "--", "applications/registry.json"],
                             cwd=repository, capture_output=True)
    if ignored.returncode != 0:
        raise ValueError("Compatibility input must be ignored by Git")
    body = path.read_bytes()
    value = json.loads(body)
    if not isinstance(value, dict) or any(not isinstance(row, dict) for row in value.values()):
        raise ValueError("Unsupported private compatibility registry")
    return body, value


def orphan_registry(files, observations, compatibility):
    """Derive import-only rows from exact, explicitly supplied source evidence."""
    if not isinstance(observations, dict) or observations.get("schema") != "apply-orphan-application-observations/v1":
        raise ValueError("Unsupported orphan application observation schema")
    rows = observations.get("observations")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Explicit orphan application observations are required")
    registered = {row.get("folder") for row in compatibility.values()}
    tracked = {path.split("/")[1] for path in files if path.startswith("applications/") and path.endswith("/status.json")}
    registry, identities, seen = {}, {}, set()
    for item in rows:
        if not isinstance(item, dict) or set(item) - {"folder", "company", "role", "url", "jd_sha256", "status_sha256", "job_id", "requisition_id"}:
            raise ValueError("Unsupported orphan application observation fields")
        folder = item.get("folder")
        if (not isinstance(folder, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", folder)
                or folder in seen or folder in registered):
            raise ValueError("Orphan folder identity is unsafe, duplicate or already registered")
        prefix = "applications/" + folder + "/"
        jd, status_body = files.get(prefix + "jd.md"), files.get(prefix + "status.json")
        if (jd is None or status_body is None
                or hashlib.sha256(jd).hexdigest() != item.get("jd_sha256")
                or hashlib.sha256(status_body).hexdigest() != item.get("status_sha256")):
            raise ValueError("Orphan JD/status source hashes differ")
        text = " ".join(jd.decode("utf-8").split()).casefold()
        for field in ("company", "role", "url"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip() or " ".join(value.split()).casefold() not in text:
                raise ValueError("Orphan identity must occur in the exact captured JD")
        for field in ("job_id", "requisition_id"):
            value = item.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip() or value.casefold() not in text):
                raise ValueError("Orphan requisition observation lacks exact JD evidence")
        state = json.loads(status_body)
        if not isinstance(state, dict) or not isinstance(state.get("status"), str):
            raise ValueError("Orphan status document is invalid")
        url = item["url"]
        if url in registry or url in compatibility:
            raise ValueError("Orphan application URL is already registered or repeated")
        registry[url] = {"company": item["company"], "role": item["role"], "url": url,
                         "folder": folder, "status": state["status"],
                         "claimed_at": state.get("claimed_at"), "claimed_by": state.get("claimed_by"),
                         "applied_at": state.get("submitted_on"),
                         "source_kind": "explicit_hash_bound_orphan_observation",
                         "official_verified": False, "receipt_verified": False}
        identities[url] = {**item, "source_kind": "exact_git_jd_and_status_observation",
                           "identity_verified": False, "official_verified": False, "receipt_verified": False}
        seen.add(folder)
    if tracked - registered != seen:
        raise ValueError("Every tracked orphan folder needs one explicit identity observation")
    return registry, identities


def select_changed_orphans(files, registry, previous_records):
    """Skip byte-identical captured packets without changing historical rows.

    Git commit identity alone does not make an unchanged packet a new event.
    A changed JD, status, answer, resume, dependency or exact registry evidence
    still produces a new historical observation with every eligibility gate off.
    """
    from cloud.applications import fold_applications
    latest = fold_applications([row for row in previous_records if row.get("kind") == "applications"])
    by_folder = {}
    for item in latest.values():
        folder = item.get("payload", {}).get("source_folder")
        if folder:
            if folder in by_folder:
                raise ValueError("Historical application folder identity is ambiguous")
            by_folder[folder] = item["payload"]
    changed, unchanged = {}, []
    for key, row in registry.items():
        folder = row["folder"]
        previous = by_folder.get(folder)
        current_files = sorted((path, len(body), hashlib.sha256(body).hexdigest())
                               for path, body in files.items()
                               if path.startswith("applications/" + folder + "/")
                               or path == "resumes/variants/" + folder + ".tex")
        previous_files = sorted((item["path"], item["bytes"], item["sha256"])
                                for item in previous.get("source_files", [])) if previous else []
        if (previous is not None and current_files and current_files == previous_files
                and previous.get("source_registry_key") == key
                and previous.get("source_registry_row") == row):
            unchanged.append(folder)
        else:
            changed[key] = row
    return changed, sorted(unchanged)


def capture(repository, source_ref, base_ref, staging, identity_observations=None,
            compatibility_registry=None, orphan_observations=None, previous_records=()):
    repository = Path(repository)
    source = git_bytes(repository, "rev-parse", source_ref).decode().strip()
    base = git_bytes(repository, "rev-parse", base_ref).decode().strip()
    paths = git_bytes(repository, "ls-tree", "-r", "--name-only", source, "applications/", "resumes/variants/").decode().splitlines()
    files = {path: git_bytes(repository, "show", source + ":" + path) for path in paths}
    for path in ("AGENTS.md", "docs/ground-truth.md"):
        files[path] = git_bytes(repository, "show", source + ":" + path)
    available = set(git_bytes(repository, 'ls-tree', '-r', '--name-only', source).decode().splitlines())
    for path in ('docs/resume-instructions.md', 'docs/jobscout-integration.md', 'scripts/resume_qa.py',
                 'resumes/master/v8preamble.tex', 'resumes/master/Narendranath-Edara-Resume.tex'):
        if path in available:
            files[path] = git_bytes(repository, 'show', source + ':' + path)
    files["audit/base-ground-truth.md"] = git_bytes(repository, "show", base + ":docs/ground-truth.md")
    files["audit/ground-truth.diff"] = git_bytes(repository, "diff", base, source, "--", "docs/ground-truth.md")
    if identity_observations is not None:
        files["audit/identity-observations.json"] = Path(identity_observations).read_bytes()
    if compatibility_registry is not None:
        if orphan_observations is None:
            raise ValueError("Private compatibility capture requires explicit orphan observations")
        raw_registry, compatibility = _private_compatibility_registry(repository, compatibility_registry, files)
        raw_observations = Path(orphan_observations).read_bytes()
        registry, identities = orphan_registry(files, json.loads(raw_observations), compatibility)
        registry, unchanged = select_changed_orphans(files, registry, previous_records)
        existing = json.loads(files.get("audit/identity-observations.json", b"{}"))
        if set(existing) & set(identities):
            raise ValueError("Duplicate identity observation source")
        files["audit/private-compatibility-registry.json"] = raw_registry
        files["audit/orphan-identity-observations.json"] = raw_observations
        # This derived registry is deliberately limited to the captured orphans.
        # The raw ignored view and its exact origin remain separate evidence.
        files["applications/registry.json"] = json.dumps(registry, sort_keys=True, ensure_ascii=False).encode()
        files["audit/identity-observations.json"] = json.dumps({**existing, **identities}, sort_keys=True).encode()
        files["audit/registry-capture-provenance.json"] = json.dumps({
            "schema": "apply-private-compatibility-capture/v1", "source_commit": source,
            "private_source_path": "applications/registry.json",
            "private_source_member": "audit/private-compatibility-registry.json",
            "private_source_sha256": hashlib.sha256(raw_registry).hexdigest(),
            "derived_registry_path": "applications/registry.json",
            "derived_registry_is_operational": False,
            "derived_registry_scope": "explicit_hash_bound_tracked_orphans_only",
            "byte_identical_prior_packets_skipped": unchanged,
            "orphan_observations_sha256": hashlib.sha256(raw_observations).hexdigest(),
            "historical_only": True, "replay_allowed": False}, sort_keys=True).encode()
        if Path(compatibility_registry).read_bytes() != raw_registry:
            raise ValueError("Private compatibility registry changed during capture")
    elif orphan_observations is not None:
        raise ValueError("Orphan observations require explicit private compatibility capture")
    if any(pattern.search(data) for data in files.values() for pattern in PATTERNS):
        raise ValueError("Credential-pattern match in source; capture blocked without printing values")
    for path, data in files.items():
        target = staging / "source" / path
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return source, base, files


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--staging", type=Path, required=True)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--baseline-receipt", type=Path,
                   help="Existing authenticated archive receipt binding the exact baseline bytes")
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--identity-observations", type=Path)
    p.add_argument("--compatibility-registry", type=Path,
                   help="Explicit ignored private registry view; evidence only, never mutated")
    p.add_argument("--orphan-observations", type=Path,
                   help="Hash-bound identities for tracked source folders absent from the compatibility view")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--private-destination-verified", action="store_true")
    args = p.parse_args()
    if ".migration-private" not in args.staging.resolve().parts:
        p.error("Raw source staging must be in .migration-private")
    if not args.private_destination_verified:
        p.error("Actual private destination must be verified before source transfer")
    baseline_bytes = args.baseline.read_bytes()
    if args.baseline_receipt is not None:
        baseline_receipt = json.loads(args.baseline_receipt.read_bytes())
        expected_member = baseline_receipt.get("normalized_member")
        expected_id = hashlib.sha256(str(expected_member).encode()).hexdigest()
        entries = [row for row in baseline_receipt.get("files", [])
                   if row.get("name") == expected_member or row.get("member_id") == expected_id]
        if (baseline_receipt.get("restore_verified") is not True or len(entries) != 1
                or entries[0].get("bytes") != len(baseline_bytes)
                or entries[0].get("sha256") != hashlib.sha256(baseline_bytes).hexdigest()):
            raise ValueError("Baseline differs from the authenticated restored archive receipt")
    repository = Path(__file__).resolve().parents[1]
    baseline = json.loads(baseline_bytes)
    source, base, files = capture(repository, args.source, args.base, args.staging,
                                 args.identity_observations, args.compatibility_registry,
                                 args.orphan_observations, baseline['records'])
    delta = application_delta(files, source_commit=source, base_commit=base, previous_records=baseline['records'])
    merged = combine_exports([baseline, delta])
    members = {"source/" + path: args.staging / "source" / path for path in files}
    # Preserve the exact normalization input, not only its parsed records.
    if args.baseline.read_bytes() != baseline_bytes:
        raise ValueError("Baseline input changed during capture")
    members["audit/baseline-input.json"] = args.baseline
    if args.baseline_receipt is not None:
        members["audit/baseline-input.receipt.json"] = args.baseline_receipt
    for name, value in (("application-delta.json", delta), ("merged-historical-records.json", merged)):
        path = args.staging / "normalized" / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        members["normalized/" + name] = path
    receipt = create_backup(members, args.output, key_path=args.key,
                            private_destination_verified=args.private_destination_verified)
    receipt.update({"archive": str(args.output), "source_commit": source, "base_commit": base,
                    "normalized_member": "normalized/merged-historical-records.json",
                    "delta_records": len(delta["records"]), "canonical_records": len(merged["records"]),
                    "source_files": len(files), "credential_pattern_scan": "passed", "source_effect_replay": False,
                    "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                    "baseline_receipt_verified": args.baseline_receipt is not None,
                    "private_compatibility_registry_captured": args.compatibility_registry is not None,
                    "orphan_identities_hash_bound": args.orphan_observations is not None})
    # Only opaque member identifiers leave the encrypted archive; exact source
    # paths remain bound by its authenticated internal manifest and packet rows.
    receipt["files"] = [{"member_id": hashlib.sha256(item["name"].encode()).hexdigest(),
                         "bytes": item["bytes"], "sha256": item["sha256"]} for item in receipt["files"]]
    args.output.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"archive_sha256": receipt["archive_sha256"], "archive_bytes": receipt["archive_bytes"],
                      "source_files": len(files), "restored_files": len(receipt["files"]),
                      "restore_verified": receipt["restore_verified"], "delta_records": len(delta["records"]),
                      "merged_records": len(merged["records"]), "verified_submissions": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
