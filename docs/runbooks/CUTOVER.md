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

No paid API, model, database or service is provisioned. Estimated two runs/day at 2–10 minutes = 120–600 runner minutes/month before CI/imports and the existing JobScout usage. Verify actual account allowance before allowing paid overage; a private repository's runner billing differs from public repository billing.
