# Prompt 2 — Shared state and agent-neutral protocol (TDD)

Implement Phase 2 of `docs/product/technical-intelligence-prd.md` in `narendranathe/apply`. Follow the agreed acceptance criteria and architecture decision. Notion Hiring & Outreach remains the operational source of truth; GitHub Actions remains the single coordinated operational writer. Reuse `cloud/agent_protocol.py`, `cloud/agent_index.py`, `cloud/agent_dispatcher.py`, `cloud/agent_worker.py`, and existing records and Notion adapters.

**Write failing tests first.** Add schemas and canonical IDs for: technical source/item, engineering observation, evidence-grounded signal, hypothesis, improvement proposal, contact-to-topic relevance, engagement intent/outcome, content draft/revision, publisher intent/receipt, source cursor, and next-action task. Each record must carry source URL/identity, author if verified, first-seen/source-published time, observed time, content hash, freshness, confidentiality, evidence references, status, and actor; keep original private content out of public logs and Git commits.

Extend—not fork—the existing compact agent protocol so any authorized agent can fetch a short daily briefing, prioritized next tasks, one contact/company/technology dossier, recent deltas, and deep evidence only on demand. Reuse source research across agents, track change cursors, and require verification that state was persisted before claiming completion. Enforce suppression/opt-outs and semantic deduplication across existing applications and connections.

Tests must cover different agents performing the same intent, racing writes, stale evidence, token/response size ceilings, denied capabilities, corrupted/partial Notion records, duplicate intake, and ambiguous post-write readback. Notion does not offer transactional locks: use the existing serialized central writer, deterministic idempotency, bounded reconciliation, and durable reservations rather than a naive `Owner` field lock.

**Red → Green → Refactor:** Show tests failing for missing behavior, implement minimal code, rerun focused and full offline suites, refactor without weakening checks. Use synthetic fixtures; never call real platforms from unit tests. Ensure migrations are append-only and backwards-compatible with the historical immutable records and old contact states.

**Exit criteria:** Working local tests, documented protocol extensions with examples for Codex/Muse/other agents, measured bytes/tokens and Notion request counts, schema compatibility proof, and a PR. No live data or provider effects during development.
