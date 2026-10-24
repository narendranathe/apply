# Bounded Candidate Progress Implementation Plan

> **For agentic workers:** Use the existing assigned implementation agent and independent production verifier. Do not stage, commit, push or mutate operational state; root owns deployment after authenticated central recovery.

**Goal:** Advance bounded daily research and application qualification beyond previously covered feed prefixes.

**Architecture:** Stable priority partitions use unchanged canonical matching and durable application/job observations. All existing verification and effect gates remain in their original loop.

**Tech Stack:** Python standard library, unittest, existing immutable records and synthetic ATS transports.

## Global Constraints

- Keep `max_candidates=5`, submission pacing and runtime budgets unchanged.
- Never rewrite unknown, submitting, applied, attempted or employer-ambiguous history.
- No new cursor, record kind, alias, credential, permission, provider or paid service.
- Do not modify the five bound recovery protocol files, orchestrator or index publisher.

## Steps

- [x] Add focused tests in `tests/test_candidate_progress.py`: two-run blocked-prefix regression, three bounded runs exhausting twelve roles, prepared/qualified priority, all suppressed statuses/attempts/aliases, broad employer uncertainty and distinct requisitions.
- [x] Add focused discovery tests in `tests/test_discovery_progress.py`: durable job-only coverage advances the second run, application coverage priority, AI allocation across fresh/covered pools, fallback rechecks and no external effects.
- [x] Run the new suites against existing code and record expected selection failures.
- [x] In `cloud/applications.py`, add one pure suppression predicate and stable ordering helper returning eligible order, suppressed count and ambiguous-hold count. Hold explicit unresolved history and missing-requisition broad matches without an existing URL intersection before budgeting and in both existing identity checks. Exact-URL parser placeholders remain retryable; no row is rewritten by selection.
- [x] In `cloud/discovery.py`, partition unmatched/covered using applications and existing jobs, reuse unchanged AI classification and allocation, and fill covered capacity without raising the overall target.
- [x] Run new suites, related application/discovery suites and full unittest suite. Run compile and whitespace checks. Inspect the diff for identity or permission changes.
- [x] Freeze owned files and give exact test commands/results to `/root/production_verifier`; address verified review findings, then hand final files to root without Git mutations.

- [x] Apply strongest suppression across every matching already-loaded application row at budgeting and both official identity checks, independent of dictionary order.
- [x] Require exactly one fresh matching whole CORE equal to the expected eligible application immediately before provider dispatch; extra identities and changed COREs stop with the existing claim-conflict error.
- [x] Preserve incoming feed effect claims as stable independent unknown observations, even when matching prepared/verified history exists; verify exact readback and idempotence across export changes.
- [x] Preserve typed application-event claims using their existing independent identity and exact CORE readback, without interpreting ordinary outreach prose.
- [x] Validate existing source claims by original immutable raw record ID from the same application query, preserving later folded outcomes and rejecting actual original-CORE corruption.
- [x] Complete independent safety review and final focused/full verification.

Final validation: 69 related application/discovery tests pass; the complete suite runs 709 tests with 708 passes and one existing PDF-tool skip. Compile and whitespace checks pass. Independent review reproduced the original mixed-order, feed-claim and typed-event hazards, then verified zero provider/preparation effects and preservation of prior COREs after correction. Whole-CORE source corruption still stops, while valid later outcomes remain idempotent. Source and tests are frozen; root owns deployment and hosted validation.
