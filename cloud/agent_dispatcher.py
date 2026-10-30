"""Central, serialized intake queue. No ATS, email or LinkedIn effects.

Exclusivity is the trusted GitHub workflow's concurrency reservation, never a
Notion transaction. Partial persistence is a reconciliation stop, not a retry.
Queued means intake persisted, not work executed. The existing scheduled
application engine retains its separate verified-eligibility pipeline; this
dispatcher is not a provider-effect executor or a LinkedIn sender.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .agent_protocol import (ACTION_CAPABILITIES, OBSERVATION_CAPABILITIES, CAPABILITIES,
                             INTAKE_SCHEMA, AgentProtocol, ProtocolError)
from .records import canonical_json, make_record, stable_id, validate_record

CONCURRENCY_GROUP = "hiring-outreach-single-writer"
MAX_INTAKE_BYTES = 32000


class DispatcherError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class DispatcherRejected(DispatcherError):
    """No mutation occurred; caller must correct authorization or preconditions."""


class DispatcherConflict(DispatcherError):
    """Conflicting durable identity requires reconciliation, never overwrite."""


class DispatcherStorageError(DispatcherError):
    """Storage is unavailable/uncertain: stop all further effects in the run."""


def _get(store, identity):
    try:
        record = store.get(identity)
        return validate_record(record) if record else None
    except Exception:
        raise DispatcherStorageError("storage_read_unavailable_or_conflicted") from None


def _put_exact(store, record):
    try:
        store.put(record)
    except Exception:
        raise DispatcherStorageError("storage_write_unconfirmed_reconcile") from None
    if _get(store, record["id"]) != record:
        raise DispatcherStorageError("storage_readback_unconfirmed_reconcile")


def _authority(envelope, github_actor, trusted_registry, writer_context):
    # These are host arguments, never read from the untrusted intake payload.
    if (not isinstance(writer_context, dict)
        or writer_context.get("concurrency_group") != CONCURRENCY_GROUP
        or writer_context.get("reservation_verified") is not True
        or not isinstance(writer_context.get("run_id"), str) or not writer_context["run_id"]):
        raise DispatcherRejected("trusted_single_writer_reservation_required")
    if not isinstance(github_actor, str) or not github_actor or not isinstance(trusted_registry, dict):
        raise DispatcherRejected("authenticated_github_actor_required")
    entry = trusted_registry.get(github_actor)
    if not isinstance(entry, dict) or entry.get("enabled", True) is False:
        raise DispatcherRejected("github_actor_not_authorized")
    principal = entry.get("principal", github_actor)
    if not isinstance(principal, str) or not principal:
        raise DispatcherRejected("trusted_principal_binding_invalid")
    capabilities = entry.get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
        raise DispatcherRejected("trusted_capabilities_invalid")
    capabilities = set(capabilities) & CAPABILITIES
    payload = envelope["payload"]
    if payload.get("actor") != principal:
        raise DispatcherRejected("authenticated_actor_principal_mismatch")
    action = payload.get("action")
    mapping = ACTION_CAPABILITIES if envelope["kind"] == "agent_proposals" else OBSERVATION_CAPABILITIES
    if not isinstance(action, dict) or action.get("type") not in mapping:
        raise DispatcherRejected("unsupported_intake_action")
    capability = mapping[action["type"]]
    if capability not in capabilities:
        raise DispatcherRejected("current_trusted_capability_denied")
    if payload.get("idempotency_key") != action.get("idempotency_key") or not isinstance(action.get("idempotency_key"), str):
        raise DispatcherRejected("intake_idempotency_identity_invalid")
    expected_id = stable_id("agent-intake", principal, action["idempotency_key"])
    if envelope["id"] != expected_id or payload.get("action_id") != expected_id:
        raise DispatcherRejected("intake_idempotency_identity_invalid")
    return principal, {principal: {**entry, "capabilities": sorted(capabilities)}}


def _envelope(value):
    try:
        if len(canonical_json(value).encode("utf-8")) > MAX_INTAKE_BYTES:
            raise DispatcherRejected("intake_size_limit_exceeded")
        record = validate_record(value)
    except DispatcherError:
        raise
    except Exception:
        raise DispatcherRejected("invalid_intake_envelope") from None
    payload = record["payload"]
    if (record["kind"] not in {"agent_proposals", "agent_observations"} or record["status"] != "proposed"
        or not isinstance(payload, dict) or payload.get("schema") != INTAKE_SCHEMA
        or payload.get("status") != "proposed" or payload.get("external_effect") is not False):
        raise DispatcherRejected("invalid_intake_contract")
    return record


def _queue_and_result(envelope):
    payload, identity = envelope["payload"], envelope["id"]
    evidence = payload["evidence_refs"]
    queue = make_record("agent_execution_queue", {"schema": "apply-agent-queue/v1", "action_id": identity,
        "intake_id": identity, "intake_hash": envelope["hash"], "actor": payload["actor"],
        "action_type": payload["action"]["type"], "entity_id": payload["action"]["entity_id"],
        "expected_projection_hash": payload["expected_projection_hash"],
        "status": "queued", "evidence_refs": evidence, "external_effect": False},
        record_id="action-queue:" + identity, status="queued")
    result = make_record("agent_action_results", {"action_id": identity, "status": "queued",
        "evidence_refs": evidence, "external_effect": False, "actor": payload["actor"]},
        record_id="action-result:" + identity, status="queued")
    return queue, result


def _semantic_reservation(envelope):
    payload, action = envelope["payload"], envelope["payload"]["action"]
    # A changed provider/source projection permits a new appropriate task; a
    # different caller, key or alias task ID cannot duplicate current intent.
    semantic = {"kind": envelope["kind"], "entity_id": action["entity_id"], "type": action["type"],
                "projection_hash": payload["expected_projection_hash"]}
    if envelope["kind"] == "agent_proposals" and action["type"] == "admit_technical_source":
        candidate = validate_record(action["candidate"])
        semantic = {"kind": envelope["kind"], "type": action["type"],
                    "entity_id": candidate["payload"]["entity_id"], "record_kind": candidate["kind"],
                    "revision": candidate["payload"]["revision"]}
    if envelope["kind"] == "agent_observations":
        semantic.pop("projection_hash")
        timestamp = action.get('occurred_at')
        if isinstance(timestamp, str):
            try:
                parsed = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                if parsed.tzinfo is not None:
                    timestamp = parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                pass  # Invalid timestamps are held by protocol validation.
        semantic.update(outcome=action.get("outcome"), occurred_at=timestamp,
                        evidence_hashes=payload.get("evidence_hashes"))
    identity = stable_id("agent_action_reservation", semantic)
    reservation = make_record("agent_action_reservations", {"schema": "apply-agent-reservation/v1",
        "action_id": envelope["id"], "intake_hash": envelope["hash"], "semantic": semantic,
        "status": "queued", "external_effect": False}, record_id=identity, status="queued")
    return reservation


def dispatch_intake(store, envelope, *, github_actor, trusted_registry, writer_context, now=None):
    """Authenticate, revalidate, append queue+ack under trusted serialization.

    ``trusted_registry`` maps an authenticated GitHub actor to a bound principal
    and current capability grants. ``writer_context`` is supplied by the Actions
    runner, asserting its common concurrency group reservation. It is not a
    lock acquired in Notion and must never be copied from an agent request.

    Return the immutable acknowledgement. Queued means only intake persisted;
    submission/delivery is not executed or represented as verified success.
    """
    record = _envelope(envelope)
    principal, registry = _authority(record, github_actor, trusted_registry, writer_context)
    payload = record["payload"]
    try:
        queue, result = _queue_and_result(record)
        reservation = _semantic_reservation(record)
    except Exception:
        raise DispatcherRejected("invalid_intake_contract") from None
    existing = _get(store, record["id"])
    old_queue, old_result = _get(store, queue["id"]), _get(store, result["id"])
    old_reservation = _get(store, reservation["id"])
    # Complete replays remain authenticated against today's registry, but do
    # not require the historical proposal's snapshot still to be the latest.
    for actual, expected in ((existing, record), (old_queue, queue), (old_result, result)):
        if actual and actual != expected:
            raise DispatcherConflict("immutable_intake_identity_conflict")
    if old_reservation and old_reservation != reservation:
        raise DispatcherConflict("semantic_action_already_reserved")
    if existing and old_queue and old_result and old_reservation:
        return old_result
    if any((existing, old_queue, old_result, old_reservation)):
        raise DispatcherStorageError("partial_intake_requires_reconciliation")
    protocol = AgentProtocol(store, registry, principal, now=now or datetime.now(timezone.utc))
    try:
        if record["kind"] == "agent_proposals":
            rebuilt = protocol.propose_action(payload["action"], payload["evidence"])["data"]["envelope"]
        else:
            rebuilt = protocol.report_observed_outcome(payload["action"], payload["evidence"])["data"]["envelope"]
    except ProtocolError as exc:
        if exc.code in {"authoritative_index_unavailable_or_conflicted", "authoritative_record_unavailable_or_conflicted", "index_missing", "indexed_record_missing"}:
            raise DispatcherStorageError("authoritative_revalidation_unavailable") from None
        raise DispatcherRejected("intake_revalidation_" + exc.code) from None
    if rebuilt != record:
        raise DispatcherRejected("intake_canonical_revalidation_mismatch")
    # No writes occur above this line. Each append requires exact readback;
    # any uncertainty aborts before the next append or business effect.
    for immutable in (record, reservation, queue, result):
        _put_exact(store, immutable)
    return result
