# Existing Notion cloud storage

The existing [Hiring & Outreach hub](https://app.notion.com/p/3e3e10221fac81af955fcd0c2f452951) remains the only CRM. Its data source is `d932083f-8d8a-4570-a027-025bbfffb878`, under database `cbb3d686-81d6-4b88-a6ef-acdf90aa0b02`. Notion persists records; GitHub Actions executes the lanes. No second database was created.

## Additive schema

The following properties were added and read back through the connected Notion integration. Existing fields, options, page bodies, and historical event identities were preserved.

| Property | REST type | Meaning |
| --- | --- | --- |
| Cloud ID | rich_text | Immutable deterministic identity |
| Cloud Kind | rich_text | Record family, including contacts, jobs, companies, applications, audit_events, agent_runs |
| Cloud Hash | rich_text | SHA-256 of canonical JSON payload |
| Cloud Status | rich_text | Status observed by this immutable record |
| Cloud Envelope | rich_text | Complete canonical `{id,kind,payload,status,hash}` envelope |
| Cloud Managed Hash | rich_text | SHA-256 of owned property values and canonical owned body |
| Section | select, Cloud | Distinguishes managed rows from original human records |

New managed rows also use existing `Type=Reference` and `Status=Done`. These keep machine records out of the action Queue, while preserving visibility in History. `Name` is a deterministic readable title derived from the immutable envelope: person/role/company/status, lane/date/status, or kind/short ID/status. A JSON code block contains the same envelope for complete inspection.

The existing database has new tabs for Cloud operations, applications, jobs, contacts, run log, exceptions, and [funnel/experiments](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f7e10221fac81e5891e000c4837db6b). These are filters on this same data source. The exception filter includes `needs_reconciliation` writer failures. Application rows are immutable transitions; the latest state is derived by application ID and revision. Views show historical transitions as well as the newest one and do not claim to be transactional materialized current-state tables.

## Client interface

`cloud.notion.NotionStore(token=None, data_source_id=...)` reads `NOTION_TOKEN` when no token is supplied. `NotionStore.from_env()` also supports `NOTION_DATA_SOURCE_ID`, defaulting to the existing data source above. The desktop connector credential is not a GitHub Actions credential. Configure a separate limited-scope integration as an Actions secret and grant it access to this existing database. Read and insert capabilities are needed for normal operation; schema modification was a one-time additive setup action.

| Method | Contract |
| --- | --- |
| `get(record_id)` | Read and validate one managed envelope; fall back to original Event ID and complete payload-v1 for historical events |
| `put(record)` | Fresh schema/identity preflight, immutable idempotent create, exact post-write readback; existing different content/status is a conflict |
| `query(kind=None)` | Paginate and validate managed records; `outreach_events` explicitly merges and validates original Event ID history |
| `preflight()` | Fresh data source property and option checks |
| `source_rows(kind=None)` | Fresh original rows and recursive bodies, optionally filtered by original Type such as Contact/Opportunity/Task |
| `current_contact_observations()` | Fresh original Contacts observations, including current No Contact/Status suppression; never grants identity/history verification |
| `snapshot_all()` | Full data source schema, original and managed rows, properties, and recursive bodies for encrypted backups |

Normal records return `id`, `kind`, `payload`, `status`, and `hash`, with Notion page ID/URL readback metadata. Complete plain-rich-text properties are paginated when the retrieve-page response may be truncated. Every data-source and block cursor is followed, including empty pages with a continuation. Missing/repeated cursors, exhausted budgets, Notion incomplete-query flags, and unsupported snapshot blocks fail closed.

## Immutability and human edits

All production writers must share one GitHub Actions concurrency group. Notion has no transaction, unique constraint, or compare-and-swap primitive that guarantees exactly-once creation. The client detects duplicates and verifies readback; it does not claim mathematical exactly-once behavior.

A status transition, correction, new verification, or new source observation appends a new deterministic record referencing the prior one. Reusing an ID with a changed payload, kind, or status is rejected even if its payload hash is unchanged. Machine-owned fields and the machine body must not be edited manually. Changed/cleared Cloud ID is also checked through envelope identity, preventing a hidden row from silently becoming a new duplicate. Changing an owned value/body/formatting without the expected managed hash raises `ConflictError`; it is never automatically repaired.

Naren can edit original Contacts/Queue properties and their bodies. Original rows remain human-owned and are freshly observed by `source_rows()`; they are not overwritten by a Cloud import. A newly set `No Contact`, or original `Status=Suppressed`, is returned immediately as suppression. New verification requires explicit source evidence; imported contacts start with `identity_verified=false`, `history_complete=false`, and `verified_at=null`. Existing human fields outside the managed projection, such as Next Action, remain editable; comments are appropriate for notes about a managed record. Append a correction for an immutable machine-data change.

For original event history, Event ID plus exact payload-v1 and raw-content SHA-256 are authoritative. The historical `Content Hash` hashes the raw message string; `Cloud Hash` hashes the complete JSON payload. They are different hash schemes. Old select properties mapped unsupported values and sometimes diverge from immutable payload fields. Those display differences are preserved as `legacy_property_differences`; a display label such as Sent verified never upgrades the original receipt or delivery state. Approved historical metadata variants require the original correction proof; they do not authorize general mutation.

## Rate limits and uncertain writes

The stdlib REST client pins `Notion-Version: 2025-09-03` and rejects redirects. Read requests retry 429/529 and transient 500/502/503/504 within a four-attempt budget. Explicit 429/529 write rejection may retry; timeout, malformed response, network failure, or other write 5xx raises `AmbiguousWriteError` without replay. Retry-After is honored; a delay beyond the 30-second budget fails instead of retrying early. Restricted connection errors are not retried. Error messages exclude raw response bodies and tokens.

An unresolved write is quarantined within the current process. Exact subsequent readback may reconcile it. Absence after an ambiguous attempt is not proof that creation failed. After a crash, orchestration must retain its unresolved run marker, block subsequent live external effects across all lanes, and require reconciliation. If the initial marker itself has an ambiguous write, revoke the cutover gate and inspect Notion by deterministic ID before another live run. GitHub's one-writer concurrency and unresolved-run gating are operational safeguards, not a replacement for a transaction.

Notion text chunks are at most 2,000 characters, each rich-text array has at most 100 items, and request size is bounded below 500 KB. Oversized packets fail with an encrypted-archive-reference requirement; they are not silently truncated. This client never sends LinkedIn messages or submits applications.

## Implementation and verification

- Canonical identities, aliases, exact JSON hashes, and readable private titles: `cloud/records.py`.
- REST pagination, immutable storage, full snapshots, and fresh human observations: `cloud/notion.py`.
- API-boundary tests including pagination caps, duplicate IDs, missing schema, manual edits, legacy full payloads, suppression updates, rate-limit rejection, and uncertain writes: `tests/test_notion.py`.

Run `python3 -m unittest discover -s tests -p test_notion.py -v`. These tests simulate the REST API boundary; they are not proof that an Actions runner has a working token. Live token, permissions, historical import, encrypted restore, and Mac-off execution are separate deployment gates.

The client follows the official [data-source query contract](https://developers.notion.com/reference/query-a-data-source), [block pagination](https://developers.notion.com/reference/get-block-children), and [request limits and retry guidance](https://developers.notion.com/reference/request-limits). Queries capped at 10,000 results are rejected until partitioned; terminal `has_more=false` alone does not imply a complete capped query.
