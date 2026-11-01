"""Public technical-record identity and timestamp boundary tests."""
import unittest

from cloud.technical_records import canonical_technical_url, make_technical_record
from tests.test_technical_records import sample


class TechnicalIdentityBoundaryTests(unittest.TestCase):
    def test_empty_url_userinfo_is_not_silently_discarded(self):
        for url in ("https://@example.org/post", "https://:@example.org/post"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_technical_url(url)
        self.assertEqual(canonical_technical_url("https://example.org/post"), "https://example.org/post")

    def test_unrepresentable_utc_timestamps_cannot_enter_technical_records(self):
        for timestamp in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                make_technical_record("technical_items", {**sample(), "first_seen_at": timestamp,
                    "observed_at": timestamp, "source_published_at": None})


if __name__ == "__main__":
    unittest.main()
