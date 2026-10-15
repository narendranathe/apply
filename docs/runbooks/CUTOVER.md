# Coordinated cutover and rollback

The approved V2 prompt authorizes one coordinated cutover. Production remains disabled until every evidence gate passes.

## Prerequisites

1. Verify the destination is private using GitHub API.
2. Create a limited-scope Notion integration for the existing Hiring & Outreach source and save its token directly as the NOTION_TOKEN Actions secret. Desktop connector credentials cannot authenticate a runner. Never paste keys in chat.
3. Fetch all paginated Queue, Contacts and History bodies; compare exact IDs and content/property hashes. Record conflicts without overwriting history.
4. Encrypt all source records, restore to a disposable path, compare manifest hashes, and keep the decryption key in the user's secure store outside Git.
5. Pass unit/security/restore/CI and cloud shadow checks for every lane. Shadow stages writes in memory and has no sends/submissions.
6. Reconcile all local actors and in-flight leases, delivery_unknown and submission_unknown. Never recycle uncertainty as permission to retry.

## Activation

Keep CLOUD_ENABLED unset/false during development. All cloud writes, imports and reconciliation use concurrency group hiring-outreach-single-writer with cancel-in-progress false.

First register the validated workflows on the default branch with CLOUD_ENABLED=false.
GitHub dispatch-only workflows must exist there before branch-specific dry-runs/imports
can be dispatched. This prepares the host without activating a competing writer.
Then complete hosted shadow and initial reconciliation while local tasks remain active.

1. Stop new local writes at one boundary and complete/reconcile in-flight work. Retire both Codex schedules through automation_update and read back their persisted states. Retire any separately discovered application scheduler; leave unrelated OpenClaw jobs alone.
2. Take a final delta snapshot and authenticated encrypted archive. Import through the central writer and record imported/unchanged/conflicted/blocked totals.
3. Append deployment:cutover:v2 in Notion with history_reconciled, backup_restore_verified, local_actors_retired and shadow_verified explicitly true, plus evidence URLs, hashes and commit. Tests alone cannot justify this record.
4. Confirm the pinned, validated default-branch deployment and set repository variable CLOUD_ENABLED=true. Schedules are 06:00 and 06:30 America/Chicago. Applications run as a distinct phase after 06:30 when no previous cadence can be established.
5. Dispatch all lanes on GitHub-hosted runners. Validate receipt handling on a permitted sandbox before production. Production applications require every eligibility gate and employer authorization.
6. Verify schedules on the default branch and record Mac-off validation separately from local tests. Verify private Notion readback and encrypted backup recovery.

GitHub can delay schedules or replace pending runs. Every production wake reconciles all due lane slots. Old missed slots produce audit observations and never replay applications/outreach. Started slots without completion require reconciliation before further external retry.

## Rollback

1. Set CLOUD_ENABLED=false, prohibit live dispatch, wait for the writer to finish, and reconcile uncertain effects.
2. Export and encrypt current Notion records. Append the rollback reason; never revert submitted state to an unsubmitted state.
3. Restore archives to a disposable path and verify manifest hashes. SQLite remains recovery state, never a concurrent cloud database.
4. Resume local schedules only after cloud stops and new outcomes are imported to local suppression history. Read back persisted schedules; never resume both systems together.

## Monitoring, privacy, costs and retention

Private Actions failures and aggregate artifacts provide baseline alerts. Notion holds run, exception and audit records. Runner output excludes contact names, message bodies and secret values. No email or webhook is sent by this migration.

Aggregate artifacts retain 14 days. Preserve encrypted migration baselines and create a new archive for each material delta. Historical records remain in the existing access-controlled Notion workspace. Preserve suppression tombstones when retiring contacts; review personal-data access and archive retention quarterly.

No paid API, model, database or service is provisioned. The successful hosted shadow took 20m09s (writer 18m34s), so the old 25-minute deadline lacked sufficient headroom. The updated job is capped at 90 minutes with a 75-minute request budget and 20 minutes reserved before any new external attempt for receipts/readback/backups. A populated snapshot can require more than 27 minutes of paced requests before network latency. A deadline or uncertain state stops further effects. At 21 rounded minutes per run, two daily runs require approximately 1,302 minutes per 31-day month before CI/imports. Actual private-run allowance and spending cap remain unavailable through the current account API; zero reported billable milliseconds does not prove zero cost. No paid overage is enabled by this migration.

## October 14 continuation and research fallback

The two latest application deltas preserve all 719 original records and combine to 727 immutable records. All six authenticated archives restore 839 exact members. Google’s and Coinbase’s source status `applied` has no imported receipt and therefore remains `submission_unknown`; Goldman’s separate verified confirmation remains intact. The new source folders and per-application variants are archived and untracked while local copies remain. Shared Git history is preserved.

