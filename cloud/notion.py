"""Append-only storage in the existing Hiring & Outreach Notion data source.

Notion has no uniqueness/compare-and-swap transaction. All production writers
must share one GitHub Actions concurrency group. An uncertain creation is
quarantined for reconciliation, never automatically replayed by this client.
"""
from __future__ import annotations

import hashlib
import datetime as dt
import json
import os
import random
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_from_bytes, unquote_to_bytes
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .records import canonical_json, content_hash, display_title, make_record, validate_record

DEFAULT_DATA_SOURCE_ID = "d932083f-8d8a-4570-a027-025bbfffb878"
API_VERSION = "2025-09-03"
MACHINE_FIELDS = {"Cloud ID": "rich_text", "Cloud Kind": "rich_text", "Cloud Hash": "rich_text",
                  "Cloud Status": "rich_text", "Cloud Envelope": "rich_text", "Cloud Managed Hash": "rich_text",
                  "Section": "select", "Name": "title", "Type": "select", "Status": "select"}
CORE_KEYS = ("id", "kind", "payload", "status", "hash")
# Closed vocabularies: API fields are untrusted, even when named "code".
# https://developers.notion.com/reference/status-codes
API_ERROR_CODES = frozenset({
    "invalid_json", "invalid_request_url", "invalid_request", "invalid_grant",
    "validation_error", "missing_version", "invalid_beta", "unauthorized",
    "restricted_resource", "object_not_found", "conflict_error", "rate_limited",
    "internal_server_error", "bad_gateway", "service_unavailable",
    "database_connection_unavailable", "gateway_timeout", "service_overload",
})
OPERATIONS = frozenset({"retrieve_data_source", "query_data_source", "list_block_children",
                        "retrieve_page_property", "create_page", "notion_request",
                        "decode_cloud_envelope", "verify_cloud_properties", "verify_cloud_body",
                        "verify_create_readback"})


def _safe_status(status):
    return status if type(status) is int and 100 <= status <= 599 else None


def _request_operation(method: str, path: str) -> str:
    # Classify the route without exposing IDs, query strings, or request data.
    parts = path.split("?", 1)[0].split("/")
    if method == "GET" and len(parts) == 3 and parts[:2] == ["", "data_sources"]:
        return "retrieve_data_source"
    if method == "POST" and len(parts) == 4 and parts[:2] == ["", "data_sources"] and parts[3] == "query":
        return "query_data_source"
    if method == "GET" and len(parts) == 4 and parts[:2] == ["", "blocks"] and parts[3] == "children":
        return "list_block_children"
    if method == "GET" and len(parts) == 5 and parts[:2] == ["", "pages"] and parts[3] == "properties":
        return "retrieve_page_property"
    if method == "POST" and path == "/pages":
        return "create_page"
    return "notion_request"


class NotionError(RuntimeError):
    pass


class ConflictError(NotionError):
    def __init__(self, message, *, operation='notion_request'):
        super().__init__(message)
        self.operation = operation

    def diagnostics(self):
        return {'http_status': None, 'error_code': 'unknown',
                'operation': self.operation if self.operation in OPERATIONS else 'notion_request'}


class AmbiguousWriteError(NotionError):
    """A write may have succeeded. Persist an exception and reconcile first."""
    def __init__(self, message, *, diagnostics=None):
        super().__init__(message)
        self._diagnostics = diagnostics

    def diagnostics(self):
        value = self._diagnostics if isinstance(self._diagnostics, dict) else {}
        code, operation = value.get('error_code'), value.get('operation')
        return {'http_status': _safe_status(value.get('http_status')),
                'error_code': code if isinstance(code, str) and code in API_ERROR_CODES else 'unknown',
                'operation': operation if isinstance(operation, str) and operation in OPERATIONS else 'notion_request'}


class PaginationError(NotionError):
    pass


