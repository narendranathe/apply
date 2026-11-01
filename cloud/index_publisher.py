"""Publish an immutable index through the existing central writer.

The manifest is appended only after every referenced record reads back exactly.
An interrupted publication leaves unreachable child records, and the previous
manifest remains the reader's visibility boundary. No external action is made.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from .agent_index import GENERATED_KINDS, SCHEMA, build_index
from .index_manifest import ManifestError, ManifestView, pack_manifest
from .notion import DEFAULT_DATA_SOURCE_ID, _managed_hash, _rich_text
from .records import canonical_json, content_hash, display_title, validate_record

CORE = ("id", "kind", "payload", "status", "hash")
INDEX_PUBLICATION_ERROR_CODES = frozenset({
    "authoritative_source_invalid", "authoritative_source_identity_conflict",
    "authoritative_snapshot_unavailable", "authoritative_snapshot_invalid",
    "previous_index_unavailable", "previous_index_invalid",
    "index_write_uncertain_stop_no_retry", "index_write_readback_mismatch_stop_no_retry",
    "index_readback_uncertain_stop_no_retry", "index_readback_mismatch_stop_no_retry",
    "index_builder_manifest_invalid", "index_storage_capacity_exceeded",
})


class IndexPublicationError(RuntimeError):
    """Fixed public code; source bodies and credentials never enter the error."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)

    def diagnostics(self):
        """Retain only fixed publication codes and sanitized Notion evidence.

        Wrappers deliberately suppress upstream exception text. Their context
        can still supply the Notion client's three safe diagnostic fields.
        """
        from .notion import AmbiguousWriteError, ConflictError, NotionAPIError
        result = {'error_code': self.code if type(self.code) is str
                  and self.code in INDEX_PUBLICATION_ERROR_CODES else 'index_publication_failed'}
        sanitizers = {NotionAPIError: NotionAPIError.diagnostics,
                      AmbiguousWriteError: AmbiguousWriteError.diagnostics,
                      ConflictError: ConflictError.diagnostics}
        pending, seen = [self], set()
        while pending and len(seen) < 16:
            error = pending.pop()
            if id(error) in seen:
                continue
            seen.add(id(error))
            sanitize = sanitizers.get(type(error))
            if sanitize is not None:
                # Use the client's sanitizer, never an upstream override.
                try:
                    result['notion'] = sanitize(error)
                    return result
                except Exception:
                    pass
            for nested in (error.__context__, error.__cause__):
                if isinstance(nested, BaseException):
                    pending.append(nested)
        return result


def _canonical(row):
    try:
        canonical = validate_record(row)
        if not isinstance(canonical["payload"], dict):
            raise ValueError("Expected a structured payload")
        return canonical
    except (ValueError, TypeError, KeyError):
        raise IndexPublicationError("authoritative_source_invalid") from None


def _generated(row):
    return (row["kind"] in GENERATED_KINDS
            or row["kind"] == "agent_tasks" and row["payload"].get("schema") == SCHEMA)


def snapshot_hash(records):
    """Hash the exact non-generated authoritative CORE signatures.

    Caller reconciliation evidence must use this representation; timestamps
    inside source payloads are protected by their immutable payload hashes.
    Notion page IDs and presentation metadata are not identity evidence.
    """
    source = {}
    for value in records:
        row = _canonical(value)
        if _generated(row):
            continue
        old = source.get(row["id"])
        if old is not None and old != row:
            raise IndexPublicationError("authoritative_source_identity_conflict")
        source[row["id"]] = row
    signatures = sorted((row["id"], row["kind"], row["hash"], row["status"]) for row in source.values())
    return content_hash(signatures)


