"""Publish an immutable index through the existing central writer.

The manifest is appended only after every referenced record reads back exactly.
An interrupted publication leaves unreachable child records, and the previous
manifest remains the reader's visibility boundary. No external action is made.
"""
from __future__ import annotations

from datetime import datetime

from .agent_index import GENERATED_KINDS, SCHEMA, build_index
from .records import content_hash, validate_record

CORE = ("id", "kind", "payload", "status", "hash")


class IndexPublicationError(RuntimeError):
    """Fixed public code; source bodies and credentials never enter the error."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _canonical(row):
    try:
        canonical = validate_record(row)
        if not isinstance(canonical["payload"], dict):
            raise ValueError("Expected a structured payload")
        return canonical
    except (ValueError, TypeError, KeyError):
        raise IndexPublicationError("authoritative_source_invalid") from None


def _generated(row):
    return (row["kind"] in GENERATED_KINDS
            or row["kind"] == "agent_tasks" and row["payload"].get("schema") == SCHEMA)


def snapshot_hash(records):
    """Hash the exact non-generated authoritative CORE signatures.

    Caller reconciliation evidence must use this representation; timestamps
    inside source payloads are protected by their immutable payload hashes.
    Notion page IDs and presentation metadata are not identity evidence.
    """
    source = {}
    for value in records:
        row = _canonical(value)
        if _generated(row):
            continue
        old = source.get(row["id"])
        if old is not None and old != row:
            raise IndexPublicationError("authoritative_source_identity_conflict")
        source[row["id"]] = row
    signatures = sorted((row["id"], row["kind"], row["hash"], row["status"]) for row in source.values())
    return content_hash(signatures)


def _read_sources(store):
    # query(None) is cloud-only in NotionStore. The explicit second query adds
    # the original journal, without requiring agents to scan it during startup.
    # These methods perform fresh transport reads; publisher retains no cache.
    try:
        cloud = store.index_source_records() if hasattr(store, 'index_source_records') else store.query()
        outreach = store.query(kind="outreach_events")
    except Exception:
        raise IndexPublicationError("authoritative_snapshot_unavailable") from None
    if not isinstance(cloud, (list, tuple)) or not isinstance(outreach, (list, tuple)):
        raise IndexPublicationError("authoritative_snapshot_invalid")
    combined = {}
    for value in [*cloud, *outreach]:
        row = _canonical(value)
        old = combined.get(row["id"])
        if old is not None and old != row:
            raise IndexPublicationError("authoritative_source_identity_conflict")
        combined[row["id"]] = row
    return sorted(combined.values(), key=lambda row: row["id"])


def _previous_manifest(store, snapshot):
    if hasattr(store, "latest_index_manifest"):
        try:
            previous = store.latest_index_manifest()
        except Exception:
            raise IndexPublicationError("previous_index_unavailable") from None
    else:
        previous = max((row for row in snapshot if row["kind"] == "index_manifests"),
                       key=lambda row: (row["payload"].get("generated_at", ""), row["id"]), default=None)
    if previous is None:
        return None
    previous = _canonical(previous)
    if previous["kind"] != "index_manifests" or previous["payload"].get("schema") != SCHEMA:
        raise IndexPublicationError("previous_index_invalid")
    return previous


def _put_verified(store, row):
    try:
        written = store.put(row)
    except Exception:
        raise IndexPublicationError("index_write_uncertain_stop_no_retry") from None
    if not isinstance(written, dict) or any(written.get(key) != row[key] for key in CORE):
        raise IndexPublicationError("index_write_readback_mismatch_stop_no_retry")
    try:
        readback = store.get(row["id"])
    except Exception:
        raise IndexPublicationError("index_readback_uncertain_stop_no_retry") from None
    if not isinstance(readback, dict) or any(readback.get(key) != row[key] for key in CORE):
        raise IndexPublicationError("index_readback_mismatch_stop_no_retry")
    return _canonical(readback)


def publish_index(store, now: datetime, source_complete=False, source_snapshot_hash=None):
    """Publish children first, then their manifest, returning a private result.

    A fresh read is necessary but does not itself prove reconciliation. Ready
    tasks require ``source_complete=True`` plus a matching, nonempty snapshot
    hash (a string or ``{hash, complete, conflicts}`` evidence envelope). A hash
    mismatch publishes an explicitly incomplete index with no ready actions.

    ``result['manifest']`` is for internal use; operational logs should print
    only the remaining bounded summary fields. A failed write or readback stops
    immediately, and is never retried by this function.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Index publication requires an aware timestamp")
    if not isinstance(source_complete, bool):
        raise ValueError("source_complete must be a boolean")
    snapshot = _read_sources(store)
    previous = _previous_manifest(store, snapshot)
    source = [row for row in snapshot if not _generated(row)]
    actual_hash = snapshot_hash(source)
    evidence_conflicts = []
    if isinstance(source_snapshot_hash, dict):
        expected_hash = source_snapshot_hash.get("hash")
        evidenced = source_snapshot_hash.get("complete") is True
        raw_conflicts = source_snapshot_hash.get("conflicts", [])
        if not isinstance(raw_conflicts, list):
            raise ValueError("Invalid snapshot evidence conflicts")
        # Public fixed codes avoid copying a source error or private message.
        evidence_conflicts.extend("source_reconciliation_conflict" for _ in raw_conflicts)
    elif source_snapshot_hash is None or isinstance(source_snapshot_hash, str):
        expected_hash, evidenced = source_snapshot_hash, source_snapshot_hash is not None
    else:
        raise ValueError("Invalid snapshot evidence")
    matching = isinstance(expected_hash, str) and bool(expected_hash) and expected_hash == actual_hash
    complete = source_complete and evidenced and matching and bool(source) and not evidence_conflicts
    if source_complete and not matching:
        evidence_conflicts.append("source_snapshot_evidence_missing_or_changed")
    if source_complete and not source:
        evidence_conflicts.append("empty_authoritative_snapshot_unreconciled")
    indexed = build_index(source, now, previous_manifest=previous,
                          source_snapshot_hash={"hash": actual_hash, "complete": complete,
                                                "conflicts": evidence_conflicts})
    manifests = [row for row in indexed if row["kind"] == "index_manifests"]
    if len(manifests) != 1:
        raise IndexPublicationError("index_builder_manifest_invalid")
    manifest = manifests[0]
    children = [row for row in indexed if row["kind"] != "index_manifests"]
    for row in children:
        _put_verified(store, row)
    verified_manifest = _put_verified(store, manifest)
    return {"manifest_id": verified_manifest["id"], "manifest_hash": verified_manifest["hash"],
            "source_snapshot_hash": actual_hash, "complete": verified_manifest["payload"]["complete"],
            "counts": verified_manifest["payload"]["counts"], "record_count": len(indexed),
            "created_or_verified": len(indexed), "manifest": verified_manifest}
