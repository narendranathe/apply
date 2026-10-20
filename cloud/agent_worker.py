"""Bounded consumer for admitted agent work under the central cloud writer.

This worker can read official jobs, prepare truthful packets, and materialize
already verified observations. It has no provider-effect execution path. Queue
acknowledgements stay immutable; uncertain execution markers require recovery.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone

from scripts.jobscout_feed import candidates, feed_metadata, fetch_feed
from .agent_dispatcher import (CONCURRENCY_GROUP, DispatcherRejected, DispatcherStorageError,
    _envelope, _get, _put_exact, _queue_and_result, _semantic_reservation)
from .agent_index import (CURRENT_OUTCOMES, _evidence, _related, _stamp,
                          valid_terminal_result)
from .agent_protocol import (ACTION_CAPABILITIES, OBSERVATION_CAPABILITIES, BLOCKED_STATES,
                             AgentProtocol, ProtocolError)
from .applications import (_employer, canonical_url, employer_identity, fold_applications,
                           matching_application, run_applications)
from .notion import NotionError
from .preparation import prepare_packet
from .records import content_hash, make_record, stable_id, validate_record
from .submission import HTTPTransport, SubmissionBlocked, adapter_for, normalize

EXECUTION_SCHEMA = "apply-agent-execution/v1"
RESULT_SCHEMA = "apply-agent-result/v1"
MAX_ACTIONS = 10
OBSERVATION_SOURCES = frozenset({"conversation_sources", "manual_action_sources",
    "conversation_events", "outreach_events", "conversation_observations", "contact_observations"})


class WorkerStorageError(DispatcherStorageError, NotionError):
    """A partial or uncertain append fences the enclosing central writer."""


class WorkerBlocked(RuntimeError):
    pass


def _read(store, identity):
    try:
        return _get(store, identity)
    except DispatcherStorageError as error:
        raise WorkerStorageError(error.code) from None


def _append(store, row):
    try:
        _put_exact(store, row)
    except DispatcherStorageError as error:
        raise WorkerStorageError(error.code) from None


def _query(store, kind):
    try:
        result = {}
        for raw in store.query(kind=kind):
            row = validate_record(raw)
            if row["kind"] != kind or (row["id"] in result and result[row["id"]] != row):
                raise ValueError("conflicted_source")
            result[row["id"]] = row
        return list(result.values())
    except Exception:
        raise WorkerStorageError("worker_source_query_unavailable_or_conflicted") from None


class ExactStore:
    """Give the existing preparation lane exact readback for every append."""
    def __init__(self, store):
        self.store = store

    def get(self, identity):
        return _read(self.store, identity)

    def query(self, kind=None):
        return _query(self.store, kind)

    def put(self, row):
        _append(self.store, row)
        return row


class ReadOnlyTransport:
    def __init__(self, transport=None):
        self.transport = transport or HTTPTransport()
        self.reads = []

    def request(self, method, url, *, headers=None, body=None):
        if method != "GET" or body is not None:
            raise WorkerStorageError("agent_worker_provider_effect_forbidden")
        result = self.transport.request(method, url, headers=headers, body=body)
        self.reads.append({"method": "GET", "url": url, "http_status": result.status,
                           "response_sha256": content_hash(result.data)})
        return result


def _host_context(writer_context):
    if (not isinstance(writer_context, dict)
            or writer_context.get("concurrency_group") != CONCURRENCY_GROUP
            or writer_context.get("reservation_verified") is not True
            or writer_context.get("orchestrator_verified") is not True
            or not isinstance(writer_context.get("run_id"), str) or not writer_context["run_id"]):
        raise DispatcherRejected("trusted_central_orchestrator_required")


def _admission(store, queue):
    payload = queue["payload"]
    identity = payload.get("action_id")
    if not isinstance(identity, str) or not identity:
        raise WorkerStorageError("worker_admission_requires_reconciliation")
    intake, ack = _read(store, identity), _read(store, "action-result:" + identity)
    try:
        canonical = _envelope(intake)
        expected_queue, expected_ack = _queue_and_result(canonical)
        expected_reservation = _semantic_reservation(canonical)
    except Exception:
        raise WorkerStorageError("worker_admission_requires_reconciliation") from None
    reservation = _read(store, expected_reservation["id"])
    if queue != expected_queue or ack != expected_ack or reservation != expected_reservation:
        raise WorkerStorageError("worker_admission_requires_reconciliation")
    p, action = intake["payload"], intake["payload"].get("action", {})
    if (intake["id"] != stable_id("agent-intake", p.get("actor"), p.get("idempotency_key"))
            or p.get("action_id") != identity or action.get("idempotency_key") != p.get("idempotency_key")
            or action.get("expected_manifest_id") != p.get("expected_manifest_id")
            or action.get("expected_projection_hash") != p.get("expected_projection_hash")
            or p.get("evidence") != p.get("evidence_refs")):
        raise WorkerStorageError("worker_admission_requires_reconciliation")
    return intake, reservation, ack


def _complete_admission_family(store, queues):
    """A failed dispatcher may stop before creating its queue row.

    Inspect all admission/execution families before new work, including orphan
    intents/results. Absence from the queue cannot clear an uncertain append.
    """
    intakes = _query(store, 'agent_proposals') + _query(store, 'agent_observations')
    known = {row['id']: row for row in intakes}
    expected_queues, expected_reservations, expected_acks = {}, {}, {}
    try:
        if len(known) != len(intakes):
            raise ValueError('ambiguous_intake')
        for row in intakes:
            canonical = _envelope(row)
            queue, ack = _queue_and_result(canonical)
            reservation = _semantic_reservation(canonical)
            expected_queues[queue['id']] = queue
            expected_reservations[reservation['id']] = reservation
            expected_acks[ack['id']] = ack
        if ({row['id']: row for row in queues} != expected_queues
                or {row['id']: row for row in _query(store, 'agent_action_reservations')} != expected_reservations):
            raise ValueError('partial_admission')
        actual_acks = {}
        for row in _query(store, 'agent_action_results'):
            identity = row['payload'].get('action_id')
            if identity not in known:
                raise ValueError('orphan_result')
            if row['id'] in expected_acks:
                actual_acks[row['id']] = row
            elif row['id'] != stable_id('agent_action_result', identity, 1):
                raise ValueError('unrecognized_result')
        if actual_acks != expected_acks:
            raise ValueError('partial_acknowledgement')
        for row in _query(store, 'agent_action_executions'):
            identity = row['payload'].get('action_id')
            if identity not in known or row['id'] != stable_id('agent_action_execution', identity):
                raise ValueError('orphan_execution')
    except WorkerStorageError:
        raise
    except Exception:
        raise WorkerStorageError('worker_admission_requires_reconciliation') from None


def _current_context(store, intake, registry, now):
    payload, action = intake["payload"], intake["payload"]["action"]
    actor = payload["actor"]
    if not isinstance(registry, dict):
        raise WorkerBlocked("current_registry_unavailable")
    bindings = [entry for key, entry in registry.items() if isinstance(entry, dict)
                and entry.get("principal", key) == actor]
    if len(bindings) != 1 or bindings[0].get("enabled", True) is False:
        raise WorkerBlocked("current_principal_revoked_or_ambiguous")
    capabilities = bindings[0].get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
        raise WorkerBlocked("current_registry_capabilities_invalid")
    mapping = ACTION_CAPABILITIES if intake["kind"] == "agent_proposals" else OBSERVATION_CAPABILITIES
    capability = mapping.get(action.get("type"))
    if not capability or capability not in capabilities:
        raise WorkerBlocked("current_capability_revoked_or_unsupported")
    client = AgentProtocol(store, {actor: bindings[0]}, actor, now=now)
    try:
        manifest = client._manifest(fresh=True)
        # Publication of queue status can renew the manifest without changing
        # the source projection. Never execute against a changed projection.
        fresh_action = {**action, "expected_manifest_id": manifest["id"]}
        _, projection, hashes = client._validate_intake(fresh_action, payload["evidence"], capability)
    except ProtocolError as error:
        if error.code in {"authoritative_index_unavailable_or_conflicted",
                "authoritative_record_unavailable_or_conflicted", "index_missing", "indexed_record_missing"}:
            raise WorkerStorageError("worker_current_context_unavailable") from None
        raise WorkerBlocked("worker_context_" + error.code) from None
    if hashes != payload.get("evidence_hashes"):
        raise WorkerStorageError("worker_evidence_hash_requires_reconciliation")
    if payload.get("required_capability") != capability:
        raise WorkerBlocked("worker_capability_mismatch")
    p = projection["payload"]
    observed = _stamp(p.get("verified_at")) or _stamp(p.get("observed_at"))
    freshness = p.get("freshness")
    freshness = freshness.get("status") if isinstance(freshness, dict) else freshness
    if (not observed or not 0 <= (now - observed).total_seconds() <= 86400
            or freshness in {"stale", "unknown", "historical", "unavailable"}):
        raise WorkerBlocked("worker_entity_source_stale_or_unverified")
    suppressed = p.get("suppression", {})
    suppressed = suppressed.get("suppressed") if isinstance(suppressed, dict) else bool(suppressed)
    if (suppressed or p.get("suppressed") or p.get("blocking_conditions")
            or p.get("state", p.get("current_state")) in BLOCKED_STATES):
        raise WorkerBlocked("worker_entity_suppressed_or_blocked")
    return projection


def _target(store, projection):
    identity = projection["payload"].get("source_record_id")
    source = _read(store, identity) if isinstance(identity, str) and identity else None
    if not source or source["kind"] not in {"jobs", "applications"}:
        raise WorkerBlocked("official_job_target_unavailable")
    payload = source["payload"]
    target = {**payload, **(payload.get("job") if isinstance(payload.get("job"), dict) else {})}
    if not all(isinstance(target.get(key), str) and target[key] for key in ("company", "url")):
        raise WorkerBlocked("official_job_target_identity_incomplete")
    return source, target


def _matches(target, candidate):
    if employer_identity(target.get("company")) != employer_identity(candidate.get("company")):
        return False
    # Entity job_id values are frequently internal CRM identities. They are
    # never promoted to official requisition IDs when a URL must establish it.
    req, other = target.get("requisition_id"), candidate.get("requisition_id")
    if req and other:
        return normalize(req) == normalize(other)
    left = {canonical_url(url) for url in [target.get("url"), *target.get("aliases", []), *target.get("url_aliases", [])] if url}
    right = {canonical_url(url) for url in [candidate.get("url"), *candidate.get("aliases", [])] if url}
    return bool(left & right)


def _verify_target(target, settings, transport, *, require_schema=False):
    employer = _employer(target, settings)
    job = adapter_for(employer, transport).verify(target, require_schema=require_schema)
    if not job.get("open"):
        raise WorkerBlocked("official_requisition_closed")
    if not _matches(target, job):
        raise WorkerBlocked("official_target_identity_changed")
    return job


def _research(store, intake, projection, config, transport, now):
    source, target = _target(store, projection)
    if intake["payload"]["evidence_hashes"].get(source["id"]) != source["hash"]:
        raise WorkerBlocked("target_source_not_admitted")
    job = _verify_target(target, config.get("applications", config), transport)
    row = make_record("agent_research_evidence", {"schema": "apply-agent-research/v1",
        "action_id": intake["id"], "entity_id": intake["payload"]["action"]["entity_id"],
        "source_record_id": source["id"], "source_record_hash": source["hash"],
        "official_job": job, "official_job_hash": content_hash(job),
        "official_reads": transport.reads, "retrieved_at": now.isoformat(), "external_effect": False},
        stable_id("agent_research_evidence", intake["id"]), "verified")
    _append(store, row)
    return "research_verified", [row]


def _prepare(store, intake, projection, config, transport, now, feed, prepare):
    source, target = _target(store, projection)
    if intake["payload"]["evidence_hashes"].get(source["id"]) != source["hash"]:
        raise WorkerBlocked("target_source_not_admitted")
    settings = copy.deepcopy(config.get("applications", config))
    try:
        live_feed = feed if feed is not None else fetch_feed(settings.get("feed_url") or None)
        meta = feed_metadata(live_feed, now, settings.get("max_feed_age_hours", 24))
    except Exception:
        raise WorkerBlocked("target_feed_unavailable_or_malformed") from None
    if meta["stale"]:
        raise WorkerBlocked("target_feed_stale_or_unverified")
    try:
        jobs = live_feed.get("jobs")
        if not isinstance(jobs, list) or any(not isinstance(row, dict) for row in jobs):
            raise ValueError("invalid_jobs")
        for row in jobs:
            score = row.get("relevance_score")
            if score is not None and (type(score) not in {float, int} or not math.isfinite(score)):
                raise ValueError("invalid_score")
            for key in ("aliases", "url_aliases"):
                if key in row and (not isinstance(row[key], list) or any(not isinstance(value, str) for value in row[key])):
                    raise ValueError("invalid_aliases")
            for key in ("company", "url"):
                if key in row and row[key] is not None and not isinstance(row[key], str):
                    raise ValueError("invalid_identity")
        selected = [row for row in jobs if _matches(target, row)]
        if len(selected) != 1:
            raise WorkerBlocked("target_feed_missing_or_ambiguous")
        filtered = {**live_feed, "jobs": selected}
        if len(candidates(filtered, settings.get("min_score", .51), settings.get("max_age_days", 7), now)) != 1:
            raise WorkerBlocked("target_feed_not_eligible")
        # Filtering candidates must not filter historical suppression. The
        # full feed may contain inactive employer-level applied observations.
        historical = {}
        for index, old in enumerate(jobs):
            if old.get("application_status") in {"applied", "applied_verified", "submitting", "submission_unknown", "interview", "rejected", "withdrawn"}:
                historical[str(index)] = {"payload": {"company": old.get("company"),
                    "requisition_id": old.get("requisition_id") or old.get("job_id"), "url": old.get("url")}}
        if matching_application(historical, target["company"], target.get("requisition_id"), [target["url"]]):
            raise WorkerBlocked("historical_target_submission_requires_reconciliation")
    except WorkerBlocked:
        raise
    except Exception:
        raise WorkerBlocked("target_feed_unavailable_or_malformed") from None
    _verify_target(target, settings, transport)
    settings.update(max_submissions_per_day=0, max_candidates=1)

    def never_submit():
        raise WorkerStorageError("agent_worker_provider_effect_forbidden")

    def checked_prepare(job, grade, prepared_settings):
        if not _matches(target, job):
            raise WorkerBlocked("official_target_identity_changed")
        return (prepare or prepare_packet)(job, grade, prepared_settings)

    result = run_applications(ExactStore(store), {"applications": settings}, False,
        feed=filtered, transport=transport, prepare=checked_prepare, effect_guard=never_submit)
    if result.get("submission_attempts") or result.get("applied_verified") or result.get("submission_unknown"):
        raise WorkerStorageError("agent_worker_provider_effect_forbidden")
    rows = fold_applications(_query(store, "applications"))
    current = matching_application(rows, target["company"], target.get("requisition_id"), [target["url"]])
    packet_id = (current or {}).get("payload", {}).get("packet", {}).get("record_id")
    packet = _read(store, packet_id) if packet_id else None
    if (result.get("prepared") != 1 or not packet or packet["kind"] != "application_packets"
            or packet["status"] != "verified" or packet["payload"].get("missing")
            or packet["payload"].get("qa", {}).get("verified") is not True):
        raise WorkerBlocked("truthful_application_preparation_incomplete")
    return "application_prepared", [current, packet]


def _observation(store, intake, now):
    action = intake["payload"]["action"]
    entity, outcome, when = action["entity_id"], action.get("outcome"), _stamp(action.get("occurred_at"))
    if outcome not in CURRENT_OUTCOMES or not when or when > now:
        raise WorkerBlocked("matching_structured_observation_required")
    matches = []
    for identity in intake["payload"]["evidence"]:
        source = _read(store, identity)
        if not source or source["kind"] not in OBSERVATION_SOURCES:
            continue
        proof = _evidence(source, now)
        related = _related(source["payload"])
        scope = {value for values in related.values() for value in values}
        source_type = source["payload"].get("source")
        if (proof and proof["outcome"] == outcome and _stamp(proof["confirmed_at"]) == when
                and entity in scope and any(entity in related.get(key, []) for key in ("contact_ids", "thread_ids"))
                and isinstance(source_type, str)
                and source_type in {"user_confirmed", "official_provider", "official_live_read", "verified_manual"}):
            matches.append((source, proof, related))
    if len(matches) != 1:
        raise WorkerBlocked("matching_structured_observation_required")
    source, proof, related = matches[0]
    for old in _query(store, "conversation_events"):
        existing = _evidence(old, now)
        if (existing and existing["event_id"] == proof["event_id"]
                and existing["evidence_url"] == proof["evidence_url"]):
            if existing["outcome"] != proof["outcome"] or existing["confirmed_at"] != proof["confirmed_at"] or _related(old["payload"]) != related:
                raise WorkerStorageError("verified_observation_identity_conflict")
            return "verified_observation_already_recorded", [old]
    payload = {"schema": "apply-agent-verified-observation/v1", "action_id": intake["id"],
        "actor": source["payload"].get("actor", "verified_source"), "source": source["payload"]["source"],
        "occurred_at": proof["confirmed_at"], "confirmation_evidence": source["payload"]["confirmation_evidence"],
        "source_record_id": source["id"], "source_record_hash": source["hash"], "evidence_refs": [source["id"]],
        "external_effect": False}
    for key, values in related.items():
        payload[key] = values
    row = make_record("conversation_events", payload,
        stable_id("agent_verified_observation", proof["event_id"], proof["evidence_url"], related), "verified")
    _append(store, row)
    return "verified_observation_recorded", [row]


def run_agent_worker(store, config, *, trusted_registry, writer_context, now=None,
                     transport=None, feed=None, prepare=None, effect_guard=None):
    """Consume a bounded admitted queue; never send, submit, or administer.

    Arguments granting authority are deployment-owned, as in dispatch_intake.
    All injected reads/preparation are deterministic test seams, not grants.
    A durable intent without its exact terminal chain is a reconciliation stop.
    """
    _host_context(writer_context)
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise DispatcherRejected("worker_aware_time_required")
    maximum = config.get("agent_worker", {}).get("max_actions", 3)
    if type(maximum) is not int or not 1 <= maximum <= MAX_ACTIONS:
        raise DispatcherRejected("worker_queue_budget_invalid")
    summary = {"lane": "agent_worker", "queued_seen": 0, "completed": 0, "blocked": 0,
               "already_finished": 0, "external_effects": 0, "exceptions": {}}
    queues = sorted(_query(store, "agent_execution_queue"), key=lambda row: row["id"])
    _complete_admission_family(store, queues)
    admitted = []
    for queue in queues:
        intake, reservation, ack = _admission(store, queue)
        identity, p = intake["id"], intake["payload"]
        execution_id, result_id = stable_id("agent_action_execution", identity), stable_id("agent_action_result", identity, 1)
        execution, terminal = _read(store, execution_id), _read(store, result_id)
        if terminal:
            sources = {row["id"]: row for row in (intake, reservation, queue, ack) + ((execution,) if execution else ())}
            for ref in p.get("evidence_hashes", {}):
                evidence = _read(store, ref)
                if evidence:
                    sources[ref] = evidence
            context_source = (execution or {}).get("payload", {}).get("source_record_id")
            if isinstance(context_source, str) and context_source:
                evidence = _read(store, context_source)
                if evidence:
                    sources[context_source] = evidence
            for ref in terminal["payload"].get("output_hashes", {}):
                output = _read(store, ref)
                if output:
                    sources[ref] = output
                    source_id = output["payload"].get("source_record_id")
                    if isinstance(source_id, str) and source_id:
                        source = _read(store, source_id)
                        if source:
                            sources[source_id] = source
            if not valid_terminal_result(terminal, sources):
                raise WorkerStorageError("worker_result_chain_requires_reconciliation")
            summary["already_finished"] += 1
            continue
        if execution:
            raise WorkerStorageError("partial_worker_execution_requires_reconciliation")
        admitted.append((queue, intake, reservation, ack))
    # Preflight the complete admitted family before any append. A partial
    # execution beyond today's processing budget still fences all new work.
    for queue, intake, reservation, ack in admitted[:maximum]:
        identity, p = intake["id"], intake["payload"]
        execution_id = stable_id("agent_action_execution", identity)
        result_id = stable_id("agent_action_result", identity, 1)
        summary["queued_seen"] += 1
        reason, projection = None, None
        try:
            projection = _current_context(store, intake, trusted_registry, now)
        except WorkerBlocked as error:
            reason = str(error)
        if effect_guard:
            effect_guard()
        source_id = projection["payload"].get("source_record_id") if projection else None
        source = _read(store, source_id) if source_id else None
        execution = make_record("agent_action_executions", {"schema": EXECUTION_SCHEMA,
            "action_id": identity, "actor": p["actor"], "intake_hash": intake["hash"],
            "reservation_hash": reservation["hash"], "queue_hash": queue["hash"], "queued_ack_hash": ack["hash"],
            "expected_projection_hash": p["expected_projection_hash"], "evidence_hashes": p["evidence_hashes"],
            "source_record_id": source_id, "source_record_hash": source["hash"] if source else None,
            "run_id": writer_context["run_id"], "started_at": now.isoformat(),
            "status": "started", "external_effect": False}, execution_id, "started")
        _append(store, execution)
        outputs, outcome = [], None
        if reason is None:
            try:
                action_type = p["action"]["type"]
                readonly = ReadOnlyTransport(transport)
                if effect_guard:
                    effect_guard()
                if action_type == "research":
                    outcome, outputs = _research(store, intake, projection, config, readonly, now)
                elif action_type == "prepare_application":
                    outcome, outputs = _prepare(store, intake, projection, config, readonly, now, feed, prepare)
                elif action_type in OBSERVATION_CAPABILITIES:
                    outcome, outputs = _observation(store, intake, now)
                else:
                    raise WorkerBlocked("worker_action_requires_unsupported_or_human_executor")
            except (WorkerBlocked, SubmissionBlocked) as error:
                reason = str(error) if isinstance(error, WorkerBlocked) else "official_read_" + str(error)
        status = "blocked" if reason else "completed"
        result = make_record("agent_action_results", {"schema": RESULT_SCHEMA, "action_id": identity,
            "actor": p["actor"], "status": status, "revision": 1, "previous_record_id": ack["id"],
            "queued_ack_hash": ack["hash"], "intake_hash": intake["hash"], "queue_hash": queue["hash"],
            "reservation_hash": reservation["hash"], "execution_id": execution_id, "execution_hash": execution["hash"],
            "entity_id": p["action"]["entity_id"], "outcome": outcome or "action_blocked",
            "blocking_conditions": [reason] if reason else [], "occurred_at": now.isoformat(),
            "verified": not bool(reason), "evidence_refs": sorted(set(p["evidence_refs"] + [r["id"] for r in outputs])),
            "output_hashes": {r["id"]: r["hash"] for r in outputs}, "external_effect": False}, result_id, status)
        _append(store, result)
        summary[status] += 1
        if reason:
            summary["exceptions"][reason] = summary["exceptions"].get(reason, 0) + 1
    return summary
