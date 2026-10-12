#!/usr/bin/env python3
"""Capture immutable Git source packets into a separate authenticated delta."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.migration import application_delta, combine_exports
from scripts.encrypted_backup import create_backup
from scripts.security_check import PATTERNS


def git_bytes(repository, *args):
    return subprocess.check_output(["git", *args], cwd=repository)


def capture(repository, source_ref, base_ref, staging, identity_observations=None):
    source = git_bytes(repository, "rev-parse", source_ref).decode().strip()
    base = git_bytes(repository, "rev-parse", base_ref).decode().strip()
    paths = git_bytes(repository, "ls-tree", "-r", "--name-only", source, "applications/", "resumes/variants/").decode().splitlines()
    files = {path: git_bytes(repository, "show", source + ":" + path) for path in paths}
    for path in ("AGENTS.md", "docs/ground-truth.md"):
        files[path] = git_bytes(repository, "show", source + ":" + path)
    available = set(git_bytes(repository, 'ls-tree', '-r', '--name-only', source).decode().splitlines())
    for path in ('docs/resume-instructions.md', 'docs/jobscout-integration.md', 'scripts/resume_qa.py'):
        if path in available:
            files[path] = git_bytes(repository, 'show', source + ':' + path)
    files["audit/base-ground-truth.md"] = git_bytes(repository, "show", base + ":docs/ground-truth.md")
    files["audit/ground-truth.diff"] = git_bytes(repository, "diff", base, source, "--", "docs/ground-truth.md")
    if identity_observations is not None:
        files["audit/identity-observations.json"] = Path(identity_observations).read_bytes()
    for path, data in files.items():
        if any(pattern.search(data) for pattern in PATTERNS):
            raise ValueError("Credential-pattern match in source; capture blocked without printing values")
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
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--identity-observations", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--private-destination-verified", action="store_true")
    args = p.parse_args()
    if ".migration-private" not in args.staging.resolve().parts:
        p.error("Raw source staging must be in .migration-private")
    if not args.private_destination_verified:
        p.error("Actual private destination must be verified before source transfer")
    repository = Path(__file__).resolve().parents[1]
    source, base, files = capture(repository, args.source, args.base, args.staging, args.identity_observations)
    baseline = json.loads(args.baseline.read_text())
    delta = application_delta(files, source_commit=source, base_commit=base, previous_records=baseline['records'])
    merged = combine_exports([baseline, delta])
    members = {"source/" + path: args.staging / "source" / path for path in files}
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
                    "source_files": len(files), "credential_pattern_scan": "passed", "source_effect_replay": False})
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
