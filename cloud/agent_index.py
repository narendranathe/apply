"""Pure, evidence-derived current-state index for agent-neutral handoffs.

The orchestrator publishes the returned immutable records through the existing
single writer. This module has no store, network, message, or submit transport.
The manifest is the visibility boundary: an incomplete source snapshot exposes
observations but no ready task. Original evidence remains in its source record.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .applications import StateConflict, canonical_url, employer_identity, fold_applications, matching_application
from .submission import normalize
from .coordination_quarantine import ContactQuarantines
from .records import content_hash, make_record, stable_id, validate_record
from .reporting import _confirmed, _human_confirmation
from .technical_records import validate_technical_aliases, validate_technical_record

SCHEMA = "agent-index/v1"
CAPABILITIES = frozenset({"read_summarize", "research_propose", "prepare_application",
                          "execute_permitted_application", "ingest_verified_conversation",
                          "record_verified_manual_action", "administer_workflow"})
MAX_HISTORY = 8
MAX_BRIEF_TASKS = 12
TECHNICAL_ENTITY_KINDS = {
    "technical_sources": "technical_source", "technical_items": "technical_item",
    "engineering_observations": "engineering_observation", "technical_signals": "technical_signal",
    "technical_hypotheses": "technical_hypothesis", "engineering_opportunities": "engineering_opportunity",
    "contact_topic_relevances": "contact_topic_relevance", "engagement_intents": "engagement_intent",
    "engagement_outcomes": "engagement_outcome", "content_drafts": "content_draft",
    "content_revisions": "content_revision", "publisher_intents": "publisher_intent",
    "publisher_receipts": "publisher_receipt", "source_cursors": "source_cursor",
    "technical_next_actions": "technical_next_action", "technical_technologies": "technology",
}
ENTITY_KINDS = {"companies": "company", "jobs": "job", "contacts": "contact",
                "threads": "thread", "conversations": "thread", "applications": "application",
                "referrals": "referral", "interviews": "interview", "tasks": "task",
                "agent_tasks": "task", "exceptions": "exception", "agent_runs": "agent_run",
                **TECHNICAL_ENTITY_KINDS}
EVENT_KINDS = {"conversation_events", "outreach_events", "contact_observations", "conversation_observations", "audit_events"}
GENERATED_KINDS = {"entity_projections", "index_manifests", "index_changes"}
CURRENT_OUTCOMES = {
    "invitation_pending_confirmed": "pending", "invitation_sent_confirmed": "pending",
    "invitation_accepted_confirmed": "connected", "connection_accepted_confirmed": "connected",
    "message_sent_confirmed": "message_sent", "message_received_confirmed": "reply_received",
    "useful_reply_confirmed": "reply_received", "recruiter_reply_confirmed": "reply_received",
    "referral_offered_confirmed": "offered", "referral_submitted_confirmed": "submitted",
    "interview_confirmed": "scheduled", "interview_scheduled_confirmed": "scheduled",
    "opt_out_confirmed": "suppressed", "delivery_unknown": "delivery_unknown",
}


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(timezone.utc) if result.tzinfo else None


def _time(payload):
    timestamp = payload.get("timestamp")
    return (_stamp(payload.get("verified_at")) or _stamp(payload.get("occurred_at"))
            or (_stamp(timestamp.get("normalized_utc")) if isinstance(timestamp, dict) else None)
            or _stamp(payload.get("observed_at")))


def _source(payload):
    value = payload.get("source")
    if isinstance(value, dict):
        return {key: value[key] for key in ("provider", "kind", "snapshot_at", "complete") if key in value}
    return value if isinstance(value, str) else "unknown"


def _historical(payload):
    source = _source(payload)
    text = str(source).lower()
    return (payload.get("historical_only") is True or payload.get("live_state_verified") is False
            or "export" in text or "historical" in text)


def _evidence(record, now):
    """Require an identifiable, source-dated structured confirmation.

    Existing reporting evidence uses the same rules; additional conversation
    outcomes deliberately extend that enum, rather than parsing status prose.
    Historical export evidence never verifies current invitation/inbox state.
    """
    payload = record["payload"]
    if record["kind"] in TECHNICAL_ENTITY_KINDS:
        return None
    if _historical(payload):
        return None
    raw = payload.get("confirmation_evidence")
    if not isinstance(raw, dict) or not isinstance(raw.get("outcome"), str):
        return None
    standard = _confirmed(raw, now)
    if standard:
        return standard
    when = _stamp(raw.get("confirmed_at"))
    url = raw.get("evidence_url")
    if (raw.get("verified") is not True or raw.get("outcome") not in CURRENT_OUTCOMES
            or not isinstance(raw.get("event_id"), str) or not raw["event_id"].strip()
            or not isinstance(url, str) or not url.startswith("https://")
            or not when or when > now):
        return None
    return {"event_id": raw["event_id"], "confirmed_at": when.isoformat(),
            "outcome": raw["outcome"], "evidence_url": url}


def _receipt(record, now):
    payload = record["payload"]
    receipt = _confirmed(payload.get("receipt"), now, receipt=True)
    if receipt:
        return {**receipt, "record_id": record["id"]}
    if _human_confirmation(payload, now):
        raw = payload["confirmation_evidence"]
        return {"event_id": raw["employer_message_id"], "confirmed_at": raw["employer_acknowledged_at"],
                "outcome": "submission_verified", "record_id": record["id"]}
    return None


def _identity(record):
    kind, payload = record["kind"], record["payload"]
    if kind in TECHNICAL_ENTITY_KINDS:
        value = payload.get("entity_id")
        return value if isinstance(value, str) and value else record["id"]
    field = {"companies": "company_id", "jobs": "job_id", "contacts": "contact_id",
             "threads": "thread_id", "conversations": "thread_id", "applications": "application_id",
             "referrals": "referral_id", "interviews": "interview_id", "tasks": "task_id",
             "agent_tasks": "task_id", "exceptions": "exception_id", "agent_runs": "run_id"}.get(kind)
    value = payload.get(field) if field else None
    if kind == "applications":
        value = value or payload.get("dedup_key")
    return value if isinstance(value, str) and value else record["id"]


def _related(payload):
    result = {}
    for kind in ("company", "job", "contact", "thread", "application", "referral", "interview",
                 "topic", "technology", *TECHNICAL_ENTITY_KINDS.values()):
        singular, plural = payload.get(kind + "_id"), payload.get(kind + "_ids")
        values = ([singular] if isinstance(singular, str) and singular else [])
        if isinstance(plural, list):
            values += [item for item in plural if isinstance(item, str) and item]
        if values:
            result[kind + "_ids"] = sorted(set(values))
    return result


def _live_contact(payload, now):
    observed = _stamp(payload.get("verified_at"))
    return bool(not _historical(payload) and _source(payload) != "unknown" and payload.get("live_state_verified") is True
                and payload.get("identity_verified") is True
                and payload.get("history_complete") is True and observed
                and timedelta(0) <= now - observed <= timedelta(hours=24))


def _suppressed(payload):
    return (payload.get("opt_out") is True or payload.get("company_opt_out") is True
            or payload.get("suppressed") is True or payload.get("no_contact") is True
            or payload.get("state") == "suppressed")


def _freshness(payload, now, *, verified=False):
    observed = _stamp(payload.get("verified_at")) if verified else _time(payload)
    if _historical(payload):
        return "historical"
    if not observed or observed > now:
        return "unknown"
    return "fresh" if now - observed <= timedelta(hours=24) else "stale"


def _record_freshness(record, now):
    payload = record["payload"]
    if record["kind"] in TECHNICAL_ENTITY_KINDS and payload.get("freshness") != "fresh":
        return payload.get("freshness", "unknown")
    return _freshness(payload, now, verified=record["kind"] in {"contacts", "threads", "conversations"})


def _revision(record):
    value = record["payload"].get("revision", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _latest(rows, conflicts, entity_id):
    revisions = [_revision(row) for row in rows]
    if any(value is None for value in revisions):
        conflicts.append({"entity_id": entity_id, "reason": "invalid_entity_revision"})
        return sorted(rows, key=lambda row: row["id"])[-1]
    maximum = max(revisions)
    candidates = [row for row in rows if _revision(row) == maximum]
    # Canonical entities without revisions can be observations. Different
    # immutable observations are ordered only by explicit source timestamp.
    explicit = any("revision" in row["payload"] for row in rows)
    if len(candidates) > 1:
        if explicit:
            conflicts.append({"entity_id": entity_id, "reason": "ambiguous_entity_revision"})
        else:
            ordered = sorted(candidates, key=lambda row: (_time(row["payload"]) or datetime.min.replace(tzinfo=timezone.utc), row["id"]))
            top = _time(ordered[-1]["payload"])
            if len(ordered) > 1 and top == _time(ordered[-2]["payload"]):
                conflicts.append({"entity_id": entity_id, "reason": "ambiguous_entity_observation"})
            return ordered[-1]
    return sorted(candidates, key=lambda row: row["id"])[-1]


def _permission(payload, now):
    permission = payload.get("permission_evidence")
    if not isinstance(permission, dict):
        return False
    checked, expires = _stamp(permission.get("verified_at")), _stamp(permission.get("expires_at"))
    return bool(permission.get("permitted") is True and permission.get("scope") == "application_submission"
                and str(permission.get("evidence_url", "")).startswith("https://")
                and checked and checked <= now and expires and now < expires)


def _outcome_state(kind, outcome):
    allowed = {
        "contact": {"pending", "connected", "message_sent", "reply_received", "suppressed", "delivery_unknown"},
        "thread": {"pending", "connected", "message_sent", "reply_received", "suppressed", "delivery_unknown"},
        "referral": {"offered", "submitted"}, "interview": {"scheduled"},
    }
    state = CURRENT_OUTCOMES.get(outcome)
    return state if state in allowed.get(kind, set()) else None


def _task(projection, action, reason, capability, priority=50, *, status="open", executor="agent", due_at=None):
    return {"entity_id": projection["entity_id"], "entity_type": projection["entity_type"],
            "action": action, "status": status, "due_at": due_at,
            "reason": reason, "priority": priority, "allowed_capabilities": [capability],
            "executor": executor, "owner": "unassigned",
            "blockers": list(projection["blockers"]) if status == "blocked" else [],
            "entity_blockers": list(projection["blockers"]),
            "source_evidence_ids": list(projection["evidence_refs"]),
            "source_version": projection["version"]}


def _dispatch_task_visibility(tasks, projection_records, sources):
    """Queue acknowledgements reserve work, never verify a business outcome.

    Keep source projections unchanged: feeding queue status into a projection
    would change the hash against which that very reservation was recorded.
    Task snapshots and their independent cursors expose dispatch availability.
    """
    result = []
    terminal_results = {row["payload"]["action_id"]: row for row in sources.values()
                        if valid_terminal_result(row, sources)}
    for task_record in tasks:
        task = task_record["payload"]
        entity_id, action_type = task["entity_id"], task["type"]
        expected_hash = projection_records[entity_id]["hash"]
        candidates, reservations = {}, {}
        for row in sources.values():
            payload = row["payload"]
            if row["kind"] == "agent_proposals":
                action = payload.get("action", {})
                if (isinstance(action, dict) and action.get("entity_id") == entity_id
                    and action.get("type") == action_type
                    and payload.get("expected_projection_hash") in (None, expected_hash)):
                    candidates[row["id"]] = row
            elif row["kind"] == "agent_action_reservations":
                semantic = payload.get("semantic", {})
                if (isinstance(semantic, dict) and semantic.get("kind") in (None, "agent_proposals")
                    and semantic.get("entity_id") == entity_id and semantic.get("type") == action_type
                    and semantic.get("projection_hash") in (None, expected_hash)):
                    identity = payload.get("action_id")
                    identity = identity if isinstance(identity, str) else row["id"]
                    candidates.setdefault(identity, sources.get(identity))
                    reservations[identity] = row
            elif row["kind"] == "agent_execution_queue":
                if (payload.get("entity_id") == entity_id and payload.get("action_type") == action_type
                    and payload.get("expected_projection_hash") in (None, expected_hash)):
                    identity = payload.get("action_id")
                    identity = identity if isinstance(identity, str) else row["id"]
                    candidates.setdefault(identity, sources.get(identity))
        if not candidates:
            result.append(task_record)
            continue
        complete, partial = [], []
        for identity, intake in candidates.items():
            reservation = reservations.get(identity)
            queue, ack = sources.get("action-queue:" + identity), sources.get("action-result:" + identity)
            intake_payload = intake["payload"] if intake else {}
            actor = intake_payload.get("actor")
            valid = bool(intake and intake["kind"] == "agent_proposals" and reservation
                and reservation["payload"].get("intake_hash") == intake["hash"]
                and queue and queue["kind"] == "agent_execution_queue" and queue["status"] == "queued"
                and queue["payload"].get("intake_hash") == intake["hash"]
                and queue["payload"].get("action_id") == identity
                and queue["payload"].get("actor") == actor and queue["payload"].get("external_effect") is False
                and ack and ack["kind"] == "agent_action_results" and ack["status"] == "queued"
                and ack["payload"].get("action_id") == identity and ack["payload"].get("status") == "queued"
                and ack["payload"].get("actor") == actor and ack["payload"].get("external_effect") is False)
            (complete if valid else partial).append(identity)
        payload = dict(task)
        if partial or len(complete) != 1:
            payload["status"] = "blocked"
            payload["blockers"] = sorted(set(payload.get("blockers", []) + ["dispatch_intake_requires_reconciliation"]))
            payload["blocking_conditions"] = payload["blockers"]
            payload["reconciliation_action_ids"] = sorted(candidates)
        else:
            # Preserve other genuine source blockers, if any.
            terminal = terminal_results.get(complete[0])
            payload["status"] = ("blocked" if task["status"] == "blocked" else
                                 terminal["status"] if terminal else "queued")
            payload["queued_action_ids"] = complete
            if terminal:
                payload["action_result_id"] = terminal["id"]
                payload["blocking_conditions"] = sorted(set(payload.get("blocking_conditions", [])
                    + terminal["payload"].get("blocking_conditions", [])))
        identity = stable_id("agent_task_dispatch", task["task_id"], content_hash(payload))
        result.append(make_record("agent_tasks", payload, identity, payload["status"]))
    return result


def valid_terminal_result(row, sources):
    """Validate one immutable terminal result against the complete admission.

    A timestamp/status cannot supersede a queued acknowledgement. Revision 1
    requires exact linkage to its immutable acknowledgement, execution marker,
    intake, reservation and every output; conflicting chains remain fenced.
    """
    if row.get("kind") != "agent_action_results" or row.get("payload", {}).get("schema") != "apply-agent-result/v1":
        return False
    try:
        from .agent_dispatcher import _queue_and_result, _semantic_reservation
        p, action_id = row["payload"], row["payload"]["action_id"]
        intake = sources[action_id]
        queue, ack = _queue_and_result(intake)
        reservation = _semantic_reservation(intake)
        execution = sources[stable_id("agent_action_execution", action_id)]
        ep = execution["payload"]
        outputs = p["output_hashes"]
        if (row["id"] != stable_id("agent_action_result", action_id, 1)
                or row["status"] not in {"blocked", "completed"} or p.get("status") != row["status"]
                or p.get("revision") != 1 or p.get("previous_record_id") != ack["id"]
                or p.get("actor") != intake["payload"]["actor"]
                or p.get("entity_id") != intake["payload"]["action"]["entity_id"]
                or p.get("external_effect") is not False or not _stamp(p.get("occurred_at"))
                or sources.get(queue["id"]) != queue or sources.get(ack["id"]) != ack
                or sources.get(reservation["id"]) != reservation
                or execution["kind"] != "agent_action_executions" or execution["status"] != "started"
                or ep.get("schema") != "apply-agent-execution/v1" or ep.get("status") != "started"
                or ep.get("external_effect") is not False or ep.get("action_id") != action_id
                or ep.get("actor") != p["actor"] or not ep.get("run_id") or not _stamp(ep.get("started_at"))
                or ep.get("evidence_hashes") != intake["payload"].get("evidence_hashes")
                or ep.get("expected_projection_hash") != intake["payload"].get("expected_projection_hash")
                or p.get("execution_id") != execution["id"] or p.get("execution_hash") != execution["hash"]
                or not isinstance(outputs, dict) or not isinstance(p.get("evidence_refs"), list)
                or not isinstance(p.get("blocking_conditions"), list)):
            return False
        for key, expected in (("intake_hash", intake), ("queue_hash", queue),
                              ("reservation_hash", reservation), ("queued_ack_hash", ack)):
            if p.get(key) != expected["hash"] or ep.get(key) != expected["hash"]:
                return False
        if any(not isinstance(ref, str) or not ref for ref in p["evidence_refs"]):
            return False
        if not set(intake["payload"]["evidence_refs"]) | set(outputs) <= set(p["evidence_refs"]):
            return False
        if any(sources.get(ref, {}).get("hash") != value for ref, value in outputs.items()):
            return False
        if any(sources.get(ref, {}).get("hash") != value
               for ref, value in intake["payload"].get("evidence_hashes", {}).items()):
            return False
        if row["status"] == "completed":
            return (p.get("verified") is True and not p["blocking_conditions"] and bool(outputs)
                    and _completed_action_outputs(p, intake, sources))
        return p.get("verified") is False and bool(p["blocking_conditions"]) and p.get("outcome") == "action_blocked"
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _completed_action_outputs(result, intake, sources):
    """A finished result proves its specific work, never a caller's label."""
    action = intake["payload"]["action"]
    rows = [sources[ref] for ref in result["output_hashes"]]
    execution = sources[result["execution_id"]]["payload"]
    context_source = sources.get(execution.get("source_record_id"), {})
    outcome = result.get("outcome")
    if action["type"] == "admit_technical_source" and outcome == "technical_source_admitted":
        from .technical_admission import _records
        try:
            candidate = validate_technical_record(action.get("candidate"))
            payload = candidate["payload"]
            refs = payload["evidence_refs"]
            if (candidate["kind"] not in {"technical_sources", "technical_items"}
                or candidate["status"] != "proposed" or payload["freshness"] != "unknown"
                or payload["author"] is not None or payload["author_verified"] is not False
                or payload["source_published_at"] is not None
                or payload["actor"] != intake["payload"]["actor"] or not refs
                or not set(refs) <= set(intake["payload"]["evidence_hashes"])):
                return False
            intent, reservations, completion = _records(candidate)
            expected = [candidate, intent, *reservations, completion]
            return (set(result["output_hashes"]) == {row["id"] for row in expected}
                    and all(sources.get(row["id"]) == row and result["output_hashes"].get(row["id"]) == row["hash"] for row in expected))
        except (KeyError, TypeError, ValueError):
            return False
    if action["type"] == "research" and outcome == "research_verified":
        if len(rows) != 1 or rows[0]["kind"] != "agent_research_evidence" or rows[0]["status"] != "verified":
            return False
        p = rows[0]["payload"]
        source = sources.get(p.get("source_record_id"), {})
        job, reads = p.get("official_job"), p.get("official_reads")
        return bool(p.get("action_id") == intake["id"] and p.get("entity_id") == action["entity_id"]
            and p.get("source_record_hash") == source.get("hash") and source.get("kind") in {"jobs", "applications"}
            and source.get("id") == execution.get("source_record_id")
            and source.get("hash") == execution.get("source_record_hash")
            and intake["payload"].get("evidence_hashes", {}).get(source.get("id")) == source.get("hash")
            and isinstance(job, dict) and job.get("open") is True and p.get("official_job_hash") == content_hash(job)
            and isinstance(job.get("evidence_url"), str) and job["evidence_url"].startswith("https://")
            and isinstance(reads, list) and reads and all(isinstance(read, dict) and read.get("method") == "GET"
                and read.get("http_status") == 200 and isinstance(read.get("url"), str)
                and read["url"].startswith("https://") and isinstance(read.get("response_sha256"), str)
                and len(read["response_sha256"]) == 64 for read in reads))
    if action["type"] == "prepare_application" and outcome == "application_prepared":
        applications = [row for row in rows if row["kind"] == "applications"]
        packets = [row for row in rows if row["kind"] == "application_packets"]
        if len(rows) != 2 or len(applications) != 1 or len(packets) != 1:
            return False
        app, packet = applications[0], packets[0]
        p, a = packet["payload"], app["payload"]
        return bool(app["status"] in {"prepared", "submission_blocked"} and not a.get("attempt")
            and context_source.get("hash") == execution.get("source_record_hash")
            and intake["payload"].get("evidence_hashes", {}).get(context_source.get("id")) == context_source.get("hash")
            and _same_application_target(context_source.get("payload", {}), a)
            and packet["status"] == "verified" and p.get("application_id") == a.get("application_id")
            and a.get("packet", {}).get("record_id") == packet["id"]
            and isinstance(p.get("qa"), dict) and p["qa"].get("verified") is True and not p.get("missing")
            and all(isinstance(p.get(key), str) and len(p[key]) == 64
                    for key in ("resume_sha256", "source_sha256", "answers_hash")))
    if action["type"] in {"conversation_observed", "manual_action_observed"} and outcome in {
            "verified_observation_recorded", "verified_observation_already_recorded"}:
        if len(rows) != 1 or rows[0]["kind"] != "conversation_events":
            return False
        event = rows[0]
        proof = _evidence(event, _stamp(result["occurred_at"]))
        related = _related(event["payload"])
        if (not proof or proof["outcome"] != action.get("outcome")
                or _stamp(proof["confirmed_at"]) != _stamp(action.get("occurred_at"))
                or not any(action["entity_id"] in related.get(key, []) for key in ("contact_ids", "thread_ids"))):
            return False
        if outcome == "verified_observation_already_recorded":
            return any(source and source["hash"] == expected
                and source["kind"] in {"conversation_sources", "manual_action_sources", "conversation_events",
                    "outreach_events", "conversation_observations", "contact_observations"}
                and _evidence(source, _stamp(result["occurred_at"])) == proof
                and _related(source["payload"]) == related
                for ref, expected in intake["payload"].get("evidence_hashes", {}).items()
                for source in [sources.get(ref)])
        source = sources.get(event["payload"].get("source_record_id"), {})
        return bool(source and source["hash"] == event["payload"].get("source_record_hash")
            and intake["payload"].get("evidence_hashes", {}).get(source["id"]) == source["hash"]
            and _evidence(source, _stamp(result["occurred_at"])) == proof
            and _related(source["payload"]) == related)
    return False


