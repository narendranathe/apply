"""Pure, fail-closed final history and cutover evidence validation.

Raw Notion snapshots belong in authenticated encrypted archives. This module
never edits source pages, contact envelopes, schedules, or deployment state.
"""
from __future__ import annotations

from collections import Counter
from copy import copy, deepcopy
from datetime import datetime
import re
from urllib.parse import urlsplit

from .migration import SCHEMA, combine_exports, event_record, merge_history
from .notion import NotionError, NotionStore, _text
from .records import content_hash, make_record, validate_record

SNAPSHOT_SCHEMA = "notion-data-source-snapshot/v1"
REQUIRED_FLAGS = ("history_reconciled", "backup_restore_verified", "local_actors_retired", "shadow_verified")
LOCAL_ACTORS = {"linkedin-accepted-connection-follow-up", "linkedin-connection-request-morning"}
CORE = ("id", "kind", "payload", "status", "hash")
FAMILIES = {"outreach_events", "audit_events", "contacts", "companies", "jobs", "applications",
            "application_packets", "conversation_observations", "notion_observations", "queue_observations",
            "agent_runs", "run_markers", "sync_cursors", "experiments", "exceptions", "reports"}
FAMILIES.update({'referrals', 'interviews', 'conversation_events', 'conversation_threads',
                 'agent_tasks', 'agent_proposals', 'action_results', 'entity_projections', 'index_manifests'})


def _require(condition, reason):
    if not condition:
        raise ValueError("cutover_validation:" + reason)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return bool(parsed.scheme == "https" and host and parsed.path not in ("", "/")
                and not parsed.username and not parsed.password
                and host not in {"localhost", "example.com", "example.org", "example.net"}
                and not host.endswith((".test", ".invalid", ".example", ".localhost")))


def _archive_location(value):
    return _url(value) or (isinstance(value, str) and value.startswith("migration-archives/")
                           and value.endswith(".aesgcm") and ".." not in value.split("/") and "\\" not in value)


