# Prompt 6 — End-to-end TDD, safe recovery, hosted rollout and proof

Integrate the PRD implementation in `narendranathe/apply` end to end. FIRST inspect current main, PR #28, `docs/runbooks/INDEX_CUSTODY.md` if present, the cloud cutover runbook and the latest GitHub Actions capacity/billing state. Do not restart historical migration. The October 29 checkpoint had scheduled jobs rejected before execution because included Actions minutes were exhausted under a $0 budget; verify whether capacity has actually returned instead of assuming the November 1 reset occurred.

**Write failing end-to-end tests BEFORE integration changes:**
- Source article arrives → source record → cited technical observation → labeled hypothesis → engineering proposal → reusable Notion context.
- Codex researches an engineer → drafts a substantive engagement → later agent retrieves the same state without chat history → no duplicate invitation/comment.
- A reply or new message arrives through an actually authorized source → contact priority changes → stale draft is invalidated.
- One source-backed thesis → LinkedIn/X/Substack channel drafts → permitted publisher executes at most once → real receipt is independently verified; blocked platforms stay manual.
- Two agents concurrently propose the same task → central writer admits at most one semantic action, including after failure/restart.
- Runner crashes after attempted submission or publication → no blind retry; exception/reconciliation preserves uncertainty.
- No access to real LinkedIn inbox → clearly reports `not checked`, never guesses live acceptance or replies.
- Mac powered off → GitHub-hosted read/research/summarization still works if Actions capacity and credentials are available.

Run offline/unit/contract/security tests and privacy checks first; document the red-green-refactor sequence and link code changes to PRD acceptance criteria. Require hosted CI on exact merged candidate revision; verify Notion readback, immutable history, complete/recoverable index, encrypted artifact restore, full writer reconciliation, idempotent rerun and consistent UTC/Chicago scheduling.

Use existing 06:00/06:30 America/Chicago schedules for operational work; avoid adding duplicate cloud schedulers. New content research can be batched into the established research phase; do not auto-publish to meet a schedule without a permitted writer and authorization. Keep `CLOUD_ENABLED=false` until PR #28 recovery and actual production gates complete. Do not bypass failed checks, spend money, modify billing, enable unapproved provider effects, or retry unknown delivery. If Actions capacity remains exhausted, leave the PR unmerged or draft and produce an exact checklist; offline tests do not count as hosted proof.

**Final deliverables:** A requirement-to-test-to-commit matrix, provider capability and permissions table, exact successful hosted CI and workflow URLs, validated sample end-to-end Notion traces, no-duplicate proof, cost/token and runtime metrics, manual vs unattended functionality matrix, current blockers, reversible activation checklist and operational runbook. Explicitly state what works today; do not claim LinkedIn live inbox, automatic publishing or application submission without verified evidence.