History reconciliation remains a readiness gate. A six-record bounded validation passed at [38630210517](https://github.com/narendranathe/apply/actions/runs/38630210517); subsequent ranges can still encounter uncertain write readback and must stop. Reconcile each uncertain creation using a separate exact-source readback before another import. Never infer that an `inspect` job’s success alone means every returned check passed.

Fresh local auditing found 366 journal events and six expired active claims without complete result/release evidence. Both desktop outreach schedules remain ACTIVE. The user confirmed Milo’s scheduler is stopped. A fresh complete Notion snapshot, all source comparisons, final local delta and exact claim/effect reconciliation are still required before retirement and activation. Do not create a ready `deployment:cutover:v2` record from test fixtures or four booleans.

When full cutover cannot pass, set `CLOUD_ENABLED=false` and `CLOUD_RESEARCH_ENABLED=true` after the merged default-branch research-only workflow passes hosted validation. Both daily 06:00/06:30 `America/Chicago` schedules then select `research-only`. The 06:30 wake includes application research/preparation. This fallback makes no Notion business writes or submissions: it stages records/indexes in memory, encrypts the private research preview with authenticated restore verification, and retains ciphertext plus aggregate artifacts for 14 days. Research markers use `research:run:*`, remain nonpersistent and never complete production slots. It cannot retire working desktop outreach or claim a checked LinkedIn inbox.

`cloud.index_publisher` appends immutable projections/tasks/changes before the manifest visibility boundary. Partial publication leaves no authoritative new manifest. Current preview indexes remain incomplete with no executable tasks. Production index authority requires an authenticated restore of all migration archives, `content_hash(combined_export)` equal to the cutover record’s `historical_manifest_sha256`, matching readback counts/families and every historical CORE identity unchanged in the fresh Notion snapshot. The validated cutover record must itself be present in that snapshot. Missing or changed history keeps the index incomplete. Applications separately recheck archived application coverage before preparation and again immediately before a provider request; no restored data substitutes for fresh deduplication. `docs/AGENT_PROTOCOL.md` documents bounded agent reads, source-evidence permission, proposal intake and acknowledgements. Queued means accepted for central review, not executed.

`history-reconciliation.yml` supports encrypted `snapshot`, full read-only `verify`, and explicitly blocked `readiness` evidence. Every writer uses `hiring-outreach-single-writer`; a blocked readiness ID cannot activate production. `agent-intake.yml` reauthenticates the GitHub actor against deployment-owned grants on `main`, validates the latest complete projection and exact source hashes, and acknowledges a durable queue without external effects.

GitHub supports IANA timezone schedules and applies daylight-saving changes; scheduled starts can be delayed. Tests cover 06:00 and06:30 around both Chicago DST transitions. See [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). Record actual hosted fallback/production run URLs after deployment; a test or dispatch is not schedule-delivery evidence.

## October 14 hosted continuation

[PR #5](https://github.com/narendranathe/apply/pull/5) preserves a provisional local recovery capture. All seven archives restore 891 members and combine to 728 records; the prior 727 immutable records are unchanged. The capture includes 366 journal events, both logs, coordination state and persisted desktop configuration. Its audit explicitly says `boundary_complete=false`, `local_actors_retired=false` and `raw_outreach_normalized=false`. It is recovery evidence before cutover, not the final delta.

The [initial full-range import](https://github.com/narendranathe/apply/actions/runs/38635869799) reported 330 imported, 339 unchanged and one uncertain readback, then stopped all subsequent writes. A [separate exact-source read](https://github.com/narendranathe/apply/actions/runs/38664446040) confirmed that record unchanged. The [remaining range](https://github.com/narendranathe/apply/actions/runs/38664569679) then passed with 36 imported, 21 unchanged and zero conflicts/blocks. The [single provisional audit import](https://github.com/narendranathe/apply/actions/runs/38665090752) passed with one imported. These results do not replace the required full fresh readback and native-source comparison.

The first [main research validation](https://github.com/narendranathe/apply/actions/runs/38635668465) stopped with a safely redacted `ValueError`. The correction adds a closed vocabulary of `cloud_operation` labels while preserving the original exception and the three-field Notion diagnostic. No private exception message or traceback is logged. A failing entry-point regression then reproduced the cause: `python -m cloud.orchestrator` defined a second `ShadowStore` class under `__main__`, so application preparation rejected valid private staging. The store and its exception now live in the shared `cloud.shadow` module. The regression exercises the actual module entry point and retains negative tests for live mode and persistent writers. A failed or cancelled diagnostic run does not satisfy the hosted-shadow gate.

Legacy claim quarantine uses immutable `exceptions` with schema `apply-coordination-quarantine/v1`. Exact contact/source/profile aliases and hashed claim/task/resource evidence bind the hold. Outreach and agent projections enforce the hold across newer contact observations; pending invitations and opt-outs remain intact. Quarantine has no expiry or automatic resolution. Unpublished company coordination is `unstarted_unverified`, not a claim that research completed or no effect occurred. Actual quarantine records require fresh identity mapping and stopped-worker evidence at the boundary. The six original claims remain untouched until that evidence exists; tests and a generic audit note cannot clear them.
