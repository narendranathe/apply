"""Offline-capable cursor commit primitive; caller supplies trusted writer context.

A committed cursor proves only that the specified immutable item admission
chains were read back. It does not verify source content, feed completeness,
provider permission, or prior-writer reconciliation on behalf of the host.
"""
from .agent_worker import _host_context
from .technical_admission import AdmissionStorageError, _check_admission_ledger, _read, _records, _write
from .technical_records import validate_technical_record
from .records import stable_id


class CursorBlocked(RuntimeError):
    """The cursor's inputs are ineligible; do not advance."""


def validate_cursor_snapshot(indexed):
    """Validate every saved cursor against its admitted source and history."""
    groups = {}
    for row in indexed.values():
        if row["kind"] != "source_cursors":
            continue
        cursor = validate_technical_record(row)
        payload = cursor["payload"]
        refs = payload["evidence_refs"]
        evidence = [indexed.get(identity) for identity in refs]
        sources = [item for item in evidence if item and item["kind"] == "technical_sources"]
        if (not refs or len(sources) != 1 or any(item is None or item["kind"] not in {"technical_sources", "technical_items"} for item in evidence)):
            raise ValueError("cursor evidence missing")
        source = sources[0]
        source_id = source["payload"]["entity_id"]
        if (payload.get("technical_source_id") != source_id
                or payload["source_identity"] != source["payload"]["source_identity"]
                or payload["entity_id"] != stable_id("source_cursor", source_id)
                or not isinstance(payload.get("cursor_value"), str)
                or not 1 <= len(payload["cursor_value"]) <= 1000
                or any(item["kind"] == "technical_items" and item["payload"].get("technical_source_id") != source_id for item in evidence)):
            raise ValueError("cursor source mismatch")
        groups.setdefault(source_id, []).append(cursor)
    for rows in groups.values():
        rows.sort(key=lambda row: row["payload"]["revision"])
        prior_hash = None
        for number, row in enumerate(rows, 1):
            if row["payload"]["revision"] != number or row["payload"].get("previous_cursor_hash") != prior_hash:
                raise ValueError("cursor history conflict")
            prior_hash = row["hash"]


def validate_cursor_evidence(store, cursor):
    """Verify source/item chains before creation; this does not authorize a writer."""
    if cursor["kind"] != "source_cursors" or not cursor["payload"]["evidence_refs"]:
        raise CursorBlocked("cursor_evidence_required")
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
    source_id = sources[0]["payload"]["entity_id"]
    if (cursor["payload"]["entity_id"] != stable_id("source_cursor", source_id)
            or not isinstance(cursor["payload"].get("cursor_value"), str)
            or not 1 <= len(cursor["payload"]["cursor_value"]) <= 1000):
        raise CursorBlocked("cursor_identity_or_value_invalid")
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
    history = []
    for row in indexed.values():
        if row["kind"] != "source_cursors" or row["payload"].get("technical_source_id") != source_id:
            continue
        try:
            row = validate_technical_record(row)
        except (TypeError, KeyError, ValueError):
            raise AdmissionStorageError("cursor_history_conflicted") from None
        if row["payload"]["entity_id"] != cursor["payload"]["entity_id"] or _read(store, row["id"]) != row:
            raise AdmissionStorageError("cursor_history_conflicted")
        history.append(row)
    history.sort(key=lambda row: row["payload"]["revision"])
    prior_hash = None
    for number, row in enumerate(history, 1):
        if row["payload"]["revision"] != number or row["payload"].get("previous_cursor_hash") != prior_hash:
            raise AdmissionStorageError("cursor_history_conflicted")
        prior_hash = row["hash"]
    revision = cursor["payload"]["revision"]
    if history and cursor == history[-1]:
        return indexed
    if revision != len(history) + 1 or cursor["payload"].get("previous_cursor_hash") != prior_hash:
        raise CursorBlocked("cursor_revision_not_current")
    return indexed


def commit_cursor(store, candidate, *, writer_context):
    try:
        _host_context(writer_context)
        if writer_context.get("previous_writer_reconciled") is not True:
            raise ValueError("unreconciled writer")
        cursor = validate_technical_record(candidate)
    except Exception:
        raise CursorBlocked("cursor_preconditions_unverified") from None
    indexed = validate_cursor_evidence(store, cursor)
    previous = _read(store, cursor["id"])
    if previous != indexed.get(cursor["id"]):
        raise AdmissionStorageError("cursor_snapshot_readback_mismatch")
    if previous is not None:
        if previous != cursor:
            raise AdmissionStorageError("cursor_immutable_identity_conflict")
        return previous
    # Ambiguous write/readback exceptions fence the enclosing writer; never retry
    # solely because a later query cannot see the attempted append.
    from .notion import CursorCohortUnavailable
    try:
        _write(store, cursor)
    except CursorCohortUnavailable:
        raise CursorBlocked("cursor_cohort_verification_unavailable") from None
    return cursor
