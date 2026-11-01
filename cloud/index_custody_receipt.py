"""Durable admission of an actual successful custody verification.

No receipt is shipped before a hosted execution is independently authenticated.
Artifact bytes are checked at admission, then retained encrypted in Git. Runtime
rechecks native runs/jobs rather than depending on expiring artifact downloads.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tempfile
from types import SimpleNamespace
import zipfile

from cloud import index_custody_evidence as evidence
from cloud import index_custody_reconciliation as recovery
from cloud.activation import _time
from cloud.contained_recovery import _cohort as contained_cohort
from cloud.cutover import validate_import
from cloud.records import content_hash, validate_record
from cloud.startup_reconciliation import SnapshotState, core, ReconciliationBlocked
from scripts.encrypted_backup import MAGIC, restore_archive

ROOT = Path(__file__).resolve().parents[1]
RECEIPT_PATH = ROOT / "recovery-evidence/2026-10-28-index-custody-verification.json"
COMPLETE_ARCHIVE_PATH = ROOT / "recovery-evidence/2026-10-28-index-custody-complete.aesgcm"
RECEIPT_SCHEMA = "apply-index-custody-verification-receipt/v1"
MAX_BYTES = 64_000_000
ARTIFACT_FIELDS = frozenset({"id", "name", "size_in_bytes", "digest", "created_at",
    "updated_at", "expires_at", "expired", "workflow_run"})
SNAPSHOT_PROOF_FIELDS = frozenset({"prior_source_fingerprint", "prior_pages_unchanged",
    "prior_cores_unchanged", "complete_pages", "complete_cores", "historical_records_unchanged",
    "cohort_whole_core_sha256", "snapshot_captured_at", "snapshot_exported_at",
    "original_clock_verified", "original_failure_cause_verified", "external_effects_reconciled",
    "index_complete", "activation_authorized"})
RECEIPT_FIELDS = frozenset({
    "schema", "protocol_schema", "correction_binding", "verification_run",
    "verification_jobs_sha256", "verification_job_id", "artifact_witness",
    "artifact_list_sha256", "artifact_zip_sha256", "archive_sha256", "member_sha256",
    "snapshot_proof", "cohort_whole_core_sha256", "incident_proof", "admitted_history",
    "independently_authenticated", "original_clock_verified", "original_failure_cause_verified",
    "external_effects_reconciled", "index_complete", "activation_authorized",
    "receipt_content_sha256",
})


def require(value, reason):
    if not value:
        raise ReconciliationBlocked(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _run_projection(run):
    require(isinstance(run, dict), "custody_receipt_native_run_invalid")
    return {**{field: run.get(field) for field in evidence.RUN_FIELDS},
        "actor": run.get("actor", {}).get("login"),
        "triggering_actor": run.get("triggering_actor", {}).get("login"),
        "repository": {field: run.get("repository", {}).get(field)
                       for field in ("full_name", "private")}}


def _verify_run(fetch, identity, *, head=None):
    require(isinstance(identity, str) and re.fullmatch("[0-9]+", identity),
            "custody_receipt_run_identity_invalid")
    run = fetch("/actions/runs/" + identity)
    require(isinstance(run, dict) and type(run.get("id")) is int and str(run["id"]) == identity
        and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
        and run.get("head_branch") == "main" and run.get("event") == "workflow_dispatch"
        and run.get("status") == "completed" and run.get("conclusion") == "success"
        and run.get("display_title") in {"index-custody:reconcile", "index-custody:verify"}
        and str(run.get("path", "")).partition("@")[0] == evidence.WORKFLOW
        and run.get("actor", {}).get("login") == run.get("triggering_actor", {}).get("login")
            == evidence.OWNER
        and run.get("repository", {}).get("private") is True
        and run.get("repository", {}).get("full_name") == evidence.REPOSITORY
        and isinstance(run.get("head_sha"), str) and re.fullmatch("[0-9a-f]{40}", run["head_sha"])
        and (head is None or run["head_sha"] == head)
        and all(_time(run.get(field)) is not None
                for field in ("created_at", "updated_at", "run_started_at")),
        "custody_receipt_successful_hosted_run_required")
    jobs_path = "/actions/runs/" + identity + "/jobs?filter=all&per_page=100&page=1"
    jobs = evidence._complete(fetch(jobs_path), "jobs", 1)
    job = jobs["jobs"][0]
    require(job.get("name") == "reconcile" and job.get("status") == "completed"
        and job.get("conclusion") == "success" and str(job.get("run_id")) == identity
        and type(job.get("id")) is int and job["id"] > 0
        and type(job.get("run_attempt")) is int and job["run_attempt"] == 1
        and job.get("head_sha") == run["head_sha"]
        and job.get("runner_group_name") == "GitHub Actions"
        and "ubuntu-latest" in job.get("labels", []) and "self-hosted" not in job.get("labels", [])
        and isinstance(job.get("steps"), list)
        and all(s.get("status") == "completed" and s.get("conclusion") in {"success", "skipped"}
                for s in job["steps"]), "custody_receipt_successful_hosted_job_required")
    steps = [step for step in job["steps"] if step.get("name") == evidence.OPERATION_STEP]
    require(len(steps) == 1 and steps[0].get("status") == "completed"
            and steps[0].get("conclusion") == "success"
            and _time(steps[0].get("started_at")) is not None
            and _time(steps[0].get("completed_at")) is not None,
            "custody_receipt_successful_operation_required")
    require(fetch(jobs_path) == jobs and _run_projection(fetch("/actions/runs/" + identity))
            == _run_projection(run), "custody_receipt_native_attempt_changed")
    return run, jobs, steps[0]


def _history(fetch, binding, selected, *, admitted=None):
    """Every maintenance attempt matters, including failures outside cloud runs."""
    runs = evidence._workflow_history(fetch)
    require(isinstance(runs, list), "custody_receipt_history_invalid")
    executor_id, selected_id = binding["run_id"], str(selected["id"])
    ids = [str(run.get("id")) for run in runs]
    require(len(ids) == len(set(ids)) and executor_id in ids and selected_id in ids,
            "custody_receipt_history_incomplete")
    # Only the executor and one successful verifier may be admitted. An extra
    # failed, cancelled, queued or even apparently successful writer is unknown.
    require(set(ids) <= {executor_id, selected_id}, "custody_receipt_unreviewed_maintenance_attempt")
    projections = sorted((_run_projection(run) for run in runs), key=lambda row: row["id"])
    if admitted is not None:
        require(projections == admitted, "custody_receipt_maintenance_history_changed")
    return projections


def validate_completed_snapshot(wrapper, binding, *, exported=None):
    """Compare the actual new snapshot with the entire reviewed source."""
    require(isinstance(wrapper, dict) and wrapper.get("schema") == "apply-cloud-snapshot/v1",
            "custody_receipt_snapshot_wrapper_invalid")
    state = SnapshotState(wrapper.get("snapshot"))
    rows = recovery.resolution_records(recovery.reviewed_plan(), binding)
    pins = evidence.SNAPSHOTS["observed"]
    require(len(state.rows) == pins["cores"] + len(rows)
            and len(state.pages) == pins["pages"] + len(rows),
            "custody_receipt_snapshot_count_changed")
    hashes = {}
    for expected in rows:
        observed = state.get(expected["id"])
        require(observed is not None and core(observed) == core(expected),
                "custody_receipt_cohort_changed")
        hashes[expected["id"]] = content_hash(core(expected))
        state.pages.pop(state.page_ids[expected["id"]])
    require(state.fingerprint() == pins["source_fingerprint"],
            "custody_receipt_post_backup_source_changed")
    contained_cohort(state.get)
    if exported is not None:
        result = validate_import(state, exported)
        require(result["records"] == result["unchanged"] == 757
                and result["conflicted"] == result["blocked"] == 0,
                "custody_receipt_historical_source_changed")
    captured, exported_at = _time(wrapper["snapshot"].get("captured_at")), _time(wrapper.get("exported_at"))
    require(captured is not None and exported_at is not None and exported_at >= captured,
            "custody_receipt_snapshot_time_invalid")
    return {"prior_source_fingerprint": pins["source_fingerprint"],
        "prior_pages_unchanged": pins["pages"], "prior_cores_unchanged": pins["cores"],
        "complete_pages": pins["pages"] + len(rows), "complete_cores": pins["cores"] + len(rows),
        "historical_records_unchanged": 757, "cohort_whole_core_sha256": hashes,
        "snapshot_captured_at": wrapper["snapshot"]["captured_at"],
        "snapshot_exported_at": wrapper["exported_at"],
        "original_clock_verified": False, "original_failure_cause_verified": False,
        "external_effects_reconciled": False, "index_complete": False, "activation_authorized": False}


def restore_and_validate_archive(archive, key, binding, *, exported=None):
    path = Path(archive)
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_BYTES
            and isinstance(key, bytes) and len(key) == 32, "custody_receipt_private_archive_required")
    cipher = path.read_bytes()
    require(cipher.startswith(MAGIC), "custody_receipt_archive_format_invalid")
    try:
        with tempfile.TemporaryDirectory(prefix="apply-custody-backup-verify-") as temporary:
            root = Path(temporary); root.chmod(0o700)
            key_path = root / "archive.key"; key_path.write_bytes(key); key_path.chmod(0o600)
            target = root / "restored"
            result = restore_archive(path, target, key_path=key_path)
            require(result.get("hashes_verified") is True and result.get("restored_files") == 1
                    and {p.name for p in target.iterdir()} == {"cloud-records.json"},
                    "custody_receipt_archive_members_invalid")
            member = (target / "cloud-records.json").read_bytes()
            proof = validate_completed_snapshot(json.loads(member), binding, exported=exported)
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked("custody_receipt_archive_authentication_failed") from None
    return proof, _sha(cipher), _sha(member)


def _artifact_projection(item):
    return {field: deepcopy(item.get(field)) for field in
        ("id", "name", "size_in_bytes", "digest", "created_at", "updated_at", "expires_at",
         "expired", "workflow_run")}


def prepare_receipt(github, run_id, key, exported):
    """Authenticate actual native success and encrypted backup; never write Git."""
    recovery.require_reviewed_pins()
    evidence.verify_incidents(github)
    run, jobs, step = _verify_run(github.fetch, str(run_id))
    path = "/actions/runs/" + str(run_id) + "/artifacts?per_page=100&page=1"
    listing = evidence._complete(github.fetch(path), "artifacts", 1)
    artifact = listing["artifacts"][0]
    require(type(artifact.get("id")) is int and artifact["id"] > 0
        and artifact.get("name") == "index-custody-evidence-" + str(run_id)
        and artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int
        and 0 < artifact["size_in_bytes"] <= MAX_BYTES
        and artifact.get("workflow_run", {}).get("id") == int(run_id)
        and artifact["workflow_run"].get("head_sha") == run["head_sha"]
        and artifact["workflow_run"].get("head_branch") == "main",
        "custody_receipt_artifact_binding_invalid")
    direct = github.fetch("/actions/artifacts/" + str(artifact["id"]))
    require(_artifact_projection(direct) == _artifact_projection(artifact),
            "custody_receipt_artifact_metadata_changed")
    raw = github.download(artifact["id"])
    require(isinstance(raw, bytes) and len(raw) == artifact["size_in_bytes"]
            and artifact.get("digest") == "sha256:" + _sha(raw),
            "custody_receipt_artifact_zip_changed")
    cipher_name = "cloud-backup-" + str(run_id) + ".aesgcm"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            require(len(members) == 2 and {m.filename for m in members}
                    == {"maintenance-summary.json", cipher_name}
                    and all(not m.is_dir() and not m.flag_bits & 1
                        and not stat.S_ISLNK(m.external_attr >> 16)
                        and 0 < m.file_size <= MAX_BYTES for m in members),
                    "custody_receipt_artifact_members_invalid")
            summary_raw = archive.read("maintenance-summary.json")
            require(len(summary_raw) <= 1_000_000, "custody_receipt_summary_too_large")
            summary = json.loads(summary_raw)
            cipher = archive.read(cipher_name)
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked("custody_receipt_artifact_members_invalid") from None
    require(isinstance(summary, dict) and summary.get("verified") is True,
            "custody_receipt_summary_invalid")
    binding = summary.get("correction_binding")
    evidence.validate_binding(github, binding)
    # A documentation-only main commit may separate append and verification.
    # The actual protocol bytes, rather than unrelated tree content, must match.
    evidence._verify_sources(github.fetch, run["head_sha"], binding["source_sha256"])
    proof = summary.get("backup_verification")
    require(isinstance(proof, dict), "custody_receipt_backup_proof_missing")
    with tempfile.TemporaryDirectory(prefix="apply-custody-native-receipt-") as temporary:
        root = Path(temporary); root.chmod(0o700)
        archive_path = root / "snapshot.aesgcm"; archive_path.write_bytes(cipher); archive_path.chmod(0o600)
        actual_proof, cipher_hash, member_hash = restore_and_validate_archive(
            archive_path, key, binding, exported=exported)
    require(proof == actual_proof and summary.get("backup", {}).get("sha256") == cipher_hash
        and summary["backup"].get("restore_verified") is True
        and summary["backup"].get("records") == actual_proof["complete_pages"],
        "custody_receipt_backup_proof_changed")
    start, end = _time(step["started_at"]), _time(step["completed_at"])
    # GitHub reports whole seconds; preserve the exclusive next-second bound.
    end = end + timedelta(seconds=1) if re.fullmatch(r".*T\d\d:\d\d:\d\dZ", step["completed_at"]) else end
    require(start <= _time(actual_proof["snapshot_captured_at"]) <= _time(actual_proof["snapshot_exported_at"])
        and _time(actual_proof["snapshot_exported_at"]) < end,
        "custody_receipt_backup_outside_native_operation")
    history = _history(github.fetch, binding, run)
    # Every mutable native surface is fetched again after actual AES verification.
    again, again_jobs, _ = _verify_run(github.fetch, str(run_id), head=run["head_sha"])
    require(_run_projection(again) == _run_projection(run) and again_jobs == jobs
        and github.fetch(path) == listing
        and _artifact_projection(github.fetch("/actions/artifacts/" + str(artifact["id"])))
            == _artifact_projection(artifact),
        "custody_receipt_native_evidence_changed")
    _history(github.fetch, binding, run, admitted=history)
    value = {"schema": RECEIPT_SCHEMA, "protocol_schema": recovery.SCHEMA,
        "correction_binding": binding, "verification_run": _run_projection(run),
        "verification_jobs_sha256": content_hash(jobs), "verification_job_id": jobs["jobs"][0]["id"],
        "artifact_witness": _artifact_projection(artifact), "artifact_list_sha256": content_hash(listing),
        "artifact_zip_sha256": _sha(raw), "archive_sha256": cipher_hash, "member_sha256": member_hash,
        "snapshot_proof": actual_proof, "cohort_whole_core_sha256": actual_proof["cohort_whole_core_sha256"],
        "incident_proof": evidence.incident_proof(), "admitted_history": history,
        "independently_authenticated": True, "original_clock_verified": False,
        "original_failure_cause_verified": False, "external_effects_reconciled": False,
        "index_complete": False, "activation_authorized": False}
    value["receipt_content_sha256"] = content_hash(value)
    return value, cipher


def _load_receipt(rows, binding):
    require(RECEIPT_PATH.is_file() and not RECEIPT_PATH.is_symlink()
        and 0 < RECEIPT_PATH.stat().st_size <= 100_000
        and COMPLETE_ARCHIVE_PATH.is_file() and not COMPLETE_ARCHIVE_PATH.is_symlink()
        and 0 < COMPLETE_ARCHIVE_PATH.stat().st_size <= MAX_BYTES,
        "custody_durable_verification_receipt_required")
    try:
        value = json.loads(RECEIPT_PATH.read_bytes())
        require(isinstance(value, dict) and set(value) == RECEIPT_FIELDS,
                "custody_durable_receipt_fields_invalid")
        unsigned = {k: v for k, v in value.items() if k != "receipt_content_sha256"}
        cipher = COMPLETE_ARCHIVE_PATH.read_bytes()
        expected_hashes = {row["id"]: content_hash(core(row)) for row in rows}
        proof = value["snapshot_proof"]
        pins = evidence.SNAPSHOTS["observed"]
        artifact, selected = value["artifact_witness"], value["verification_run"]
        require(isinstance(selected, dict) and type(selected.get("id")) is int and selected["id"] > 0
            and type(value["verification_job_id"]) is int and value["verification_job_id"] > 0
            and isinstance(artifact, dict) and set(artifact) == ARTIFACT_FIELDS
            and type(artifact["id"]) is int and artifact["id"] > 0
            and artifact["name"] == "index-custody-evidence-" + str(selected["id"])
            and type(artifact["size_in_bytes"]) is int and 0 < artifact["size_in_bytes"] <= MAX_BYTES
            and artifact["expired"] is False
            and isinstance(artifact["workflow_run"], dict)
            and artifact["workflow_run"].get("id") == selected["id"]
            and artifact["workflow_run"].get("head_sha") == selected["head_sha"]
            and artifact["workflow_run"].get("head_branch") == "main"
            and all(_time(artifact.get(field)) is not None
                    for field in ("created_at", "updated_at", "expires_at"))
            and _time(artifact["created_at"]) <= _time(artifact["updated_at"]) <= _time(artifact["expires_at"])
            and isinstance(proof, dict) and set(proof) == SNAPSHOT_PROOF_FIELDS
            and all(type(proof[field]) is int for field in ("prior_pages_unchanged", "prior_cores_unchanged",
                "complete_pages", "complete_cores", "historical_records_unchanged"))
            and _time(proof["snapshot_captured_at"]) is not None
            and _time(proof["snapshot_exported_at"]) is not None
            and _time(proof["snapshot_captured_at"]) <= _time(proof["snapshot_exported_at"]),
            "custody_durable_receipt_proof_changed")
        require(value["schema"] == RECEIPT_SCHEMA and value["protocol_schema"] == recovery.SCHEMA
            and value["receipt_content_sha256"] == content_hash(unsigned)
            and value["correction_binding"] == binding and value["independently_authenticated"] is True
            and all(value[field] is False for field in ("original_clock_verified",
                "original_failure_cause_verified", "external_effects_reconciled", "index_complete",
                "activation_authorized"))
            and value["cohort_whole_core_sha256"] == expected_hashes
            and value["incident_proof"] == evidence.incident_proof()
            and isinstance(proof, dict) and proof["cohort_whole_core_sha256"] == expected_hashes
            and proof["prior_source_fingerprint"] == pins["source_fingerprint"]
            and proof["prior_pages_unchanged"] == pins["pages"]
            and proof["prior_cores_unchanged"] == pins["cores"]
            and proof["complete_pages"] == pins["pages"] + len(rows)
            and proof["complete_cores"] == pins["cores"] + len(rows)
            and proof["historical_records_unchanged"] == 757
            and all(proof[field] is False for field in ("original_clock_verified",
                "original_failure_cause_verified", "external_effects_reconciled", "index_complete",
                "activation_authorized"))
            and all(_hash(value[field]) for field in ("archive_sha256", "member_sha256",
                "artifact_zip_sha256", "artifact_list_sha256", "verification_jobs_sha256"))
            and cipher.startswith(MAGIC) and len(cipher) > len(MAGIC) + 28
            and _sha(cipher) == value["archive_sha256"]
            and value["artifact_witness"]["digest"] == "sha256:" + value["artifact_zip_sha256"],
            "custody_durable_receipt_proof_changed")
    except ReconciliationBlocked:
        raise
    except Exception:
        raise ReconciliationBlocked("custody_durable_receipt_invalid") from None
    return value


def custody_resolution_matches(identity, marker, fetch, repository, *, read_record=None):
    """Typed runtime clearance, impossible without admitted actual hosted proof."""
    try:
        recovery.require_reviewed_pins()
        require(identity in recovery.FAILURES and repository == evidence.REPOSITORY and callable(read_record),
                "custody_runtime_scope_invalid")
        github = SimpleNamespace(fetch=fetch)
        rows, binding = recovery.validate_complete_cohort(read_record, github)
        require(core(validate_record(marker)) == core(next(row for row in rows
            if row["id"] == "writer-resolution:" + identity)), "custody_runtime_marker_changed")
        contained_cohort(read_record)
        value = _load_receipt(rows, binding)
        evidence.native_incidents(fetch)
        selected = value["verification_run"]
        run, jobs, _ = _verify_run(fetch, str(selected["id"]), head=selected["head_sha"])
        evidence._verify_sources(fetch, selected["head_sha"], binding["source_sha256"])
        require(_run_projection(run) == selected
            and content_hash(jobs) == value["verification_jobs_sha256"]
            and jobs["jobs"][0]["id"] == value["verification_job_id"],
            "custody_runtime_verification_changed")
        _history(fetch, binding, run, admitted=value["admitted_history"])
        return True
    except Exception:
        return False


def verify_maintenance_history(read_record, fetch, repository):
    """Fence this workflow even if an old cloud run later leaves its history."""
    history = evidence._workflow_history(fetch)
    if not history:
        return
    marker = read_record("writer-resolution:" + recovery.FAILURES[0])
    require(custody_resolution_matches(recovery.FAILURES[0], marker, fetch, repository,
                                      read_record=read_record),
            "custody_maintenance_requires_reconciliation")