def _same_application_target(source, application):
    source = {**source, **(source.get("job") if isinstance(source.get("job"), dict) else {})}
    if employer_identity(source.get("company")) != employer_identity(application.get("company")):
        return False
    left, right = source.get("requisition_id"), application.get("requisition_id")
    if left and right:
        return normalize(left) == normalize(right)
    return bool(source.get("url") and application.get("url")
                and canonical_url(source["url"]) == canonical_url(application["url"]))


def _action_results(sources, conflicts):
    result = {}
    for row in sorted(sources.values(), key=lambda item: item["id"]):
        if row["kind"] != "agent_action_results":
            continue
        p = row["payload"]
        identity = p.get("action_id")
        if not isinstance(identity, str) or not identity:
            continue
        if p.get("schema") == "apply-agent-result/v1":
            if not valid_terminal_result(row, sources):
                conflicts.append({"record_id": row["id"], "reason": "invalid_agent_result_chain"})
            else:
                result[identity] = row["id"]
        elif (row["id"] == "action-result:" + identity
                and row["status"] in {"queued", "duplicate", "blocked", "needs_reconciliation", "completed"}
                and p.get("status") == row["status"] and p.get("external_effect") is False
                and isinstance(p.get("actor"), str) and p["actor"]
                and isinstance(p.get("evidence_refs"), list)
                and all(isinstance(ref, str) and ref for ref in p["evidence_refs"])):
            result.setdefault(identity, row["id"])
    for row in sources.values():
        if row["kind"] == "agent_action_executions":
            identity = row["payload"].get("action_id")
            terminal = sources.get(stable_id("agent_action_result", identity, 1), {})
            if not valid_terminal_result(terminal, sources):
                conflicts.append({"record_id": row["id"], "reason": "partial_agent_execution_requires_reconciliation"})
    return result


