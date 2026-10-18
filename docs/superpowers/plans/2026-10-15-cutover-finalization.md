# Cutover finalization implementation plan

**Goal:** Complete the existing V2 cutover with authenticated boundary evidence and a guarded central-writer activation record.

**Architecture:** Extend the existing history-reconciliation workflow and common concurrency group. Keep deployment evidence as non-normalized encrypted archive members; compute the historical manifest, deployment commit, archive hash and hosted validation proof at runtime so no record depends on its own hash.

**Tech stack:** Existing Python, Notion, authenticated AES-GCM archives and GitHub Actions. No new service, secret or paid capability.

## Global constraints

- Preserve immutable historical CORE fields, opt-outs and uncertainty. No provider requests in capture or finalization.
- Keep `CLOUD_ENABLED=false` until actual readiness record write/readback passes.
- Validate the replacement before pausing desktop schedules; check terminal workers at one boundary.
- Preserve three uncertain contact effects with exact durable holds and three unpublished company reservations as unverified work. Expiry does not establish an outcome.
- Missing live LinkedIn access remains `not checked`; unsupported ATS submissions remain durable exceptions.

## Tasks

- [x] Finish the current 742-record hosted readback and populated all-lane research validation.
- [x] Implement and independently review guarded activation in the existing maintenance workflow, with negative tests for missing/stale/hash-mismatched evidence, active workers, uncovered claims, wrong main/shadow commit, uncertain write and conflicting replay.
- [x] Implement final capture using existing stable source/WAL, normalization and authenticated restore primitives. Include full native Notion bytes, both logs, SQLite, actual paused automation configurations, terminal-worker readbacks and direct-user Milo confirmation. Keep ready records outside normalized history.
- [x] After hosted replacement passes, pause both desktop schedules through the app tool, read back unchanged prompts/configuration and terminal workers, and capture the final source delta. Persist exact quarantine evidence through the sole writer with post-write readback.
- [x] Deploy the final archive/code through passing CI, import only missing exact records, and run all lanes in research-only mode on the final main commit.
- [x] Reconcile the actual unstarted cancellation and subsequent guard-only failure using complete GitHub job metadata, pinned source/log proof, authenticated before/after snapshots and fresh central readback. The central operation 39042791719 and its authenticated 1,006-page backup passed. A failed or interrupted business writer retains its fence.
- [ ] Run guarded finalization, freshly verify the ready record, then enable production and dispatch on main. Verify persistent completion markers and encrypted recovery; rerun and confirm no repeated external effects.
- [ ] Record actual schedule delivery and remaining capability exceptions. If a prerequisite fails, preserve the safe scheduled research fallback and report the exact blocker.

## Verification

Run focused negative tests, the complete test suite, preserved legacy tests, security/workflow checks and all real archive restores. Hosted evidence must include exact run URLs, commits and aggregate counts. Configured schedules alone do not prove actual delivery.
