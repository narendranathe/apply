"""Pure immutable technical intelligence record envelopes; no storage or effects."""
from copy import deepcopy
from datetime import datetime
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cloud.records import make_record, stable_id, validate_record

SCHEMA = "apply-technical-record/v1"
TECHNICAL_KINDS = frozenset({
    "technical_sources", "technical_items", "engineering_observations", "technical_signals",
    "technical_hypotheses", "engineering_opportunities", "contact_topic_relevances",
    "engagement_intents", "engagement_outcomes", "content_drafts", "content_revisions",
    "publisher_intents", "publisher_receipts", "source_cursors", "technical_next_actions",
})
REQUIRED = frozenset({"entity_id", "revision", "source_url", "source_identity", "author",
                      "author_verified", "first_seen_at", "source_published_at", "observed_at",
                      "content_hash", "freshness", "confidentiality", "evidence_refs", "status", "actor"})


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _identifier(value):
    # Bounded identifier syntax reduces accidental prose; it cannot detect secrets.
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value))


def _technical_identifier(value):
    # Shape is not proof that hashed input or the namespace contains no secret.
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}:[0-9a-f]{64}", value))


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Expected timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Expected timezone-aware timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Expected timezone-aware timestamp")
    return parsed


def canonical_technical_url(url):
    """Conservative technical-source identity; fetch safety is a separate gate."""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Expected a public HTTP(S) URL")
        port = parts.port
    except (AttributeError, ValueError) as error:
        raise ValueError("Expected a public HTTP(S) URL") from error
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port and not (parts.scheme.lower() == "https" and port == 443 or parts.scheme.lower() == "http" and port == 80):
        host += ":" + str(port)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def make_technical_record(kind, payload):
    if not isinstance(kind, str) or kind not in TECHNICAL_KINDS:
        raise ValueError("Unknown technical record kind")
    if not isinstance(payload, dict) or not REQUIRED.issubset(payload):
        raise ValueError("Incomplete technical provenance")
    data = deepcopy(payload)
    if data.get("schema", SCHEMA) != SCHEMA:
        raise ValueError("Unsupported technical schema")
    data["schema"] = SCHEMA
    if any(not _text(data[key]) for key in ("entity_id", "source_identity", "status", "actor")):
        raise ValueError("Invalid technical identity or actor")
    if not _technical_identifier(data["entity_id"]) or not _technical_identifier(data["source_identity"]):
        raise ValueError("Invalid technical identifier")
    if not isinstance(data["revision"], int) or isinstance(data["revision"], bool) or data["revision"] < 1:
        raise ValueError("Invalid technical revision")
    if not isinstance(data["author_verified"], bool) or (
        (data["author_verified"] and not _text(data["author"])) or
        (not data["author_verified"] and data["author"] is not None)
    ):
        raise ValueError("Author must be verified or unknown")
    seen, observed = _timestamp(data["first_seen_at"]), _timestamp(data["observed_at"])
    published = _timestamp(data["source_published_at"]) if data["source_published_at"] is not None else None
    if seen > observed or (published and published > observed):
        raise ValueError("Source time is later than observation")
    if not isinstance(data["content_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["content_hash"]):
        raise ValueError("Invalid content hash")
    if data["freshness"] not in ("fresh", "stale", "unknown", "historical"):
        raise ValueError("Invalid freshness")
    if data["confidentiality"] not in ("public", "private", "restricted"):
        raise ValueError("Invalid confidentiality")
    refs = data["evidence_refs"]
    if not isinstance(refs, list) or any(not _identifier(ref) for ref in refs) or len(refs) != len(set(refs)):
        raise ValueError("Invalid evidence references")
    if data["source_url"] is None:
        if data["source_identity"] not in refs:
            raise ValueError("Internal origin requires its evidence reference")
    else:
        data["source_url"] = canonical_technical_url(data["source_url"])
    if kind == "publisher_receipts" and data["status"] == "published_verified":
        raise ValueError("Publication verification requires a dedicated receipt verifier")
    identity = stable_id(kind, data["entity_id"], data["revision"])
    return make_record(kind, data, identity, data["status"])


def validate_technical_record(record):
    original = validate_record(record)
    if make_technical_record(original["kind"], original["payload"]) != original:
        raise ValueError("Invalid technical record")
    return original


def validate_technical_aliases(records):
    """Detect exact source aliases among supplied complete records, per family.

    This is not a lock or a durable reservation: a serialized writer must check
    the fresh authoritative set and reserve aliases before accepting records.
    """
    owners = {}
    for raw in records:
        if not isinstance(raw, dict) or raw.get("kind") not in {"technical_sources", "technical_items"}:
            continue
        row = validate_technical_record(raw)
        data = row["payload"]
        aliases = [(row["kind"], "source_identity", data["source_identity"])]
        if data["source_url"] is not None:
            aliases.append((row["kind"], "source_url", data["source_url"]))
        for alias in aliases:
            previous = owners.setdefault(alias, data["entity_id"])
            if previous != data["entity_id"]:
                raise ValueError("Conflicting technical source alias")
