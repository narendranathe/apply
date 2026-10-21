"""Single-writer, append-only application lane using JobScout and Notion.

Run only through the common cloud orchestrator. Notion cannot provide atomic
claims; serialization and fresh readback are prerequisites, not exactly-once
guarantees. All records are immutable transition snapshots.
"""
from __future__ import annotations

import datetime as dt
import base64
import hashlib
import json
import os
import re
import urllib.parse
from zoneinfo import ZoneInfo

from scripts.jobscout_feed import candidates, fetch_feed, parse_dt, feed_metadata
from .preparation import digest, grade_job, prepare_packet
from .submission import (HTTPTransport, ProviderBudgetTransport, SubmissionBlocked,
                         adapter_for, normalize, permitted, timestamp)
from .notion import NotionError
from .shadow import ShadowStore


STATUSES = {"discovered", "qualified", "prepared", "submission_eligible", "submitting", "applied_verified",
            "needs_user_fact", "submission_blocked", "submission_unknown", "manual_action_needed", "closed", "rejected", "withdrawn", "interview"}
LEGACY = {"sourced": "discovered", "draft_ready": "prepared", "approved": "prepared", "stale": "closed"}
TRANSITIONS = {
    "discovered": {"qualified", "needs_user_fact", "submission_blocked", "closed", "rejected"},
    "qualified": {"prepared", "needs_user_fact", "submission_blocked", "closed"},
    "prepared": {"submission_eligible", "needs_user_fact", "submission_blocked", "closed"},
    "submission_eligible": {"submitting", "submission_blocked", "closed"},
    "submitting": {"applied_verified", "submission_unknown"},
    "submission_unknown": {"applied_verified", "closed", "withdrawn"},
    "applied_verified": {"interview", "rejected", "withdrawn", "closed"},
    "interview": {"rejected", "withdrawn", "closed"},
    "needs_user_fact": {"qualified", "submission_blocked", "closed"},
    "submission_blocked": {"qualified", "needs_user_fact", "closed"},
    "manual_action_needed": {"qualified", "applied_verified", "submission_unknown", "closed"},
    "closed": set(), "rejected": set(), "withdrawn": set(),
}


class StateConflict(Exception):
    pass


def employer_identity(company):
    # Explicit brand/legal-name equivalence, not generic suffix stripping.
    # Primary source: https://www.jpmorganchase.com/legal
    identity = normalize(company)
    return {'jpmorganchaseco': 'jpmorganchase'}.get(identity, identity)


