"""Canonical immutable records. Historical payload values are never rewritten."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value: Any) -> str:
    """SHA-256 of canonical JSON, distinct from legacy raw-message hashes."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(kind: str, *identity: Any) -> str:
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("Record kind is required")
    return kind + ":" + content_hash(list(identity))


def canonical_url(url: str) -> str:
    """Discard tracking, retain identity-bearing query parameters and path case."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Expected a public HTTP(S) URL")
    host = parts.hostname.lower()
    port = parts.port
    if port and not (parts.scheme.lower() == "https" and port == 443 or parts.scheme.lower() == "http" and port == 80):
        host += ":" + str(port)
    tracking = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer", "source", "trk", "trackingid"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in tracking]
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def normalized_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def application_aliases(company: str, requisition_id: str | None = None, url: str | None = None,
                        company_aliases: dict[str, str] | None = None) -> list[str]:
    """Emit all verified aliases; callers must check every alias before claiming."""
    employer = normalized_identity(company)
    if company_aliases:
        employer = normalized_identity(company_aliases.get(employer, employer))
    if not employer:
        raise ValueError("Employer identity is required")
    result = []
    if requisition_id:
        req = normalized_identity(requisition_id)
        if not req:
            raise ValueError("Invalid requisition identity")
        result.append("requisition:" + employer + ":" + req)
    if url:
        result.append("url:" + canonical_url(url))
    if not result:
        raise ValueError("Official URL or requisition identity is required")
    return sorted(set(result))


def make_record(kind: str, payload: Any, record_id: str | None = None,
                status: str = "observed", identity: Any = None) -> dict:
    if not isinstance(kind, str) or not kind.strip() or not isinstance(status, str) or not status.strip():
        raise ValueError("Nonempty kind and status are required")
    record_id = record_id or stable_id(kind, payload if identity is None else identity)
    if not isinstance(record_id, str) or not record_id or len(record_id) > 2000:
        raise ValueError("Invalid record identity")
    return {"id": record_id, "kind": kind, "payload": payload, "status": status, "hash": content_hash(payload)}


def validate_record(record: dict) -> dict:
    if not isinstance(record, dict) or any(k not in record for k in ("id", "kind", "payload", "status", "hash")):
        raise ValueError("Expected an immutable record envelope")
    canonical = make_record(record["kind"], record["payload"], record["id"], record["status"])
    if record["hash"] != canonical["hash"]:
        raise ValueError("Payload hash mismatch")
    return canonical


def display_title(record: dict) -> str:
    """Deterministic human title for the private CRM; never print in run logs."""
    kind, status = record["kind"], record["status"]
    payload = record.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    def text(*keys):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                value = value.get("name") or value.get("title")
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:160]
        return ""
    company = text("company", "company_name")
    role = text("role", "role_name", "job_title", "title", "name")
    if kind == "contacts":
        parts = [text("name", "contact_name") or "Contact", company, status]
    elif kind in {"jobs", "applications"}:
        parts = [role or kind.capitalize(), company, status]
    elif kind in {"agent_runs", "runs", "run_markers"}:
        parts = [text("lane") or "Run", text("date", "run_date", "day"), status]
    elif kind == "companies":
        parts = [text("name", "company") or "Company", status]
    else:
        parts = [kind, record["id"][:36], status]
    return " · ".join(part for part in parts if part)
