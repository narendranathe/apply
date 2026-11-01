#!/usr/bin/env python3
"""Owner-only hosted custody recovery; no business action or activation."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud import index_custody_evidence as evidence
from cloud import index_custody_reconciliation as recovery
from cloud.backup import backup_store, validate_key
from cloud.baseline import restore_baseline
from cloud.index_custody_receipt import restore_and_validate_archive
from cloud.notion import NotionStore
from cloud.orchestrator import _persist_failure_summary as persist_summary
from cloud.runtime_budget import RuntimeBudget
from cloud.startup_reconciliation import ReconciliationBlocked
from scripts.reconcile_readback import _failure_summary
from scripts.reconcile_startup import StartupGitHubEvidence

OUTPUT = "run-output/maintenance-summary.json"
OPERATION = "pinned_index_custody_reconciliation"


def context():
    return {"actions": os.environ.get("GITHUB_ACTIONS") == "true",
        "ref": os.environ.get("GITHUB_REF"), "repository": os.environ.get("GITHUB_REPOSITORY"),
        "actor": os.environ.get("GITHUB_ACTOR"), "triggering_actor": os.environ.get("GITHUB_TRIGGERING_ACTOR"),
        "event": os.environ.get("GITHUB_EVENT_NAME"), "commit": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "cloud_enabled": os.environ.get("CLOUD_ENABLED"),
        "cloud_research_enabled": os.environ.get("CLOUD_RESEARCH_ENABLED"),
        "mode": os.environ.get("CUSTODY_MODE")}


def _local_gate(current):
    recovery.require(current["actions"] and current["ref"] == "refs/heads/main"
        and current["repository"] == evidence.REPOSITORY
        and current["actor"] == current["triggering_actor"] == evidence.OWNER
        and current["event"] == "workflow_dispatch" and current["run_attempt"] == "1"
        and current["cloud_enabled"] == "false" and current["cloud_research_enabled"] == "true"
        and current["mode"] in {"reconcile", "verify"},
        "custody_owner_disabled_hosted_maintenance_required")


def main():
    current = context()
    _local_gate(current)  # Before credentials, archive restore or network.
    recovery.require_reviewed_pins()
    key = validate_key()
    exported, _ = restore_baseline()
    config = json.loads(Path("config/cloud.json").read_text())
    budget = RuntimeBudget(seconds=9000)
    store = budget.attach(NotionStore(token=os.environ["NOTION_TOKEN"],
                                     data_source_id=config["notion_data_source_id"]))
    github = StartupGitHubEvidence(os.environ["GITHUB_TOKEN"], budget=budget)
    result = recovery.reconcile(store, exported, github, current, key, mode=current["mode"])
    result["backup"] = backup_store(store)
    proof, cipher_hash, _ = restore_and_validate_archive(
        result["backup"]["path"], key, result["correction_binding"], exported=exported)
    recovery.require(cipher_hash == result["backup"]["sha256"]
        and result["backup"]["restore_verified"] is True
        and result["backup"]["records"] == proof["complete_pages"],
        "custody_post_backup_verification_failed")
    result["backup_verification"] = proof
    # A success artifact is emitted only after the actual restored post-backup
    # source/cohort equality check. No receipt or variable is written here.
    persist_summary(result, OUTPUT)
    print(json.dumps(result, sort_keys=True))
    return result


def cli():
    try:
        return main()
    except Exception as error:
        failure = _failure_summary(error)
        failure["operation"] = OPERATION
        if isinstance(error, ReconciliationBlocked):
            reason = error.args[0] if len(error.args) == 1 else None
            reasons = set(recovery.SAFE_REASONS) | set(getattr(evidence, "SAFE_REASONS", ())) | {
                "custody_post_backup_verification_failed",
                "custody_owner_disabled_hosted_maintenance_required"}
            if isinstance(reason, str) and reason in reasons:
                failure["reason"] = reason
        try:
            persist_summary(failure, OUTPUT)
        except OSError:
            pass
        print(json.dumps(failure, sort_keys=True))
        raise SystemExit(2)


if __name__ == "__main__":
    cli()