def _read_sources(store):
    # query(None) is cloud-only in NotionStore. The explicit second query adds
    # the original journal, without requiring agents to scan it during startup.
    # These methods perform fresh transport reads; publisher retains no cache.
    try:
        cloud = store.index_source_records() if hasattr(store, 'index_source_records') else store.query()
        outreach = store.query(kind="outreach_events")
    except Exception:
        raise IndexPublicationError("authoritative_snapshot_unavailable") from None
    if not isinstance(cloud, (list, tuple)) or not isinstance(outreach, (list, tuple)):
        raise IndexPublicationError("authoritative_snapshot_invalid")
    combined = {}
    for value in [*cloud, *outreach]:
        row = _canonical(value)
        old = combined.get(row["id"])
        if old is not None and old != row:
            raise IndexPublicationError("authoritative_source_identity_conflict")
        combined[row["id"]] = row
    return sorted(combined.values(), key=lambda row: row["id"])


def _previous_manifest(store, snapshot):
    if hasattr(store, "latest_index_manifest"):
        try:
            previous = store.latest_index_manifest()
        except Exception:
            raise IndexPublicationError("previous_index_unavailable") from None
    else:
        previous = max((row for row in snapshot if row["kind"] == "index_manifests"),
                       key=lambda row: (row["payload"].get("generated_at", ""), row["id"]), default=None)
    if previous is None:
        return None
    previous = _canonical(previous)
    if previous["kind"] != "index_manifests":
        raise IndexPublicationError("previous_index_invalid")
    try:
        # Fully reconstruct prior logical state before sampling the publication
        # clock or building semantic deltas. No partial directory is authority.
        logical = ManifestView(previous, store.get).materialize()
    except ManifestError as error:
        code = "previous_index_unavailable" if error.code == "manifest_page_unavailable" else "previous_index_invalid"
        raise IndexPublicationError(code) from None
    if logical["payload"].get("schema") != SCHEMA:
        raise IndexPublicationError("previous_index_invalid")
    return logical


def _notion_write_request(row, data_source_id):
    """Pure capacity model of the frozen NotionStore.put request.

    Share its rich-text and managed-hash helpers and retain its limits. This
    performs no schema read, exact-identity lookup, write or write replay.
    """
    record = validate_record(row)
    envelope = canonical_json(record)
    props = {"Cloud ID": {"rich_text": _rich_text(record["id"])},
             "Cloud Kind": {"rich_text": _rich_text(record["kind"])},
             "Cloud Hash": {"rich_text": _rich_text(record["hash"])},
             "Cloud Status": {"rich_text": _rich_text(record["status"])},
             "Cloud Envelope": {"rich_text": _rich_text(envelope)},
             "Section": {"select": {"name": "Cloud"}}, "Type": {"select": {"name": "Reference"}},
             "Status": {"select": {"name": "Done"}},
             "Name": {"title": _rich_text(display_title(record))}}
    blocks = [{"object": "block", "type": "code", "code": {"language": "json", "rich_text": _rich_text(envelope)}}]
    props["Cloud Managed Hash"] = {"rich_text": _rich_text(_managed_hash(props, blocks))}
    request = {"parent": {"type": "data_source_id", "data_source_id": data_source_id},
               "properties": props, "children": blocks}
    if len(canonical_json(request).encode("utf-8")) > 490_000:
        raise ValueError("index_storage_capacity_exceeded")
    return request


def _storage_data_source_id(store):
    # Preview/shadow wrappers retain the concrete store under .base. Synthetic
    # stores without a concrete Notion parent use the existing default only.
    seen = set()
    while store is not None and id(store) not in seen:
        seen.add(id(store))
        parent = getattr(store, "data_source_id", None)
        if isinstance(parent, str) and parent:
            return parent
        store = getattr(store, "base", None)
    return DEFAULT_DATA_SOURCE_ID


def _preflight_capacity(store, records):
    parent = _storage_data_source_id(store)
    try:
        for row in records:
            _notion_write_request(row, parent)
    except (ValueError, TypeError, KeyError):
        raise IndexPublicationError("index_storage_capacity_exceeded") from None


