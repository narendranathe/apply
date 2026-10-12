import copy
import hashlib
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlsplit, parse_qs

from cloud.notion import (AmbiguousWriteError, ConflictError, MACHINE_FIELDS, NotionAPIError,
                          NotionError, NotionStore, PaginationError, _rich_text, _text)
from cloud.records import (application_aliases, canonical_json, canonical_url, content_hash,
                           display_title, make_record, stable_id, validate_record)


def page_list(items):
    return {"results": items, "has_more": False, "next_cursor": None}


class FakeNotion:
    """Fake the API boundary, including pagination and server block decoration."""
    def __init__(self):
        self.pages, self.blocks, self.calls, self.faults = [], {}, [], []
        self.page_size = 2
        self.schema = {"properties": {k: {"type": v} for k, v in MACHINE_FIELDS.items()}}
        for name, option in (("Section", "Cloud"), ("Type", "Reference"), ("Status", "Done")):
            self.schema["properties"][name]["select"] = {"options": [{"name": option}]}
        self.write_then_timeout = False
        self.hide_created = False

    def matches(self, page, filters):
        if "and" in filters:
            return all(self.matches(page, f) for f in filters["and"])
        if "or" in filters:
            return any(self.matches(page, f) for f in filters["or"])
        value = _text(page.get("properties", {}).get(filters["property"]))
        operation = filters.get("rich_text", filters.get("select", {}))
        if "equals" in operation:
            return value == operation["equals"]
        if "contains" in operation:
            return operation["contains"] in value
        return not bool(value) if operation.get("is_empty") else bool(value)

    def __call__(self, method, path, body, headers):
        self.calls.append((method, path, copy.deepcopy(body)))
        if self.faults and self.faults[0][0] == method and self.faults[0][1] in path:
            _, _, outcome = self.faults.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if method == "GET" and path.startswith("/data_sources/"):
            return copy.deepcopy(self.schema)
        if method == "POST" and path.endswith("/query"):
            items = [] if self.hide_created else [p for p in self.pages if "filter" not in body or self.matches(p, body["filter"])]
            offset = int(body.get("start_cursor", "0"))
            result = copy.deepcopy(items[offset:offset + self.page_size])
            more = offset + self.page_size < len(items)
            return {"results": result, "has_more": more, "next_cursor": str(offset + self.page_size) if more else None}
        if method == "GET" and path.startswith("/blocks/"):
            key = path.split("/")[2]
            qs = parse_qs(urlsplit(path).query)
            offset = int(qs.get("start_cursor", ["0"])[0])
            all_blocks = self.blocks.get(key, [])
            items = copy.deepcopy(all_blocks[offset:offset + self.page_size])
            for block in items:
                for item in block.get(block["type"], {}).get("rich_text", []):
                    item.update(plain_text=item["text"]["content"], href=None,
                                annotations={"bold": False, "italic": False, "strikethrough": False,
                                             "underline": False, "code": False, "color": "default"})
                    item["text"]["link"] = None
                if block["type"] == "code":
                    block["code"]["caption"] = []
            more = offset + self.page_size < len(all_blocks)
            return {"results": items, "has_more": more, "next_cursor": str(offset + self.page_size) if more else None}
        if method == "POST" and path == "/pages":
            index = str(len(self.pages) + 1)
            page = {"id": index, "url": "https://notion.so/" + index, "properties": copy.deepcopy(body["properties"])}
            self.pages.append(page)
            self.blocks[index] = copy.deepcopy(body["children"])
            if self.write_then_timeout:
                self.write_then_timeout = False
                raise TimeoutError()
            return copy.deepcopy(page)
        raise AssertionError((method, path))

    def writes(self):
        return [c for c in self.calls if c[:2] == ("POST", "/pages")]


