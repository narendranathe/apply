"""Offline versioned URL identity comparison; no records are migrated."""
import unittest
from unittest.mock import patch

from cloud.technical_url_identity_comparison import compare_url_identities


class TechnicalUrlIdentityComparisonTests(unittest.TestCase):
    def test_legacy_collision_requires_reconciliation_when_parameter_order_differs(self):
        result = compare_url_identities("https://example.org/p?a=1&b=2", "https://example.org/p?b=2&a=1")
        self.assertEqual(result["outcome"], "legacy_collision_reconciliation_required")
        self.assertTrue(result["legacy_v1_equal"])
        self.assertFalse(result["candidate_v2_equal"])
        self.assertFalse(result["migration_authorized"])
        self.assertFalse(result["identity_independently_verified"])

    def test_tracking_parameters_do_not_change_candidate_identity(self):
        result = compare_url_identities("https://example.org/p?utm_source=x&a=1&fbclid=z&b=2",
                                        "https://example.org/p?a=1&b=2&gclid=y")
        self.assertEqual(result["outcome"], "same_normalized_candidate")
        self.assertTrue(result["candidate_v2_equal"])

    def test_repeated_values_retain_order_and_distinct_urls_are_not_collisions(self):
        result = compare_url_identities("https://example.org/p?a=1&a=2", "https://example.org/p?a=2&a=1")
        self.assertEqual(result["outcome"], "distinct_normalized_candidates")
        self.assertFalse(result["legacy_v1_equal"])
        self.assertFalse(result["candidate_v2_equal"])

    def test_invalid_inputs_fail_closed_without_echoing_the_url(self):
        for invalid in ("https://user:secret@example.org/p", "https://example.org/p?a=%FF", None):
            with self.subTest(kind=type(invalid).__name__), self.assertRaisesRegex(ValueError, "invalid_url_identity") as error:
                compare_url_identities(invalid, "https://example.org/p")
            self.assertNotIn("secret", str(error.exception))

    def test_comparison_has_no_network_effects(self):
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = compare_url_identities("https://example.org/p?a=1", "https://example.org/p?a=2")
        self.assertEqual(result["outcome"], "distinct_normalized_candidates")
        self.assertNotIn("example.org", str(result))


if __name__ == "__main__":
    unittest.main()
