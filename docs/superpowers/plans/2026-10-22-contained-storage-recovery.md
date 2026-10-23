# Contained Storage Recovery Implementation Plan

> For agentic workers: use subagent-driven development for independent implementation and review tasks, with root-owned deployment checkpoints.

**Goal:** Reconcile the fixed evidence-only maintenance incident without replaying either uncertain observation, then finish actual cloud production validations.

**Architecture:** A separate pinned V3 maintenance protocol preserves the existing hold and old partial guard. A lossless audit bundle and three proven-unattempted strict resolutions form a new complete cohort, while shared storage permanently rejects the two reserved identities.

**Tech stack:** Existing Python standard library, cryptography, Notion adapter, GitHub Actions and unittest; no new provider, credential or payment.

## Global constraints

- Keep `CLOUD_ENABLED=false`, `CLOUD_RESEARCH_ENABLED=true` until independently authenticated recovery and fresh deployment gates pass.
- Never POST either reserved auxiliary ID. Never infer absent records mean their write failed.
- Leave the old reconciliation and its partial-batch rejection unchanged.
- All operational mutations use the existing common GitHub concurrency group; no independent live writer.
- Preserve all 757 historical COREs, all prior source pages, whole ready record and application/outreach effect signatures.
- No tokens, headers, response bodies, contact identities or message contents in logs or committed plaintext.

## Task 1: Fixed evidence, bundle and V3 cohort

Files: create `cloud/contained_recovery.py`, `cloud/storage_reservations.py`, `tests/test_contained_recovery.py`; add the actual encrypted partial snapshot in `recovery-evidence/`.

Interfaces: export `SCHEMA`, `FAILURES`, `reconcile(store, exported, github, context, key)` and `contained_resolution_matches(identity, marker, fetch, repository, *, read_record)`. The reservation module exports `RESERVED_RECORD_IDS` as a fixed frozenset. The V3 constructor derives its bundle and peer resolutions from the authenticated existing encrypted packet and original strict proof, and checks the resulting reviewed whole-CORE hashes.

- [ ] Write regressions for no-mutation rejection of changed source/timing/metadata/hold/observations, proper partial new cohorts and foreign schemas.
- [ ] Record the red failures against missing V3 implementation. Include a prior failed V3 invocation with an empty observed cohort; require zero new writes. Bind actual execution context and require successful hosted backup/verification before startup clearance.
- [ ] Implement the fixed protocol and safe aggregate summary. Validate actual source/key inputs only inside private ephemeral staging; commit ciphertext only.
- [ ] Run `python -m unittest tests.test_contained_recovery -v`; require zero failures and exact first-write/read-only-repeat counts.
- [ ] Independently reconstruct actual bundle/resolution hashes and compare every pin before deployment.

## Task 2: Mutation reservation and strict dispatch

Files: modify `cloud/notion.py`, `cloud/runtime_guard.py`; create focused reservation and V3 routing tests.

- [ ] Add `if record_id in RESERVED_RECORD_IDS: raise AmbiguousWriteError(..., diagnostics={'operation': 'verify_create_readback'})` after canonical record validation and before any transport/preflight call. No opt-out.
- [ ] Test both reserved identities across absent/existing states, zero requests, nested-ID bundle acceptance and unchanged normal writes.
- [ ] Route the exact V3 schema inside the known readback-incident branch before generic startup handling. Reject foreign V3 schemas/IDs and incomplete peers.
- [ ] Test every required record, reserved-ID appearance, changed/rerun original or maintenance jobs and incomplete metadata. Preserve existing V2/strict-routing regressions.

## Task 3: Hosted entry point and workflow

Files: create `scripts/reconcile_contained.py`; modify `.github/workflows/history-reconciliation.yml`, `scripts/check_workflows.py` and corresponding workflow tests only as needed.

- [ ] Reject local/non-owner/non-main/enabled contexts before credentials or network. Use the existing budgeted store, authenticated baseline and GitHub evidence client.
- [ ] Dispatch only the explicit `reconcile-contained` mode through the existing single-writer job. Retain the 150-minute job and 120-minute request budget.
- [ ] On success, create the full encrypted backup and safe aggregate summary. On failure, preserve safe diagnostics, exit nonzero, never retry a write or fabricate a backup.
- [ ] Run full unittest, 85 legacy tests, security scan, compile, workflow checker and `git diff --check`. Push a completed unit, create/attach the PR, require hosted CI and merge normally.

## Task 4: Actual reconciliation and production

- [ ] Refresh private/default-branch/main CI, flags, paused actors and GitHub failed-writer evidence.
- [ ] Dispatch the fixed maintenance mode once on main. Freeze main until terminal evidence is authenticated. A failure is never a blind rerun.
- [ ] Authenticate actual run/job/artifact provenance and AESGCM restore; require exactly four new records, 2,902 pages/2,655 COREs, all prior source/history/ready/suppressions preserved and zero new effects.
- [ ] Commit the independent fixed-path verification receipt and actual encrypted complete backup, outside the five protocol files; require hosted CI and fresh strict matcher checks. No receipt means no startup clearance, including after successful backup.
- [ ] Only after all gates pass, enable production and dispatch the existing all-lane live workflow on main.
- [ ] Authenticate persistent index, completion markers and backup; run a same-Chicago-day repeat and prove zero repeated external effects.
- [ ] Complete the real encrypted agent-intake research action, central consumer and repeat verification against that fresh complete index.
- [ ] Update current runbook/checklist/evidence with exact hosted results, active Chicago schedules and observed delays, capability exceptions, headless status and any genuinely external blocker. Remove only the temporary backup-key file after all remaining proofs are complete.
