"""Versioned, read-mostly agent contract over the existing immutable records.

This module never writes Notion or performs an external action. Intake envelopes
are proposals, even when their evidence describes a verified manual outcome.
The authoritative dispatcher must recheck identity, permissions and fresh state.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Mapping

from .records import canonical_json, content_hash, make_record, stable_id, validate_record

SCHEMA = "apply-agent-protocol/v1"
INTAKE_SCHEMA = "apply-agent-intake/v1"
CAPABILITIES = frozenset({"read_summarize", "research_propose", "prepare_application",
    "execute_permitted_application", "ingest_verified_conversation", "record_verified_manual_action", "administer_workflow"})
ACTION_CAPABILITIES = {"research": "research_propose", "prepare_application": "prepare_application",
    "submit_application": "execute_permitted_application", "review_outreach": "research_propose",
    "prepare_interview": "research_propose", "review_referral": "research_propose",
    "reconcile": "research_propose", "configure_workflow": "administer_workflow"}
OBSERVATION_CAPABILITIES = {"conversation_observed": "ingest_verified_conversation",
    "manual_action_observed": "record_verified_manual_action"}
# An allowlist avoids accidentally exposing imported source bodies or messages.
PROJECTION_FIELDS = ("entity_id", "entity_type", "state", "current_state", "last_verified_action",
    "source", "verified_at", "observed_at", "next_action", "due_at", "blocking_conditions",
    "related_ids", "responsible_agent", "version", "evidence_refs", "confidence", "freshness",
    "suppression", "suppressed", "history_complete", "source_complete", "application_state")
TASK_FIELDS = ("task_id", "entity_id", "type", "action_type", "status", "due_at", "reason",
    "priority", "evidence_refs", "allowed_capabilities", "allowed_executor_capabilities",
    "executor", "responsible_agent", "blocking_conditions", "expected_projection_hash")
EVENT_FIELDS = ("event_id", "entity_id", "contact_id", "type", "action_type", "status", "outcome",
    "occurred_at", "observed_at", "actor", "source", "verified_at", "evidence_refs", "hash")
BLOCKED_STATES = frozenset({"delivery_unknown", "submission_unknown", "submitting", "suppressed",
    "ambiguous", "conflict", "needs_reconciliation"})


class ProtocolError(RuntimeError):
    """Only the fixed code, never a private source/error body, leaves the adapter."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def _safe(value, limit=500):
    """Bound metadata recursively; remove free-form message and document fields."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [_safe(x, limit) for x in value[:20]]
    if isinstance(value, dict):
        forbidden = {"message", "body", "text", "content", "raw", "receipt", "answers", "payload",
                     "document", "resume", "email", "phone", "token", "credential", "secret"}
        return {str(k)[:100]: _safe(v, limit) for k, v in list(value.items())[:30]
                if str(k).casefold() not in forbidden}
    return value if value is None or isinstance(value, (bool, int, float)) else None


def _select(payload, keys):
    return {k: _safe(payload[k]) for k in keys if k in payload}


class AgentProtocol:
    """Use a deployment-owned registry and an authenticated principal.

    Caller labels are never credentials. A missing/unregistered principal can
    read summaries only. Evidence access and all intake need explicit grants.
    The local stdio adapter relies on private OS/configuration ownership rather
    than offering a public multi-user authentication service.
    """
    def __init__(self, store, registry: Mapping | None = None, principal: str = "reader", *,
                 manifest_id: str | None = None, now=None, max_age_seconds=86400,
                 max_context_bytes=8000, max_evidence_bytes=32000):
        if max_age_seconds < 1 or max_context_bytes < 1000 or max_evidence_bytes < 1000:
            raise ValueError("invalid_protocol_budget")
        self.store, self.registry, self.principal = store, dict(registry or {}), principal
        self.manifest_id, self._fixed_now = manifest_id, now
        if self.now.tzinfo is None:
            raise ValueError("protocol_time_requires_timezone")
        self.max_age_seconds, self.max_context_bytes = max_age_seconds, min(max_context_bytes, 8000)
        self.max_evidence_bytes = min(max_evidence_bytes, 64000)
        entry = self.registry.get(principal, {})
        self.capabilities = frozenset(entry.get("capabilities", [])) & CAPABILITIES
        self.allow_source_evidence = entry.get("allow_source_evidence") is True
        if entry.get("enabled", True) is False:
            self.capabilities = frozenset()
            self.allow_source_evidence = False
        elif not entry:
            self.capabilities = frozenset({"read_summarize"})
        self._manifest_cache = None
        self._intake = {}
        self._calls = 0
        self._provider_input_tokens = None

    @property
    def now(self):
        # Long-lived read sessions must not freeze freshness at process start.
        return self._fixed_now or dt.datetime.now(dt.timezone.utc)

    def record_input_usage(self, usage):
        """Record actual usage only when a model provider reports input_tokens."""
        tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        if type(tokens) is not int or tokens < 0:
            raise ProtocolError("provider_input_usage_unavailable")
        self._provider_input_tokens = tokens

    def _require(self, capability="read_summarize"):
        if capability not in self.capabilities:
            raise ProtocolError("capability_denied")

    def _get(self, identity):
        self._calls += 1
        try:
            result = self.store.get(identity)
            return validate_record(result) if result else None
        except Exception:
            raise ProtocolError("authoritative_record_unavailable_or_conflicted") from None

    def _manifest(self, *, fresh=False):
        if self._manifest_cache is not None and not fresh:
            return self._manifest_cache
        # A pinned snapshot is sufficient for reads, never proposal authority.
        # Intake must compare against the current writer-published manifest.
        if self.manifest_id and not fresh:
            manifest = self._get(self.manifest_id)
        else:
            self._calls += 1
            try:
                if hasattr(self.store, "latest_index_manifest"):
                    manifest = self.store.latest_index_manifest()
                else:
                    manifests = self.store.query(kind="index_manifests")
                    manifest = max(manifests, key=lambda r: r["payload"].get("generated_at", ""), default=None)
                manifest = validate_record(manifest) if manifest else None
            except Exception:
                raise ProtocolError("authoritative_index_unavailable_or_conflicted") from None
        if not manifest or manifest.get("kind") != "index_manifests":
            raise ProtocolError("index_missing")
        payload = manifest["payload"]
        if not isinstance(payload, dict):
            raise ProtocolError("index_invalid")
        self._manifest_cache = manifest
        return manifest

    def _health(self, manifest):
        payload = manifest["payload"]
        generated = _time(payload.get("generated_at"))
        age = (self.now - generated).total_seconds() if generated else None
        blockers = []
        if age is None or age < 0 or age > self.max_age_seconds:
            blockers.append("index_stale_or_unverified")
        if payload.get("complete") is not True:
            blockers.append("index_incomplete")
        if payload.get("conflicts"):
            blockers.append("index_conflicted")
        return {"generated_at": payload.get("generated_at"), "age_seconds": age,
                "complete": payload.get("complete") is True, "blocking_conditions": blockers}

    def _projection(self, entity_id, manifest):
        mapping = manifest["payload"].get("projections", {})
        # Explicit immutable mappings are preferred. A fixed ID is supported
        # for older fixtures/imports, never rewritten by the protocol.
        identity = mapping.get(entity_id) if isinstance(mapping, dict) else None
        record = self._get(identity or "projection:" + entity_id)
        if not record or record.get("kind") != "entity_projections":
            raise ProtocolError("entity_projection_missing")
        if record["payload"].get("entity_id") != entity_id:
            raise ProtocolError("entity_projection_identity_mismatch")
        return record

    def _response(self, operation, data, *, start, evidence=False):
        size = len(canonical_json(data).encode("utf-8"))
        budget = self.max_evidence_bytes if evidence else self.max_context_bytes
        if size > budget:
            raise ProtocolError("context_budget_exceeded_select_smaller_scope")
        return {"schema": SCHEMA, "operation": operation, "data": data,
                "usage": {"context_bytes": size, "estimated_input_tokens": math.ceil(size / 4),
                          "estimate_method": "utf8_bytes_divided_by_4",
                          "actual_input_tokens": self._provider_input_tokens,
                          "store_calls": self._calls - start}}

    def _records(self, identities, limit):
        results = []
        for identity in list(identities)[:limit]:
            record = self._get(identity)
            if not record:
                raise ProtocolError("indexed_record_missing")
            results.append(record)
        return results

    def get_campaign_brief(self):
        self._require()
        start = self._calls
        manifest = self._manifest()
        payload = manifest["payload"]
        data = {"manifest_id": manifest["id"], "cursor": payload.get("source_cursor", manifest["id"]),
                "health": self._health(manifest), "counts": _safe(payload.get("counts", {})),
                "brief": _safe(payload.get("campaign_brief", {}), 250),
                "execution_authority": "central_github_actions_dispatcher",
                "linkedin_executor": "human_unless_official_permission_verified"}
        return self._response("get_campaign_brief", data, start=start)

    def get_ready_tasks(self, agent=None, capabilities=None, limit=10):
        self._require()
        if agent is not None and agent != self.principal:
            raise ProtocolError("agent_identity_mismatch")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ProtocolError("invalid_limit")
        if capabilities is not None and (not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities)):
            raise ProtocolError("invalid_capability_filter")
        requested = self.capabilities if capabilities is None else frozenset(capabilities) & self.capabilities
        start = self._calls
        manifest = self._manifest()
        health = self._health(manifest)
        tasks = []
        if not health["blocking_conditions"]:
            ids = manifest["payload"].get("tasks", [])
            # Publisher supplies a bounded priority-ordered task window.
            for identity in ids[:40]:
                record = self._get(identity)
                if not record:
                    raise ProtocolError("indexed_record_missing")
                payload = record["payload"]
                allowed = set(payload.get("allowed_capabilities", payload.get("allowed_executor_capabilities", [])))
                assigned = payload.get("responsible_agent")
                if (payload.get("status", record.get("status")) in {"open", "due", "reconciliation", "ready", "pending", "available"}
                    and not payload.get("blocking_conditions")
                    and assigned in (None, "unassigned", self.principal)
                    and allowed and allowed <= requested):
                    tasks.append({"record_id": record["id"], "hash": record["hash"], **_select(payload, TASK_FIELDS)})
                if len(tasks) >= limit:
                    break
        return self._response("get_ready_tasks", {"manifest_id": manifest["id"], "health": health,
            "tasks": tasks, "requested_capabilities": sorted(requested), "available_window": 40}, start=start)

    def get_entity_context(self, entity_id, depth=2, *, task_id=None, evidence_ids=None):
        self._require()
        if depth not in (1, 2, 3):
            raise ProtocolError("invalid_context_depth")
        start = self._calls
        manifest = self._manifest()
        record = self._projection(entity_id, manifest)
        payload = record["payload"]
        data = {"manifest_id": manifest["id"], "projection_hash": record["hash"],
                "health": self._health(manifest), "entity": _select(payload, PROJECTION_FIELDS)}
        if depth == 2:
            events = self._records(payload.get("recent_event_ids", payload.get("history_ids", [])), 5)
            data["latest_events"] = [{"record_id": r["id"], "hash": r["hash"],
                                      **_select(r["payload"], EVENT_FIELDS)} for r in events]
        elif depth == 3:
            if not self.allow_source_evidence:
                raise ProtocolError("source_evidence_permission_denied")
            if (not isinstance(task_id, str) or not task_id or not isinstance(evidence_ids, list)
                or not evidence_ids or len(evidence_ids) > 5 or any(not isinstance(ref, str) for ref in evidence_ids)):
                raise ProtocolError("specific_task_and_evidence_selection_required")
            task_map = manifest["payload"].get("task_map", manifest["payload"].get("all_tasks", {}))
            if not isinstance(task_map, dict):
                task_map = {}
            if "task_map" in manifest["payload"] and task_id not in task_map:
                raise ProtocolError("evidence_task_outside_current_index")
            task_record = self._get(task_map.get(task_id, task_id))
            if (not task_record or task_record.get("kind") != "agent_tasks"
                or task_record["payload"].get("entity_id") != entity_id):
                raise ProtocolError("evidence_task_identity_mismatch")
            authorized = set(payload.get("evidence_refs", [])) & set(task_record["payload"].get("evidence_refs", []))
            if not set(evidence_ids) <= authorized:
                raise ProtocolError("evidence_outside_selected_task")
            data["task_id"] = task_id
            data["evidence"] = self._records(evidence_ids, 5)
        return self._response("get_entity_context", data, start=start, evidence=depth == 3)

    def get_recent_changes(self, since_cursor=None, *, limit=20):
        self._require()
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ProtocolError("invalid_limit")
        start = self._calls
        manifest = self._manifest()
        payload = manifest["payload"]
        current = payload.get("source_cursor", manifest["id"])
        if since_cursor == current:
            selected = []
        elif isinstance(payload.get("change_cursors"), list):
            cursors = payload["change_cursors"]
            identities = payload.get("changes", [])
            if len(cursors) != len(identities):
                raise ProtocolError("index_invalid")
            if since_cursor is None or since_cursor == payload.get("previous_cursor"):
                offset = 0
            elif since_cursor in cursors:
                offset = cursors.index(since_cursor) + 1
            else:
                raise ProtocolError("cursor_expired_refresh_brief")
            selected = []
            for index in range(offset, min(len(identities), offset + limit)):
                record = self._get(identities[index])
                if not record:
                    raise ProtocolError("indexed_record_missing")
                selected.append({"record_id": record["id"], "hash": record["hash"], "cursor": cursors[index],
                    **_select(record["payload"], (*EVENT_FIELDS, "from_cursor", "projection_hash", "projection_id", "change", "state"))})
        else:
            changes = []
            for item in payload.get("changes", [])[:40]:
                if isinstance(item, str):
                    record = self._get(item)
                    if not record:
                        raise ProtocolError("indexed_record_missing")
                    changes.append({"record_id": record["id"], "hash": record["hash"],
                        **_select(record["payload"], (*EVENT_FIELDS, "cursor", "from_cursor", "projection_hash", "projection_id", "change", "state"))})
                elif isinstance(item, dict):  # compatibility with fixture snapshots
                    changes.append(_safe(item))
                else:
                    raise ProtocolError("index_invalid")
            cursors = [item.get("cursor", item.get("record_id")) for item in changes]
            if since_cursor is None or since_cursor == payload.get("previous_cursor"):
                selected = changes[:limit]
            elif since_cursor in cursors:
                selected = changes[cursors.index(since_cursor) + 1:cursors.index(since_cursor) + 1 + limit]
            else:
                raise ProtocolError("cursor_expired_refresh_brief")
        next_cursor = selected[-1].get("cursor", selected[-1].get("record_id")) if selected else current
        return self._response("get_recent_changes", {"manifest_id": manifest["id"], "changes": _safe(selected),
            "next_cursor": next_cursor, "latest_cursor": current, "has_more": bool(selected and next_cursor != current)}, start=start)

    def get_interaction_history(self, contact_id):
        # Metadata only; original messages require depth 3 plus evidence grant.
        response = self.get_entity_context(contact_id, 2)
        response["operation"] = "get_interaction_history"
        return response

    def get_application_state(self, job_id):
        response = self.get_entity_context(job_id, 2)
        response["operation"] = "get_application_state"
        return response

    def _validate_intake(self, action, evidence, capability):
        self._require(capability)
        if not isinstance(action, dict) or not isinstance(evidence, list) or not evidence or len(evidence) > 20:
            raise ProtocolError("invalid_intake_schema")
        if any(action.get(key, self.principal) != self.principal for key in ("actor", "agent")):
            raise ProtocolError("agent_identity_mismatch")
        required = ("type", "entity_id", "idempotency_key", "expected_projection_hash", "expected_manifest_id")
        if any(not isinstance(action.get(k), str) or not action[k] or len(action[k]) > 500 for k in required):
            raise ProtocolError("intake_preconditions_required")
        if any(not isinstance(ref, str) or not ref or len(ref) > 2000 for ref in evidence):
            raise ProtocolError("evidence_references_required")
        if len(canonical_json(action).encode()) > 16000:
            raise ProtocolError("intake_too_large")
        manifest = self._manifest(fresh=True)
        if self._health(manifest)["blocking_conditions"]:
            raise ProtocolError("index_not_eligible")
        if manifest["id"] != action["expected_manifest_id"]:
            raise ProtocolError("stale_manifest")
        projection = self._projection(action["entity_id"], manifest)
        if projection["hash"] != action["expected_projection_hash"]:
            raise ProtocolError("stale_entity_projection")
        payload = projection["payload"]
        if not set(evidence) <= set(payload.get("evidence_refs", [])):
            raise ProtocolError("unindexed_evidence_reference")
        # References alone cannot prove source existence or unchanged content.
        # Validate only selected sources, never scan the history family.
        hashes = {record["id"]: record["hash"] for record in self._records(sorted(set(evidence)), 20)}
        return manifest, projection, hashes

    def _envelope(self, kind, action, evidence, capability):
        manifest, projection, evidence_hashes = self._validate_intake(action, evidence, capability)
        # Caller claims neither grant authority nor enter the durable contract.
        action = {key: value for key, value in action.items()
                  if key not in {"actor", "agent", "capabilities", "trusted_capabilities", "required_capability"}}
        identity = stable_id("agent-intake", self.principal, action["idempotency_key"])
        payload = {"schema": INTAKE_SCHEMA, "action_id": identity, "actor": self.principal,
            "action": action, "idempotency_key": action["idempotency_key"],
            "evidence": sorted(set(evidence)), "evidence_refs": sorted(set(evidence)), "expected_manifest_id": manifest["id"],
            "evidence_hashes": evidence_hashes,
            "expected_projection_hash": projection["hash"], "required_capability": capability,
            "status": "proposed", "external_effect": False}
        envelope = make_record(kind, payload, record_id=identity, status="proposed")
        prior = self._intake.get(identity) or self._get(identity)
        if prior and (prior["hash"] != envelope["hash"] or prior["kind"] != envelope["kind"]):
            raise ProtocolError("idempotency_key_conflict")
        self._intake[identity] = envelope
        return envelope

    def propose_action(self, action, evidence):
        if not isinstance(action, dict) or action.get("type") not in ACTION_CAPABILITIES:
            raise ProtocolError("unsupported_action_type")
        kind = action["type"]
        # Observations and opt-out reconciliation may be proposed while effects
        # are suppressed. Actions requiring contact/submission cannot be.
        start = self._calls
        envelope = self._envelope("agent_proposals", action, evidence, ACTION_CAPABILITIES[kind])
        projection = self._projection(action["entity_id"], self._manifest())
        payload = projection["payload"]
        state = payload.get("state", payload.get("current_state"))
        if kind not in {"reconcile", "research", "configure_workflow"}:
            suppression = payload.get("suppression")
            suppressed = suppression.get("suppressed", False) if isinstance(suppression, dict) else bool(suppression)
            if payload.get("suppressed") or suppressed or payload.get("blocking_conditions") or state in BLOCKED_STATES:
                raise ProtocolError("entity_suppressed_or_blocked")
            if kind == "review_outreach" and payload.get("history_complete") is not True:
                raise ProtocolError("complete_contact_history_required")
            if kind == "review_outreach" and payload.get("entity_type") in {"contact", "thread"} and state not in {"connected", "accepted", "replied", "reply_received", "message_sent"}:
                raise ProtocolError("accepted_connection_required")
        if kind in {"submit_application", "review_outreach"}:
            verified = _time(payload.get("verified_at"))
            freshness = payload.get("freshness")
            freshness_state = freshness.get("status") if isinstance(freshness, dict) else freshness
            if (not verified or not 0 <= (self.now - verified).total_seconds() <= self.max_age_seconds
                or freshness_state in {"stale", "unknown", "historical", "unavailable"}):
                raise ProtocolError("entity_source_stale_or_unverified")
        app_state = payload.get("application_state", state)
        if kind == "submit_application" and app_state != "submission_eligible":
            raise ProtocolError("application_not_submission_eligible")
        return self._response("propose_action", {"action_id": envelope["id"], "status": "proposed",
            "envelope": envelope, "execution_authority": "central_dispatcher"}, start=start, evidence=True)

    def report_observed_outcome(self, event, evidence):
        if not isinstance(event, dict) or event.get("type") not in OBSERVATION_CAPABILITIES:
            raise ProtocolError("unsupported_observation_type")
        if not _time(event.get("occurred_at")):
            raise ProtocolError("observation_timestamp_required")
        start = self._calls
        envelope = self._envelope("agent_observations", event, evidence, OBSERVATION_CAPABILITIES[event["type"]])
        return self._response("report_observed_outcome", {"action_id": envelope["id"],
            "status": "proposed", "envelope": envelope}, start=start, evidence=True)

    def get_action_result(self, action_id):
        self._require()
        start = self._calls
        manifest = self._manifest()
        mapping = manifest["payload"].get("action_results", {})
        record = self._get(mapping.get(action_id, "action-result:" + action_id))
        if record:
            data = {"action_id": action_id, "record_id": record["id"], "hash": record["hash"],
                    "status": record.get("status"), **_select(record["payload"],
                    ("outcome", "blocking_conditions", "evidence_refs", "occurred_at", "attempt_id", "verified"))}
        else:
            data = {"action_id": action_id, "status": "unacknowledged", "verified": False,
                    "retry_allowed": False}
        return self._response("get_action_result", data, start=start)
