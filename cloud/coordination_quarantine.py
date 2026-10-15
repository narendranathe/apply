"""Permanent, contact-scoped holds for uncertain legacy coordination work.

These records establish neither a delivery outcome nor completed work. They
have no expiry or observation-driven resolution. This module performs no I/O.
"""
from copy import deepcopy
from datetime import datetime
import re
from urllib.parse import urlsplit

from .linkedin_ingestion import _contact_urls, profile_url
from .records import make_record, validate_record

SCHEMA = "apply-coordination-quarantine/v1"
PREFIX = "coordination-quarantine:"
IDENTITIES = {"contact_ids", "source_notion_ids", "profile_urls", "company_ids"}
FIELDS = {"schema", "active", "classification", "identity", "provenance", "observed_at", "evidence_refs"}
PROVENANCE = {"claim_sha256", "task_sha256", "resource_sha256"}


class QuarantineError(ValueError):
    def __init__(self):
        super().__init__("coordination_quarantine_invalid")


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def _stored_profile(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in {"www.linkedin.com", "linkedin.com"}
                or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment
                or not re.fullmatch(r"/in/[^/]+/?", parsed.path)):
            return None
    except ValueError:
        return None
    return "https://www.linkedin.com" + parsed.path.rstrip("/")


def _profile(value):
    if not isinstance(value, str):
        return None
    try:
        return profile_url(value)
    except ValueError:
        return None


def _notion(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
        return None
    normalized = value.replace("-", "").lower()
    return normalized if len(normalized) == 32 else None


def _valid_payload(payload):
    try:
        if not isinstance(payload, dict) or set(payload) != FIELDS:
            return False
        if payload["schema"] != SCHEMA or payload["active"] is not True:
            return False
        classification, identity = payload["classification"], payload["identity"]
        if classification not in {"effect_unknown", "unstarted_unverified"}:
            return False
        if not isinstance(identity, dict) or not identity or set(identity) - IDENTITIES:
            return False
        for key, values in identity.items():
            if (not isinstance(values, list) or not values or not all(_text(v) for v in values)
                    or len(values) != len(set(values))):
                return False
            if key == "profile_urls" and not all(_stored_profile(v) for v in values):
                return False
            if key == "source_notion_ids" and not all(_notion(v) for v in values):
                return False
        if classification == "unstarted_unverified":
            if set(identity) != {"company_ids"}:
                return False
        elif not set(identity) & {"contact_ids", "source_notion_ids", "profile_urls"} or "company_ids" in identity:
            return False
        provenance = payload["provenance"]
        if (not isinstance(provenance, dict) or set(provenance) != PROVENANCE
                or not all(isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v) for v in provenance.values())):
            return False
        observed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
        refs = payload["evidence_refs"]
        return (observed.tzinfo is not None and isinstance(refs, list) and bool(refs)
                and all(_text(v) for v in refs) and len(refs) == len(set(refs)))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def make_quarantine(payload):
    """Construct evidence only; the caller must establish a stopped boundary."""
    if not _valid_payload(payload):
        raise QuarantineError()
    status = "unstarted_unverified" if payload["classification"] == "unstarted_unverified" else "needs_reconciliation"
    return make_record("exceptions", deepcopy(payload), PREFIX + payload["provenance"]["claim_sha256"], status)


def validate_quarantine(record):
    try:
        canonical = validate_record(record)
        expected = make_quarantine(canonical["payload"])
        if canonical != expected:
            raise QuarantineError()
        return canonical
    except (ValueError, TypeError, KeyError, AttributeError):
        raise QuarantineError() from None


def _candidate(row):
    if not isinstance(row, dict):
        return False
    payload = row.get("payload")
    schema = payload.get("schema") if isinstance(payload, dict) else None
    return (isinstance(row.get("id"), str) and row["id"].startswith(PREFIX)
            or isinstance(schema, str) and schema.startswith("apply-coordination-quarantine/"))


def _aliases(identity):
    if not isinstance(identity, dict):
        return set()
    result = set()
    for key in ("contact_ids", "source_notion_ids", "profile_urls"):
        values = identity.get(key, [])
        # A malformed scalar still identifies the contact that must be held.
        values = values if isinstance(values, list) else [values]
        for value in values:
            normalized = _profile(value) if key == "profile_urls" else _notion(value) if key == "source_notion_ids" else value
            if _text(normalized):
                result.add((key, normalized))
    return result


class ContactQuarantines:
    def __init__(self, records):
        self.entries = []
        for row in records:
            if not _candidate(row):
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            try:
                validate_quarantine(row)
                valid = True
            except QuarantineError:
                valid = False
            if valid and payload["classification"] == "unstarted_unverified":
                continue  # A company research reservation is no contact ban.
            aliases = _aliases(payload.get("identity"))
            if not aliases:
                # Its scope cannot be recovered safely; stop before any effect.
                raise QuarantineError()
            self.entries.append((row.get("id"), aliases, valid))

    def hold_for(self, contact_records, *, contact_ids=()):
        aliases = _aliases({"contact_ids": list(contact_ids)})
        for row in contact_records:
            payload = row.get("payload", {})
            aliases |= _aliases({"contact_ids": [row.get("id"), payload.get("contact_id")],
                                 "source_notion_ids": [payload.get("source_notion_id")]})
            # Share every recognized public-profile field/alias and URL rule
            # with member-export ingestion, including tracking and locale forms.
            aliases.update(("profile_urls", value) for value in _contact_urls(payload))
        matched = [(identity, valid) for identity, scope, valid in self.entries if aliases & scope]
        if not matched:
            return None
        return {"reason": "coordination_effect_unknown" if all(valid for _, valid in matched) else "coordination_quarantine_invalid",
                "evidence_refs": sorted({identity for identity, _ in matched if isinstance(identity, str)})}