class NotionAPIError(NotionError):
    def __init__(self, status: int, code: str = "api_error", retry_after: float | None = None,
                 rate_limit_reason: str | None = None):
        # Do not include Notion response messages: they can contain private data.
        self.status, self.retry_after = status, retry_after
        self.api_code = code if isinstance(code, str) and code in API_ERROR_CODES else "unknown"
        self.code = code if isinstance(code, str) and code in API_ERROR_CODES | {"api_error", "retry_after_exceeds_budget"} else "unknown"
        self.operation = "notion_request"
        self.rate_limit_reason = rate_limit_reason
        super().__init__("Notion API HTTP " + str(_safe_status(status)) + " (" + self.code + ")")

    def diagnostics(self) -> dict:
        """Only these three independently sanitized fields may enter runner logs."""
        return {"http_status": _safe_status(self.status),
                "error_code": self.api_code if isinstance(self.api_code, str) and self.api_code in API_ERROR_CODES else "unknown",
                "operation": self.operation if isinstance(self.operation, str) and self.operation in OPERATIONS else "notion_request"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _text(value: dict | None) -> str:
    if not value:
        return ""
    kind = value.get("type")
    if kind in {"rich_text", "title"} or "rich_text" in value or "title" in value:
        items = value.get(kind or "rich_text", value.get("rich_text", value.get("title", [])))
        return "".join(i.get("plain_text", i.get("text", {}).get("content", "")) for i in items)
    if kind in {"select", "status"} or "select" in value or "status" in value:
        item = value.get(kind or "select") or {}
        return item.get("name", "")
    return ""


def _rich_text(text: str) -> list[dict]:
    chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)] or [""]
    if len(chunks) > 100:
        raise ValueError("Record exceeds Notion rich-text array limit; use an encrypted archive reference")
    return [{"type": "text", "text": {"content": chunk}} for chunk in chunks]


def _owned_rich_text(items: list[dict]) -> list[dict]:
    """Canonical text segmentation retains formatting, links, and other values.

    Notion may join adjacent text items on read. Rebuild identical runs with
    the original 2000-character write chunks so stored managed hashes remain
    valid without changing the page or accepting a meaningful edit.
    """
    result, metadata, content = [], None, []

    def flush():
        if metadata is not None:
            text = "".join(content)
            for chunk in [text[i:i + 2000] for i in range(0, len(text), 2000)] or [""]:
                result.append({**metadata, "text": {**metadata["text"], "content": chunk}})

    for item in items:
        owned = {k: v for k, v in item.items() if k not in {"plain_text", "href"}}
        if owned.get("annotations") == {"bold": False, "italic": False, "strikethrough": False,
                                        "underline": False, "code": False, "color": "default"}:
            owned.pop("annotations")
        if isinstance(owned.get("text"), dict):
            owned["text"] = dict(owned["text"])
            if owned["text"].get("link") is None:
                owned["text"].pop("link", None)
        if owned.get("type") == "text" and isinstance(owned.get("text", {}).get("content"), str):
            value = owned["text"].pop("content")
            if owned != metadata:
                flush()
                metadata, content = owned, []
            content.append(value)
        else:
            flush()
            metadata, content = None, []
            result.append(owned)
    flush()
    return result


def _owned_body(blocks: list[dict]) -> list[dict]:
    """Stable block projection excludes server IDs/timestamps, retains content."""
    result = []
    for block in blocks:
        kind = block.get("type")
        body = dict(block.get(kind, {}))
        for key in ("rich_text", "caption"):
            if key in body:
                body[key] = _owned_rich_text(body[key])
        if body.get("color") == "default":
            body.pop("color")
        if kind == "code" and body.get("caption") == []:
            body.pop("caption")
        projected = {"type": kind, kind: body}
        if block.get("has_children") or block.get("children"):
            projected["children"] = _owned_body(block.get("children", []))
        result.append(projected)
    return result


def _managed_hash(properties: dict, blocks: list[dict]) -> str:
    owned = {name: _text(properties.get(name)) for name in MACHINE_FIELDS if name != "Cloud Managed Hash"}
    return content_hash({"properties": owned, "body": _owned_body(blocks)})


