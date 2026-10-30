"""Offline-capable cursor commit primitive; caller supplies trusted writer context.

A committed cursor proves only that the specified immutable item admission
chains were read back. It does not verify source content, feed completeness,
provider permission, or prior-writer reconciliation on behalf of the host.
"""
from .agent_worker import _host_context
from .technical_admission import AdmissionStorageError, _check_admission_ledger, _read, _records, _write
from .technical_records import validate_technical_record


class CursorBlocked(RuntimeError):
    """The cursor's inputs are ineligible; do not advance."""


def commit_cursor(store, candidate, *, writer_context):
    try:
        _host_context(writer_context)
        if writer_context.get("previous_writer_reconciled") is not True:
            raise ValueError("unreconciled writer")
        cursor = validate_technical_record(candidate)
        if cursor["kind"] != "source_cursors" or not cursor["payload"]["evidence_refs"]:
            raise ValueError("cursor evidence required")
    except Exception:
        raise CursorBlocked("cursor_preconditions_unverified") from None
    try:
        snapshot = store.index_source_records()
        if not isinstance(snapshot, list):
            raise ValueError("incomplete snapshot")
        indexed = _check_admission_ledger(snapshot)
    except Exception:
        raise AdmissionStorageError("cursor_snapshot_requires_reconciliation") from None
    evidence = [indexed.get(identity) for identity in cursor["payload"]["evidence_refs"]]
    sources = [row for row in evidence if row and row["kind"] == "technical_sources"]
    if (len(sources) != 1 or sources[0]["payload"].get("entity_id") != cursor["payload"].get("technical_source_id")
            or sources[0]["payload"].get("source_identity") != cursor["payload"].get("source_identity")
            or any(row and row["kind"] == "technical_items"
                   and row["payload"].get("technical_source_id") != sources[0]["payload"].get("entity_id")
                   for row in evidence)):
        raise CursorBlocked("cursor_source_relation_unverified")
    for identity in cursor["payload"]["evidence_refs"]:
        item = indexed.get(identity)
        if item is None or item["kind"] not in {"technical_sources", "technical_items"}:
            raise CursorBlocked("cursor_admitted_item_required")
        try:
            item = validate_technical_record(item)
            intent, aliases, completion = _records(item)
        except (TypeError, KeyError, ValueError):
            raise AdmissionStorageError("cursor_evidence_conflicted") from None
        for expected in (item, intent, *aliases, completion):
            if indexed.get(expected["id"]) != expected or _read(store, expected["id"]) != expected:
                raise AdmissionStorageError("cursor_admission_readback_mismatch")
    previous = _read(store, cursor["id"])
    if previous != indexed.get(cursor["id"]):
        raise AdmissionStorageError("cursor_snapshot_readback_mismatch")
    if previous is not None:
        if previous != cursor:
            raise AdmissionStorageError("cursor_immutable_identity_conflict")
        return previous
    # Ambiguous write/readback exceptions fence the enclosing writer; never retry
    # solely because a later query cannot see the attempted append.
    _write(store, cursor)
    return cursor