class NotionTests(unittest.TestCase):
    def setUp(self):
        self.api, self.delays = FakeNotion(), []
        self.store = NotionStore("test-token", transport=self.api, sleeper=self.delays.append)
        self.record = make_record("contacts", {"source": "verified", "message": "exact Unicode λ\nbody"}, "contact:test")

    def test_put_idempotent_and_readback_verifies_real_body(self):
        first = self.store.put(self.record)
        second = self.store.put(self.record)
        self.assertEqual(first, second)
        self.assertEqual(first["payload"], self.record["payload"])
        self.assertEqual(len(self.api.writes()), 1)

    def test_existing_id_changed_payload_conflicts_without_write(self):
        self.store.put(self.record)
        changed = make_record("contacts", {"message": "changed"}, self.record["id"])
        with self.assertRaises(ConflictError):
            self.store.put(changed)
        self.assertEqual(len(self.api.writes()), 1)

    def test_existing_id_changed_status_also_conflicts(self):
        self.store.put(self.record)
        changed = dict(self.record, status="new status")
        with self.assertRaises(ConflictError):
            self.store.put(changed)

    def test_fresh_schema_checked_for_each_write(self):
        self.store.put(self.record)
        del self.api.schema["properties"]["Cloud Hash"]
        with self.assertRaises(ConflictError):
            self.store.put(make_record("contacts", {}, "next"))
        self.assertEqual(len(self.api.writes()), 1)

    def test_missing_option_fails_preflight(self):
        self.api.schema["properties"]["Section"]["select"]["options"] = []
        with self.assertRaises(ConflictError):
            self.store.put(self.record)
        self.assertFalse(self.api.writes())

    def test_manual_machine_property_edit_blocks(self):
        self.store.put(self.record)
        self.api.pages[0]["properties"]["Cloud Status"] = {"rich_text": _rich_text("manual")}
        with self.assertRaises(ConflictError):
            self.store.get(self.record["id"])

    def test_cleared_identity_cannot_hide_record_and_trigger_duplicate(self):
        self.store.put(self.record)
        self.api.pages[0]["properties"]["Cloud ID"] = {"rich_text": []}
        with self.assertRaises(ConflictError):
            self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 1)

    def test_manual_body_edit_blocks(self):
        self.store.put(self.record)
        self.api.blocks["1"][0]["code"]["rich_text"] = _rich_text("changed by human")
        with self.assertRaises(ConflictError):
            self.store.put(self.record)

    def test_manual_text_formatting_edit_blocks(self):
        self.store.put(self.record)
        # Nondefault formatting is part of the owned content projection.
        api = self.api
        original = api.__call__
        def transport(method, path, body, headers):
            response = original(method, path, body, headers)
            if path.startswith("/blocks/"):
                response["results"][0]["code"]["rich_text"][0]["annotations"]["bold"] = True
            return response
        self.store.transport = transport
        with self.assertRaises(ConflictError):
            self.store.get(self.record["id"])

    def test_human_fields_outside_owned_projection_survive(self):
        self.store.put(self.record)
        self.api.pages[0]["properties"]["Next Action"] = {"rich_text": _rich_text("human note")}
        self.assertEqual(self.store.put(self.record)["hash"], self.record["hash"])
        self.assertEqual(_text(self.api.pages[0]["properties"]["Next Action"]), "human note")

    def test_duplicate_cloud_id_blocks(self):
        self.store.put(self.record)
        self.api.pages.append(copy.deepcopy(self.api.pages[0]))
        with self.assertRaises(ConflictError):
            self.store.get(self.record["id"])

    def test_query_is_fully_paginated_and_kind_filtered(self):
        for i in range(5):
            self.store.put(make_record("contacts", {"i": i}, "contact:" + str(i)))
        self.store.put(make_record("runs", {}, "run:one"))
        self.assertEqual(len(self.store.query("contacts")), 5)
        self.assertEqual(len(self.store.query()), 6)

    def test_empty_result_page_with_cursor_is_followed(self):
        self.api.faults = [("POST", "/query", {"results": [], "has_more": True, "next_cursor": "2"}),
                           ("POST", "/query", page_list([]))]
        self.assertEqual(self.store.query(), [])
        self.assertEqual(self.api.calls[-1][2]["start_cursor"], "2")

    def test_repeated_pagination_cursor_fails(self):
        repeated = {"results": [], "has_more": True, "next_cursor": "same"}
        self.api.faults = [("POST", "/query", repeated), ("POST", "/query", repeated)]
        with self.assertRaises(PaginationError):
            self.store.query()

    def test_notion_query_cap_is_never_treated_as_complete(self):
        capped = dict(page_list([]), request_status={"type": "incomplete", "incomplete_reason": "query_result_limit_reached"})
        self.api.faults = [("POST", "/query", capped)]
        with self.assertRaises(PaginationError):
            self.store.query()

    def test_429_read_honors_retry_after(self):
        self.api.faults = [("POST", "/query", NotionAPIError(429, "rate_limited", 7))]
        self.assertEqual(self.store.query(), [])
        self.assertGreaterEqual(self.delays[0], 7)
        self.assertLess(self.delays[0], 7.26)

    def test_long_retry_after_refuses_instead_of_retrying_early(self):
        self.api.faults = [("POST", "/query", NotionAPIError(429, "rate_limited", 120))]
        with self.assertRaises(NotionAPIError):
            self.store.query()
        self.assertFalse(self.delays)
        self.assertEqual(len(self.api.calls), 1)

    def test_restricted_connection_429_is_not_retried(self):
        self.api.faults = [("POST", "/query", NotionAPIError(429, "rate_limited", 1, "public_api_request_blocked"))]
        with self.assertRaises(NotionAPIError):
            self.store.query()
        self.assertEqual(len(self.api.calls), 1)
        self.assertFalse(self.delays)

    def test_529_explicit_write_rejection_can_retry(self):
        self.api.faults = [("POST", "/pages", NotionAPIError(529, "service_overload", 1))]
        self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 2)
        self.assertEqual(len(self.api.pages), 1)

    def test_explicit_failed_write_does_not_quarantine_known_rejection(self):
        self.api.faults = [("POST", "/pages", NotionAPIError(400, "validation_error"))]
        with self.assertRaises(NotionAPIError):
            self.store.put(self.record)
        self.assertEqual(self.store.put(self.record)["hash"], self.record["hash"])
        self.assertEqual(len(self.api.pages), 1)

    def test_read_5xx_retry_is_bounded(self):
        self.api.faults = [("POST", "/query", NotionAPIError(503))] * 4
        with self.assertRaises(NotionAPIError):
            self.store.query()
        self.assertEqual(len(self.api.calls), 4)
        self.assertEqual(len(self.delays), 3)

    def test_429_write_can_retry_an_explicit_rejection(self):
        self.api.faults = [("POST", "/pages", NotionAPIError(429, "rate_limited", 2))]
        self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 2)
        self.assertEqual(len(self.api.pages), 1)

    def test_write_5xx_is_ambiguous_without_retry(self):
        self.api.faults = [("POST", "/pages", NotionAPIError(503))]
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 1)
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 1)

    def test_timeout_after_success_reconciles_exact_readback(self):
        self.api.write_then_timeout = True
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(self.record)
        self.assertEqual(self.store.put(self.record)["hash"], self.record["hash"])
        self.assertEqual(len(self.api.writes()), 1)

    def test_network_write_failure_does_not_retry(self):
        self.api.faults = [("POST", "/pages", URLError("private response must not leak"))]
        with self.assertRaisesRegex(AmbiguousWriteError, "reconcile"):
            self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 1)

    def test_failed_readback_is_quarantined(self):
        original = self.api.__call__
        def transport(method, path, body, headers):
            result = original(method, path, body, headers)
            if path == "/pages":
                self.api.hide_created = True
            return result
        self.store.transport = transport
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(self.record)
        with self.assertRaises(AmbiguousWriteError):
            self.store.put(self.record)
        self.assertEqual(len(self.api.writes()), 1)

    def add_legacy(self):
        payload = {"event_id": "event:test", "title": "Historical test", "actor": "Naren", "kind": "Message",
                   "content": "preserve\nexact private-like fixture", "delivery_state": "Delivery unknown",
                   "occurred_at": "2026-10-01T10:00:00Z"}
        payload["content_hash"] = hashlib.sha256(payload["content"].encode()).hexdigest()
        props = {"Event ID": {"rich_text": _rich_text(payload["event_id"])},
                 "Content Hash": {"rich_text": _rich_text(payload["content_hash"])},
                 "Payload JSON": {"rich_text": _rich_text("Full payload-v1 JSON body below")},
                 "Delivery State": {"select": {"name": payload["delivery_state"]}}}
        self.api.pages.append({"id": "legacy", "url": "https://notion.so/legacy", "properties": props})
        self.api.blocks["legacy"] = [{"type": "code", "code": {"rich_text": _rich_text(canonical_json(payload)), "language": "json"}}]
        return payload

    def test_existing_legacy_event_is_preserved_without_second_cloud_row(self):
        payload = self.add_legacy()
        record = make_record("outreach_events", payload, payload["event_id"], payload["delivery_state"])
        result = self.store.put(record)
        self.assertEqual(result["payload"], payload)
        self.assertTrue(result["legacy"])
        self.assertFalse(self.api.writes())
        self.assertEqual(self.store.query(), [])  # Cloud views do not silently mix legacy rows.
        self.assertEqual(self.store.query('outreach_events')[0]['payload'], payload)

    def test_legacy_raw_content_hash_mismatch_blocks(self):
        payload = self.add_legacy()
        self.api.pages[0]["properties"]["Content Hash"] = {"rich_text": _rich_text("wrong")}
        with self.assertRaises(ConflictError):
            self.store.get(payload["event_id"])

    def test_legacy_display_differences_preserve_exact_payload_without_upgrading_delivery(self):
        payload = self.add_legacy()
        self.api.pages[0]["properties"]["Delivery State"] = {"select": {"name": "Sent verified"}}
        observed = self.store.get(payload["event_id"])
        self.assertEqual(observed["status"], "Delivery unknown")
        self.assertEqual(observed["legacy_property_differences"][0]["property_value"], "Sent verified")

    def test_full_snapshot_includes_original_and_cloud_records_and_bodies(self):
        self.add_legacy()
        self.store.put(self.record)
        snapshot = self.store.snapshot_all()
        self.assertTrue(snapshot["complete"])
        self.assertEqual(len(snapshot["pages"]), 2)
        self.assertEqual(len(snapshot["pages"][0]["blocks"]), 1)
        self.assertIn("data_source", snapshot)

    def test_snapshot_unsupported_block_cannot_claim_complete(self):
        self.add_legacy()
        self.api.blocks["legacy"].append({"type": "unsupported", "unsupported": {}})
        with self.assertRaises(ConflictError):
            self.store.snapshot_all()

    def test_fresh_original_contact_suppression_is_read_each_time(self):
        props = {"Type": {"select": {"name": "Contact"}}, "No Contact": {"checkbox": False},
                 "Status": {"select": {"name": "Needs verification"}}}
        self.api.pages.append({"id": "manual", "url": "https://notion.so/manual", "properties": props})
        first = self.store.current_contact_observations()[0]
        self.assertFalse(first["suppressed"])
        props["No Contact"]["checkbox"] = True
        second = self.store.current_contact_observations()[0]
        self.assertTrue(second["suppressed"])
        self.assertFalse(second["identity_verified"])
        self.assertFalse(second["history_complete"])
        self.assertFalse(self.api.writes())

    def test_paginated_rich_text_property_is_completed(self):
        self.store.put(self.record)
        page = copy.deepcopy(self.api.pages[0])
        chunks = [{"type": "text", "text": {"content": "x"}} for _ in range(26)]
        page["properties"]["Notes"] = {"id": "notes:property", "type": "rich_text", "rich_text": chunks[:25]}
        original = self.api.__call__
        def transport(method, path, body, headers):
            if path.startswith("/pages/1/properties/notes%3Aproperty"):
                offset = 25 if "start_cursor=next" in path else 0
                return {"results": [{"type": "rich_text", "rich_text": x} for x in chunks[offset:offset + 25]],
                        "has_more": offset == 0, "next_cursor": "next" if offset == 0 else None}
            return original(method, path, body, headers)
        self.store.transport = transport
        complete = self.store._complete_page(page)
        self.assertEqual(_text(complete["properties"]["Notes"]), "x" * 26)

    def test_from_env_never_reads_desktop_auth(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(NotionError, "NOTION_TOKEN"):
                NotionStore.from_env()

    def test_bad_input_hash_fails_before_any_api(self):
        bad = dict(self.record, hash="bad")
        with self.assertRaises(ValueError):
            self.store.put(bad)
        self.assertFalse(self.api.calls)

    def test_large_payload_is_chunked_without_changing_text(self):
        record = make_record("events", {"text": "λ" * 5000}, "large:test")
        self.assertEqual(self.store.put(record)["payload"], record["payload"])
        self.assertTrue(all(len(c["text"]["content"]) <= 2000 for c in self.api.blocks["1"][0]["code"]["rich_text"]))

    def test_overlarge_record_stops_before_post(self):
        with self.assertRaises(ValueError):
            self.store.put(make_record("events", {"text": "x" * 210000}, "huge:test"))
        self.assertFalse(self.api.writes())


class RecordTests(unittest.TestCase):
    def test_canonical_order_unicode_and_newlines_are_stable(self):
        self.assertEqual(content_hash({"b": "λ\n", "a": 1}), content_hash({"a": 1, "b": "λ\n"}))
        self.assertEqual(validate_record(make_record("test", {"unicode": "λ"}))["payload"], {"unicode": "λ"})

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            content_hash({"n": float("nan")})

    def test_stable_id_uses_ordered_identity(self):
        self.assertEqual(stable_id("test", "employer", "req"), stable_id("test", "employer", "req"))
        self.assertNotEqual(stable_id("test", "req", "employer"), stable_id("test", "employer", "req"))

    def test_human_title_is_deterministic_and_avoids_hash_for_contacts(self):
        record = make_record('contacts', {'name': 'Fixture Person', 'company': 'Example'}, 'contact:hash', 'Needs verification')
        self.assertEqual(display_title(record), 'Fixture Person · Example · Needs verification')
        application = make_record('applications', {'role': 'Data Engineer', 'company': 'Example'}, 'app:hash', 'prepared')
        self.assertEqual(display_title(application), 'Data Engineer · Example · prepared')

    def test_official_requisition_alias_bridges_tracking_links(self):
        one = application_aliases("Acme Inc.", "R-123", "https://EXAMPLE.com/Job?jobId=123&utm_source=x")
        two = application_aliases("Acme Inc", "R123", "https://example.com/Job?utm_source=y&jobId=123#ignored")
        self.assertEqual(one, two)
        self.assertNotEqual(canonical_url("https://example.com/Job?jobId=123"), canonical_url("https://example.com/Job?jobId=124"))

    def test_credential_urls_rejected(self):
        with self.assertRaises(ValueError):
            canonical_url("https://user:secret@example.com/job")


if __name__ == "__main__":
    unittest.main()
