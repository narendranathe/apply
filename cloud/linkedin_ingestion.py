"""Read-only LinkedIn evidence normalization; this module has no send or IO API.

Member exports are historical evidence, even when imported immediately. The
central writer owns persistence, reconciliation and any operational projection.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from cloud.records import content_hash, make_record, stable_id

SCHEMA = "apply-linkedin-ingestion/v1"
_CATEGORIES = ("messages", "invitations", "connections")
_SUPPRESSION = ("opt_out", "suppressed", "no_contact", "company_opt_out")


def profile_url(value: str) -> str:
    """Canonical public profile alias, never a name or internal member ID."""
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if (parts.scheme.lower() not in {"https", "http"} or parts.username or parts.password
            or parts.port not in {None, 80, 443} or not re.fullmatch(r"(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com", host)
            or not re.fullmatch(r"/in/[^/]+/?", parts.path)):
        raise ValueError("Expected a LinkedIn public profile URL")
    # Profile URLs have no identity-bearing query parameters. Preserve path case.
    return urlunsplit(("https", "www.linkedin.com", parts.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class AccountIdentity:
    profile_url: str
    verified: bool
    evidence_ref: str
    profile_aliases: tuple[str, ...] = ()

    def urls(self) -> set[str]:
        if self.verified is not True or not self.evidence_ref.strip():
            raise ValueError("Verified export account identity and evidence are required")
        return {profile_url(url) for url in (self.profile_url, *self.profile_aliases)}


@dataclass(frozen=True)
class ExportSource:
    """Caller-verified archive metadata; file mtime is never an export time."""
    evidence_ref: str
    exported_at: str | None = None
    completeness: str = "unknown"
    covered_categories: tuple[str, ...] = ()
    date_order: str | None = None
    date_order_evidence_ref: str | None = None

    def validate(self) -> None:
        if not self.evidence_ref.strip() or self.completeness not in {"unknown", "partial", "complete_for_supplied_categories"}:
            raise ValueError("Explicit source evidence and completeness are required")
        if any(name not in _CATEGORIES for name in self.covered_categories):
            raise ValueError("Unsupported source category")
        if self.date_order not in {None, "MDY", "DMY"}:
            raise ValueError("Date order must be explicitly MDY or DMY")
        if self.date_order and not self.date_order_evidence_ref:
            raise ValueError("Numeric date order requires source evidence")


class LinkedInIngestionProvider(Protocol):
    """An approved provider returns evidence; it cannot send via this interface."""
    def capabilities(self) -> dict: ...
    def fetch_evidence(self, *, cursor: str | None = None, limit: int = 100) -> dict: ...


class LiveUnavailable:
    def capabilities(self) -> dict:
        return {"provider": "linkedin", "live_inbox": False, "invitations": False,
                "outbound": False, "reason": "authorized_live_connector_unavailable"}

    def fetch_evidence(self, *, cursor: str | None = None, limit: int = 100) -> dict:
        return {"schema": SCHEMA, "records": [], "cursor": cursor,
                "inbox_checked": False, "available": False,
                "reason": "authorized_live_connector_unavailable"}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold().lstrip("\ufeff"))


def _field(row: Mapping[str, str], *names: str) -> str:
    lookup = {_key(key): value for key, value in row.items()}
    return next((lookup[_key(name)] for name in names if _key(name) in lookup), "")


def _timestamp(raw: str, source: ExportSource) -> dict:
    result = {"raw": raw, "value": None, "precision": "source_text" if raw else "unknown",
              "timezone": None, "normalized_utc": None}
    text = raw.strip()
    if not text:
        return result
    explicit_zone = re.search(r"\s+(UTC|GMT)$", text, re.I)
    if explicit_zone:
        text = text[:explicit_zone.start()].strip() + "+00:00"
    text = re.sub(r"\s+([+-]\d\d:?\d\d)$", r"\1", text)
    parsed = None
    if re.match(r"^\d{4}-\d\d-\d\d(?:$|[ T])", text):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            result["precision"] = "date" if len(text) == 10 else (
                "fractional_seconds:" + str(len(re.search(r"\.([0-9]+)", text).group(1)))
                if re.search(r"\.([0-9]+)", text) else "second" if re.search(r"\d\d:\d\d:\d\d", text) else "minute")
        except ValueError:
            pass
    if parsed is None:
        formats = [("%d %b %Y", "date"), ("%d %B %Y", "date"), ("%b %d, %Y", "date")]
        if source.date_order:
            day = "%m/%d" if source.date_order == "MDY" else "%d/%m"
            formats += [(day + year + suffix, precision) for year in ("/%Y",)
                        for suffix, precision in (("", "date"), (", %I:%M %p", "minute"), (" %H:%M:%S", "second"))]
        # Two-digit years have no unambiguous century; retain them as source text.
        for fmt, precision in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                result["precision"] = precision
                break
            except ValueError:
                continue
    if parsed:
        result["value"] = parsed.date().isoformat() if result["precision"] == "date" else parsed.isoformat()
        if parsed.tzinfo:
            result["timezone"] = explicit_zone.group(1).upper() if explicit_zone else parsed.strftime("%z")
            result["normalized_utc"] = parsed.astimezone(timezone.utc).isoformat()
    return result


def _table(data: str | bytes, category: str) -> tuple[list[tuple[int, dict]], str, int]:
    """Find the CSV header after LinkedIn's Notes preamble; parse quoted cells."""
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    text = raw.decode("utf-8-sig")
    digest = hashlib.sha256(raw).hexdigest()
    required = {"messages": {"from", "to", "date", "content"},
                "invitations": {"from", "to", "sentat", "message"},
                "connections": {"firstname", "lastname", "connectedon"}}[category]
    lines = text.splitlines(keepends=True)
    header = None
    delimiter = None
    for index, line in enumerate(lines[:101]):
        for candidate in (",", "\t", ";"):
            cells = next(csv.reader([line], delimiter=candidate), [])
            if required.issubset({_key(cell) for cell in cells}):
                header, delimiter = index, candidate
                break
        if header is not None:
            break
    if header is None:
        raise ValueError("Unsupported or missing export header")
    reader = csv.DictReader(io.StringIO("".join(lines[header:])), delimiter=delimiter, strict=True)
    fields = reader.fieldnames or []
    if len({_key(field) for field in fields}) != len(fields):
        raise ValueError("Duplicate export columns")
    rows = []
    for row in reader:
        line = header + reader.line_num
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed export row")
        if any(value for value in row.values()):
            rows.append((line, row))
    return rows, digest, header + 1


