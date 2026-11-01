# Bounded manifest publication and reader integration

The frozen semantic index builder produces a valid logical v1 manifest that can exceed Notion's existing rich-text and request limits. The storage codec owns only its physical representation. Integrating it must preserve semantic records, publication order, authority checks and reader budgets.

## Scope and interfaces

- Modify only `cloud/index_publisher.py` and `cloud/agent_protocol.py`; add synthetic integration tests.
- Consume `cloud.index_manifest.pack_manifest(logical_record) -> (bottom_up_pages, physical_root)` and `ManifestView(raw_record, get_record)` as a logical payload Mapping. The codec implementation is independently owned.
- Keep `cloud/agent_index.py`, the V3 operational files, Notion serialization, application gates, credentials and hosted configuration unchanged.
- No network calls, write retries, new external effects, live dispatches or replay of unresolved writers are added by this unit.

## Publisher

Hydrate a previous physical manifest into its exact logical v1 record before sampling the publication clock. Feed that record to the unchanged semantic builder. Small manifests retain their original CORE. Large manifests use immutable directory pages, with the physical root as the last visibility boundary.

Before the first put, validate the complete physical plan against the exact frozen Notion request representation. Reuse `_rich_text` and `_managed_hash`, including the existing 100-item and 490000-byte limits. Determine the actual data-source ID through store wrappers. Do not truncate values, widen limits or infer byte capacity from character counts. A semantic child that cannot fit stops the publication before any prefix is written.

Publish semantic children, then codec pages in bottom-up order, then the physical root. Each record retains the existing exact put and independent fresh-readback checks, without write replay. Summaries report logical health/counts, physical root identity and actual physical record count. Missing or corrupt prior pages stop before the publication clock or writes.

## Reader

Retain the raw physical manifest record for callers that compare its identity. Internal payload reads use a separate ManifestView with `self._get` as its lazy getter so store-call usage includes directory reads. A fresh authority read always discards the previous view and page cache, even if root identity is unchanged.

Logical header fields drive health, counts and the existing first-40 task window. Selected projection/task/action maps accept Mapping values. Paged projection lookup requires published membership; schema-less inline fixtures retain their existing fixed-ID compatibility. Queued action-result lookup retains its direct ACK fallback. Paged change traversal uses a bounded codec cursor window and hydrates only those change records. All inline, paged and legacy metadata paths reject typed manifest storage pages as public change events, including after an index shrinks to inline storage.

Translate codec errors to fixed protocol storage errors; preserve the existing expired-cursor code. Selected missing/tampered pages must prevent intake validation before any dispatcher write. Existing context/evidence byte budgets, grants, task windows, response fields and legacy fixture behavior remain in force.

## Evidence

Use a 400-contact fixture produced by the actual frozen builder (about 278 KB logical manifest), the actual frozen `NotionStore.put` with a synthetic transport, differential request-body checks including Unicode, and fresh-cache corruption tests. All validation is local and contains synthetic data only. Root owns combined testing, review, deployment and hosted reconciliation.
