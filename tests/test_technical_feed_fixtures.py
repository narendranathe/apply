"""Synthetic feed-response replay: no transport, grants, or provider effects."""
import unittest
from unittest.mock import patch

from cloud.technical_feed_fixtures import replay_feed_responses


class TechnicalFeedFixtureTests(unittest.TestCase):
    def test_malformed_fixture_is_unavailable_without_exposing_payload(self):
        base = {"url": "https://example.test/feed", "permission": "fixture_allowed", "status": 200,
                "body": b'<rss><channel/></rss>'}
        malformed = [{**base, "body": "secret unexpected payload"},
                     {key: value for key, value in base.items() if key != "body"},
                     {key: value for key, value in base.items() if key != "url"},
                     {**base, "status": True}]
        result = replay_feed_responses(malformed, max_requests=4, max_bytes=1000)
        self.assertEqual(result["outcomes"], [{"state": "fixture_invalid"}] * 4)
        self.assertNotIn("secret unexpected payload", str(result))

    def test_invalid_limits_are_rejected_even_for_empty_fixture_batches(self):
        for requests, size in ((-1, 100), (True, 100), (1, 0), (1, 2_000_001)):
            with self.subTest(requests=requests, size=size), self.assertRaises(ValueError):
                replay_feed_responses([], max_requests=requests, max_bytes=size)

    def test_unavailable_or_malformed_responses_are_reported_without_private_text(self):
        fixture = {"url": "https://example.test/feed", "permission": "fixture_allowed", "status": 503,
                   "body": b'provider internal error'}
        invalid = {**fixture, "status": 200, "body": b'invalid feed text'}
        result = replay_feed_responses([fixture, invalid], max_requests=2, max_bytes=1000)
        self.assertEqual([x["state"] for x in result["outcomes"]], ["source_unavailable", "feed_invalid"])
        self.assertNotIn("provider internal error", str(result))
        self.assertNotIn("invalid feed text", str(result))

    def test_oversized_fixture_body_is_not_parsed_and_exhausts_byte_budget(self):
        fixture = {"url": "https://example.test/feed", "permission": "fixture_allowed", "status": 200,
                   "body": b'<rss><channel/></rss>'}
        result = replay_feed_responses([fixture, fixture], max_requests=2, max_bytes=len(fixture["body"]) - 1)
        self.assertEqual([x["state"] for x in result["outcomes"]],
                         ["byte_budget_exhausted", "byte_budget_exhausted"])
        self.assertEqual(result["fixture_responses_consumed"], 1)

    def test_denied_and_rate_limited_responses_are_not_parsed_or_retried(self):
        fixture = {"url": "https://example.test/feed", "permission": "fixture_allowed", "status": 403,
                   "body": b'secret error text'}
        limited = {**fixture, "status": 429}
        success = {**fixture, "status": 200, "body": b'<rss><channel/></rss>'}
        result = replay_feed_responses([fixture, limited, success], max_requests=3, max_bytes=1000)
        self.assertEqual([x["state"] for x in result["outcomes"]],
                         ["access_denied", "rate_limited", "rate_limit_hold"])
        self.assertEqual(result["fixture_responses_consumed"], 2)
        self.assertNotIn("secret error text", str(result))

    def test_request_budget_stops_before_consuming_later_fixtures(self):
        fixture = {"url": "https://example.test/feed", "permission": "fixture_allowed", "status": 200,
                   "body": b'<rss><channel/></rss>'}
        result = replay_feed_responses([fixture, fixture], max_requests=1, max_bytes=1000)
        self.assertEqual([x["state"] for x in result["outcomes"]], ["parsed_fixture", "request_budget_exhausted"])
        self.assertEqual(result["fixture_responses_consumed"], 1)

    def test_missing_fixture_permission_has_no_response_or_claimed_access(self):
        fixture = {"url": "https://example.test/feed", "permission": "unknown", "status": 200,
                   "body": b'<rss><channel/></rss>'}
        result = replay_feed_responses([fixture], max_requests=1, max_bytes=1000)
        self.assertEqual(result["outcomes"], [{"state": "permission_unavailable"}])
        self.assertEqual(result["fixture_responses_consumed"], 0)
        self.assertFalse(result["provider_permission_verified"])

    def test_success_replays_feed_with_an_explicit_unverified_boundary(self):
        fixture = {"url": "https://example.test/feed", "permission": "fixture_allowed", "status": 200,
                   "body": b'<rss><channel><item><link>https://example.test/p</link></item></channel></rss>'}
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = replay_feed_responses([fixture], max_requests=1, max_bytes=1000)
        self.assertEqual(result["outcomes"][0]["state"], "parsed_fixture")
        self.assertEqual(result["outcomes"][0]["feed"]["items"][0]["url"], "https://example.test/p")
        self.assertEqual(result["fixture_responses_consumed"], 1)
        self.assertFalse(result["provider_permission_verified"])
        self.assertFalse(result["transport_verified"])
        self.assertFalse(result["feed_completeness_verified"])


if __name__ == "__main__":
    unittest.main()
