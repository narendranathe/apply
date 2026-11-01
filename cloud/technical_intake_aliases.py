"""Immutable, hashed alias claims for centrally serialized technical intake.

These records are not locks. The authenticated host must serialize all writers
and reconcile any uncertain previous write before attempting another admission.
"""
from .records import content_hash, make_record, stable_id
from .technical_records import validate_technical_record

BINDING_KIND = "technical_intake_alias_bindings"
INTENT_KIND = "technical_intake_alias_intents"


def technical_alias_bindings(candidate):
    candidate = validate_technical_record(candidate)
    payload = candidate["payload"]
    aliases = [("source_identity", payload["source_identity"])]
    if payload["source_url"] is not None:
        aliases.append(("source_url", payload["source_url"]))
    bindings = []
    for alias_type, value in sorted(aliases):
        binding_id = stable_id("technical_intake_alias_binding", candidate["kind"], alias_type, value)
        binding = make_record(BINDING_KIND, {"schema": "technical-intake-alias-binding/v1",
            "record_kind": candidate["kind"], "alias_type": alias_type,
            "alias_hash": content_hash(value), "entity_id": payload["entity_id"]}, binding_id, "bound")
        bindings.append(binding)
    return bindings


def technical_intake_alias_records(envelope):
    action = envelope["payload"]["action"]
    if envelope["kind"] != "agent_proposals" or action.get("type") != "admit_technical_source":
        return [], []
    candidate = validate_technical_record(action["candidate"])
    bindings = technical_alias_bindings(candidate)
    intents = []
    for binding in bindings:
        intent_id = stable_id("technical_intake_alias_intent", binding["id"], candidate["payload"]["revision"])
        intent = make_record(INTENT_KIND, {"schema": "technical-intake-alias-intent/v1",
            "binding_id": binding["id"], "revision": candidate["payload"]["revision"],
            "action_id": envelope["id"], "intake_hash": envelope["hash"]}, intent_id, "reserved")
        intents.append(intent)
    return bindings, intents


def validate_technical_intake_alias_snapshot(indexed):
    """Require every technical intake and alias member to form a complete chain."""
    from .agent_dispatcher import _envelope, _queue_and_result, _semantic_reservation

    expected_aliases = {}
    for row in indexed.values():
        action = row["payload"].get("action") if row["kind"] == "agent_proposals" else None
        if not isinstance(action, dict) or action.get("type") != "admit_technical_source":
            continue
        intake = _envelope(row)
        bindings, intents = technical_intake_alias_records(intake)
        queue, acknowledgement = _queue_and_result(intake)
        expected = [*bindings, *intents, _semantic_reservation(intake), queue, acknowledgement]
        for member in expected:
            if indexed.get(member["id"]) != member:
                raise ValueError("incomplete technical intake")
        for member in [*bindings, *intents]:
            old = expected_aliases.setdefault(member["id"], member)
            if old != member:
                raise ValueError("conflicting technical intake alias")
    for row in indexed.values():
        if row["kind"] in {BINDING_KIND, INTENT_KIND} and expected_aliases.get(row["id"]) != row:
            raise ValueError("orphaned technical intake alias")
