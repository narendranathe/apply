#!/usr/bin/env python3
"""Verify archived custody locally, or admit an actual hosted receipt for review.

No operational writes, variable changes or workflow dispatches are performed.
Never use a successful offline validation as a production activation proof.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud.backup import validate_key
from cloud.baseline import restore_baseline
from cloud.index_custody_reconciliation import build_plan, verify_archived_inputs
from cloud.index_custody_receipt import (COMPLETE_ARCHIVE_PATH, RECEIPT_PATH, prepare_receipt)
from cloud.orchestrator import _persist_failure_summary as persist_summary
from cloud.records import content_hash
from scripts.reconcile_startup import StartupGitHubEvidence


def _write_once(path, raw):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--archives-only", action="store_true")
    args = parser.parse_args()
    if bool(args.run_id) == args.archives_only:
        parser.error("choose exactly one of --archives-only or --run-id")
    key = validate_key()
    exported, _ = restore_baseline()
    before, after = verify_archived_inputs(key)
    plan = build_plan(before, after, exported)
    if args.archives_only:
        result = {"status": "verified_offline_archives_only", "plan_sha256": plan["plan_sha256"],
            "plan_content_sha256": content_hash(plan),
            "operational_writes": 0, "hosted_verification": False, "activation_authorized": False}
    else:
        github = StartupGitHubEvidence(os.environ["GITHUB_TOKEN"])
        receipt, cipher = prepare_receipt(github, args.run_id, key, exported)
        # Refuse to overwrite either durable evidence file. A partial local
        # admission is incomplete and cannot pass the runtime matcher.
        if RECEIPT_PATH.exists() or COMPLETE_ARCHIVE_PATH.exists():
            raise ValueError("durable_evidence_destination_exists")
        _write_once(COMPLETE_ARCHIVE_PATH, cipher)
        _write_once(RECEIPT_PATH, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode())
        result = {"status": "actual_hosted_receipt_prepared_for_review",
            "receipt_content_sha256": receipt["receipt_content_sha256"],
            "operational_writes": 0, "activation_authorized": False}
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # The native/AES verifier can encounter private source data. Keep all
        # exception strings, tracebacks, headers and request values out of logs.
        failure = {"status": "blocked", "operation": "verify_index_custody",
                   "reason": "custody_verification_not_proven", "activation_authorized": False}
        try:
            persist_summary(failure, "run-output/custody-verification-summary.json")
        except OSError:
            pass
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)