def _url_list(value: str) -> tuple[list[str], bool]:
    if not value.strip():
        return [], False
    cells = re.split(r"\s*[,;|]\s*|\s+(?=https?://)", value.strip())
    try:
        return [profile_url(cell) for cell in cells if cell], False
    except (ValueError, TypeError):
        return [], True


def _contact_urls(payload: dict) -> set[str]:
    values = [payload.get(name) for name in ("profile_url", "linkedin_url", "linkedin", "contact_key", "url")]
    values += payload.get("profile_aliases", []) if isinstance(payload.get("profile_aliases"), list) else []
    result = set()
    for value in values:
        if isinstance(value, str):
            try:
                result.add(profile_url(value))
            except ValueError:
                pass
    return result


class _Contacts:
    def __init__(self, rows: Iterable[dict]):
        self.rows = {}
        self.by_url: dict[str, set[str]] = {}
        for row in rows:
            payload = row.get("payload", row)
            identity = row.get("id") or payload.get("contact_id")
            if not identity:
                continue
            self.rows.setdefault(identity, []).append(row)
            for url in _contact_urls(payload):
                self.by_url.setdefault(url, set()).add(identity)

    def participant(self, name: str, url: str | None) -> dict:
        matches = self.by_url.get(url, set()) if url else set()
        identity = next(iter(matches)) if len(matches) == 1 else stable_id("contact", url) if url and not matches else None
        return {"name": name, "profile_url": url, "contact_id": identity,
                "identity_verified": bool(url and identity), "identity_conflict": len(matches) > 1}

    def suppression(self, identity: str) -> dict:
        rows = self.rows.get(identity, [])
        flags = [{"record_id": row.get("id"), "flags": {key: row.get("payload", row).get(key)
                  for key in _SUPPRESSION if key in row.get("payload", row)}} for row in rows]
        suppressed = any(any(item["flags"].values()) for item in flags)
        # Absence in an incomplete historical context is never clearance.
        return {"status": "suppressed" if suppressed else "unknown", "sources": flags,
                "history_complete": False, "outbound_clearance": False}