def canonical_url(value):
    parsed = urllib.parse.urlsplit(value or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return str(value or "").strip().lower()
    # Preserve identity query fields, discard referral/tracking aliases.
    identity_query = sorted((key, val) for key, val in urllib.parse.parse_qsl(parsed.query) if key.lower() in {"gh_jid", "jobid", "job_id", "requisitionid"})
    host = parsed.hostname.lower().replace("job-boards.greenhouse.io", "boards.greenhouse.io")
    return urllib.parse.urlunsplit(("https", host, parsed.path.rstrip("/").removesuffix("/apply"), urllib.parse.urlencode(identity_query), ""))


def application_key(company, requisition_id=None, url=None, role=None, location=None):
    if requisition_id:
        return employer_identity(company) + ":" + normalize(requisition_id)
    if url:
        return canonical_url(url)
    return ":".join(normalize(value) for value in (company, role, location))


def record_payload(record):
    return record.get("payload", record)


def fold_applications(records):
    latest = {}
    for record in records:
        payload = record_payload(record)
        key = payload.get("application_id") or payload.get("dedup_key") or record.get("id")
        if not key:
            continue
        status = record.get("status") or payload.get("status", "discovered")
        # A historical claim of application without a receipt is uncertainty,
        # and blocks a future submission until reconciled.
        if status == "applied":
            status = "applied_verified" if payload.get("confirmation_evidence") or payload.get("receipt") else "submission_unknown"
        status = LEGACY.get(status, status)
        candidate = {**record, "status": status, "payload": payload}
        revision = payload.get("revision", 0)
        existing = latest.get(key)
        if existing and revision == existing["payload"].get("revision", 0) and record.get("id") != existing.get("id"):
            raise StateConflict("ambiguous_application_revision")
        if not existing or revision > existing["payload"].get("revision", 0):
            latest[key] = candidate
    return latest


def matching_application(records, company, requisition_id=None, urls=(), role=None):
    aliases = {canonical_url(url) for url in urls if url}
    for record in records.values():
        payload = record["payload"]
        old_urls = [payload.get("url"), payload.get("posting_url"), *payload.get("url_aliases", []), *payload.get("aliases", [])]
        if aliases & {canonical_url(url) for url in old_urls if url}:
            return record
        if employer_identity(payload.get("company")) == employer_identity(company):
            old_req = payload.get("requisition_id") or payload.get("job_id")
            if old_req and requisition_id and normalize(old_req) == normalize(requisition_id):
                return record
            if payload.get("employer_history_unresolved"):
                return record
            # A changed title does not establish a distinct requisition.
            # Employer-level legacy identity must be reconciled first.
            if not old_req:
                return record
    return None


def daily_attempt_count(records, now, timezone_name="America/Chicago"):
    """Count attempts on the scheduler's local day, including unknown effects."""
    zone = ZoneInfo(timezone_name)
    if now.tzinfo is None:
        raise StateConflict("application_pacing_timezone_unverified")
    local_day = now.astimezone(zone).date()
    total = 0
    for record in records.values():
        attempt = record["payload"].get("attempt")
        if not attempt:
            continue
        started = timestamp(attempt.get("started_at"))
        if started is None:
            raise StateConflict("application_attempt_date_unverified")
        total += started.astimezone(zone).date() == local_day
    return total


def _make(kind, payload, status, record_id):
    return {"id": record_id, "kind": kind, "payload": payload, "status": status, "hash": digest(payload)}


def transition(store, application_id, status, updates=None, expected=None):
    """Append, verify readback, and detect a concurrent/manual edit first."""
    if status not in STATUSES:
        raise ValueError("unsupported_application_status")
    current = fold_applications(store.query(kind="applications")).get(application_id)
    if (current or {}).get("id") != (expected or {}).get("id"):
        raise StateConflict("application_changed_before_transition")
    if current and status not in TRANSITIONS.get(current["status"], set()):
        raise StateConflict("invalid_application_transition")
    payload = dict(current["payload"] if current else {})
    payload.update(updates or {})
    payload.update({"application_id": application_id, "revision": payload.get("revision", 0) + 1,
                    "previous_record_id": current.get("id") if current else None, "status": status,
                    "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "actor": "cloud_application_lane"})
    record_id = "application-transition:" + digest({"application_id": application_id, "revision": payload["revision"], "payload": payload})
    record = _make("applications", payload, status, record_id)
    store.put(record)
    readback = store.get(record_id)
    if not readback or readback.get("hash") != record["hash"] or readback.get("status") != status:
        raise StateConflict("application_transition_readback_failed")
    event_payload = {"application_id": application_id, "transition_record_id": record_id,
                     "status": status, "payload_hash": record["hash"], "occurred_at": payload["observed_at"]}
    event = _make("audit_events", event_payload, "recorded", "application-event:" + digest(event_payload))
    store.put(event)
    if not store.get(event["id"]):
        raise StateConflict("application_audit_readback_failed")
    return readback


def _employer(lead, config):
    employers = config.get("employers", {})
    employer = employers.get(normalize(lead.get("company"))) or employers.get(lead.get("company"))
    if employer:
        return employer
    parsed = urllib.parse.urlsplit(lead.get("url") or "")
    match = re.fullmatch(r"/([^/]+)/jobs/\d+/?", parsed.path)
    if parsed.hostname in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and match:
        return {"ats": "greenhouse", "board_token": match[1]}
    match = re.fullmatch(r"/([^/]+)/[a-zA-Z0-9-]+(?:/apply)?/?", parsed.path)
    if parsed.hostname == "jobs.lever.co" and match:
        return {"ats": "lever", "board_token": match[1]}
    raise SubmissionBlocked("employer_board_config_unavailable")


def _safe_packet(packet):
    # Durable records contain hashes/provenance, never a runner-only /tmp link.
    return {key: packet.get(key) for key in ("resume_sha256", "source_sha256", "master_sha256", "ground_truth_sha256", "answers_hash", "answer_evidence", "qa", "claims", "changes", "arc", "missing")}


def screening_eligibility(job, employer, now):
    """Apply documented standing gates, without inventing sponsorship or COL."""
    screening = employer.get("screening", {})
    sponsorship = screening.get("sponsorship_evidence", {})
    observed = timestamp(sponsorship.get("verified_at"))
    fiscal_year = now.year + (1 if now.month >= 10 else 0)
    years = sponsorship.get("fiscal_years", [])
    if not (sponsorship.get("verified") is True and sponsorship.get("recent_h1b_filings") is True
            and sponsorship.get("source_url", "").startswith("https://") and observed and observed <= now
            and (now - observed).total_seconds() <= 90 * 86400
            and any(isinstance(year, int) and fiscal_year - 2 <= year < fiscal_year for year in years)):
        raise SubmissionBlocked("recent_sponsorship_evidence_missing")
    # $175K is the standing Dallas-equivalent target, not an automatically
    # converted target for expensive relocation destinations.
    target = 175000 if re.search(r"Dallas|Addison", job.get("location", ""), re.I) else None
    compensation = screening.get("compensation_policy", {})
    if compensation.get("verified") is True and compensation.get("source"):
        target = compensation.get("minimum_base_salary")
    if not isinstance(target, (int, float)) or target <= 0:
        raise SubmissionBlocked("location_compensation_policy_missing")
    ranges = [pay for pay in job.get("salary_ranges", []) if pay.get("currency_type") == "USD"
              and isinstance(pay.get("max_cents"), (int, float)) and not re.search(r"hour|month|equity|bonus", pay.get("title", ""), re.I)]
    if not ranges:
        raise SubmissionBlocked("official_compensation_range_unverified")
    if max(pay["max_cents"] for pay in ranges) < target * 100:
        raise SubmissionBlocked("compensation_below_standing_target")
    return {"sponsorship_source": sponsorship["source_url"], "sponsorship_verified_at": sponsorship["verified_at"],
            "minimum_base_salary": target, "compensation_policy_source": compensation.get("source", "knockout-answers:Compensation"),
            "official_salary_ranges_hash": digest(ranges)}


def persist_packet(store, application_id, job, packet):
    """Store one recoverable private packet, referenced by transition snapshots.

    Small PDFs use a bounded binary representation in Notion's existing record
    system. Export validates its SHA-256 before writing a reviewable file.
    Larger packets block for an approved artifact-storage route.
    """
    from pathlib import Path
    payload = {"application_id": application_id, "jd": job["jd"], "jd_hash": digest(job["jd"]),
               "questions": job.get("questions", []), "answers": packet.get("fields", {}),
               "answers_hash": packet.get("answers_hash"), "changelog": packet.get("changes", []),
               "claim_manifest": packet.get("claims", []), "qa": packet.get("qa", {}),
               "resume_sha256": packet.get("resume_sha256"), "source_sha256": packet.get("source_sha256"),
               "answer_evidence": packet.get("answer_evidence", []), "missing": packet.get("missing", [])}
    if packet.get("artifact_dir"):
        source = Path(packet["artifact_dir"]) / "resume.tex"
        if source.is_file():
            payload["resume_source"] = source.read_text()
            if digest(source.read_bytes()) != packet.get("source_sha256"):
                raise SubmissionBlocked("packet_source_hash_mismatch")
    if packet.get("resume_path") and Path(packet["resume_path"]).is_file():
        binary = Path(packet["resume_path"]).read_bytes()
        if digest(binary) != packet.get("resume_sha256"):
            raise SubmissionBlocked("packet_pdf_hash_mismatch")
        payload["resume_pdf_base64"] = base64.b64encode(binary).decode()
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 190_000:
        raise SubmissionBlocked("private_packet_exceeds_notion_storage_bound")
    identity = "application-packet:" + digest(payload)
    record = _make("application_packets", payload, "verified" if packet.get("ready") else "incomplete", identity)
    store.put(record)
    readback = store.get(identity)
    if not readback or readback.get("hash") != record["hash"]:
        raise StateConflict("application_packet_readback_failed")
    return {**_safe_packet(packet), "record_id": identity}


def run_applications(store, config, dry_run=True, *, feed=None, transport=None, prepare=None, effect_guard=None,
                     preview_packets=False, provider_budget_guard=None):
    """Process independently; return only redacted aggregate evidence.

    Injection keywords are for deterministic sandbox tests. Production uses
    official transports and evidence-checked preparation, never a config flag
    that certifies a fixture resume.
    """
    if preview_packets:
        if not dry_run or not isinstance(store, ShadowStore):
            raise ValueError('Private preview packets require a shadow store')
    if not dry_run and provider_budget_guard is not None:
        transport = ProviderBudgetTransport(transport or HTTPTransport(), provider_budget_guard)
    settings = config.get("applications", config)
    now = dt.datetime.now(dt.timezone.utc)
    summary = {"lane": "applications", "dry_run": dry_run, "feed_checked": False,
               "candidates_seen": 0, "duplicates": 0, "qualified": 0, "prepared": 0,
               "submission_attempts": 0, "applied_verified": 0, "submission_unknown": 0,
               "exceptions": {}, "statuses": {}}
    def count(name):
        summary["exceptions"][name] = summary["exceptions"].get(name, 0) + 1
    try:
        feed = feed if feed is not None else fetch_feed(settings.get("feed_url") or None)
        meta = feed_metadata(feed, now, settings.get("max_feed_age_hours", 24))
        summary.update({"feed_checked": True, "feed_exported_at": meta["exported_at"], "feed_fresh": not meta["stale"]})
        records = fold_applications(store.query(kind="applications"))
        # Explicitly typed legacy application events are separate from ordinary
        # outreach prose. Preserve their structured evidence and block unknown
        # employer/requisition history; never infer an application from text.
        for event in store.query(kind="outreach_events"):
            observation = record_payload(event)
            if str(observation.get("kind", "")).casefold() not in {"application", "application_submission", "application_submitted"}:
                continue
            company = observation.get("company")
            role_key = observation.get("role_key")
            if not company and isinstance(role_key, str) and ":" in role_key and not role_key.startswith("http"):
                company = role_key.split(":", 1)[0]
            url = observation.get("job_url") or observation.get("posting_url") or observation.get("source_url")
            req = observation.get("requisition_id") or observation.get("job_id")
            if not company and not url:
                count("unresolved_historical_application_identity")
                return summary
            if matching_application(records, company, req, [url]):
                continue
            identity = "application-history:" + digest(event["id"])
            payload = {"application_id": identity, "company": company, "requisition_id": req,
                       "url": url, "dedup_key": application_key(company, req, url), "revision": 0,
                       "source_event_id": event["id"], "source_event_hash": event.get("hash"),
                       "employer_history_unresolved": not req, "historical_only": True,
                       "reason": "historical_application_event_requires_reconciliation"}
            historical_record = _make("applications", payload, "submission_unknown", identity)
            if not dry_run:
                store.put(historical_record)
                if not store.get(identity):
                    raise StateConflict("historical_application_readback_failed")
            records[identity] = historical_record
        # Imported applied/unknown observations are suppression, not proof of a
        # confirmed submission. Preserve even closed/inactive/low-score jobs.
        for historical in feed.get("jobs", []):
            if historical.get("application_status") not in {"applied", "applied_verified", "submitting", "submission_unknown", "interview", "rejected", "withdrawn"}:
                continue
            old = matching_application(records, historical.get("company"), historical.get("requisition_id") or historical.get("job_id"), [historical.get("url")], historical.get("title"))
            if old:
                continue
            key = application_key(historical.get("company"), historical.get("requisition_id") or historical.get("job_id"), historical.get("url"))
            identity = "application:" + digest(key)
            payload = {"application_id": identity, "dedup_key": key, "revision": 0,
                       "company": historical.get("company"), "role": historical.get("title"),
                       "url": historical.get("url"), "requisition_id": historical.get("requisition_id") or historical.get("job_id"),
                       "source": "jobscout_feed", "source_exported_at": feed.get("exported_at"),
                       "historical_reported_status": historical["application_status"],
                       "historical_reported_applied_at": historical.get("application_applied_at"),
                       "reason": "historical_submission_requires_receipt_reconciliation"}
            historic_record = _make("applications", payload, "submission_unknown", identity + ":jobscout-history")
            if not dry_run:
                store.put(historic_record)
                if not store.get(historic_record["id"]):
                    raise StateConflict("historical_application_readback_failed")
            records[identity] = historic_record
        if meta["stale"]:
            count("feed_stale_or_timestamp_unknown")
            return summary
    except (NotionError, StateConflict):
        raise
    except Exception:
        count("feed_or_state_unavailable")
        return summary
    maximum = settings.get("max_submissions_per_day", 3)
    leads = candidates(feed, settings.get("min_score", 0.51), settings.get("max_age_days", 7), now)
    for lead in leads[:settings.get("max_candidates", 25)]:
        summary["candidates_seen"] += 1
        application_id, current = None, None
        local_seen = records
        try:
            existing = matching_application(local_seen, lead["company"], lead.get("requisition_id"), [lead["url"]], lead.get("title"))
            if existing and (existing["payload"].get("attempt") or existing["status"] not in {"discovered", "needs_user_fact", "submission_blocked", "qualified", "prepared"}):
                summary["duplicates"] += 1
                continue
            # Verification may reject or temporarily fail before a canonical
            # job is returned. Keep the fresh dedup match bound to this work
            # so that rejection records an exception against the known
            # application instead of recreating it under a feed URL alias.
            current = existing
            if existing:
                application_id = (existing["payload"].get("application_id")
                                  or existing["payload"].get("dedup_key") or existing["id"])
            employer = _employer(lead, settings)
            adapter = adapter_for(employer, transport)
            job = adapter.verify(lead)
            if not job.get("open"):
                count("official_requisition_closed")
                if existing and not dry_run:
                    current = transition(store, existing["payload"]["application_id"], "closed", {"reason": "official_requisition_closed"}, existing)
                continue
            existing = matching_application(local_seen, job["company"], job["requisition_id"], job["aliases"], job["title"])
            if existing and (existing["payload"].get("attempt") or existing["status"] not in {"discovered", "needs_user_fact", "submission_blocked", "qualified", "prepared"}):
                summary["duplicates"] += 1
                continue
            key = application_key(job["company"], job["requisition_id"])
            application_id = ((existing["payload"].get("application_id")
                               or existing["payload"].get("dedup_key") or existing["id"])
                              if existing else "application:" + digest(key))
            current = existing
            base = {"company": job["company"], "role": job["title"], "requisition_id": job["requisition_id"],
                    "posting_id": job["posting_id"], "url": job["url"], "url_aliases": sorted(set(job["aliases"])),
                    "dedup_key": key, "job": job, "jd_hash": digest(job["jd"]), "official_evidence_url": job["evidence_url"]}
            if not current and not dry_run:
                current = transition(store, application_id, "discovered", base)
            grade = grade_job(job, {**settings, **employer.get("screening", {})})
            if not grade["qualified"]:
                count(grade["reason"])
                if not dry_run and current["status"] != "submission_blocked":
                    current = transition(store, application_id, "submission_blocked", {"qualification": grade, "reason": grade["reason"]}, current)
                records[application_id] = current or {"id": application_id, "status": "submission_blocked", "payload": {**base, "application_id": application_id}}
                continue
            summary["qualified"] += 1
            if not dry_run and current["status"] != "qualified":
                # Prepared snapshots are regenerated from fresh official facts;
                # the graph never skips an eligibility check.
                if current["status"] == "prepared":
                    current = transition(store, application_id, "submission_blocked", {"reason": "fresh_packet_required"}, current)
                current = transition(store, application_id, "qualified", {**base, "qualification": grade}, current)
            packet = (prepare or prepare_packet)(job, grade, settings)
            packet_reference = _safe_packet(packet) if dry_run else persist_packet(store, application_id, job, packet)
            if not packet.get("ready"):
                status = "needs_user_fact" if packet.get("missing") else "submission_blocked"
                count("verified_answer_unavailable" if packet.get("missing") else packet.get("reason", "preparation_unavailable"))
                if not dry_run:
                    current = transition(store, application_id, status, {"packet": packet_reference, "reason": packet.get("reason")}, current)
                records[application_id] = current or {"id": application_id, "status": status, "payload": {**base, "application_id": application_id}}
                continue
            summary["prepared"] += 1
            if preview_packets:
                # Recoverable research materials go only to private staging;
                # the orchestrator encrypts that staging before runner cleanup.
                persist_packet(store, application_id, job, packet)
            if not dry_run:
                current = transition(store, application_id, "prepared", {"packet": packet_reference}, current)
            if not permitted(employer, now):
                raise SubmissionBlocked("employer_submission_permission_missing")
            screening_evidence = screening_eligibility(job, employer, now)
            if not packet.get("qa", {}).get("verified") or not all(packet.get(key) for key in ("resume_sha256", "answers_hash", "ground_truth_sha256", "source_sha256")):
                raise SubmissionBlocked("packet_verification_evidence_missing")
            if not os.environ.get(employer.get("credential_env", "")):
                raise SubmissionBlocked("employer_scoped_credential_missing")
            if daily_attempt_count(records, dt.datetime.now(dt.timezone.utc)) >= maximum:
                raise SubmissionBlocked("daily_application_pacing_reached")
            if dry_run:
                summary["statuses"]["submission_eligible"] = summary["statuses"].get("submission_eligible", 0) + 1
                records[application_id] = {"id": application_id, "status": "submission_eligible", "payload": {**base, "application_id": application_id}}
                continue
            current = transition(store, application_id, "submission_eligible", {"permission_evidence": employer["permission"], "screening_evidence": screening_evidence}, current)
            # Recheck identity, open status, live question schema, and artifacts
            # immediately before logging the claim and performing the effect.
            live = adapter.verify(lead)
            if not live.get("open"):
                current = transition(store, application_id, "closed", {"reason": "closed_at_action_time"}, current)
                continue
            screening_eligibility(live, employer, dt.datetime.now(dt.timezone.utc))
            if digest(live["questions"]) != digest(job["questions"]) or digest(live["jd"]) != digest(job["jd"]) or live["requisition_id"] != job["requisition_id"] or live.get("form_schema_hash") != job.get("form_schema_hash"):
                raise SubmissionBlocked("official_post_changed_at_action_time")
            with open(packet["resume_path"], "rb") as handle:
                artifact_hash = digest(handle.read())
            if artifact_hash != packet["resume_sha256"] or digest(packet["fields"]) != packet["answers_hash"]:
                raise SubmissionBlocked("packet_changed_at_action_time")
            fresh_records = fold_applications(store.query(kind="applications"))
            duplicate = matching_application(fresh_records, job["company"], job["requisition_id"], job["aliases"], job["title"])
            if not duplicate or duplicate["id"] != current["id"]:
                raise StateConflict("application_claim_changed")
            action_time = dt.datetime.now(dt.timezone.utc)
            if daily_attempt_count(fresh_records, action_time) >= maximum:
                raise SubmissionBlocked("daily_application_pacing_reached")
            if effect_guard is not None:
                effect_guard()  # Before intent/provider request; preserve recovery time.
            attempt = {"started_at": action_time.isoformat(), "idempotency_key": application_id,
                       "resume_hash": packet["resume_sha256"], "answers_hash": packet["answers_hash"],
                       "payload_hash": digest({"fields": packet["fields"], "resume_hash": packet["resume_sha256"], "posting_id": job["posting_id"]})}
            current = transition(store, application_id, "submitting", {"attempt": attempt}, current)
            summary["submission_attempts"] += 1
            result = adapter.submit(job, packet)
            outcome = result.get("status", "submission_unknown")
            if outcome == "applied_verified" and not result.get("receipt_id"):
                outcome = "submission_unknown"
            current = transition(store, application_id, outcome, {"receipt": result}, current)
            summary[outcome] = summary.get(outcome, 0) + 1
            records[application_id] = current
        except (NotionError, StateConflict):
            # An uncertain Notion write means the central writer cannot safely
            # continue with other external effects or report a completed run.
            raise
        except SubmissionBlocked as error:
            reason = str(error)
            count(reason)
            if not current and not dry_run:
                key = application_key(lead.get("company"), lead.get("requisition_id"), lead.get("url"))
                application_id = "application:" + digest(key)
                current = transition(store, application_id, "discovered", {
                    "company": lead.get("company"), "role": lead.get("title"), "url": lead.get("url"),
                    "url_aliases": [lead.get("url")], "dedup_key": key, "official_verified": False,
                    "source": "jobscout_feed", "source_exported_at": feed.get("exported_at")})
            if current and not dry_run and "submission_blocked" in TRANSITIONS.get(current["status"], set()):
                try:
                    current = transition(store, application_id, "submission_blocked", {"reason": reason}, current)
                except (NotionError, StateConflict):
                    raise
            if application_id:
                records[application_id] = current or {"id": application_id, "status": "submission_blocked", "payload": {"application_id": application_id, "company": lead["company"], "url": lead["url"]}}
        except Exception:
            count("application_processing_or_storage_failure")
            # The durable submitting snapshot is the guardrail if a receipt or
            # store write fails. No second call to submit is made.
            if current and current["status"] == "submitting" and not dry_run:
                try:
                    current = transition(store, application_id, "submission_unknown", {"reason": "submission_or_receipt_persistence_uncertain"}, current)
                except (NotionError, StateConflict):
                    raise
            if application_id and current:
                records[application_id] = current
    if not dry_run:
        for record in records.values():
            status = record["status"]
            summary["statuses"][status] = summary["statuses"].get(status, 0) + 1
    return summary