def _put_verified(store, row):
    try:
        written = store.put(row)
    except Exception:
        raise IndexPublicationError("index_write_uncertain_stop_no_retry") from None
    if not isinstance(written, dict) or any(written.get(key) != row[key] for key in CORE):
        raise IndexPublicationError("index_write_readback_mismatch_stop_no_retry")
    try:
        readback = store.get(row["id"])
        if readback is None:
            # A fully verified put can precede stable query visibility. Poll
            # only absence through fresh reads, never replay the write or
            # recover a non-None mismatch, conflict, or transport failure.
            sleeper = getattr(store, 'sleeper', None)
            if callable(sleeper):
                for delay in (1, 2, 4):
                    sleeper(delay)
                    readback = store.get(row["id"])
                    if readback is not None:
                        break
    except Exception:
        raise IndexPublicationError("index_readback_uncertain_stop_no_retry") from None
    if not isinstance(readback, dict) or any(readback.get(key) != row[key] for key in CORE):
        raise IndexPublicationError("index_readback_mismatch_stop_no_retry")
    return _canonical(readback)


def _reconciled_source_proof(source, reconciled_export, cutover_record):
    """Bind genuine cutover evidence to every restored historical CORE row."""
    from .cutover import validate_cutover_record
    from .migration import SCHEMA as MIGRATION_SCHEMA
    try:
        cutover = validate_cutover_record(cutover_record)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False, ['cutover_evidence_invalid']
    if (not isinstance(reconciled_export, dict) or reconciled_export.get('schema') != MIGRATION_SCHEMA
            or reconciled_export.get('blocked') != [] or not isinstance(reconciled_export.get('records'), list)
            or not reconciled_export['records']):
        return False, ['reconciled_export_incomplete']
    if cutover['payload']['historical_manifest_sha256'] != content_hash(reconciled_export):
        return False, ['reconciled_export_deployment_hash_mismatch']
    baseline = {}
    try:
        for row in reconciled_export['records']:
            canonical = _canonical(row)
            if canonical['id'] in baseline:
                return False, ['reconciled_export_identity_conflict']
            baseline[canonical['id']] = canonical
    except IndexPublicationError:
        return False, ['reconciled_export_invalid']
    report = cutover['payload']['history']['readback']
    families = dict(Counter(row['kind'] for row in baseline.values()))
    if report['records'] != len(baseline) or report['families'] != families:
        return False, ['reconciled_export_readback_totals_mismatch']
    observed = {row['id']: row for row in source}
    if observed.get(cutover['id']) != cutover:
        return False, ['cutover_evidence_not_in_fresh_source']
    for identity, expected in baseline.items():
        if observed.get(identity) != expected:
            return False, ['reconciled_export_source_missing_or_changed']
    return True, []