def _timestamp(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _blocks_complete(items):
    _require(isinstance(items, list), "snapshot_blocks_missing")
    for block in items:
        _require(isinstance(block, dict) and isinstance(block.get("type"), str)
                 and isinstance(block.get(block["type"]), dict), "snapshot_block_malformed")
        _require(block["type"] != "unsupported", "snapshot_block_unsupported")
        _require(not block.get("has_more"), "snapshot_block_truncated")
        if block.get("has_children"):
            _require("children" in block, "snapshot_children_missing")
        if "children" in block:
            _blocks_complete(block["children"])


class _CapturedStore(NotionStore):
    def __init__(self, bodies):
        self.bodies = bodies

    def _blocks(self, identity):
        _require(identity in self.bodies, "snapshot_body_missing")
        return deepcopy(self.bodies[identity])

    def _complete_page(self, page):
        return deepcopy(page)

    def _request(self, *args, **kwargs):
        raise ValueError("cutover_validation:snapshot_decode_network_forbidden")


def build_source_delta(snapshot, baseline_export, *, archive_reference=None, store=None):
    """Append new history and one snapshot audit; never replace source contacts.

    ``archive_reference`` binds the caller's real raw snapshot member bytes to
    their authenticated archive. An optional decoder store is copied and all
    body/property reads are rebound to captured data, prohibiting network reads.
    """
    _require(isinstance(snapshot, dict) and snapshot.get("schema") == SNAPSHOT_SCHEMA
             and snapshot.get("complete") is True, "snapshot_incomplete")
    _require(isinstance(snapshot.get("data_source"), dict)
             and isinstance(snapshot["data_source"].get("properties"), dict)
             and isinstance(snapshot.get("data_source_id"), str) and snapshot["data_source_id"]
             and _timestamp(snapshot.get("captured_at")), "snapshot_schema_missing")
    _require(isinstance(snapshot.get("pages"), list), "snapshot_pages_missing")
    combine_exports([baseline_export])
    raw_hash = content_hash({k: v for k, v in snapshot.items() if k != "hash"})
    _require("hash" not in snapshot or snapshot["hash"] == raw_hash, "snapshot_hash_mismatch")
    reference = archive_reference or snapshot.get("archive_reference")
    _require(isinstance(reference, dict) and _archive_location(reference.get("archive"))
             and ("archive_sha256" not in reference or _hash(reference["archive_sha256"]))
             and _hash(reference.get("member_sha256"))
             and isinstance(reference.get("member"), str) and reference["member"]
             and reference.get("snapshot_sha256") == raw_hash, "snapshot_archive_reference_missing")
    bodies, pages, source_hashes = {}, [], {}
    counts = Counter()
    for entry in snapshot["pages"]:
        _require(isinstance(entry, dict) and isinstance(entry.get("page"), dict), "snapshot_page_missing")
        page = entry["page"]
        identity = page.get("id")
        _require(isinstance(identity, str) and identity and identity not in bodies, "snapshot_page_identity_conflict")
        props = page.get("properties")
        _require(isinstance(props, dict) and page.get("object", "page") == "page", "snapshot_page_malformed")
        _require(all(isinstance(prop, dict) and not prop.get("has_more") for prop in props.values()),
                 "snapshot_property_truncated")
        _blocks_complete(entry.get("blocks"))
        bodies[identity] = entry["blocks"]
        pages.append(page)
        counts["pages"] += 1
        cloud = bool(_text(props.get("Cloud ID")) or _text(props.get("Cloud Envelope")))
        counts["cloud_pages" if cloud else "native_pages"] += 1
        if not cloud:
            # Same exact hash shape consumed by run_outreach's source truth gate.
            source_hashes[identity] = content_hash({"properties": props, "body": entry["blocks"]})
            if _text(props.get("Type")) == "Contact":
                counts["contacts"] += 1
                if props.get("No Contact", {}).get("checkbox") is True or _text(props.get("Status")) == "Suppressed":
                    counts["suppressed_contacts"] += 1
        display = _text(props.get("Delivery State"))
        if display.lower().replace("_", " ") == "delivery unknown":
            counts["display_delivery_unknown"] += 1
        if display.lower() == "pending":
            counts["display_pending"] += 1
    decoder = _CapturedStore(bodies)
    if store is not None:
        decoder = copy(store)
        decoder._blocks = _CapturedStore(bodies)._blocks
        decoder._complete_page = lambda page: deepcopy(page)
        decoder._request = _CapturedStore(bodies)._request
    remote, seen = [], set()
    for page in pages:
        props = page["properties"]
        try:
            if _text(props.get("Cloud ID")) or _text(props.get("Cloud Envelope")):
                # Validate managed bytes without turning them into source imports.
                decoded = decoder._decode_cloud(page)
                if decoded["kind"] != "outreach_events":
                    continue
            elif _text(props.get("Event ID")):
                decoded = decoder._decode_legacy(page)
            else:
                continue
        except NotionError:
            raise ValueError('cutover_validation:snapshot_record_integrity_failed') from None
        _require(decoded["id"] not in seen, "snapshot_event_identity_conflict")
        seen.add(decoded["id"])
        remote.append({"payload": deepcopy(decoded["payload"]), "notion_url": page.get("url", "")})
    baseline = {row["id"]: row for row in baseline_export["records"] if row["kind"] == "outreach_events"}
    _require(set(baseline) <= seen, "baseline_history_missing")
    local = {"schema": SCHEMA, "records": list(baseline.values()), "blocked": []}
    merged = merge_history(local, [], remote)
    for row in merged["records"]:
        old = baseline.get(row["id"])
        _require(old is None or all(old[field] == row[field] for field in CORE), "baseline_history_changed")
    records = [row for row in merged["records"] if row["id"] not in baseline]
    counts["outreach_events"] = len(remote)
    counts["new_outreach_events"] = len(records)
    payload = {"schema": "apply-source-cutover-audit/v1", "captured_at": snapshot["captured_at"],
               "raw_snapshot_sha256": raw_hash, "source_members_sha256": content_hash(source_hashes),
               "source_member_hashes": source_hashes, "source_counts": dict(sorted(counts.items())),
               "archive_reference": deepcopy(reference), "baseline_export_sha256": content_hash(baseline_export),
               "baseline_history_unchanged": True, "historical_only": True, "replay_allowed": False,
               "contact_payloads_replaced": 0}
    records.append(make_record("audit_events", payload, "source-cutover-snapshot:" + raw_hash, "evidence_observation"))
    return {"schema": SCHEMA, "records": sorted(records, key=lambda row: row["id"]), "blocked": [],
            "source_manifest": [], "historical_only": True, "cloud_authoritative": False, "replay_allowed": False}


def validate_import(store, export):
    """Fresh immutable readback, with aggregate output only and no writes."""
    result = {"records": 0, "families": {}, "unchanged": 0, "conflicted": 0, "blocked": 0}
    if not isinstance(export, dict) or export.get("schema") != SCHEMA or export.get("blocked"):
        result["blocked"] = 1
        return result
    incoming, collisions = {}, set()
    if not isinstance(export.get("records"), list):
        result["blocked"] = 1
        return result
    for item in export["records"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            result["records"] += 1
            result["blocked"] += 1
            continue
        key = item["id"]
        if key in incoming and any(incoming[key].get(field) != item.get(field) for field in CORE):
            collisions.add(key)
        incoming[key] = item
    families = Counter()
    for key, item in incoming.items():
        result["records"] += 1
        kind = item.get("kind")
        families[kind if kind in FAMILIES else "other"] += 1
        if key in collisions:
            result["conflicted"] += 1
            continue
        try:
            validate_record(item)
            live = store.get(key)
            if live is None:
                result["blocked"] += 1
            elif any(live.get(field) != item.get(field) for field in CORE):
                result["conflicted"] += 1
            else:
                validate_record(live)
                result["unchanged"] += 1
        except Exception:
            result["blocked"] += 1
    result["families"] = dict(sorted(families.items()))
    return result


def make_cutover_record(evidence):
    """Return an activation record only with concrete, cross-bound evidence.

    The caller obtains/archives evidence and performs the single-writer write.
    This pure constructor cannot establish the truth of an external assertion.
    """
    _require(isinstance(evidence, dict) and all(evidence.get(flag) is True for flag in REQUIRED_FLAGS), "flags_missing")
    _require(re.fullmatch(r"[0-9a-f]{40}", evidence.get("commit", "")) is not None, "commit_missing")
    _require(_hash(evidence.get("historical_manifest_sha256")) and _timestamp(evidence.get("observed_at")), "manifest_missing")
    urls = evidence.get("evidence_urls")
    _require(isinstance(urls, list) and urls and all(_url(url) for url in urls), "evidence_urls_missing")
    history = evidence.get("history", {})
    _require(isinstance(history, dict) and isinstance(history.get("readback"), dict), "history_readback_missing")
    report = history.get("readback", {})
    _require(_url(history.get("evidence_url")) and history.get("historical_manifest_sha256") == evidence["historical_manifest_sha256"]
             and isinstance(report.get("records"), int) and not isinstance(report.get("records"), bool)
             and report["records"] > 0 and report.get("unchanged") == report["records"]
             and report.get("conflicted") == 0 and report.get("blocked") == 0
             and isinstance(report.get("families"), dict) and all(isinstance(n, int) and n >= 0 for n in report["families"].values())
             and sum(report["families"].values()) == report["records"], "history_readback_missing")
    archive = evidence.get("archive", {})
    _require(isinstance(archive, dict), "archive_restore_missing")
    _require(archive.get("restore_verified") is True and archive.get("hashes_verified") is True
             and _hash(archive.get("archive_sha256")) and _hash(archive.get("raw_snapshot_sha256"))
             and archive.get("historical_manifest_sha256") == evidence["historical_manifest_sha256"]
             and _url(archive.get("evidence_url")), "archive_restore_missing")
    actors = evidence.get("local_actors", {})
    _require(isinstance(actors, dict), "actor_readbacks_missing")
    readbacks = actors.get("readbacks", [])
    _require(isinstance(readbacks, list) and len(readbacks) == len(LOCAL_ACTORS)
             and {r.get("id") for r in readbacks if isinstance(r, dict)} == LOCAL_ACTORS
             and all(isinstance(r, dict) and r.get("status") == "PAUSED" and r.get("persisted") is True
                     and _hash(r.get("readback_sha256")) and _url(r.get("evidence_url")) for r in readbacks), "actor_readbacks_missing")
    application = actors.get("application_writer", {})
    _require(isinstance(application, dict), "application_actor_stop_unconfirmed")
    _require(application.get("actor") == "Milo" and application.get("stopped") is True
             and application.get("source") == "direct_user" and _hash(application.get("confirmation_sha256"))
             and _url(application.get("evidence_url")), "application_actor_stop_unconfirmed")
    shadow = evidence.get("shadow", {})
    _require(isinstance(shadow, dict), "hosted_shadow_missing")
    _require(shadow.get("hosted") is True and shadow.get("dry_run") is True
             and shadow.get("conclusion") == "success" and shadow.get("commit") == evidence["commit"]
             and isinstance(shadow.get("lanes"), list) and len(shadow["lanes"]) == 3
             and set(shadow["lanes"]) == {"accepted", "first_touch", "applications"}
             and _url(shadow.get("evidence_url")), "hosted_shadow_missing")
    return make_record("audit_events", deepcopy(evidence), "deployment:cutover:v2", "ready")


def validate_cutover_record(record):
    canonical = validate_record(record)
    expected = make_cutover_record(canonical["payload"])
    _require(all(canonical[field] == expected[field] for field in CORE), "deployment_record_invalid")
    return canonical


def make_readiness_record(evidence, reasons):
    """Append blocked readiness evidence under an ID that cannot activate gates."""
    _require(isinstance(evidence, dict) and isinstance(reasons, list) and reasons
             and all(isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]+", reason) for reason in reasons), "readiness_reasons_missing")
    payload = {"schema": "apply-cutover-readiness/v1", "evidence": deepcopy(evidence),
               "reasons": sorted(set(reasons)), **{flag: False for flag in REQUIRED_FLAGS}, "activation_authorized": False}
    return make_record("audit_events", payload, "deployment:cutover:blocked:" + content_hash(payload), "blocked")
