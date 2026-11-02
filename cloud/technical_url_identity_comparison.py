"""Offline identity comparison only; no migration, network, or authority.

The v2 candidate retains query parameter order after the legacy tracking
parameters are removed. Equivalence is syntactic and does not establish that
the URLs identify the same resource or authorize a change to persisted keys.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .technical_records import canonical_technical_url


def _identities(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("invalid_url_identity")
    try:
        legacy = canonical_technical_url(url)
        original = urlsplit(url)
        base = urlsplit(legacy)
        query = [(key, value) for key, value in parse_qsl(original.query, keep_blank_values=True, errors="strict")
                 if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
        candidate = urlunsplit((base.scheme, base.netloc, base.path, urlencode(query), ""))
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid_url_identity") from None
    return legacy, candidate


def compare_url_identities(left, right):
    """Return fixed comparison results without disclosing either URL."""
    first_legacy, first_candidate = _identities(left)
    second_legacy, second_candidate = _identities(right)
    legacy_equal = first_legacy == second_legacy
    candidate_equal = first_candidate == second_candidate
    if legacy_equal and not candidate_equal:
        outcome = "legacy_collision_reconciliation_required"
    elif candidate_equal:
        outcome = "same_normalized_candidate"
    else:
        outcome = "distinct_normalized_candidates"
    return {"outcome": outcome, "legacy_v1_equal": legacy_equal, "candidate_v2_equal": candidate_equal,
            "candidate_version": "query-order-v2", "migration_authorized": False,
            "identity_independently_verified": False}
