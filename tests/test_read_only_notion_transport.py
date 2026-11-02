"""Offline transport contract for full Notion snapshots; no provider access."""
import unittest

from cloud.notion import NotionStore
from cloud.read_only_notion_transport import ReadOnlyNotionTransport, ReadOnlyTransportBlocked
from tests.test_notion import FakeNotion


class ReadOnlyNotionTransportTests(unittest.TestCase):
    def test_existing_store_reads_paginated_complete_snapshot_without_mutations(self):
        api = FakeNotion()
        api.pages = [{"id": str(i), "object": "page", "properties": {}} for i in range(1, 4)]
        api.blocks = {str(i): [] for i in range(1, 4)}
        api.blocks["1"] = [{"id": str(i), "type": "paragraph", "paragraph": {"rich_text": []}}
                           for i in range(3)]
        source_id = "d932083f-8d8a-4570-a027-025bbfffb878"
        store = NotionStore("synthetic-token", data_source_id=source_id,
                            transport=ReadOnlyNotionTransport(source_id, api), request_interval=0)
        snapshot = store.snapshot_all()
        self.assertTrue(snapshot["complete"])
        self.assertEqual(len(snapshot["pages"]), 3)
        self.assertFalse(api.writes())
        self.assertEqual(len([call for call in api.calls if call[0] == "POST"]), 2)
        self.assertTrue(any("start_cursor=2" in path for method, path, _ in api.calls if method == "GET"))

    def test_mutations_ambiguous_routes_and_override_headers_never_reach_delegate(self):
        calls = []
        guard = ReadOnlyNotionTransport("abc123", lambda *args: calls.append(args))
        headers = {"Authorization": "Bearer synthetic-private", "Notion-Version": "2025-09-03", "Content-Type": "application/json"}
        attempts = [
            ("POST", "/pages", {"private": "secret"}, headers),
            ("PATCH", "/pages/abc", {}, headers),
            ("DELETE", "/blocks/abc", None, headers),
            ("GET", "https://attacker.test/data_sources/abc123", None, headers),
            ("GET", "/data_sources/abc123?redirect=https://attacker.test", None, headers),
            ("GET", "/blocks/abc/../children?page_size=100", None, headers),
            ("GET", "/pages/abc/properties/x%2Fy?page_size=100", None, headers),
            ("GET", "/pages/abc/properties/x%252Fy?page_size=100", None, headers),
            ("GET", "/pages/abc/properties/x%5Cy?page_size=100", None, headers),
            ("GET", "/blocks/abc/children?page_size=100#fragment", None, headers),
            ("POST", "/data_sources/abc123/query", {"page_size": 100, "filter": {}}, headers),
            ("POST", "/data_sources/other/query", {"page_size": 100}, headers),
            ("GET", "/data_sources/abc123", None, {**headers, "X-HTTP-Method-Override": "PATCH"}),
        ]
        for attempt in attempts:
            with self.subTest(method=attempt[0]), self.assertRaises(ReadOnlyTransportBlocked) as error:
                guard(*attempt)
            self.assertEqual(str(error.exception), "read_only_transport_blocked")
        self.assertEqual(calls, [])

    def test_complete_property_pages_can_be_read_without_double_decoding(self):
        class PropertyAPI(FakeNotion):
            def __call__(self, method, path, body, headers):
                if method == "GET" and path == "/pages/1/properties/notes%3Aproperty?page_size=100":
                    self.calls.append((method, path, body))
                    return {"results": [{"type": "rich_text", "rich_text": {"plain_text": "synthetic"}}],
                            "has_more": False, "next_cursor": None}
                return super().__call__(method, path, body, headers)
        api = PropertyAPI()
        api.pages = [{"id": "1", "properties": {"Notes": {"id": "notes:property", "type": "rich_text",
                       "rich_text": [], "has_more": True}}}]
        api.blocks = {"1": []}
        source_id = "abc123"
        store = NotionStore("synthetic-token", data_source_id=source_id,
                            transport=ReadOnlyNotionTransport(source_id, api), request_interval=0)
        snapshot = store.snapshot_all()
        self.assertEqual(snapshot["pages"][0]["page"]["properties"]["Notes"]["rich_text"],
                         [{"plain_text": "synthetic"}])
        self.assertFalse(api.writes())


if __name__ == "__main__":
    unittest.main()
