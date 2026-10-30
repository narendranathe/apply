# Prompt 1 — Product requirements document and test plan

Act as the product manager, staff engineer, and test architect for the EXISTING private repository `narendranathe/apply`. Extend—not replace—its Notion Hiring & Outreach source of truth, agent-neutral protocol, legacy outreach rules, and 06:00/06:30 America/Chicago GitHub Actions schedules.

**Goal:** Build an agent-independent technical-intelligence and professional-engagement system: understand engineering blogs and public technical posts; identify evidenced engineering priorities and improvements; help maintain authentic professional relationships and connection requests; compose original LinkedIn, X, and Substack content; and record every action and outcome for Codex, Muse, and future agents.

**First, inspect current reality:** Read `docs/AGENT_PROTOCOL.md`, `docs/LINKEDIN_INGESTION.md`, `cloud/outreach.py`, `cloud/discovery.py`, `cloud/agent_worker.py`, `docs/runbooks/CUTOVER.md`, `AGENTS.md`, and open PR #28. Read the current Notion Start Here strategy and archived Company Blog Cache design. Classify each desired capability as production-verified, implemented but unverified, designed only, or absent. The older playbook is historical, not executable authority.

**PRD deliverables:** Create `docs/product/technical-intelligence-prd.md`, `docs/product/platform-capabilities.md`, and an architecture decision record (ADR). Include personas and user journeys; measurable user stories; scope/non-goals; source and consent rules; Notion entity/event schema; cross-agent handoff; evidence provenance, freshness and uncertainty; source-to-hypothesis-to-technical-recommendation traceability; contact/suppression policy; editorial lifecycle; per-platform permissions, draft/publish/receipt capabilities; privacy, cost, observability, rollout and rollback.

Require substantive engineering analysis: observed facts vs inferred direction, architectural trade-offs (latency, cost, reliability, safety, developer productivity), alternative explanations, falsifiable hypotheses, likely feasibility, and explicit uncertainty. Do not invent internal team roadmaps or claim private knowledge.

**TDD contract before implementation:** Turn every PRD acceptance criterion into a named failing test plan with fixture data and Given/When/Then examples. Include duplicate articles, edited posts, conflicting authors, stale profiles, partial LinkedIn archives, blocked provider permissions, duplicate publishing, changed content after approval, provider timeouts, ambiguous receipts, cross-agent contention, token budgets, and offline operation.

Inspect official current developer documentation for LinkedIn, X, and Substack before specifying any write adapter. An unsupported API must be recorded as unavailable, not simulated as operational. No unauthorized scraping, automated LinkedIn engagement, credential/session extraction, or claims of actual publication without receipts.

**Constraints:** Preserve truthful standing authorization for eligible job-application paths; do not reintroduce blanket per-application approval. Preserve existing uncertainty holds. Do not expand operational effects or change `CLOUD_ENABLED` while recovery is unresolved. The current Actions allowance/PR #28 issue must be treated as a deployment blocker rather than a reason to skip hosted tests.

**Exit criteria:** Commit the PRD, ADR, capability matrix, acceptance-criteria-to-test traceability matrix, dependency-ordered milestones, and an implementation risk register on a feature branch. Do not build production functionality in this phase. Report exact file paths and which decisions need genuine owner credentials or permissions; use reasonable defaults for everything else.