def _next_action(projection, latest, now):
    kind, state, payload = projection["entity_type"], projection["state"], latest["payload"]
    if projection["suppression"]["suppressed"]:
        return None
    if any(reason.startswith("coordination_") for reason in projection["blockers"]):
        return _task(projection, "reconcile_uncertain_action", "Legacy coordination remains quarantined; no retry is authorized.",
                     "research_propose", 100, status="blocked")
    if state in {"delivery_unknown", "submission_unknown", "submitting", "needs_reconciliation"}:
        return _task(projection, "reconcile_uncertain_action", "Prior effect is uncertain; retries are blocked.",
                     "research_propose", 100, status="reconciliation")
    if kind == "application":
        if state == "applied_verified":
            return _task(projection, "research_hiring_team", "Verified application receipt suppresses duplicate submission.", "research_propose", 60)
        if state == "interview":
            return _task(projection, "prepare_interview", "A verified interview takes priority for this application.", "research_propose", 100)
        if state in {"closed", "withdrawn", "rejected"}:
            return None
        if state in {"needs_user_fact", "manual_action_needed"}:
            return _task(projection, "resolve_user_fact", "A documented personal fact or human action is required.", "research_propose", 70, status="blocked", executor="human")
        if state == "submission_eligible" and not projection["blockers"]:
            return _task(projection, "submit_application", "Recorded V2 eligibility and provider permission require central revalidation.",
                         "execute_permitted_application", 70, executor="central_dispatcher")
        if state in {"prepared", "submission_eligible", "submission_blocked"}:
            return _task(projection, "resolve_submission_exception", "Submission requires verified permitted provider and fresh eligibility evidence.",
                         "research_propose", 60, status="blocked")
        return _task(projection, "prepare_application" if state == "qualified" else "verify_job_and_qualification",
                     "Use current official job evidence and documented facts.",
                     "prepare_application" if state == "qualified" else "research_propose", 50)
    if kind in {"contact", "thread"}:
        if state == "pending":
            return _task(projection, "verify_invitation_status", "Confirmed pending invitation blocks another invitation and accepted-connection message.", "research_propose", 30)
        if "history_missing_or_stale" in projection["blockers"] or "identity_unverified" in projection["blockers"]:
            return _task(projection, "verify_identity_and_history", "Fresh identity and complete all-agent history are required before a conversational draft.", "research_propose", 100 if state == "reply_received" else 50)
        if state == "reply_received":
            return _task(projection, "prepare_human_reply", "Confirmed professional reply requires a human response.", "research_propose", 100, executor="human")
        if state == "connected":
            return _task(projection, "prepare_human_conversation", "Verified accepted connection and current complete history support a conversational draft.", "research_propose", 60, executor="human")
        return _task(projection, "research_contact", "Determine a relevant next step without inferring delivery or connection state.", "research_propose", 30)
    if kind == "referral":
        if state == "offered":
            return _task(projection, "review_referral_offer", "An offered referral is not a submitted referral.", "research_propose", 80, executor="human")
        if state == "submitted":
            return _task(projection, "review_referral_status", "Verified referral submission is retained; do not submit it again.", "research_propose", 40)
        return _task(projection, "verify_referral_status", "Referrals require explicit evidence of their outcome.", "research_propose", 50)
    if kind == "interview":
        if state == "scheduled":
            return _task(projection, "prepare_interview", "A confirmed scheduled interview has priority.", "research_propose", 100, due_at=payload.get("scheduled_at"))
        if state not in {"completed", "cancelled"}:
            return _task(projection, "verify_interview_status", "Interview scheduling is not inferred from a plan.", "research_propose", 60)
    if kind == "job" and state not in {"closed", "applied_verified", "interview", "submission_unknown", "submitting"}:
        return _task(projection, "verify_job_and_qualification", "Verify current official opening and deduplication before preparation.", "research_propose", 50)
    if kind == "technical_next_action" and state in {"observed", "open", "ready"}:
        return _task(projection, "review_technical_next_action", "Review the cited technical evidence and current relationship context.",
                     "research_propose", 40, status="blocked" if projection["blockers"] else "open", executor="human")
    return None