def _direction(row: dict, sender_urls: list[str], recipient_urls: list[str], account_urls: set[str]) -> tuple[str, bool, str | None]:
    raw = _field(row, "Direction").strip().casefold()
    explicit = {"outgoing": "sent", "sent": "sent", "incoming": "received", "received": "received"}.get(raw)
    inferred = "sent" if set(sender_urls) & account_urls else "received" if set(recipient_urls) & account_urls else None
    if (len(sender_urls) > 1 or explicit == "sent" and sender_urls and not set(sender_urls) & account_urls
            or explicit == "received" and recipient_urls and not set(recipient_urls) & account_urls):
        return "unknown", False, "direction_conflict"
    if explicit and inferred and explicit != inferred:
        return "unknown", False, "direction_conflict"
    if raw and not explicit:
        return "unknown", False, "unsupported_direction"
    direction = explicit or inferred
    return (direction or "unknown", bool(direction), None if direction else "direction_unverified")


def _timestamp_key(timestamp: dict) -> str:
    return timestamp.get("normalized_utc") or timestamp.get("value") or timestamp.get("raw", "")


def exact_event_alias(payload: dict, account_url: str) -> str | None:
    """Exact message identity, also usable by verified legacy extraction adapters.

    The raw journal-envelope hash is not the message-content hash. No timestamp,
    direction or profile identity is inferred from an English journal heading.
    """
    if payload.get("direction") not in {"sent", "received", "not_applicable"}:
        return None
    event_type = payload.get("event_type")
    if event_type not in {"message", "invitation", "connection"}:
        return None
    timestamp = payload.get("timestamp")
    if not timestamp:
        timestamp = _timestamp(payload.get("occurred_at", ""), ExportSource("legacy_explicit_timestamp"))
    if event_type in {"message", "invitation"} and not timestamp.get("normalized_utc"):
        return None
    if event_type == "connection" and not timestamp.get("value"):
        return None
    when = _timestamp_key(timestamp)
    if not when:
        return None
    participants = payload.get("participants", [])
    urls = [person.get("profile_url") for person in participants if person.get("profile_url")]
    if not urls:
        urls = list(_contact_urls(payload))
    try:
        urls = sorted({profile_url(url) for url in urls} | {profile_url(account_url)})
    except ValueError:
        return None
    if len(urls) < 2 or any(not person.get("identity_verified") for person in participants):
        return None
    text = payload.get("text", payload.get("content"))
    if not isinstance(text, str) or event_type != "connection" and not text:
        return None
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return "linkedin-exact:" + content_hash([profile_url(account_url), event_type, urls,
                                             text_hash, when, payload["direction"]])


