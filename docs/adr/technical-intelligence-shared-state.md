# ADR: Technical intelligence in the existing shared state

Status: proposed, 2026-10-30. Applies to development only; it does not authorize deployment or external effects.

## Context

The existing Hiring & Outreach Notion data source is authoritative and contains immutable historical application and outreach evidence. The cloud orchestrator uses a serialized GitHub Actions writer, immutable envelopes and verified readback. Its agent index publishes bounded projections with an explicit manifest. The archived Company Blog Cache describes reuse, but its permanent-cache rule is superseded by current freshness and live-verification requirements. The database referenced by the old playbook returned Notion `NOT_FOUND` on 2026-10-30; absence of access is not proof of deletion. PR #28 and Actions capacity remain independent release gates.

## Decision

Extend existing record kinds and agent-index mappings for technical source versions, observations, signals, hypotheses, opportunities, relationship intents and outcomes, editorial revisions, publication intents and receipts, cursors and tasks. Keep every change append-only with deterministic semantic identifiers, exact provenance and hashes; reject conflicting identity or incomplete/corrupt evidence. Do not create another Notion database. Reuse existing readback, shadow store, reservations, dispatcher and common writer concurrency; a page owner is not a lock. An ambiguous write fences subsequent effects until reconciliation. A published index becomes authoritative only after the existing custody and manifest checks.

Sources are allowlisted and access-checked, bounded, dated and revision-aware. Compact projections expose links, citations, uncertainty, freshness and next actions; full private evidence remains on demand and in approved private storage. Factual observations and inferred hypotheses remain distinct. A contact action requires the current identity, complete history, suppression and existing authorization gates; cached research or an imported archive cannot prove current inbox state. Editorial changes invalidate prior review of a different hash. Unknown delivery or publication prevents retry until reconciled. A provider response is not a verified receipt without independently retrievable identity and proof.

The 06:00 and 06:30 America/Chicago schedules and current application authorization remain unchanged. Development uses synthetic fixtures and no live business writes. Research-only shadow output is nonpersistent and does not establish unattended durable operation. `CLOUD_ENABLED=false` remains required until the existing recovery and rollout gates pass. No unsupported social effects are enabled.

## Alternatives

1. A new database and independent writer would be easier to prototype but would duplicate history, split suppression and create races. Rejected.
2. Store everything in chat or Git would simplify tooling but lose shared state, expose private content, and prevent durable cross-agent handoff. Rejected.
3. Extend the existing envelope and writer (chosen): more integration work and Notion request cost, but preserves custody and existing safety controls. Notion is not transactional; serialization and reconciliation reduce risk without promising exactly-once delivery.

## Consequences and rollout

Every new kind needs validation, index/protocol mapping, bounded request and storage budgets, privacy review, and backwards-compatible tests. Roll out offline fixtures first, then exact-revision hosted CI, then read-only and shadow validation; activate only after PR #28, complete index custody, restored encrypted backup, reconciliation and idempotent rerun pass. Rollback disables production and preserves all immutable records and uncertainty holds. Missing credentials, permission, budget or trustworthy receipt remain explicit exceptions, not successes.
