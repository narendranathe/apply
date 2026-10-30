# Master prompt — execute the PRD/TDD program in one Codex conversation

Work in my existing PRIVATE GitHub repository `narendranathe/apply`. Read all seven files in `docs/prompts/technical-intelligence/`, beginning with `README.md` and then executing the six numbered phase prompts `01` through `06` IN ORDER. Treat each phase file as a detailed, binding specification; this instruction does not replace their acceptance criteria.

## Product outcome

Build an **agent-independent technical intelligence, professional relationship and publishing system** that uses my existing Notion Hiring & Outreach workspace and cloud orchestration. Codex, Muse, and future agents must share durable, compact, evidence-grounded state. It should research company engineering blogs and permitted public technical posts, distinguish verified signals from hypotheses about engineering priorities, formulate technically substantial improvements and counterarguments, recommend authentic engagement with contacts, manage invitations/conversation history, and create original LinkedIn, X, and Substack content with verifiable publication status.

## Development process (mandatory)

1. **PRD before code:** Inspect the actual current codebase, Notion strategy, production status and PR #28. Write the PRD, ADR, capability matrix, measurable acceptance criteria and test-traceability matrix from Prompt 01. Preserve the existing system and existing controls; do not restart the historical migration.
2. **Strict TDD for each phase:** Show an actual failing relevant test against missing behavior, implement the smallest working change, rerun tests, then refactor. Prefer deterministic fixture, contract and integration tests. Treat model outputs as uncertain evidence to evaluate; never turn invented facts into assertions.
3. **Work through Prompts 02–05 in dependency order**, updating requirement-to-test-to-code traceability after each phase. Reuse existing Notion, agent contract, GitHub Actions, suppressed/uncertain contact and application states. Put code and docs in separate reviewable PRs where practical; avoid an unreviewable mega-commit.
4. **Execute Prompt 06 last:** Prove integration and hosted operation only with actual runner, Notion readback, receipts, and reproducible test evidence. Honor the current GitHub Actions capacity limits; do not claim hosted success from local tests. Do not spend money or change billing settings without my authorization.
5. **No invented integrations:** Verify current official LinkedIn, X and Substack provider permissions before enabling social reading, sending, reacting or publishing. If unsupported or ungranted, implement honest drafts/imports/manual workflow and mark capability unavailable. No unauthorized scraping, private APIs, browser cookie/session copying, spam or fabricated likes and sends.
6. **Preserve my application preference:** Never reinstate blanket per-application approval for truthful, verified, eligible submissions through permitted integrations. Existing uncertainty/idempotency/provider gates still apply.
7. **Stay safely deployable:** Keep `CLOUD_ENABLED=false` until existing custody/PR #28 recovery and production gates are genuinely passed. Keep the existing 06:00/06:30 America/Chicago schedules, no competing writer, and no hidden local-Mac dependency.

## Output contract

At the end of each phase give: PR/commit URL, exact files changed, test cases added and their red→green evidence, documentation links, limitations, and remaining platform/account dependencies. Keep working through subsequent phases whenever safe and access allows; stop only for real credentials, consent/permissions, spending decisions, or failed mandatory deployment gates. At the end give a truthful matrix of **implemented**, **hosted-verified**, **manually executable**, and **not possible with current permissions**.
