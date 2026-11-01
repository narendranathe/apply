"""URL identity must not collapse malformed or undecodable query bytes."""
import unittest

from cloud.technical_records import canonical_technical_url


class TechnicalUrlEncodingTests(unittest.TestCase):
    def test_malformed_percent_escape_is_not_an_alias_of_encoded_percent(self):
        for url in ("https://example.org/p?id=%", "https://example.org/p?id=%2",
                    "https://example.org/p?id=%GG", "https://example.org/p?%broken=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_technical_url(url)
        self.assertEqual(canonical_technical_url("https://example.org/p?id=%25"),
                         "https://example.org/p?id=%25")

    def test_undecodable_query_bytes_do_not_collapse_to_replacement_character(self):
        for url in ("https://example.org/p?id=%FF", "https://example.org/p?id=%FE",
                    "https://example.org/p?%C0%AF=value"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_technical_url(url)
        self.assertEqual(canonical_technical_url("https://example.org/p?id=%E2%82%AC"),
                         "https://example.org/p?id=%E2%82%AC")


if __name__ == "__main__":
    unittest.main()