class _Dedup:
    def __init__(self, rows: Iterable[dict], account_url: str, account_urls: set[str]):
        self.exact: dict[str, set[str]] = {}
        self.existing = []
        self.account_urls = account_urls
        for row in rows:
            payload = row.get("payload", row)
            identity = row.get("id") or payload.get("event_id")
            if not identity:
                continue
            aliases = payload.get("ingestion_aliases", [])
            aliases = aliases if isinstance(aliases, list) else []
            computed = exact_event_alias(payload, account_url)
            for alias in [*aliases, *([computed] if computed else [])]:
                self.exact.setdefault(alias, set()).add(identity)
            if row.get("kind") in {"outreach_events", "conversation_events"}:
                self.existing.append((identity, payload))

    def possible(self, payload: dict) -> list[str]:
        urls = {p["profile_url"] for p in payload["participants"] if p["profile_url"]} - self.account_urls
        text = payload["text"]
        result = []
        for identity, old in self.existing:
            if old.get("event_type") and old["event_type"] != payload["event_type"]:
                continue
            old_time = old.get("timestamp") or _timestamp(old.get("occurred_at", ""), ExportSource("legacy_explicit_timestamp"))
            new_time = payload["timestamp"]
            if old_time.get("normalized_utc") and new_time.get("normalized_utc") and old_time["normalized_utc"] != new_time["normalized_utc"]:
                continue
            old_urls = (_contact_urls(old) | {p.get("profile_url") for p in old.get("participants", []) if p.get("profile_url")}) - self.account_urls
            body = old.get("text", old.get("content", ""))
            same_hash = payload["content_hash"] in (old.get("message_content_hash"), old.get("content_hash"))
            body_match = isinstance(body, str) and (text == body or len(text) >= 12 and text in body)
            if urls & old_urls and text and (same_hash or body_match):
                result.append(identity)
        return sorted(set(result))


