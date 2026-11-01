"""Replay synthetic feed responses only; this is not a network collector."""
from .technical_feeds import parse_feed


def replay_feed_responses(fixtures, *, max_requests, max_bytes):
    if (not isinstance(fixtures, list) or len(fixtures) > 500
            or type(max_requests) is not int or not 0 <= max_requests <= 500
            or type(max_bytes) is not int or not 1 <= max_bytes <= 2_000_000
            or any(not isinstance(item, dict) for item in fixtures)):
        raise ValueError("invalid_fixture_limits")
    outcomes = []
    consumed = 0
    rate_limited = False
    bytes_used = 0
    byte_exhausted = False
    for fixture in fixtures:
        if rate_limited:
            outcomes.append({"state": "rate_limit_hold"})
            continue
        if fixture.get("permission") != "fixture_allowed":
            outcomes.append({"state": "permission_unavailable"})
            continue
        if consumed >= max_requests:
            outcomes.append({"state": "request_budget_exhausted"})
            continue
        if byte_exhausted:
            outcomes.append({"state": "byte_budget_exhausted"})
            continue
        consumed += 1
        body = fixture.get("body")
        if (not isinstance(body, bytes) or not isinstance(fixture.get("url"), str)
                or type(fixture.get("status")) is not int):
            outcomes.append({"state": "fixture_invalid"})
            continue
        if len(body) > max_bytes - bytes_used:
            byte_exhausted = True
            outcomes.append({"state": "byte_budget_exhausted"})
            continue
        bytes_used += len(body)
        if fixture.get("status") in (401, 403):
            outcomes.append({"state": "access_denied"})
            continue
        if fixture.get("status") == 429:
            rate_limited = True
            outcomes.append({"state": "rate_limited"})
            continue
        if fixture.get("status") != 200:
            outcomes.append({"state": "source_unavailable"})
            continue
        try:
            feed = parse_feed(body, fixture["url"])
        except ValueError:
            outcomes.append({"state": "feed_invalid"})
            continue
        outcomes.append({"state": "parsed_fixture", "feed": feed})
    return {"outcomes": outcomes, "fixture_responses_consumed": consumed, "fixture_bytes_processed": bytes_used,
            "provider_permission_verified": False, "transport_verified": False,
            "feed_completeness_verified": False}
