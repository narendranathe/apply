# Existing Notion audit, October 11, 2026

The connected desktop integration fetched the existing [hub](https://app.notion.com/p/3e3e10221fac81af955fcd0c2f452951), [version 4 guide](https://app.notion.com/p/3e4e10221fac8103a006f5a8b7f1daa2), database, complete data-source schema, and all paginated Queue/Contacts/History rows. Raw records and message bodies are restricted to ignored `.migration-private/` exports. They are inputs to the encrypted migration archive, never plaintext committed files.

| Existing view | Pages | Rows | Terminal pagination |
| --- | ---: | ---: | --- |
| Queue | 1 | 51 | has_more=false |
| Contacts | 2 | 154 | has_more=false |
| History & reference | 4 | 388 | has_more=false |

The views overlap by one row, producing **592 unique original pages**. Archived queries for all three views returned zero rows and terminal pagination. Every unique page was fetched with its full body. Fetch errors, truncation flags, unknown blocks, and unresolved body conflicts: **0**. One Task returned the connector's explicit `<blank-page>` marker, which is preserved as an empty body.

All **354 Event IDs** had complete payload-v1 JSON. Their raw-content SHA-256 matched both payload content_hash and the original Content Hash property. Against the read-only local journal's 354 events, **352 full payloads matched exactly**. The other **2 were authorized metadata variants**, validated using the source journal's existing `_approved_variant` logic and October 1 correction proofs. No local-only, Notion-only, or unresolved event payload conflict remained. Original local and Notion variants are both preserved; no historical page was overwritten.

There were **214 historical select-property differences** across Actor, Kind, and Delivery State. These are preserved in the private comparison file. Existing select values normalized older labels, including historical observations. They are not evidence of current accepted connections, sent messages, application receipts, or live authorization. Cloud records retain the exact immutable payload rather than upgrading those display labels.

Private normalized export contains **313 records**: 154 contacts, 18 job/opportunity observations, 38 Queue observations, 28 other reference/guide observations, and 75 company observations. Each preserves the exact fetched properties, complete body, source Notion ID/URL, and export/body hashes. Contacts have no duplicate canonical IDs. Original opt-outs and suppression remain active safety constraints. Historical contact identity/history flags start false; last-edited timestamps are provenance rather than proof of current employment, acceptance, or job availability.

The audit's primary private outputs are `notion-event-payloads.json`, `notion-normalized-records.json`, `notion-comparison-details.json`, and `notion-audit-summary.json` within `.migration-private/`, together with all individual body exports. Audit completion timestamp is recorded there in UTC; local audit date is October 11, America/Chicago. The next coordinated import must capture any source delta before cutover.

## Verified integration work

Connector read access and compatible additive schema-write access were demonstrated. New Cloud ID/Kind/Hash/Status/Envelope/Managed Hash and Section properties were fetched again after creation. Existing properties, historical rows, original Queue filters, and contact identities were left intact. New reporting tabs use the same data source:

- [Cloud operations](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f6e10221fac815cbce5000c78c38fbd)
- [Cloud applications](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f7e10221fac814fb9ff000cc6a40ec3)
- [Cloud jobs](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f7e10221fac81e59a4a000cd97c8fc8)
- [Cloud contacts](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f7e10221fac8147a93e000c86f6fd4a)
- [Cloud run log](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f7e10221fac813eba46000c1766ac00)
- [Cloud exceptions](https://app.notion.com/p/cbb3d68681d64b88a6efacdf90aa0b02?v=3f7e10221fac815b8a6b000cce385ce4)

At the storage audit, this local process had no `NOTION_TOKEN`. Connector access does not establish GitHub runner authentication. A runner secret and the integration's explicit access to this same database still need a live read/write/readback smoke test before operational cutover. The audit performed schema/reporting setup and read-only history preservation; it does not claim historical import, cloud deployment, application submission, local-scheduler retirement, or Mac-off operation completed.

See [NOTION_SCHEMA.md](NOTION_SCHEMA.md) for immutable record, human-edit, pagination, uncertainty, and authentication contracts.
