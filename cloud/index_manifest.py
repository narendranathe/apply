"""Bounded immutable storage for the unchanged logical agent-index manifest.

This module has no store or transport dependency. Readers supply their accounted
get operation; a physical root is never presented as a hydrated logical record.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Callable

from cloud.records import canonical_json, content_hash, make_record, stable_id, validate_record


INLINE_MAX_BYTES = 48 * 1024
PAGE_MAX_BYTES = 16 * 1024
FANOUT = 16
PAGED_SCHEMA = "agent-index-manifest/v2"
PAGE_SCHEMA = "agent-index-manifest-page/v1"
PAGE_KIND = "index_changes"
PAGE_STATUS = "manifest_page"
LOGICAL_SCHEMA = "agent-index/v1"
MAX_DEPTH = 16
MAX_COUNT = 2 ** 63 - 1
MAX_WINDOW = 40
MAP_SECTIONS = ("projections", "entity_versions", "task_map", "action_results")
SEQ_SECTIONS = ("changes", "change_cursors", "conflicts")
LOGICAL_SECTIONS = MAP_SECTIONS + SEQ_SECTIONS
POSITION_SECTION = "change_positions"
ALL_SECTIONS = LOGICAL_SECTIONS + (POSITION_SECTION,)


class ManifestError(ValueError):
    """A static diagnostic code. Source values and transport errors never leak."""

    def __init__(self, code: str = "manifest_invalid"):
        self.code = code
        super().__init__(code)


def _fail(code="manifest_invalid"):
    raise ManifestError(code)


def _size(value):
    try:
        return len(canonical_json(value).encode("utf-8"))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ManifestError("manifest_invalid") from None


def _canonical(record):
    try:
        return deepcopy(validate_record(record))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ManifestError("manifest_invalid") from None


def _integer(value, minimum=0, maximum=MAX_COUNT):
    return type(value) is int and minimum <= value <= maximum


def _is_map(section):
    return section in MAP_SECTIONS or section == POSITION_SECTION


def _empty_ref(section):
    ref = {"id": None, "hash": None, "count": 0, "depth": 0}
    if _is_map(section):
        ref.update(first=None, last=None)
    return ref


def _check_ref(ref, section, *, empty=True):
    fields = {"id", "hash", "count", "depth"} | ({"first", "last"} if _is_map(section) else set())
    if not isinstance(ref, dict) or set(ref) != fields:
        _fail("manifest_page_invalid")
    if not _integer(ref["count"]) or not _integer(ref["depth"], maximum=MAX_DEPTH):
        _fail("manifest_page_invalid")
    if ref["count"] == 0:
        if not empty or ref != _empty_ref(section):
            _fail("manifest_page_invalid")
        return
    if not isinstance(ref["id"], str) or not ref["id"].startswith("index_manifest_page:"):
        _fail("manifest_page_invalid")
    if not isinstance(ref["hash"], str) or len(ref["hash"]) != 64 or any(c not in "0123456789abcdef" for c in ref["hash"]):
        _fail("manifest_page_invalid")
    if _is_map(section) and (not isinstance(ref["first"], str) or not isinstance(ref["last"], str) or ref["first"] > ref["last"]):
        _fail("manifest_page_invalid")


def _page(section, node, entries=None, children=None):
    if entries is not None:
        payload = {"schema": PAGE_SCHEMA, "section": section, "node": node,
                   "count": len(entries), "depth": 0, "entries": entries}
    else:
        payload = {"schema": PAGE_SCHEMA, "section": section, "node": node,
                   "count": sum(child["count"] for child in children),
                   "depth": children[0]["depth"] + 1, "children": children}
    return make_record(PAGE_KIND, payload,
                       stable_id("index_manifest_page", content_hash(payload)), PAGE_STATUS)


def _descriptor(row):
    payload = row["payload"]
    result = {"id": row["id"], "hash": row["hash"], "count": payload["count"], "depth": payload["depth"]}
    if _is_map(payload["section"]):
        if payload["node"] == "map_leaf":
            result.update(first=payload["entries"][0][0], last=payload["entries"][-1][0])
        else:
            result.update(first=payload["children"][0]["first"], last=payload["children"][-1]["last"])
    return result


def _pack_section(section, data, pages):
    """Greedy canonical byte partitions followed by bounded fanout levels."""
    is_map = _is_map(section)
    if is_map:
        if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
            _fail("manifest_section_invalid")
        values = [[key, data[key]] for key in sorted(data)]
    else:
        if not isinstance(data, list):
            _fail("manifest_section_invalid")
        values = data
    if not values:
        return _empty_ref(section)
    leaf_node, branch_node = ("map_leaf", "map_branch") if is_map else ("seq_leaf", "seq_branch")
    refs, chunk = [], []

    def emit(row):
        if _size(row) > PAGE_MAX_BYTES or row["payload"]["depth"] > MAX_DEPTH:
            _fail("manifest_capacity_exceeded")
        pages.setdefault(row["id"], row)
        return _descriptor(row)

    for value in values:
        candidate = _page(section, leaf_node, entries=chunk + [value])
        if _size(candidate) <= PAGE_MAX_BYTES:
            chunk.append(value)
        else:
            if not chunk:
                _fail("manifest_capacity_exceeded")
            refs.append(emit(_page(section, leaf_node, entries=chunk)))
            chunk = [value]
            if _size(_page(section, leaf_node, entries=chunk)) > PAGE_MAX_BYTES:
                _fail("manifest_capacity_exceeded")
    refs.append(emit(_page(section, leaf_node, entries=chunk)))
    while len(refs) > 1:
        parents, chunk = [], []
        for ref in refs:
            candidate = _page(section, branch_node, children=chunk + [ref])
            if len(chunk) < FANOUT and _size(candidate) <= PAGE_MAX_BYTES:
                chunk.append(ref)
            else:
                if not chunk:
                    _fail("manifest_capacity_exceeded")
                parents.append(emit(_page(section, branch_node, children=chunk)))
                chunk = [ref]
        if chunk:
            parents.append(emit(_page(section, branch_node, children=chunk)))
        if len(parents) >= len(refs):
            _fail("manifest_capacity_exceeded")
        refs = parents
    return refs[0]


def _change_positions(payload):
    changes, cursors = payload.get("changes", []), payload.get("change_cursors")
    if cursors is None:
        if not isinstance(changes, list):
            _fail("manifest_section_invalid")
        cursors = [item.get("cursor") if isinstance(item, dict) else None for item in changes]
    if not isinstance(changes, list) or not isinstance(cursors, list) or len(changes) != len(cursors):
        _fail("manifest_section_invalid")
    if any(not isinstance(item, str) or not item for item in cursors) or len(set(cursors)) != len(cursors):
        _fail("manifest_section_invalid")
    return {cursor: offset + 1 for offset, cursor in enumerate(cursors)}


def _validate_inline(row):
    if row["kind"] != "index_manifests" or not isinstance(row["payload"], dict):
        _fail()
    if row["payload"].get("schema") not in (None, LOGICAL_SCHEMA):
        _fail("manifest_format_unsupported")


def pack_manifest(logical_v1_record: dict) -> tuple[list[dict], dict]:
    """Return bottom-up immutable pages and a small physical publication root.

    Existing small manifests retain their exact logical identity and payload.
    Every capacity decision uses canonical UTF-8; transport wire preflight is a
    separate publisher responsibility and must happen before the first write.
    """
    logical = _canonical(logical_v1_record)
    _validate_inline(logical)
    if _size(logical) <= INLINE_MAX_BYTES:
        return [], logical
    payload = logical["payload"]
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list) or len(tasks) > MAX_WINDOW:
        _fail("manifest_capacity_exceeded")
    header = {key: value for key, value in payload.items() if key not in LOGICAL_SECTIONS}
    pages, sections = {}, {}
    positions = _change_positions(payload)
    for section in ALL_SECTIONS:
        default = {} if _is_map(section) else []
        data = positions if section == POSITION_SECTION else payload.get(section, default)
        sections[section] = _pack_section(section, data, pages)
    root_payload = {"schema": PAGED_SCHEMA, "generated_at": payload.get("generated_at"),
                    "header": header, "logical_id": logical["id"], "logical_hash": logical["hash"],
                    "logical_status": logical["status"],
                    "logical_sections": [section for section in LOGICAL_SECTIONS if section in payload],
                    "sections": sections}
    root = make_record("index_manifests", root_payload,
                       stable_id("index_manifest_root", content_hash(root_payload)), "published")
    if _size(root) > INLINE_MAX_BYTES:
        _fail("manifest_capacity_exceeded")
    return list(pages.values()), root


class _Map(Mapping):
    def __init__(self, view, section):
        self.view, self.section = view, section

    def __len__(self):
        return self.view._sections[self.section]["count"]

    def __getitem__(self, key):
        found, value = self.view._lookup(self.section, key)
        if not found:
            raise KeyError(key)
        return value

    def __iter__(self):
        for key, _ in self.view._entries(self.section):
            yield key


class _Sequence(Sequence):
    def __init__(self, view, section):
        self.view, self.section = view, section

    def __len__(self):
        return self.view._sections[self.section]["count"]

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                return [self[i] for i in range(start, stop, step)]
            return list(self.view._sequence_items(self.section, start, max(0, stop - start)))
        if not _integer(index, -MAX_COUNT):
            raise TypeError("sequence index must be an integer")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError("sequence index out of range")
        return next(self.view._sequence_items(self.section, index, 1))


class ManifestView(Mapping):
    """A lazy logical payload with a separate canonical physical record.

    Construct a fresh view for each authority read. Its page cache belongs only
    to this immutable root; no record cache is shared across freshness checks.
    """

    def __init__(self, record: dict, get_record: Callable[[str], dict | None]):
        self._record = _canonical(record)
        self._get_record, self._cache = get_record, {}
        payload = self._record["payload"]
        self.is_paged = isinstance(payload, dict) and payload.get("schema") == PAGED_SCHEMA
        if not self.is_paged:
            _validate_inline(self._record)
            self._header, self._sections = payload, {}
            self._logical_sections = ()
            return
        self._validate_root()
        self._header = payload["header"]
        self._sections = payload["sections"]
        self._logical_sections = tuple(payload["logical_sections"])

    @property
    def record(self):
        return deepcopy(self._record)

    @property
    def payload(self):
        return self

    @property
    def header(self):
        return deepcopy(self._header)

    def _validate_root(self):
        row, payload = self._record, self._record["payload"]
        fields = {"schema", "generated_at", "header", "logical_id", "logical_hash", "logical_status", "logical_sections", "sections"}
        if (row["kind"] != "index_manifests" or row["status"] != "published" or set(payload) != fields
                or row["id"] != stable_id("index_manifest_root", row["hash"]) or _size(row) > INLINE_MAX_BYTES):
            _fail()
        header = payload["header"]
        if (not isinstance(header, dict) or any(key in header for key in LOGICAL_SECTIONS)
                or header.get("schema") not in (None, LOGICAL_SCHEMA)
                or payload["generated_at"] != header.get("generated_at")):
            _fail()
        if not isinstance(header.get("tasks", []), list) or len(header.get("tasks", [])) > MAX_WINDOW:
            _fail("manifest_capacity_exceeded")
        if (not isinstance(payload["logical_id"], str) or not payload["logical_id"] or len(payload["logical_id"]) > 2000
                or not isinstance(payload["logical_status"], str) or not payload["logical_status"].strip()
                or not isinstance(payload["logical_hash"], str) or len(payload["logical_hash"]) != 64
                or any(c not in "0123456789abcdef" for c in payload["logical_hash"])):
            _fail()
        present = payload["logical_sections"]
        if (not isinstance(present, list) or present != [section for section in LOGICAL_SECTIONS if section in present]):
            _fail()
        if not isinstance(payload["sections"], dict) or set(payload["sections"]) != set(ALL_SECTIONS):
            _fail()
        for section in ALL_SECTIONS:
            _check_ref(payload["sections"][section], section)
            if section not in present and section != POSITION_SECTION and payload["sections"][section]["count"]:
                _fail()
        count = payload["sections"]["changes"]["count"]
        if payload["sections"][POSITION_SECTION]["count"] != count:
            _fail()
        if "change_cursors" in present and payload["sections"]["change_cursors"]["count"] != count:
            _fail()

    def __iter__(self):
        yield from self._header
        yield from self._logical_sections

    def __len__(self):
        return len(self._header) + len(self._logical_sections)

    def __getitem__(self, key):
        if self.is_paged and key in self._logical_sections:
            return _Map(self, key) if _is_map(key) else _Sequence(self, key)
        return deepcopy(self._header[key])

    def _load(self, ref, section, path):
        if ref["id"] in path:
            _fail("manifest_page_invalid")
        if ref["id"] in self._cache:
            row = self._cache[ref["id"]]
        else:
            try:
                raw = self._get_record(ref["id"])
            except Exception:
                raise ManifestError("manifest_page_unavailable") from None
            if raw is None:
                _fail("manifest_page_unavailable")
            try:
                row = _canonical(raw)
            except ManifestError:
                raise ManifestError("manifest_page_invalid") from None
            self._validate_page(row, section)
            self._cache[ref["id"]] = row
        if row["payload"]["section"] != section or _descriptor(row) != ref:
            _fail("manifest_page_invalid")
        return row["payload"]

    def _validate_page(self, row, section):
        payload = row["payload"]
        if (row["kind"] != PAGE_KIND or row["status"] != PAGE_STATUS
                or row["id"] != stable_id("index_manifest_page", row["hash"])
                or _size(row) > PAGE_MAX_BYTES or not isinstance(payload, dict)
                or payload.get("schema") != PAGE_SCHEMA or payload.get("section") != section
                or not _integer(payload.get("count"), 1) or not _integer(payload.get("depth"), maximum=MAX_DEPTH)):
            _fail("manifest_page_invalid")
        is_map = _is_map(section)
        leaf, branch = ("map_leaf", "map_branch") if is_map else ("seq_leaf", "seq_branch")
        base = {"schema", "section", "node", "count", "depth"}
        if payload.get("node") == leaf:
            if (set(payload) != base | {"entries"} or payload["depth"] != 0
                    or not isinstance(payload["entries"], list) or len(payload["entries"]) != payload["count"]):
                _fail("manifest_page_invalid")
            if is_map:
                previous = None
                for pair in payload["entries"]:
                    if (not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str)
                            or (previous is not None and pair[0] <= previous)):
                        _fail("manifest_page_invalid")
                    if section == POSITION_SECTION and (not pair[0] or not _integer(pair[1], 1, self._sections["changes"]["count"])):
                        _fail("manifest_page_invalid")
                    previous = pair[0]
        elif payload.get("node") == branch:
            if (set(payload) != base | {"children"} or payload["depth"] < 1
                    or not isinstance(payload["children"], list) or not 1 <= len(payload["children"]) <= FANOUT):
                _fail("manifest_page_invalid")
            previous, count = None, 0
            for child in payload["children"]:
                _check_ref(child, section, empty=False)
                if child["depth"] != payload["depth"] - 1:
                    _fail("manifest_page_invalid")
                if is_map and previous is not None and child["first"] <= previous:
                    _fail("manifest_page_invalid")
                previous = child.get("last")
                count += child["count"]
            if count != payload["count"]:
                _fail("manifest_page_invalid")
        else:
            _fail("manifest_page_invalid")

    def _lookup(self, section, key):
        if not isinstance(key, str):
            return False, None
        ref, path = self._sections[section], set()
        while ref["count"]:
            if key < ref["first"] or key > ref["last"]:
                return False, None
            node = self._load(ref, section, path)
            path.add(ref["id"])
            if node["node"] == "map_leaf":
                for candidate, value in node["entries"]:
                    if candidate == key:
                        return True, deepcopy(value)
                return False, None
            ref = next((child for child in node["children"] if child["first"] <= key <= child["last"]), _empty_ref(section))
        return False, None

    def lookup(self, section, key, default=None):
        if section not in MAP_SECTIONS:
            _fail("manifest_section_invalid")
        if not self.is_paged:
            data = self._header.get(section, {})
            if not isinstance(data, dict):
                _fail("manifest_section_invalid")
            return deepcopy(data.get(key, default))
        found, value = self._lookup(section, key)
        return value if found else default

    def _entries(self, section, ref=None, path=frozenset()):
        ref = self._sections[section] if ref is None else ref
        if not ref["count"]:
            return
        node = self._load(ref, section, path)
        if node["node"] == "map_leaf":
            yield from deepcopy(node["entries"])
        else:
            for child in node["children"]:
                yield from self._entries(section, child, path | {ref["id"]})

    def _sequence_items(self, section, offset, limit, ref=None, path=frozenset()):
        ref = self._sections[section] if ref is None else ref
        if not limit or offset >= ref["count"] or not ref["count"]:
            return
        node = self._load(ref, section, path)
        if node["node"] == "seq_leaf":
            yield from deepcopy(node["entries"][offset:offset + limit])
        else:
            for child in node["children"]:
                if offset >= child["count"]:
                    offset -= child["count"]
                    continue
                take = min(limit, child["count"] - offset)
                yield from self._sequence_items(section, offset, take, child, path | {ref["id"]})
                limit -= take
                offset = 0
                if not limit:
                    break

    def window(self, section, offset, limit):
        if section not in SEQ_SECTIONS:
            _fail("manifest_section_invalid")
        if not _integer(offset) or not _integer(limit, maximum=MAX_WINDOW):
            _fail("manifest_window_invalid")
        if not self.is_paged:
            data = self._header.get(section, [])
            if not isinstance(data, list):
                _fail("manifest_section_invalid")
            return deepcopy(data[offset:offset + limit])
        return list(self._sequence_items(section, offset, limit))

    def change_window(self, since_cursor=None, limit=20):
        if not _integer(limit, 1, 20):
            _fail("manifest_window_invalid")
        if since_cursor is not None and not isinstance(since_cursor, str):
            _fail("cursor_expired_refresh_brief")
        latest, previous = self.get("source_cursor"), self.get("previous_cursor")
        count = self._sections["changes"]["count"] if self.is_paged else len(self.get("changes", []))
        start = 0
        if since_cursor == latest:
            start = count
        elif since_cursor is not None and since_cursor != previous:
            if self.is_paged:
                found, start = self._lookup(POSITION_SECTION, since_cursor)
                if not found:
                    _fail("cursor_expired_refresh_brief")
                if not _integer(start, 1, count):
                    _fail("manifest_page_invalid")
                if "change_cursors" in self:
                    matched = self.window("change_cursors", start - 1, 1)
                else:
                    prior = self.window("changes", start - 1, 1)
                    matched = [item.get("cursor") if isinstance(item, dict) else None for item in prior]
                if matched != [since_cursor]:
                    _fail("manifest_page_invalid")
            else:
                positions = _change_positions(self._header)
                if since_cursor not in positions:
                    _fail("cursor_expired_refresh_brief")
                start = positions[since_cursor]
        changes = self.window("changes", start, limit)
        if "change_cursors" in self:
            cursors = self.window("change_cursors", start, limit)
        else:
            cursors = [item.get("cursor") if isinstance(item, dict) else None for item in changes]
        if len(cursors) != len(changes) or any(not isinstance(cursor, str) or not cursor for cursor in cursors):
            _fail("manifest_page_invalid")
        return {"changes": changes, "cursors": cursors,
                "next_cursor": cursors[-1] if cursors else since_cursor or latest,
                "latest_cursor": latest, "has_more": start + len(changes) < count}

    def materialize(self):
        if not self.is_paged:
            return deepcopy(self._record)
        payload = deepcopy(self._header)
        for section in self._logical_sections:
            payload[section] = dict(self._entries(section)) if _is_map(section) else list(self._sequence_items(section, 0, self._sections[section]["count"]))
        root = self._record["payload"]
        if content_hash(payload) != root["logical_hash"]:
            _fail("manifest_invalid")
        if dict(self._entries(POSITION_SECTION)) != _change_positions(payload):
            _fail("manifest_page_invalid")
        return make_record("index_manifests", payload, root["logical_id"], root["logical_status"])