def _aware_time(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _publication_time(now, previous, publication_clock):
    if publication_clock is None:
        return now  # Explicit-time historical constructors remain deterministic.
    if not callable(publication_clock):
        raise ValueError("Index publication requires a callable clock")
    try:
        published = publication_clock()
    except Exception:
        raise ValueError("Index publication clock unavailable") from None
    if not _aware_time(published):
        raise ValueError("Index publication requires an aware timestamp")
    if published < now:
        raise ValueError("Index publication clock moved backwards")
    if previous is not None:
        try:
            prior = datetime.fromisoformat(previous["payload"].get("generated_at", "").replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Previous index requires an aware publication timestamp") from None
        if not _aware_time(prior) or published < prior:
            raise ValueError("Index publication clock precedes previous manifest")
    return published


def publish_index(store, now: datetime, source_complete=False, source_snapshot_hash=None,
                  *, reconciled_export=None, cutover_record=None, publication_clock=None):
    """Publish children first, then their manifest, returning a private result.

    A fresh read is necessary but does not itself prove reconciliation. Ready
    tasks require ``source_complete=True`` plus a matching, nonempty snapshot
    hash (a string or ``{hash, complete, conflicts}`` evidence envelope). A hash
    mismatch publishes an explicitly incomplete index with no ready actions.

    Production can instead supply the authenticated restored export and the
    actual cutover record. Their manifest hash, exact readback totals and every
    immutable historical CORE row must match the fresh source snapshot. A
    supplied partial/invalid proof never falls back to caller authority flags.

    ``result['manifest']`` is for internal use; operational logs should print
    only the remaining bounded summary fields. After an exact put, only absent
    independent readbacks receive bounded read-only visibility polling. Failed
    writes, read errors, and non-None mismatches stop without a write replay.

    An optional trusted publication clock is sampled once after fresh source
    and previous-manifest reads. It must be aware and nondecreasing. Without
    that callable, the explicit ``now`` remains the deterministic build time.
    """
    if not _aware_time(now):
        raise ValueError("Index publication requires an aware timestamp")
    if not isinstance(source_complete, bool):
        raise ValueError("source_complete must be a boolean")
    snapshot = _read_sources(store)
    previous = _previous_manifest(store, snapshot)
    published = _publication_time(now, previous, publication_clock)
    source = [row for row in snapshot if not _generated(row)]
    actual_hash = snapshot_hash(source)
    evidence_conflicts = []
    if isinstance(source_snapshot_hash, dict):
        expected_hash = source_snapshot_hash.get("hash")
        evidenced = source_snapshot_hash.get("complete") is True
        raw_conflicts = source_snapshot_hash.get("conflicts", [])
        if not isinstance(raw_conflicts, list):
            raise ValueError("Invalid snapshot evidence conflicts")
        # Public fixed codes avoid copying a source error or private message.
        evidence_conflicts.extend("source_reconciliation_conflict" for _ in raw_conflicts)
    elif source_snapshot_hash is None or isinstance(source_snapshot_hash, str):
        expected_hash, evidenced = source_snapshot_hash, source_snapshot_hash is not None
    else:
        raise ValueError("Invalid snapshot evidence")
    matching = isinstance(expected_hash, str) and bool(expected_hash) and expected_hash == actual_hash
    if reconciled_export is not None or cutover_record is not None:
        proven, proof_conflicts = _reconciled_source_proof(source, reconciled_export, cutover_record)
        evidence_conflicts.extend(proof_conflicts)
        if source_snapshot_hash is not None and not matching:
            evidence_conflicts.append('source_snapshot_evidence_missing_or_changed')
        complete = proven and bool(source) and not evidence_conflicts
    else:
        complete = source_complete and evidenced and matching and bool(source) and not evidence_conflicts
        if source_complete and not matching:
            evidence_conflicts.append("source_snapshot_evidence_missing_or_changed")
        if source_complete and not source:
            evidence_conflicts.append("empty_authoritative_snapshot_unreconciled")
    indexed = build_index(source, published, previous_manifest=previous,
                          source_snapshot_hash={"hash": actual_hash, "complete": complete,
                                                "conflicts": evidence_conflicts})
    manifests = [row for row in indexed if row["kind"] == "index_manifests"]
    if len(manifests) != 1:
        raise IndexPublicationError("index_builder_manifest_invalid")
    logical_manifest = manifests[0]
    try:
        pages, manifest = pack_manifest(logical_manifest)
    except ManifestError as error:
        code = "index_storage_capacity_exceeded" if error.code == "manifest_capacity_exceeded" else "index_builder_manifest_invalid"
        raise IndexPublicationError(code) from None
    children = [row for row in indexed if row["kind"] != "index_manifests"]
    physical = [*children, *pages, manifest]
    # Reject any semantic child, storage page or root that cannot be represented
    # by the existing Notion writer before publishing even a harmless prefix.
    _preflight_capacity(store, physical)
    for row in [*children, *pages]:
        _put_verified(store, row)
    verified_manifest = _put_verified(store, manifest)
    return {"manifest_id": verified_manifest["id"], "manifest_hash": verified_manifest["hash"],
            "source_snapshot_hash": actual_hash, "complete": logical_manifest["payload"]["complete"],
            "counts": logical_manifest["payload"]["counts"], "record_count": len(physical),
            "created_or_verified": len(physical), "manifest": verified_manifest}
