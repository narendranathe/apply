# Codex PRD + TDD prompts — technical intelligence, engagement, publishing

This pack is for the existing private `narendranathe/apply` project. It is NOT a replacement for the cloud migration prompts, database, Notion strategy, or current cloud recovery.

## Recommended usage

1. Place these Markdown files inside `apply/docs/prompts/technical-intelligence/` (do not commit a ZIP archive).
2. Start ONE Codex implementation conversation with the `apply` repository selected.
3. **One-paste option:** Paste: `Read and execute docs/prompts/technical-intelligence/00_MASTER_ORCHESTRATION.md. Follow all six numbered PRD/TDD phases in order, with real red-green-refactor tests, reviewable PRs, and verified hosted deployment gates.`
4. **Manual phase option:** Run each numbered prompt in order only after its predecessor's acceptance criteria and tests are met. Ask Codex to update the PRD traceability matrix and create reviewable PRs for each phase.
5. Review GitHub-hosted CI before merging. Do not mistake local passing tests for hosted operation. GitHub Actions capacity/production recovery is currently a separate gate; preserve existing schedules and source data.

## Files

- `00_MASTER_ORCHESTRATION.md` — one Codex conversation to execute all six stages.
- `01_PRD_SCOPE_AND_TEST_PLAN.md` — PRD, ADR and acceptance test plan.
- `02_TDD_SHARED_STATE_AND_AGENT_PROTOCOL.md` — shared Notion records and compact agent context.
- `03_TDD_PUBLIC_SOURCES_AND_TECHNICAL_INFERENCE.md` — public source ingestion and grounded technical analysis.
- `04_TDD_CONNECTIONS_AND_ENGINEERING_ENGAGEMENT.md` — connection histories and meaningful post engagement.
- `05_TDD_EDITORIAL_LINKEDIN_X_SUBSTACK.md` — original content and evidence-based publishing lifecycle.
- `06_INTEGRATION_E2E_AND_CLOUD_RELEASE.md` — integration tests, recovery, CI and release proof.

All provider integrations must be validated against current official documentation and actually granted permissions. Missing platform access is a capability exception, never an invitation to scrape or to claim successful sending.
