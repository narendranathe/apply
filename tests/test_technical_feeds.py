"""Offline feed parser contract: untrusted synthetic bytes only."""
import unittest
from unittest.mock import patch

from cloud.technical_feeds import parse_feed


class TechnicalFeedTests(unittest.TestCase):
    def test_unrepresentable_utc_dates_are_retained_as_unknown(self):
        feed = b'''<rss><channel>
          <item><link>https://example.test/early</link><pubDate>0001-01-01T00:00:00+14:00</pubDate></item>
          <item><link>https://example.test/late</link><pubDate>9999-12-31T23:59:59-14:00</pubDate></item>
        </channel></rss>'''
        early, late = parse_feed(feed, "https://example.test/feed")["items"]
        self.assertIsNone(early["published_at"])
        self.assertIsNone(late["published_at"])
        self.assertEqual(early["source_published_at_raw"], "0001-01-01T00:00:00+14:00")
        self.assertEqual(late["source_published_at_raw"], "9999-12-31T23:59:59-14:00")

    def test_conflicting_authors_across_revisions_remain_ambiguous(self):
        feed = b'''<rss><channel>
          <item><title>T</title><link>https://example.test/post</link><description>original</description><author>Alice</author></item>
          <item><title>T</title><link>https://example.test/post</link><description>revised</description><author>Bob</author></item>
        </channel></rss>'''
        first, second = parse_feed(feed, "https://example.test/feed")["items"]
        self.assertNotEqual(first["feed_projection_sha256"], second["feed_projection_sha256"])
        self.assertTrue(first["authorship_ambiguous"])
        self.assertTrue(second["authorship_ambiguous"])
        self.assertFalse(first["author_verified"])
        self.assertFalse(second["author_verified"])

    def test_atom_content_is_used_when_summary_is_absent(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
          <link href="https://example.test/post"/><content type="text">An engineering insight.</content>
        </entry></feed>'''
        item, = parse_feed(feed, "https://example.test/feed")["items"]
        self.assertEqual(item["excerpt"], "An engineering insight.")

    def test_feed_origin_is_bounded_and_has_no_control_characters(self):
        feed = b'<rss><channel/></rss>'
        for origin in ('https://example.test/' + 'a' * 2100, 'https://exa\nmple.test/feed'):
            with self.subTest(origin=origin[:30]), self.assertRaisesRegex(ValueError, 'invalid_feed_url'):
                parse_feed(feed, origin)

    def test_xml_base_is_not_silently_resolved_against_the_wrong_host(self):
        feed = b'<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://other.example/"><entry><link href="post"/></entry></feed>'
        with self.assertRaisesRegex(ValueError, "unsupported_xml_base"):
            parse_feed(feed, "https://example.test/feed")

    def test_offline_replay_does_not_open_network(self):
        feed = b'<rss><channel><item><title>T</title><link>https://example.test/p</link></item></channel></rss>'
        with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            first = parse_feed(feed, "https://example.test/feed")
            self.assertEqual(parse_feed(feed, "https://example.test/feed"), first)

    def test_conflicting_alternate_urls_are_withheld_not_guessed(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
          <link href="https://example.test/one"/><link href="https://example.test/two"/>
        </entry></feed>'''
        result = parse_feed(feed, "https://example.test/feed")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["withheld_count"], 1)

    def test_multiple_atom_authors_are_claims_and_never_silently_verified(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
          <link href="https://example.test/post"/><author><name>Alice</name></author>
          <author><name>Bob</name></author></entry></feed>'''
        item, = parse_feed(feed, "https://example.test/feed")["items"]
        self.assertEqual(item["author_claims"], ["Alice", "Bob"])
        self.assertTrue(item["authorship_ambiguous"])
        self.assertFalse(item["author_verified"])

    def test_unsupported_xml_and_malformed_feed_are_not_empty_success(self):
        for raw, reason in ((b"<html><body>not a feed</body></html>", "unsupported_feed_format"),
                            (b"<rss><channel>", "invalid_feed_xml")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                parse_feed(raw, "https://example.test/feed")

    def test_missing_or_invalid_article_links_are_withheld_and_dates_remain_unknown(self):
        feed = b'''<rss><channel>
          <item><title>Missing</title></item>
          <item><title>Unsafe</title><link>file:///tmp/private</link></item>
          <item><title>Fragment only</title><link>#same-page</link></item>
          <item><title>Unknown date</title><link>https://example.test/p</link><pubDate>2026-10-30</pubDate></item>
        </channel></rss>'''
        result = parse_feed(feed, "https://example.test/feed")
        item, = result["items"]
        self.assertEqual(result["withheld_count"], 3)
        self.assertIsNone(item["published_at"])
        self.assertEqual(item["source_published_at_raw"], "2026-10-30")
        self.assertEqual(item["url"], "https://example.test/p")

    def test_excerpt_is_bounded_but_edit_beyond_excerpt_changes_digest(self):
        text = "a" * 800
        feed = (f'<rss><channel><item><title>T</title><link>https://example.test/a</link><description>{text}A</description></item>'
                f'<item><title>T</title><link>https://example.test/a</link><description>{text}B</description></item></channel></rss>').encode()
        first, second = parse_feed(feed, "https://example.test/feed")["items"]
        self.assertLessEqual(len(first["excerpt"]), 400)
        self.assertEqual(first["excerpt"], second["excerpt"])
        self.assertNotEqual(first["feed_projection_sha256"], second["feed_projection_sha256"])
        self.assertNotIn(text, str(first))

    def test_duplicate_aliases_merge_ambiguous_authors_but_edits_make_revisions(self):
        feed = b'''<rss><channel>
          <item><title>CDC</title><link>https://example.test/post?id=9&amp;utm_source=one</link><description>original</description><author>Alice</author></item>
          <item><title>CDC</title><link>https://example.test/post?id=9&amp;utm_source=two</link><description>original</description><author>Bob</author></item>
          <item><title>CDC</title><link>https://example.test/post?id=9</link><description>revised</description><author>Alice</author></item>
        </channel></rss>'''
        result = parse_feed(feed, "https://example.test/feed")
        self.assertEqual(len(result["items"]), 2)
        first, second = result["items"]
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(first["author_claims"], ["Alice", "Bob"])
        self.assertTrue(first["authorship_ambiguous"])
        self.assertFalse(first["author_verified"])
        self.assertNotEqual(first["feed_projection_sha256"], second["feed_projection_sha256"])
        self.assertEqual(len(first["feed_projection_sha256"]), 64)
        self.assertEqual(result["duplicate_count"], 1)

    def test_feed_byte_item_and_encoding_limits_fail_closed(self):
        oversized = b"<rss><channel>" + b"x" * 256 + b"</channel></rss>"
        with self.assertRaisesRegex(ValueError, "feed_too_large"):
            parse_feed(oversized, "https://example.test/feed", max_bytes=100)
        items = b"<rss><channel>" + b"<item><link>https://example.test/x</link></item>" * 3 + b"</channel></rss>"
        with self.assertRaisesRegex(ValueError, "feed_item_limit_exceeded"):
            parse_feed(items, "https://example.test/feed", max_items=2)
        with self.assertRaisesRegex(ValueError, "unsupported_feed_encoding"):
            parse_feed('<rss><channel><title>日本語</title></channel></rss>'.encode('utf-16'), "https://example.test/feed")
        with self.assertRaisesRegex(ValueError, "unsupported_feed_encoding"):
            parse_feed(b'<?xml version="1.0" encoding="ISO-8859-1"?><rss/>', "https://example.test/feed")

    def test_doctype_and_entities_rejected_without_expansion(self):
        xml = b'<!DOCTYPE rss [<!ENTITY boom "synthetic private text">]><rss><channel><title>&boom;</title></channel></rss>'
        with self.assertRaisesRegex(ValueError, "unsafe_xml"):
            parse_feed(xml, "https://example.test/feed")

    def test_atom_multilingual_and_relative_link_preserves_source_dates(self):
        feed = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>数据可靠性</title>
        <link rel="alternate" href="/posts/α?ref=identity&amp;utm_campaign=test" />
        <summary>Observability 日本語</summary><author><name>Engineer</name></author>
        <published>2026-10-28T08:30:00-05:00</published></entry></feed>'''.encode()
        result = parse_feed(feed, "https://blog.example.test/feed")
        item, = result["items"]
        self.assertEqual(item["url"], "https://blog.example.test/posts/α?ref=identity")
        self.assertEqual(item["title"], "数据可靠性")
        self.assertEqual(item["published_at"], "2026-10-28T13:30:00+00:00")
        self.assertEqual(item["author_claims"], ["Engineer"])
        self.assertEqual(item["excerpt"], "Observability 日本語")

    def test_rss_item_has_canonical_identity_and_unverified_attribution(self):
        feed = b'''<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><item><title>Checkpointing</title>
        <link>https://blog.example.test/posts/1?utm_source=feed&amp;id=7</link>
        <description>Recover from an interrupted job.</description>
        <author>Example Author</author>
        <pubDate>Wed, 28 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'''
        result = parse_feed(feed, "https://blog.example.test/feed")
        item, = result["items"]
        self.assertEqual(item["url"], "https://blog.example.test/posts/1?id=7")
        self.assertEqual(item["title"], "Checkpointing")
        self.assertEqual(item["published_at"], "2026-10-28T10:00:00+00:00")
        self.assertEqual(item["author_claims"], ["Example Author"])
        self.assertIsNone(item["verified_author"])
        self.assertFalse(item["author_verified"])
        self.assertEqual(result["feed_completeness"], "not_established")


if __name__ == "__main__":
    unittest.main()
