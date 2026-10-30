"""Isolated central-writer admission; not an orchestrator or authorization layer.

The host must supply verified serialization and previous-writer reconciliation.
A complete, fresh ``index_source_records`` snapshot is a store contract, not
something this function can independently establish. An ambiguous write requires
reconciliation even if the following snapshot does not show it.
"""
from cloud.agent_worker import _host_context
from cloud.agent_dispatcher import DispatcherRejected
from cloud.records import content_hash, make_record, stable_id, validate_record
from cloud.technical_records import validate_technical_record, validate_technical_aliases


class AdmissionRejected(RuntimeError):
    pass


class AdmissionConflict(RuntimeError):
    pass


class AdmissionStorageError(RuntimeError):
    pass


def _read(store, identity):
    try:
        row = store.get(identity)
        return validate_record(row) if row else None
    except Exception:
        raise AdmissionStorageError("read_unconfirmed_reconcile") from None


def _write(store, row):
    try:
        store.put(row)
    except Exception:
        raise AdmissionStorageError("write_unconfirmed_reconcile") from None
    if _read(store, row["id"]) != row:
        raise AdmissionStorageError("readback_unconfirmed_reconcile")


def _records(record):
    payload = record["payload"]
    aliases = [("source_identity", payload["source_identity"])]
    if payload["source_url"] is not None:
        aliases.append(("source_url", payload["source_url"]))
    reservations = []
    for name, value in sorted(aliases):
        alias_id = stable_id("technical_alias", record["kind"], name, value)
        data = {"schema": "technical-alias/v1", "family": record["kind"], "alias_type": name,
                "alias_hash": content_hash(value), "entity_id": payload["entity_id"]}
        reservations.append(make_record("technical_alias_reservations", data, alias_id, "reserved"))
    intent_id = stable_id("technical_admission", record["id"])
    intent = make_record("technical_admission_intents", {
        "schema": "technical-admission/v1", "target_id": record["id"], "target_hash": record["hash"],
        "reservation_ids": [row["id"] for row in reservations], "entity_id": payload["entity_id"]
    }, intent_id, "started")
    completion = make_record("technical_admission_completions", {
        "schema": "technical-admission-completion/v1", "intent_id": intent_id,
        "target_id": record["id"], "target_hash": record["hash"],
        "reservation_ids": [row["id"] for row in reservations], "status": "admitted"
    }, stable_id("technical_admission_completion", intent_id), "admitted")
    return intent, reservations, completion


def _check_admission_ledger(snapshot):
    """Reject orphaned or inconsistent reservation chains from any prior run."""
    indexed = {}
    for raw in snapshot:
        row = validate_record(raw)
        if row["id"] in indexed:
            raise ValueError("duplicate source identity")
        indexed[row["id"]] = row
    intents = [row for row in indexed.values() if row["kind"] == "technical_admission_intents"]
    expected_members = set()
    for intent in intents:
        target = indexed.get(intent["payload"].get("target_id"))
        if target is None:
            raise ValueError("incomplete admission")
        target = validate_technical_record(target)
        expected_intent, reservations, completion = _records(target)
        if intent != expected_intent:
            raise ValueError("invalid admission intent")
        for expected in [*reservations, completion]:
            if indexed.get(expected["id"]) != expected:
                raise ValueError("incomplete admission")
            expected_members.add(expected["id"])
    for row in indexed.values():
        if row["kind"] in {"technical_alias_reservations", "technical_admission_completions"}:
            if row["id"] not in expected_members:
                raise ValueError("orphaned admission member")
    return indexed


def admit_technical_record(store, candidate, *, writer_context):
    try:
        _host_context(writer_context)
    except DispatcherRejected:
        raise AdmissionRejected("trusted_central_writer_required") from None
    if writer_context.get("previous_writer_reconciled") is not True:
        raise AdmissionRejected("previous_writer_reconciliation_required")
    try:
        record = validate_technical_record(candidate)
        if record["kind"] not in {"technical_sources", "technical_items"}:
            raise ValueError("unsupported")
        snapshot = store.index_source_records()
        if not isinstance(snapshot, list):
            raise ValueError("incomplete snapshot")
        indexed = _check_admission_ledger(snapshot)
        validate_technical_aliases(snapshot + [record])
    except Exception:
        raise AdmissionStorageError("snapshot_or_source_conflict_reconcile") from None
    intent, reservations, completion = _records(record)
    expected = [intent, *reservations, record, completion]
    prior = [_read(store, row["id"]) for row in expected]
    for current, row in zip(prior, expected):
        if current != indexed.get(row["id"]):
            raise AdmissionStorageError("snapshot_readback_mismatch_reconcile")
        if current is not None and current != row:
            raise AdmissionConflict("immutable_or_alias_conflict")
    if all(prior):
        return completion["payload"]
    if prior[0] or prior[-1] or prior[-2]:
        raise AdmissionStorageError("partial_admission_reconcile")
    for row, current in zip(expected, prior):
        if current is None:
            _write(store, row)
    return completion["payload"]