class NotionStore:
    def __init__(self, token: str | None = None, data_source_id: str = DEFAULT_DATA_SOURCE_ID,
                 *, transport=None, sleeper=time.sleep, max_attempts: int = 4,
                 max_delay: float = 30, max_pages: int = 1000, request_interval: float = 0.4):
        self.token = token or os.environ.get("NOTION_TOKEN", "")
        if not self.token:
            raise NotionError("NOTION_TOKEN is required; desktop connector authentication is not a runner credential")
        if not data_source_id or any(c not in "0123456789abcdefABCDEF-" for c in data_source_id):
            raise ValueError("Invalid Notion data source identity")
        if max_attempts < 1 or max_pages < 1 or max_delay < 0 or request_interval < 0:
            raise ValueError("Invalid retry/pagination budget")
        self.data_source_id = data_source_id
        self.transport = transport or self._transport
        self.sleeper, self.max_attempts, self.max_delay, self.max_pages = sleeper, max_attempts, max_delay, max_pages
        # One serial writer: pace starts below the lowest 180/minute connection
        # budget, including successful reads, pagination, and retry attempts.
        self.clock, self.request_interval = time.monotonic, request_interval
        self._next_request_at = None
        self._uncertain = set()

    @classmethod
    def from_env(cls, **kwargs):
        """Runner auth must be provisioned separately from the desktop connector."""
        return cls(token=os.environ.get("NOTION_TOKEN"),
                   data_source_id=os.environ.get("NOTION_DATA_SOURCE_ID", DEFAULT_DATA_SOURCE_ID), **kwargs)

    def _transport(self, method: str, path: str, body: dict | None, headers: dict) -> dict:
        data = canonical_json(body).encode("utf-8") if body is not None else None
        request = Request("https://api.notion.com/v1" + path, data=data, headers=headers, method=method)
        try:
            with build_opener(_NoRedirect()).open(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeError):
                error = {}
            if not isinstance(error, dict):
                error = {}
            additional = error.get("additional_data", {})
            if not isinstance(additional, dict):
                additional = {}
            retry = exc.headers.get("Retry-After") or additional.get("retry_after")
            try:
                retry = float(retry) if retry is not None else None
            except (TypeError, ValueError):
                retry = None
            raise NotionAPIError(exc.code, error.get("code", "api_error"), retry,
                                 additional.get("rate_limit_reason")) from None

    def _request(self, method: str, path: str, body: dict | None = None, *, write: bool = False) -> dict:
        headers = {"Authorization": "Bearer " + self.token, "Notion-Version": API_VERSION,
                   "Content-Type": "application/json"}
        operation = _request_operation(method, path)
        for attempt in range(self.max_attempts):
            try:
                started = self.clock()
                if self._next_request_at is not None and started < self._next_request_at:
                    self.sleeper(self._next_request_at - started)
                    started = self.clock()
                self._next_request_at = started + self.request_interval
                result = self.transport(method, path, body, headers)
                if not isinstance(result, dict):
                    raise ValueError("Malformed Notion response")
                return result
            except NotionAPIError as exc:
                exc.operation = operation
                if write and exc.status >= 500 and exc.status != 529:
                    raise AmbiguousWriteError("Notion write outcome is uncertain; reconcile the record ID before retry",
                                              diagnostics=exc.diagnostics()) from None
                retryable = ((exc.status in {429, 529} and exc.rate_limit_reason != "public_api_request_blocked")
                             or not write and exc.status in {500, 502, 503, 504})
                if not retryable or attempt == self.max_attempts - 1:
                    raise
                delay = max(0, exc.retry_after) if exc.retry_after is not None else min(2 ** attempt, self.max_delay)
                if delay > self.max_delay:
                    bounded = NotionAPIError(exc.status, "retry_after_exceeds_budget", delay)
                    bounded.api_code, bounded.operation = exc.api_code, operation
                    raise bounded from None
                self.sleeper(delay + random.uniform(0, min(0.25, max(0, self.max_delay - delay))))
            except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
                if write:
                    raise AmbiguousWriteError("Notion write transport failed; reconcile the record ID before retry") from None
                if attempt == self.max_attempts - 1:
                    raise NotionError("Notion read transport failed after bounded retries") from None
                self.sleeper(min(2 ** attempt, self.max_delay))
        raise NotionError("Notion retry budget exhausted")

    def _paginate(self, method: str, path: str, body: dict | None = None) -> list[dict]:
        results, seen, cursor = [], set(), None
        for _ in range(self.max_pages):
            if method == "GET":
                url = path + ("&" if "?" in path else "?") + "page_size=100"
                if cursor:
                    url += "&start_cursor=" + quote(cursor, safe="")
                response = self._request(method, url)
            else:
                data = dict(body or {}, page_size=100)
                if cursor:
                    data["start_cursor"] = cursor
                response = self._request(method, path, data)
            if response.get("request_status", {}).get("type") == "incomplete":
                raise PaginationError("Notion query was capped; partition before trusting this snapshot")
            if not isinstance(response.get("results"), list) or not isinstance(response.get("has_more"), bool):
                raise PaginationError("Notion returned malformed pagination")
            results.extend(response["results"])
            if not response["has_more"]:
                return results
            cursor = response.get("next_cursor")
            if not cursor or cursor in seen:
                raise PaginationError("Notion pagination cursor is missing or repeated")
            seen.add(cursor)
        raise PaginationError("Notion pagination budget exhausted")

    def _pages(self, filters: dict) -> list[dict]:
        return self._paginate("POST", "/data_sources/" + self.data_source_id + "/query", {"filter": filters})

    def _blocks(self, page_id: str) -> list[dict]:
        blocks = self._paginate("GET", "/blocks/" + page_id + "/children")
        for block in blocks:
            if block.get("has_children"):
                block["children"] = self._blocks(block["id"])
        return blocks

    def preflight(self) -> dict:
        schema = self._request("GET", "/data_sources/" + self.data_source_id)
        properties = schema.get("properties", {})
        wrong = [name for name, kind in MACHINE_FIELDS.items() if properties.get(name, {}).get("type") != kind]
        if wrong:
            raise ConflictError("Notion schema is missing or incompatible: " + ", ".join(wrong))
        for prop, option in (("Section", "Cloud"), ("Type", "Reference"), ("Status", "Done")):
            options = properties[prop].get("select", {}).get("options", [])
            if option not in {x.get("name") for x in options}:
                raise ConflictError("Notion schema option is missing: " + prop + "/" + option)
        return schema

    def _decode_cloud(self, page: dict) -> dict:
        page = self._complete_page(page)
        props = page.get("properties", {})
        try:
            record = validate_record(json.loads(_text(props.get("Cloud Envelope"))))
        except (ValueError, TypeError, KeyError):
            raise ConflictError("Cloud envelope is malformed or has a payload hash mismatch", operation='decode_cloud_envelope') from None
        expected = {"Cloud ID": record["id"], "Cloud Kind": record["kind"], "Cloud Hash": record["hash"],
                    "Cloud Status": record["status"], "Section": "Cloud", "Type": "Reference", "Status": "Done",
                    "Name": display_title(record)}
        if any(_text(props.get(k)) != v for k, v in expected.items()):
            raise ConflictError("Cloud machine-owned properties were manually edited", operation='verify_cloud_properties')
        blocks = self._blocks(page["id"])
        if _text(props.get("Cloud Managed Hash")) != _managed_hash(props, blocks):
            raise ConflictError("Cloud body or machine-owned properties were manually edited", operation='verify_cloud_body')
        record["notion_page_id"] = page["id"]
        record["notion_url"] = page.get("url", "")
        return record

    def _complete_page(self, page: dict) -> dict:
        props = page.get("properties", {})
        # Retrieve-page responses may truncate rich-text property items. Fetch
        # their complete paginated values before comparing an immutable envelope.
        for name, prop in list(props.items()):
            kind = prop.get("type")
            if prop.get("id") and kind in {"rich_text", "title", "relation", "people"} and (prop.get("has_more") or len(prop.get(kind, [])) >= 25):
                # API IDs are already URL encoded. Canonicalize once, also
                # accepting raw IDs, without encoding their percent signs twice.
                property_id = quote_from_bytes(unquote_to_bytes(prop["id"]), safe="")
                items = self._paginate("GET", "/pages/" + page["id"] + "/properties/" + property_id)
                if any(item.get("type") != kind for item in items):
                    raise ConflictError("Notion page property has incompatible paginated items")
                props[name] = dict(prop, **{kind: [item[kind] for item in items]}, has_more=False)
        return page

    def snapshot_all(self) -> dict:
        """Full original + Cloud source snapshot for encrypted backups only."""
        schema = self._request("GET", "/data_sources/" + self.data_source_id)
        pages = self._paginate("POST", "/data_sources/" + self.data_source_id + "/query")
        snapshot = []
        for page in pages:
            if page.get("object", "page") != "page":
                raise ConflictError("Unexpected non-page data source entry")
            blocks = self._blocks(page["id"])
            def check(items):
                for block in items:
                    if block.get("type") == "unsupported":
                        raise ConflictError("Snapshot contains an unsupported block; cannot claim complete preservation")
                    check(block.get("children", []))
            check(blocks)
            snapshot.append({"page": self._complete_page(page), "blocks": blocks})
        return {"schema": "notion-data-source-snapshot/v1", "data_source_id": self.data_source_id,
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "data_source": schema,
                "pages": snapshot, "complete": True}

    def source_rows(self, kind: str | None = None) -> list[dict]:
        """Fresh original observations, separate from immutable Cloud records.

        Type names are the existing schema values, e.g. Contact/Opportunity/Task.
        No observation grants identity/history verification or sending permission.
        """
        data = {"filter": {"property": "Cloud ID", "rich_text": {"is_empty": True}}}
        if kind:
            data["filter"] = {"and": [data["filter"], {"property": "Type", "select": {"equals": kind}}]}
        pages = self._paginate("POST", "/data_sources/" + self.data_source_id + "/query", data)
        observed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        observations = []
        for page in pages:
            page = self._complete_page(page)
            props = page.get("properties", {})
            observations.append({"source_notion_id": page["id"], "source_notion_url": page.get("url", ""),
                                 "observed_at": observed_at, "properties": props, "body": self._blocks(page["id"]),
                                 "no_contact": bool(props.get("No Contact", {}).get("checkbox", False)),
                                 "suppressed": bool(props.get("No Contact", {}).get("checkbox", False)) or _text(props.get("Status")) == "Suppressed",
                                 "identity_verified": False, "history_complete": False})
        return observations

    def current_contact_observations(self) -> list[dict]:
        return self.source_rows("Contact")

    def _decode_legacy(self, page: dict) -> dict:
        props = page.get("properties", {})
        record_id = _text(props.get("Event ID"))
        candidates = []
        raw = _text(props.get("Payload JSON"))
        if raw:
            try:
                candidates.append(json.loads(raw))
            except ValueError:
                pass  # Existing rows point to their payload-v1 JSON body.
        blocks = self._blocks(page["id"])
        def visit(items):
            for block in items:
                if block.get("type") == "code":
                    value = _text({"type": "rich_text", "rich_text": block.get("code", {}).get("rich_text", [])})
                    try:
                        candidates.append(json.loads(value))
                    except ValueError:
                        pass
                visit(block.get("children", []))
        visit(blocks)
        candidates = [x for x in candidates if isinstance(x, dict) and x.get("event_id") == record_id]
        unique = {canonical_json(x): x for x in candidates}
        if len(unique) != 1:
            raise ConflictError("Legacy Event ID has missing or conflicting full payload-v1 bodies")
        payload = next(iter(unique.values()))
        if not isinstance(payload.get("content"), str):
            raise ConflictError("Legacy event content is missing")
        digest = hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
        if payload.get("content_hash") != digest or _text(props.get("Content Hash")) != digest:
            raise ConflictError("Legacy event content/property hashes disagree")
        # Old select properties deliberately mapped values unsupported by their
        # option lists. Preserve these differences as evidence, never reinterpret
        # a display label (e.g. Sent verified) as an actual verified receipt.
        differences = []
        for prop, key in (("Actor", "actor"), ("Kind", "kind"), ("Delivery State", "delivery_state")):
            value = _text(props.get(prop))
            if value and value != payload.get(key):
                differences.append({"property": prop, "property_value": value, "payload_value": payload.get(key)})
        record = make_record("outreach_events", payload, record_id, payload.get("delivery_state", "Historical observation"))
        record.update(notion_page_id=page["id"], notion_url=page.get("url", ""), legacy=True)
        if differences:
            record["legacy_property_differences"] = differences
        return record

    def get(self, record_id: str) -> dict | None:
        # Envelope identity is an independent lookup path when someone edits or
        # clears the display Cloud ID. A corrupted existing row must block a
        # creation instead of silently becoming a duplicate with its old ID.
        identity_fragment = canonical_json({"id": record_id})[1:-1]
        pages = self._pages({"or": [{"property": "Cloud ID", "rich_text": {"equals": record_id}},
                                     {"property": "Cloud Envelope", "rich_text": {"contains": identity_fragment}}]})
        if len(pages) > 1:
            raise ConflictError("Duplicate Cloud ID; reconcile before writing")
        if pages:
            return self._decode_cloud(pages[0])
        legacy = self._pages({"property": "Event ID", "rich_text": {"equals": record_id}})
        if len(legacy) > 1:
            raise ConflictError("Duplicate legacy Event ID; reconcile before writing")
        return self._decode_legacy(legacy[0]) if legacy else None

    def query(self, kind: str | None = None) -> list[dict]:
        # A cleared/edited identity or kind must not hide applied history from
        # dedup. Query both independent identity projections and malformed Cloud
        # rows, then validate before filtering the decoded record family.
        filters = {"or": [{"property": "Section", "select": {"equals": "Cloud"}},
                          {"property": "Cloud ID", "rich_text": {"is_not_empty": True}},
                          {"property": "Cloud Envelope", "rich_text": {"is_not_empty": True}}]}
        if kind:
            fragment = canonical_json({"kind": kind})[1:-1]
            matching_kind = {"property": "Cloud Kind", "rich_text": {"equals": kind}}
            # Distribute the old managed-row/kind predicate into OR-of-ANDs.
            # Notion permits only two compound levels. These branches preserve
            # all independent lookups and the malformed-identity guard.
            filters = {"or": [
                *({"and": [managed, matching_kind]} for managed in filters["or"]),
                {"property": "Cloud Envelope", "rich_text": {"contains": fragment}},
                *({"and": [{"property": "Section", "select": {"equals": "Cloud"}},
                            {"property": field, "rich_text": {"is_empty": True}}]}
                  for field in ("Cloud ID", "Cloud Kind", "Cloud Envelope")),
            ]}
        pages = self._pages(filters)
        records, seen = [], set()
        for page in pages:
            record = self._decode_cloud(page)
            if record["id"] in seen:
                raise ConflictError("Duplicate Cloud ID in query")
            seen.add(record["id"])
            # A substring can match a nested payload's kind. Only skip after
            # verifying the complete envelope, properties, body, and identity.
            if kind is not None and record['kind'] != kind:
                continue
            records.append(record)
        if kind == "outreach_events":
            legacy_pages = self._pages({"and": [{"property": "Event ID", "rich_text": {"is_not_empty": True}},
                                                 {"property": "Cloud ID", "rich_text": {"is_empty": True}}]})
            for page in legacy_pages:
                record = self._decode_legacy(page)
                if record["id"] in seen:
                    raise ConflictError("Duplicate Event ID across Cloud and original history")
                seen.add(record["id"])
                records.append(record)
        return records

    def latest_index_manifest(self) -> dict | None:
        """One server-sorted manifest read; never download every past index.

        Both independent identity projections are searched. A malformed newest
        candidate blocks the read instead of silently falling back to stale state.
        """
        fragment = canonical_json({'kind': 'index_manifests'})[1:-1]
        response = self._request('POST', '/data_sources/' + self.data_source_id + '/query', {
            'filter': {'or': [
                {'property': 'Cloud Kind', 'rich_text': {'equals': 'index_manifests'}},
                {'property': 'Cloud Envelope', 'rich_text': {'contains': fragment}},
                *({'and': [{'property': 'Section', 'select': {'equals': 'Cloud'}},
                           {'property': field, 'rich_text': {'is_empty': True}}]}
                  for field in ('Cloud ID', 'Cloud Kind', 'Cloud Envelope'))]},
            'sorts': [{'timestamp': 'created_time', 'direction': 'descending'}], 'page_size': 1})
        if (response.get('request_status', {}).get('type') == 'incomplete'
                or not isinstance(response.get('results'), list)
                or not isinstance(response.get('has_more'), bool)):
            raise PaginationError('Index lookup is incomplete')
        results = response['results']
        if not results:
            return None
        if len(results) != 1:
            raise ConflictError('Index lookup returned an invalid bounded result')
        candidate = self._decode_cloud(results[0])
        if candidate['kind'] != 'index_manifests':
            raise ConflictError('Index candidate identity is inconsistent')
        # A second independent exact search verifies uniqueness of this manifest.
        verified = self.get(candidate['id'])
        if not verified or any(verified[k] != candidate[k] for k in CORE_KEYS):
            raise ConflictError('Index manifest readback is inconsistent')
        return verified

    def index_source_records(self) -> list[dict]:
        """Fresh authoritative sources, excluding old generated index versions.

        Independent property/envelope paths retain corruption detection. Flatten
        into OR-of-ANDs to respect Notion's two compound-level limit. Generated
        task snapshots are excluded only when both kind and schema agree.
        """
        generated = ('entity_projections', 'index_manifests', 'index_changes')
        not_kind = [{'property': 'Cloud Kind', 'rich_text': {'does_not_equal': kind}} for kind in generated]
        not_envelope = [{'property': 'Cloud Envelope', 'rich_text': {
            'does_not_contain': canonical_json({'kind': kind})[1:-1]}} for kind in generated]
        managed = [{'property': 'Section', 'select': {'equals': 'Cloud'}},
                   {'property': 'Cloud ID', 'rich_text': {'is_not_empty': True}},
                   {'property': 'Cloud Envelope', 'rich_text': {'is_not_empty': True}}]
        original_task = [
            {'property': 'Cloud Kind', 'rich_text': {'does_not_equal': 'agent_tasks'}},
            {'property': 'Cloud Envelope', 'rich_text': {
                'does_not_contain': canonical_json({'schema': 'agent-index/v1'})[1:-1]}}]
        branches = [{'and': [identity, *exclusions, task]} for identity in managed
                    for exclusions in (not_kind, not_envelope) for task in original_task]
        branches += [{'and': [managed[0], {'property': field, 'rich_text': {'is_empty': True}}]}
                     for field in ('Cloud ID', 'Cloud Kind', 'Cloud Envelope')]
        pages = self._pages({'or': branches})
        records, seen = [], set()
        for page in pages:
            row = self._decode_cloud(page)
            if row['id'] in seen:
                raise ConflictError('Duplicate source identity in index query')
            seen.add(row['id'])
            if row['kind'] in generated or (row['kind'] == 'agent_tasks' and row['payload'].get('schema') == 'agent-index/v1'):
                continue
            records.append(row)
        return records

    def put(self, record: dict) -> dict:
        """Return a fully verified existing row or exact post-create readback.

        A successful return covers the immutable envelope, machine properties,
        and managed body. Uncertain writes/readbacks raise instead of returning.
        """
        record = validate_record(record)
        record_id = record["id"]
        self.preflight()  # Fresh schema and exact-identity read before every write.
        existing = self.get(record_id)
        if existing:
            if any(existing[k] != record[k] for k in CORE_KEYS):
                raise ConflictError("Immutable record ID already has different content; append a correction")
            self._uncertain.discard(record_id)
            return existing
        if record_id in self._uncertain:
            raise AmbiguousWriteError("Previous write is unresolved; absence is not proof that replay is safe")
        envelope = canonical_json(record)
        props = {"Cloud ID": {"rich_text": _rich_text(record_id)},
                 "Cloud Kind": {"rich_text": _rich_text(record["kind"])},
                 "Cloud Hash": {"rich_text": _rich_text(record["hash"])},
                 "Cloud Status": {"rich_text": _rich_text(record["status"])},
                 "Cloud Envelope": {"rich_text": _rich_text(envelope)},
                 "Section": {"select": {"name": "Cloud"}}, "Type": {"select": {"name": "Reference"}},
                 "Status": {"select": {"name": "Done"}},
                 "Name": {"title": _rich_text(display_title(record))}}
        blocks = [{"object": "block", "type": "code", "code": {"language": "json", "rich_text": _rich_text(envelope)}}]
        props["Cloud Managed Hash"] = {"rich_text": _rich_text(_managed_hash(props, blocks))}
        request = {"parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
                   "properties": props, "children": blocks}
        if len(canonical_json(request).encode("utf-8")) > 490_000:
            raise ValueError("Record exceeds the safe Notion request size; use an encrypted archive reference")
        self._uncertain.add(record_id)  # Also quarantines readback failures after a successful POST.
        try:
            self._request("POST", "/pages", request, write=True)
        except NotionAPIError:
            # An explicit non-5xx rejection is known not to have created a page.
            # AmbiguousWriteError deliberately does not enter this branch.
            self._uncertain.discard(record_id)
            raise
        try:
            readback = self.get(record_id)
            # A successful create can precede data-source query visibility.
            # Only an absent result is polled, while the ID stays quarantined.
            # No creation retry, conflict retry, or inferred success is allowed.
            for delay in (1, 2, 4):
                if readback is not None:
                    break
                self.sleeper(delay)
                readback = self.get(record_id)
        except NotionError as error:
            safe = error.diagnostics() if isinstance(error, (NotionAPIError, AmbiguousWriteError, ConflictError)) else None
            raise AmbiguousWriteError("Write readback failed; reconcile before any replay", diagnostics=safe) from None
        if not readback or any(readback[k] != record[k] for k in CORE_KEYS):
            raise AmbiguousWriteError("Write did not read back exactly; reconcile before any replay",
                                      diagnostics={'operation': 'verify_create_readback'})
        self._uncertain.discard(record_id)
        return readback