def import_member_export(files: Mapping[str, str | bytes], *, account: AccountIdentity,
                         source: ExportSource, existing_records: Iterable[dict] = (),
                         existing_contacts: Iterable[dict] = ()) -> dict:
    """Normalize supplied official files; never read paths, make requests or write.

    The caller must pass *all* prior event and contact context, including legacy
    journal events, before the central writer imports results. Unknown aliases
    become reconciliation conflicts rather than new delivery records.
    """
    account_urls = account.urls()
    account_url = profile_url(account.profile_url)
    source.validate()
    contacts = _Contacts(existing_contacts)
    dedup = _Dedup(existing_records, account_url, account_urls)
    result = {"schema": SCHEMA, "records": [], "aliases": [], "duplicates": [], "conflicts": [],
              "source": {"provider": "linkedin_member_export", "evidence_ref": source.evidence_ref,
                         "exported_at": source.exported_at, "completeness": source.completeness,
                         "covered_categories": list(source.covered_categories), "supplied_categories": [],
                         "missing_categories": [], "historical_only": True, "inbox_checked": False,
                         "live_state_verified": False, "history_complete": False, "files": []}}
    parsed = []
    for filename, data in sorted(files.items()):
        category = filename.replace("\\", "/").rsplit("/", 1)[-1].casefold().removesuffix(".csv")
        if category not in _CATEGORIES or not filename.casefold().endswith(".csv"):
            result["conflicts"].append({"reason": "unsupported_export_file", "file": filename})
            continue
        try:
            rows, digest, header_line = _table(data, category)
        except (ValueError, UnicodeError, csv.Error):
            result["conflicts"].append({"reason": "malformed_or_unsupported_export", "file": filename})
            continue
        result["source"]["supplied_categories"].append(category)
        result["source"]["files"].append({"file": filename, "sha256": digest, "header_line": header_line, "rows": len(rows)})
        parsed.extend((category, filename, digest, line, row) for line, row in rows)
    result["source"]["missing_categories"] = sorted(set(_CATEGORIES) - set(result["source"]["supplied_categories"]))
    if result["source"]["missing_categories"] or result["conflicts"]:
        result["source"]["effective_completeness"] = "partial"
    else:
        result["source"]["effective_completeness"] = source.completeness
    observed_contacts, threads = {}, {}
    for category, filename, file_hash, line, row in parsed:
        issues = []
        event_type = category[:-1] if category != "connections" else "connection"
        if category == "connections":
            urls, invalid = _url_list(_field(row, "URL", "Profile URL"))
            participants = [contacts.participant((_field(row, "First Name") + " " + _field(row, "Last Name")).strip(), urls[0] if len(urls) == 1 else None)]
            raw_timestamp, text = _field(row, "Connected On"), ""
            direction, direction_verified, direction_issue = "not_applicable", True, None
        else:
            sender_urls, invalid_sender = _url_list(_field(row, "SENDER PROFILE URL", "inviterProfileUrl", "From Profile URL"))
            recipient_urls, invalid_recipient = _url_list(_field(row, "RECIPIENT PROFILE URLS", "inviteeProfileUrl", "To Profile URL"))
            invalid = invalid_sender or invalid_recipient
            participants = [contacts.participant(_field(row, "From"), sender_urls[0] if len(sender_urls) == 1 else None)]
            # A group name or name-only recipient is not an exact identity.
            names = _field(row, "To")
            if recipient_urls:
                participants.extend(contacts.participant(names if len(recipient_urls) == 1 else "", url) for url in recipient_urls)
            else:
                participants.append(contacts.participant(names, None))
            raw_timestamp = _field(row, "DATE") if category == "messages" else _field(row, "Sent At")
            text = _field(row, "CONTENT") if category == "messages" else _field(row, "Message")
            direction, direction_verified, direction_issue = _direction(row, sender_urls, recipient_urls, account_urls)
        if invalid:
            issues.append("invalid_participant_profile_url")
        if any(p["identity_conflict"] for p in participants):
            issues.append("canonical_contact_conflict")
        if any(not p["identity_verified"] for p in participants):
            issues.append("participant_identity_unverified")
        if direction_issue:
            issues.append(direction_issue)
        timestamp = _timestamp(raw_timestamp, source)
        if not timestamp["value"]:
            issues.append("timestamp_unparsed")
        if timestamp["precision"] != "date" and not timestamp["timezone"]:
            issues.append("timestamp_timezone_unknown")
        contact_ids = sorted({p["contact_id"] for p in participants if p["contact_id"] and p["profile_url"] not in account_urls})
        source_thread_id = _field(row, "CONVERSATION ID", "Thread ID")
        # Do not invent a conversation identity from a shared pair of names.
        thread_id = stable_id("linkedin-thread", account_url, source_thread_id) if source_thread_id else None
        payload = {"schema": SCHEMA, "event_type": event_type, "account_profile_url": account_url,
                   "contact_id": contact_ids[0] if len(contact_ids) == 1 else None, "contact_ids": contact_ids,
                   "thread_id": thread_id, "source_thread_id": source_thread_id or None, "participants": participants,
                   "direction": direction, "direction_verified": direction_verified, "direction_source": "export_field_or_exact_account_url",
                   "text": text, "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "occurred_at": raw_timestamp, "timestamp": timestamp, "subject": _field(row, "SUBJECT"),
                   "source_folder": _field(row, "FOLDER", "Folder"), "source_status": _field(row, "Status", "State"),
                   "historical_state": "connected_at_export" if category == "connections" else "invitation_recorded" if category == "invitations" else "message_recorded",
                   "historical_only": True, "live_state_verified": False, "history_complete": False,
                   "suppression": {identity: contacts.suppression(identity) for identity in contact_ids},
                   "source": {"provider": "linkedin_member_export", "file": filename, "file_sha256": file_hash,
                              "line": line, "row_sha256": content_hash(row), "exported_at": source.exported_at,
                              "evidence_ref": source.evidence_ref, "account_evidence_ref": account.evidence_ref,
                              "completeness": result["source"]["effective_completeness"]},
                   "source_row": row, "issues": sorted(set(issues))}
        alias = exact_event_alias(payload, account_url)
        row_alias = "linkedin-source-row:" + content_hash([account_url, event_type, row])
        aliases = [row_alias, *([alias] if alias else [])]
        matches = set().union(*(dedup.exact.get(value, set()) for value in aliases))
        if len(matches) > 1:
            result["conflicts"].append({"reason": "exact_alias_multiple_existing_events", "existing_ids": sorted(matches), "observation": payload})
            continue
        if matches:
            identity = next(iter(matches))
            old_threads = {old.get("source_thread_id") for old_id, old in dedup.existing
                           if old_id == identity and old.get("source_thread_id")}
            if source_thread_id and old_threads and source_thread_id not in old_threads:
                result["conflicts"].append({"reason": "exact_alias_conflicting_native_threads",
                                            "existing_ids": [identity], "observation": payload})
                continue
            result["aliases"].extend({"alias": value, "event_id": identity, "source_row_sha256": payload["source"]["row_sha256"]} for value in aliases)
            result["duplicates"].append({"event_id": identity, "file": filename, "line": line, "reason": "exact_existing_event"})
            continue
        possible = dedup.possible(payload)
        if possible:
            result["conflicts"].append({"reason": "possible_existing_event_requires_reconciliation", "existing_ids": possible, "observation": payload})
            continue
        identity = stable_id("linkedin-export-event", account_url, alias or [event_type, row])
        payload["event_id"] = identity
        payload["ingestion_aliases"] = aliases
        result["records"].append(make_record("conversation_events", payload, identity, "historical_export" if not issues else "needs_reconciliation"))
        for value in aliases:
            dedup.exact.setdefault(value, set()).add(identity)
            result["aliases"].append({"alias": value, "event_id": identity, "source_row_sha256": payload["source"]["row_sha256"]})
        dedup.existing.append((identity, payload))
        for person in participants:
            if person["contact_id"] not in contact_ids:
                continue
            observation_id = stable_id("linkedin-contact-observation", identity, person["contact_id"])
            observed_contacts[observation_id] = make_record("contact_observations", {
                "contact_id": person["contact_id"], "profile_url": person["profile_url"], "name": person["name"],
                "event_id": identity, "historical_only": True, "live_state_verified": False,
                "identity_verified": person["identity_verified"], "history_complete": False,
                "state": payload["historical_state"], "source": payload["source"],
                **({"suppressed": True} if payload["suppression"][person["contact_id"]]["status"] == "suppressed" else {}),
                "suppression": payload["suppression"][person["contact_id"]]}, observation_id, "historical_export")
        if thread_id:
            threads.setdefault(thread_id, []).append(payload)
    result["records"].extend(observed_contacts.values())
    for thread_id, events in sorted(threads.items()):
        timed = [event for event in events if event["timestamp"]["normalized_utc"] and event["event_type"] == "message"]
        last = max(timed, key=lambda event: event["timestamp"]["normalized_utc"], default=None)
        received = [event for event in timed if event["direction"] == "received" and event["direction_verified"]]
        reply = max(received, key=lambda event: event["timestamp"]["normalized_utc"], default=None)
        payload = {"thread_id": thread_id, "contact_ids": sorted({identity for event in events for identity in event["contact_ids"]}),
                   "event_ids": sorted(event["event_id"] for event in events), "source_thread_id": events[0]["source_thread_id"],
                   "last_message_at": last["occurred_at"] if last else None, "last_reply_at": reply["occurred_at"] if reply else None,
                   "last_message_timestamp": last["timestamp"] if last else None, "last_reply_timestamp": reply["timestamp"] if reply else None,
                   "historical_only": True, "live_state_verified": False, "history_complete": False,
                   "current_state": "unknown", "source": result["source"]}
        observation_id = stable_id("linkedin-thread-observation", thread_id, payload["event_ids"])
        result["records"].append(make_record("conversation_observations", payload, observation_id, "historical_export"))
    return result