def build_index(records: Iterable[dict], now: datetime, previous_manifest: dict | None = None,
                source_snapshot_hash: str | dict | None = None, *, source_complete: bool = False) -> list[dict]:
    """Return immutable projections, task snapshots, changes, and one manifest.

    ``records`` is the authoritative writer's full, readback-verified snapshot.
    ``source_complete=False`` is required after any partial query/import or
    failed writer. No task in such a manifest is ready, regardless of agent.
    Record identities and semantic versions are independent of run timestamps.
    ``previous_manifest`` carries maps/cursors, never an agent chat transcript.
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Index requires an aware source timestamp")
    records = list(records)
    quarantines = ContactQuarantines(records)
    now = now.astimezone(timezone.utc)
    conflicts, sources = [], {}
    for raw in records:
        try:
            row = validate_record(raw)
            if not isinstance(row["payload"], dict):
                raise ValueError("Expected structured source payload")
            if row["kind"] in TECHNICAL_ENTITY_KINDS:
                row = validate_technical_record(row)
        except (ValueError, TypeError, KeyError):
            conflicts.append({"record_id": raw.get("id") if isinstance(raw, dict) else None, "reason": "invalid_source_record"})
            continue
        if row["kind"] in GENERATED_KINDS or (row["kind"] == "agent_tasks" and row["payload"].get("schema") == SCHEMA):
            continue
        old = sources.get(row["id"])
        if old and old != row:
            conflicts.append({"record_id": row["id"], "reason": "source_identity_conflict"})
        sources[row["id"]] = row
        if row["kind"] in {"import_manifests", "migration_manifests", "source_snapshots"}:
            if row["payload"].get("complete") is False or row["payload"].get("conflicts") or row["status"] in {"incomplete", "conflicted", "partial"}:
                conflicts.append({"record_id": row["id"], "reason": "partial_or_conflicted_source"})
    try:
        validate_technical_aliases(sources.values())
    except ValueError:
        conflicts.append({"reason": "technical_alias_conflict"})
    action_results = _action_results(sources, conflicts)
    verified_identities = {}
    for row in sorted(sources.values(), key=lambda item: item["id"]):
        proof = _receipt(row, now) if row["kind"] == "applications" else _evidence(row, now)
        if not proof:
            continue
        identity = proof.get("identity") or stable_id("verified_evidence", proof["event_id"], proof.get("evidence_url"))
        related = _related(row["payload"])
        subject = (_identity(row) if row["kind"] == "applications" else
                   {"contact_ids": related["contact_ids"]} if related.get("contact_ids") else related)
        claim = content_hash({"subject": subject, "outcome": proof["outcome"], "confirmed_at": proof["confirmed_at"]})
        if identity in verified_identities and verified_identities[identity] != claim:
            conflicts.append({"record_id": row["id"], "reason": "verified_evidence_identity_conflict"})
        verified_identities[identity] = claim
    signatures = sorted((row["id"], row["kind"], row["hash"], row["status"]) for row in sources.values())
    if isinstance(source_snapshot_hash, dict):
        source_complete = source_snapshot_hash.get("complete") is True
        source_conflicts = source_snapshot_hash.get("conflicts", [])
        if not isinstance(source_conflicts, list):
            raise ValueError("Invalid source snapshot conflicts")
        conflicts.extend({"reason": "caller_source_conflict", "reference": str(value)[:200]} for value in source_conflicts)
        snapshot_hash = source_snapshot_hash.get("hash")
    else:
        snapshot_hash = source_snapshot_hash or content_hash(signatures)
    if not isinstance(snapshot_hash, str) or not snapshot_hash:
        raise ValueError("Invalid source snapshot hash")
    previous = (previous_manifest or {}).get("payload", previous_manifest or {})
    if not isinstance(previous, dict):
        raise ValueError("Invalid previous index manifest")
    groups, events = defaultdict(list), defaultdict(list)
    for row in sources.values():
        if row["kind"] in ENTITY_KINDS:
            groups[_identity(row)].append(row)
        if row["kind"] in EVENT_KINDS | {"referrals", "interviews"}:
            for values in _related(row["payload"]).values():
                for entity_id in values:
                    events[entity_id].append(row)
    app_rows = [row for row in sources.values() if row["kind"] == "applications"]
    try:
        if any(_revision(row) is None for row in app_rows):
            raise StateConflict("invalid_application_revision")
        applications = fold_applications(app_rows)
    except (StateConflict, TypeError) as error:
        conflicts.append({"reason": str(error) or "application_state_conflict"})
        applications = {}
    # Stub entities link verified events without a name/URL guess. Unknown
    # contact identity remains a blocker even when an event has a contact ID.
    for entity_id, linked in list(events.items()):
        if entity_id in groups:
            continue
        relation = next((key for row in linked for key, values in _related(row["payload"]).items() if entity_id in values), None)
        kind = {"company_ids": "companies", "job_ids": "jobs", "contact_ids": "contacts", "thread_ids": "threads",
                "application_ids": "applications", "referral_ids": "referrals", "interview_ids": "interviews"}.get(relation)
        if kind:
            groups[entity_id] = [make_record(kind, {"source": "unknown", "entity_id": entity_id,
                                                  "_derived_stub": True, "derived_from": linked[0]["id"]}, entity_id, "unknown")]
    suppressed_ids = set()
    for entity_id, rows in groups.items():
        if any(_suppressed(row["payload"]) for row in rows):
            suppressed_ids.add(entity_id)
    for entity_id, rows in events.items():
        if any((_evidence(row, now) or {}).get("outcome") == "opt_out_confirmed" or _suppressed(row["payload"]) for row in rows):
            suppressed_ids.add(entity_id)
    for entity_id, rows in groups.items():
        if (any(row["kind"] == "contacts" for row in rows)
                and any(company in suppressed_ids for row in rows
                        for company in _related(row["payload"]).get("company_ids", []))):
            suppressed_ids.add(entity_id)
    outputs, projections, latest_sources = [], {}, {}
    selected = {entity_id: applications.get(entity_id) or _latest(rows, conflicts, entity_id) for entity_id, rows in groups.items()}
    def compatible_relation(key, identity):
        target = selected.get(identity)
        return target is None or ENTITY_KINDS[target["kind"]] == key.removesuffix("_ids")
    for row in selected.values():
        if row["kind"] in TECHNICAL_ENTITY_KINDS:
            if any(not compatible_relation(key, identity) for key, values in _related(row["payload"]).items() for identity in values):
                conflicts.append({"record_id": row["id"], "reason": "technical_relation_type_conflict"})
    for entity_id, rows in sorted(groups.items()):
        types = {ENTITY_KINDS[row["kind"]] for row in rows}
        if len(types) != 1:
            conflicts.append({"entity_id": entity_id, "reason": "entity_type_conflict"})
        latest = selected[entity_id]
        payload, kind = latest["payload"], ENTITY_KINDS[latest["kind"]]
        technical = latest["kind"] in TECHNICAL_ENTITY_KINDS
        state = (latest["status"] if technical else payload.get("state") or latest["status"])
        if technical and state not in {"observed", "open", "ready", "blocked", "closed", "historical", "rejected"}:
            state = "unknown"
        if not isinstance(state, str):
            conflicts.append({"entity_id": entity_id, "reason": "invalid_entity_state"})
            state = "unknown"
        history = {row["id"]: row for row in rows + events.get(entity_id, []) if row["payload"].get("_derived_stub") is not True}
        ordered = sorted(history.values(), key=lambda row: (_time(row["payload"]) or datetime.min.replace(tzinfo=timezone.utc), row["id"]))
        confirmed = [(row, _evidence(row, now)) for row in ordered]
        confirmed = [(row, evidence) for row, evidence in confirmed if evidence]
        confirmed.sort(key=lambda pair: (pair[1]["confirmed_at"], pair[0]["id"]))
        related = _related(payload)
        for row in ordered:
            for key, values in _related(row["payload"]).items():
                related[key] = sorted(set(related.get(key, []) + values))
        if technical:
            related = {key: kept for key, values in _related(payload).items()
                       if (kept := [identity for identity in values if compatible_relation(key, identity)])}
        suppressed = entity_id in suppressed_ids or any(value in suppressed_ids for key, values in related.items() if key in {"company_ids", "contact_ids"} for value in values)
        blockers, last_action = [], None
        if confirmed:
            row, evidence = confirmed[-1]
            outcome = evidence["outcome"]
            confirmed_state = _outcome_state(kind, outcome)
            if confirmed_state:
                state = confirmed_state
            last_action = {"action": outcome, "actor": row["payload"].get("actor", "unknown"),
                           "occurred_at": evidence["confirmed_at"], "event_id": evidence["event_id"], "evidence_ref": row["id"]}
        if kind == "application":
            receipts = [(row, _receipt(row, now)) for row in rows]
            receipts = [(row, receipt) for row, receipt in receipts if receipt]
            if receipts:
                row, receipt = sorted(receipts, key=lambda pair: (pair[1]["confirmed_at"], pair[0]["id"]))[-1]
                if state not in {"closed", "rejected", "withdrawn", "interview"}:
                    state = "applied_verified"
                last_action = {"action": "submission_verified", "actor": row["payload"].get("actor", "unknown"),
                               "occurred_at": receipt["confirmed_at"], "event_id": receipt["event_id"], "evidence_ref": row["id"]}
            elif state in {"applied", "applied_verified"} or payload.get("attempt"):
                state = "submitting" if state == "submitting" else "submission_unknown"
            if state in {"prepared", "submission_eligible", "submission_blocked"} and not _permission(payload, now):
                blockers.append("permitted_submission_provider_unverified")
            if payload.get("employer_history_unresolved") is True:
                blockers.append("ambiguous_application_identity")
            if state == "qualified":
                job, grade = payload.get("job"), payload.get("qualification")
                if (not isinstance(job, dict) or job.get("open") is not True or job.get("employer_identity_verified") is not True
                        or not isinstance(grade, dict) or grade.get("qualified") is not True
                        or _historical(payload)):
                    state = "needs_verification"
                    blockers.append("current_qualification_unverified")
            if state == "submission_eligible":
                packet = payload.get("packet")
                if (not isinstance(packet, dict) or not isinstance(packet.get("qa"), dict)
                        or packet["qa"].get("verified") is not True or packet.get("missing")
                        or not all(isinstance(packet.get(key), str) and len(packet[key]) == 64
                                   and all(char in "0123456789abcdef" for char in packet[key])
                                   for key in ("resume_sha256", "answers_hash", "source_sha256", "ground_truth_sha256"))):
                    blockers.append("verified_packet_unavailable")
                job, grade, screening = payload.get("job"), payload.get("qualification"), payload.get("screening_evidence")
                observed = _stamp(job.get("observed_at")) if isinstance(job, dict) else None
                if (not isinstance(job, dict) or job.get("open") is not True or job.get("schema_verified") is not True
                        or job.get("employer_identity_verified") is not True or not job.get("requisition_id")
                        or not observed or not timedelta(0) <= now - observed <= timedelta(hours=24)
                        or not isinstance(grade, dict) or grade.get("qualified") is not True
                        or not isinstance(screening, dict) or not screening):
                    blockers.append("current_submission_eligibility_unverified")
        if kind in {"contact", "thread"}:
            contact_payloads = [payload] if kind == "contact" else [selected[c]["payload"] for c in related.get("contact_ids", []) if c in selected and selected[c]["kind"] == "contacts"]
            if not contact_payloads or not all(item.get("identity_verified") is True for item in contact_payloads):
                blockers.append("identity_unverified")
            if not contact_payloads or not all(_live_contact(item, now) for item in contact_payloads):
                blockers.append("history_missing_or_stale")
            if not confirmed and not _live_contact(payload, now):
                state = "historical_observation" if _historical(payload) else "needs_verification"
            # A sent export or a planned follow-up cannot prove delivery.
            if payload.get("delivery_unknown") is True:
                state = "delivery_unknown"
        if kind in {"referral", "interview"} and not confirmed:
            state = "historical_observation" if _historical(payload) else "needs_verification"
        if state in {"delivery_unknown", "submission_unknown", "submitting"}:
            blockers.append("retry_suppressed_pending_reconciliation")
        if suppressed:
            state = "suppressed"
            blockers.append("global_opt_out")
        if kind == "technical_next_action":
            contact_ids = related.get("contact_ids", [])
            if any(identity not in selected or selected[identity]["kind"] != "contacts"
                   or not _live_contact(selected[identity]["payload"], now) for identity in contact_ids):
                blockers.append("history_missing_or_stale")
            if any(identity not in selected or selected[identity]["kind"] != "companies"
                   for identity in related.get("company_ids", [])):
                blockers.append("company_context_missing")
            refs = payload["evidence_refs"]
            if not refs or any(ref not in sources for ref in refs):
                blockers.append("technical_evidence_unavailable")
            elif any(_record_freshness(sources[ref], now) != "fresh" for ref in refs):
                blockers.append("technical_evidence_stale")
        if technical:
            # Link only identities actually present in this snapshot. Arbitrary
            # extra fields are not safe to copy into compact index metadata.
            related = {key: kept for key, values in related.items()
                       if (kept := [value for value in values if value in selected])}
        hold = None
        if kind in {"contact", "thread"}:
            contact_ids = [entity_id] if kind == "contact" else related.get("contact_ids", [])
            contact_rows = rows + [row for identity in contact_ids for row in groups.get(identity, [])]
            hold = quarantines.hold_for(contact_rows, contact_ids=contact_ids)
            if hold:
                blockers.append(hold["reason"])
        projection = {"schema": SCHEMA, "entity_id": entity_id, "entity_type": kind,
                      "state": state, "current_state": state, "last_verified_action": last_action,
                      "source": "technical_record" if technical else _source(payload), "source_record_id": latest["id"] if technical else payload.get("derived_from", latest["id"]),
                      "verified_at": last_action["occurred_at"] if last_action else None if technical else payload.get("verified_at"),
                      "observed_at": (_time(payload).isoformat() if _time(payload) else None),
                      "freshness": _record_freshness(latest, now),
                      "confidence": "verified" if last_action or (kind in {"contact", "thread"} and _live_contact(payload, now)) else "unverified",
                      "blockers": sorted(set(blockers)), "related_ids": related,
                      "owner": payload.get("owner") if not technical and isinstance(payload.get("owner"), str) else "unassigned",
                      "suppression": {"suppressed": suppressed, "retry_blocked": bool(hold) or state in {"delivery_unknown", "submission_unknown", "submitting", "applied_verified", "pending"}},
                      "evidence_refs": sorted(({row["id"] for row, _ in confirmed} | {last_action["evidence_ref"]} if last_action else {payload.get("derived_from", latest["id"])}) | set(hold["evidence_refs"] if hold else [])),
                      "history_ids": [row["id"] for row in ordered[-MAX_HISTORY:]], "history_count": len(ordered),
                      "history_truncated": len(ordered) > MAX_HISTORY,
                      "source_version": content_hash(sorted((row["id"], row["hash"], row["status"]) for row in history.values()))}
        if latest["kind"] in TECHNICAL_ENTITY_KINDS:
            projection["evidence_refs"] = sorted({latest["id"]} | {ref for ref in payload["evidence_refs"] if ref in sources})
        projection.update(blocking_conditions=projection["blockers"], responsible_agent=projection["owner"],
                          suppressed=suppressed, history_complete=bool(kind in {"contact", "thread"} and contact_payloads and all(_live_contact(item, now) for item in contact_payloads)),
                          source_complete=source_complete is True,
                          recent_event_ids=[row["id"] for row in reversed(ordered[-5:])])
        if kind == "application":
            projection["application_state"] = state
        # Source-version is the triggering evidence; projection version also
        # changes on fresh->stale or suppression without wall-clock churn.
        projection["version"] = content_hash(projection)
        projections[entity_id], latest_sources[entity_id] = projection, latest
    # Reverse links are compact metadata only. Use current revisions, never raw
    # evidence, and hash all linked revisions so a change advances the dossier's
    # cursor even when its stable linked entity ID does not change.
    reverse = defaultdict(list)
    for source_id, source in selected.items():
        if source["kind"] not in TECHNICAL_ENTITY_KINDS:
            continue
        for key, values in _related(source["payload"]).items():
            for target_id in set(values):
                if target_id in projections and target_id != source_id and compatible_relation(key, target_id):
                    reverse[target_id].append((source_id, source["kind"], source["hash"]))
    for target_id, links in reverse.items():
        projection = projections[target_id]
        links = sorted(set(links))
        for source_id, source_kind, _ in links[:20]:
            key = TECHNICAL_ENTITY_KINDS[source_kind] + "_ids"
            projection["related_ids"][key] = sorted(set(projection["related_ids"].get(key, []) + [source_id]))
        projection["technical_links_truncated"] = len(links) > 20
        projection["technical_context_hash"] = content_hash(links)
        projection["version"] = content_hash({key: value for key, value in projection.items() if key != "version"})
    for projection in projections.values():
        if projection["entity_type"] != "technical_next_action":
            continue
        contacts = [projections[identity] for identity in projection["related_ids"].get("contact_ids", []) if identity in projections]
        if any(contact["suppression"]["retry_blocked"] or any(reason.startswith("coordination_") for reason in contact["blockers"]) for contact in contacts):
            projection["blockers"] = sorted(set(projection["blockers"] + ["contact_action_held"]))
            projection["blocking_conditions"] = projection["blockers"]
            projection["version"] = content_hash({key: value for key, value in projection.items() if key != "version"})
    # Applications suppress every official URL alias/job representation, using
    # the existing conservative employer/requisition identity helper.
    for entity_id, projection in projections.items():
        if projection["entity_type"] != "job":
            continue
        payload = latest_sources[entity_id]["payload"]
        job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
        try:
            app = matching_application(applications, job.get("company"), job.get("requisition_id"),
                                       [job.get("url"), *job.get("url_aliases", []), *job.get("aliases", [])], job.get("title"))
        except (AttributeError, TypeError):
            app = None
        if not app:
            continue
        app_id = _identity(app)
        app_projection = projections.get(app_id)
        ambiguous_identity = app["payload"].get("employer_history_unresolved") is True or not app["payload"].get("requisition_id")
        if app_projection and (ambiguous_identity or app_projection["state"] in {"applied_verified", "submission_unknown", "submitting", "interview"}):
            projection["state"] = projection["current_state"] = app_projection["state"]
            if ambiguous_identity:
                projection["state"] = projection["current_state"] = "needs_reconciliation"
            projection["related_ids"]["application_ids"] = sorted(set(projection["related_ids"].get("application_ids", []) + [app_id]))
            projection["suppression"]["retry_blocked"] = True
            projection["blockers"] = sorted(set(projection["blockers"] + ["duplicate_application_suppressed"]))
            if ambiguous_identity:
                projection["blockers"].append("ambiguous_application_identity")
            projection["blocking_conditions"] = projection["blockers"]
            projection["application_state"] = app_projection["state"]
            projection["version"] = content_hash({key: value for key, value in projection.items() if key != "version"})
    # Interview evidence also supersedes application prospecting in this run.
    for projection in list(projections.values()):
        if projection["entity_type"] == "interview" and projection["state"] == "scheduled":
            for app_id in projection["related_ids"].get("application_ids", []):
                app_projection = projections.get(app_id)
                if app_projection:
                    app_projection["state"] = app_projection["current_state"] = "interview"
                    app_projection["application_state"] = "interview"
                    app_projection["version"] = content_hash({key: value for key, value in app_projection.items() if key != "version"})
    complete = source_complete is True and not conflicts
    task_records, projection_map, versions = [], {}, {}
    candidates = {entity_id: _next_action(projection, latest_sources[entity_id], now) for entity_id, projection in projections.items()}
    shared_tasks = {}
    for entity_id, projection in projections.items():
        if not candidates[entity_id]:
            continue
        if projection["entity_type"] == "thread":
            contacts = projection["related_ids"].get("contact_ids", [])
            if len(contacts) == 1 and candidates.get(contacts[0]):
                shared_tasks[entity_id] = contacts[0]
        elif projection["entity_type"] == "interview" and projection["state"] == "scheduled":
            for app_id in projection["related_ids"].get("application_ids", []):
                if candidates.get(app_id) and candidates[app_id]["action"] == "prepare_interview":
                    shared_tasks[app_id] = entity_id
    for entity_id, projection in sorted(projections.items()):
        task = None if entity_id in shared_tasks else candidates[entity_id]
        if task:
            if not task["due_at"]:
                proposed_due = latest_sources[entity_id]["payload"].get("due_at")
                task["due_at"] = proposed_due if latest_sources[entity_id]["kind"] not in TECHNICAL_ENTITY_KINDS else (_stamp(proposed_due).isoformat() if _stamp(proposed_due) else None)
            due = _stamp(task["due_at"])
            if due and due <= now and task["status"] == "open":
                task["status"] = "due"
            if not complete:
                task["status"] = "blocked"
                task["blockers"] = sorted(set(task["blockers"] + ["source_snapshot_incomplete"]))
            action = task["action"]
            task_type = ("reconcile" if action == "reconcile_uncertain_action" else "review_outreach" if action.startswith("prepare_human")
                         else "review_referral" if "referral" in action else "prepare_interview" if action == "prepare_interview"
                         else action if action in {"prepare_application", "submit_application", "review_technical_next_action"} else "research")
            task.update(type=task_type, action_type=task_type, blocking_conditions=task["blockers"],
                        responsible_agent=task["owner"], evidence_refs=task["source_evidence_ids"])
            task.update(schema=SCHEMA, task_key=stable_id("task", entity_id, task["action"]))
            task_id = stable_id("agent_task", entity_id, task["action"], content_hash(task))
            task["task_id"] = task_id
            task_records.append(make_record("agent_tasks", task, task_id, task["status"]))
            projection["next_action"] = {"task_id": task_id, "action": task["action"], "status": task["status"], "priority": task["priority"]}
            projection["due_at"] = task["due_at"]
        else:
            projection["next_action"], projection["due_at"] = None, None
    for entity_id, representative in shared_tasks.items():
        projections[entity_id]["next_action"] = projections[representative]["next_action"]
        projections[entity_id]["due_at"] = projections[representative]["due_at"]
    for entity_id, projection in sorted(projections.items()):
        projection_id = stable_id("entity_projection", entity_id, projection["version"], content_hash(projection))
        outputs.append(make_record("entity_projections", projection, projection_id, projection["state"]))
        projection_map[entity_id], versions[entity_id] = projection_id, projection["version"]
    projection_records = {row["payload"]["entity_id"]: row for row in outputs if row["kind"] == "entity_projections"}
    task_records = _dispatch_task_visibility(task_records, projection_records, sources)
    outputs.extend(task_records)
    task_map = {row["payload"]["task_id"]: row["id"] for row in task_records}
    semantic_hash = content_hash({"projections": projection_map, "tasks": task_map, "complete": complete, "conflicts": conflicts})
    cursor = previous.get("source_cursor") or semantic_hash
    change_records = []
    previous_map = previous.get("projections", {}) if isinstance(previous.get("projections", {}), dict) else {}
    for entity_id in sorted(set(projection_map) | set(previous_map)):
        current, prior = projection_map.get(entity_id), previous_map.get(entity_id)
        if current == prior:
            continue
        next_cursor = stable_id("index_cursor", cursor, entity_id, prior, current)
        change = {"schema": SCHEMA, "entity_id": entity_id, "previous_projection_id": prior,
                  "projection_id": current, "from_cursor": cursor, "cursor": next_cursor,
                  "change": "removed" if current is None else "updated" if prior else "added"}
        change_records.append(make_record("index_changes", change, stable_id("index_change", content_hash(change)), "recorded"))
        cursor = next_cursor
    previous_tasks = previous.get("task_map", {}) if isinstance(previous.get("task_map"), dict) else {}
    task_entities = {row["payload"]["task_id"]: row["payload"]["entity_id"] for row in task_records}
    for task_id in sorted(set(task_map) | set(previous_tasks)):
        current, prior = task_map.get(task_id), previous_tasks.get(task_id)
        if current == prior:
            continue
        next_cursor = stable_id("index_cursor", cursor, task_id, prior, current)
        change = {"schema": SCHEMA, "entity_id": task_entities.get(task_id), "task_id": task_id,
                  "previous_task_record_id": prior, "task_record_id": current,
                  "from_cursor": cursor, "cursor": next_cursor,
                  "change": "task_removed" if current is None else "task_updated" if prior else "task_added"}
        change_records.append(make_record("index_changes", change, stable_id("index_change", content_hash(change)), "recorded"))
        cursor = next_cursor
    outputs.extend(change_records)
    counts = dict(Counter(projection["entity_type"] for projection in projections.values()))
    ready = [row for row in task_records if row["status"] in {"open", "due", "reconciliation"}]
    priority = sorted(ready, key=lambda row: (-row["payload"]["priority"], row["id"]))
    brief = {"schema": "campaign-brief/v1", "complete": complete, "source_cursor": cursor,
             "counts": counts, "ready_tasks": len(ready), "blocked_tasks": sum(row["status"] == "blocked" for row in task_records),
             "queued_tasks": sum(row["status"] == "queued" for row in task_records),
             "confirmed_applications": sum(item["state"] == "applied_verified" and item["entity_type"] == "application" for item in projections.values()),
             "uncertain_actions": sum(item["state"] in {"submission_unknown", "delivery_unknown", "submitting"} for item in projections.values()),
             "active_conversations": sum(item["state"] in {"connected", "reply_received"} and item["entity_type"] == "contact" for item in projections.values()),
             "priorities": [{key: row["payload"][key] for key in ("task_id", "entity_id", "action", "status", "priority", "reason")} for row in priority[:MAX_BRIEF_TASKS]],
             "history_retrieval": "Fetch bounded entity context; retrieve original evidence only for the selected task."}
    manifest = {"schema": SCHEMA, "projections": projection_map, "entity_versions": versions,
                "tasks": [row["id"] for row in sorted(task_records, key=lambda row: (-row["payload"]["priority"], row["id"]))[:40]],
                "task_map": task_map,
                "task_count": len(task_records), "tasks_truncated": len(task_records) > 40,
                "changes": [row["id"] for row in change_records], "change_count": len(change_records),
                "change_cursors": [row["payload"]["cursor"] for row in change_records],
                "index_state_hash": semantic_hash,
                "source_cursor": cursor, "previous_cursor": previous.get("source_cursor"),
                "source_snapshot_hash": snapshot_hash, "generated_at": now.isoformat(),
                "complete": complete, "conflicts": conflicts, "counts": counts, "campaign_brief": brief}
    manifest["action_results"] = action_results
    outputs.append(make_record("index_manifests", manifest, stable_id("index_manifest", content_hash(manifest)), "complete" if complete else "incomplete"))
    return outputs
