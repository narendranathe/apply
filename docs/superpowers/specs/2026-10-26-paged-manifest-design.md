# Bounded agent index manifests

The logical v1 index can exceed the existing Notion rich-text limit even when all of its individual child records fit. This codec changes physical storage only. The exact old semantic builder, historical reconstruction and five V3 recovery files remain unchanged. Actual attribution of the latest production incident remains a separate authenticated-evidence gate.

## Interface and storage

The new cloud/index_manifest.py module exposes pack_manifest(logical_record) and ManifestView(record, get_record). A canonical record of at most 48 KiB UTF-8 remains inline. Larger records become a physical index_manifests root with schema agent-index-manifest/v2, status published, outer generated_at, bounded logical header, logical ID/hash/status and a fixed set of section roots. It has no outer complete=true field; legacy readers therefore fail closed.

Seven unbounded logical sections are projections, entity_versions, task_map, action_results, changes, change_cursors and conflicts. A derived change_positions map locates a cursor without scanning the sequence. Maps use sorted key ranges; sequences use ordered subtree counts. Every page is an immutable index_changes record with schema agent-index-manifest-page/v1 and status manifest_page. Pages never enter the public logical changes list. The existing source query already excludes this physical family, avoiding unbounded source-read growth.

Each page is at most 16 KiB canonical UTF-8, branches have at most 16 children, and a tree has bounded depth. IDs are content addressed and contain neither parent identity nor publication time. Unchanged pages can be reused. Empty sections have explicit empty descriptors.

The physical root payload has exactly schema, generated_at, header, logical_id, logical_hash, logical_status, logical_sections and sections. Its ID is stable_id("index_manifest_root", hash_of_root_payload). logical_sections records which of the seven original sections existed, so a legacy absent field is not silently added during reconstruction. sections always contains those seven trees plus change_positions. The logical header retains the original schema, complete flag, brief, counts, source hash, cursors, generated time and at most forty task IDs.

A section descriptor has id, hash, count and depth; map descriptors also have first and last string keys. Empty descriptors use null identities and range bounds, count zero and depth zero. Page payloads have exactly schema, section, node, count, depth and either entries or children. Map leaves hold ordered [key,value] pairs; sequence leaves hold the original ordered values. Branch children share one depth and their counts sum to the parent count; map child ranges are strictly increasing and nonoverlapping. Maximum depth is sixteen. A record that cannot satisfy these finite bounds fails before storage, with no truncation. Repeated identical sequence subtrees are valid content reuse rather than cycles.

ManifestView is a logical-payload Mapping, not a dictionary or a forged record. Its record property retains the canonical physical CORE; payload aliases the view. Header exposes bounded logical metadata. Lookup resolves a map key, window reads at most forty ordered items, change_window returns at most twenty changes with cursors, and materialize reconstructs the original valid v1 record with its exact logical ID, hash and status. The injected getter must be the caller's accounted read method. View caches belong to one immutable root and must be replaced for fresh authority checks.

The public signatures are pack_manifest(logical_v1_record) -> (bottomup_pages, physical_root) and ManifestView(record, get_record). The view also provides is_paged, lookup(section,key,default=None), window(section,offset,limit), change_window(since_cursor=None,limit=20) and materialize(). change_window returns changes, cursors, next_cursor, latest_cursor and has_more. A cursor lookup reads its indexed offset and verifies the corresponding sequence cursor, then reads only the requested window. Unknown cursors report cursor_expired_refresh_brief; other errors use static manifest error codes. Caller-owned response and evidence byte budgets still apply after retrieval.

## Validation and compatibility

The codec validates immutable hashes, page IDs, schemas, sections, statuses, descriptor counts/depths, sorted nonoverlapping key ranges, contiguous sequence counts and size bounds. Missing or altered pages, unsupported formats and oversized indivisible values fail using static error codes. Materialization verifies the complete logical checksum and cursor lookup agreement. There is no compression, data truncation, increased Notion limit or permission change.

The immutable semantic-builder SHA-256 remains 7a63ce93fb1fdeee5c1df41bee9e3fd4c4ad07bec9b36a9bbd9dabfafe9bf6d5. Whole-snapshot differential verification must compare decoded logical records with that unchanged builder rather than attaching the physical root hash to a hydrated payload.

Old inline manifests, including schema-less protocol fixtures, remain readable. New physical roots keep actual storage identity separate from logical identity. A page cannot masquerade as a public change event. The codec performs no storage writes, network requests, credential access or Git operations.

## Integration seam

Publisher integration is owned separately. It materializes the previous logical manifest before the publication clock and unchanged builder call; preflights every planned wire envelope before any put; publishes semantic children and page children with exact readback; and publishes the physical root last. AgentProtocol consumes the view through its own getter and retains its response/evidence byte budgets, grants, freshness checks and queued acknowledgement behavior. SnapshotState already stores the generic physical records.

## Verification

Tests cover small inline and large legacy-builder manifests, exact round trips, 334 and larger entity sets, Unicode and escaped values, page reuse, bounded key/window/cursor reads, corrupted or missing records, range/ordering/depth/duplicate failures, old-reader refusal of new authority, and preservation of the frozen builder byte hash. Hosted validation and actual failed-run reconciliation remain root-owned gates.
