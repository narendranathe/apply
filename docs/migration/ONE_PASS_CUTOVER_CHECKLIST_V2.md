# One-pass cloud migration: revised V2 cutover checklist

**Target:** One private GitHub Actions cloud execution system using the EXISTING Notion Hiring & Outreach workspace as the primary operational CRM, plus encrypted copies of local source records for disaster recovery. Mac OFF is acceptable after cutover.

**Schedules (America/Chicago):**
- **06:00 AM daily:** accepted connections, replies, follow-up and referral intelligence (old 09:00).
- **06:30 AM daily:** new roles, qualified contacts, first-touch research and outreach drafts (old 09:30).
- **Applications:** discover the actual existing cadence; merge it into the one-writer cloud system. Routine qualifying applications should **auto-submit where authorized/permitted and technically reliable**, without per-application approval.

## Before pushing sensitive data
- [ ] Confirm `narendranathe/apply` is **PRIVATE** or use an approved private destination. Inspect pre-existing public Git history and forks; changing visibility does not retract already-shared content.
- [ ] Inspect existing `apply` pipeline, real auto-apply scheduler, `job-scout` feeder, both Codex automations, local SQLite journal, Python scripts, two Outreach_Log mirrors, all Notion Queue/Contacts/History data and blocked/uncertain deliveries.
- [ ] Define file classifications: safe code and policies → private git; local SQLite and unredacted logs → **encrypted** archive in private git and normalized import to Notion; cookies/login sessions/API keys → **NEVER** git, not even private.
- [ ] Create encrypted migration archives, verify a restore and hashes, and securely keep decryption keys **outside** the repository. Harden `.gitignore` and CI secret scanning.

## Build the shared cloud system
- [ ] Extend the **existing** Notion Hub as the operational system for companies, roles, contacts, applications, messages/outcomes, referrals, interviews and event/audit records. Do not duplicate the CRM.
- [ ] Create a limited-scope Notion API integration for GitHub Actions. The current ChatGPT/desktop login is not the runner's credential.
- [ ] Use one GitHub Actions orchestrator/common concurrency group for all writers, deterministic IDs, idempotent Notion writes, post-write readback, backups and uncertainty suppression. Notion is not transactional; do **not** promise mathematically exactly-once operations without additional storage.
- [ ] Update `apply/AGENTS.md`, `pipeline.md`, scripts, tests and status machine: remove mandatory individual approval and browser-submission requirement for routine applications. Implement permitted cloud auto-submission with truthful answers, official requisition dedup and verifiable receipts. Unknown/blocked sites become exceptions, never fabricated successful submissions.
- [ ] Keep LinkedIn invitations/messages human-executed absent a specifically permitted integration. Do not copy desktop browser cookies or authenticated profiles to the cloud.
- [ ] Configure **06:00/06:30 America/Chicago** GitHub Action schedules with DST support and a manual dry-run mode. Preserve application schedule as discovered.
- [ ] Run tests for duplicates, Notion conflicts, rate limits, failed or uncertain application submissions, encrypted backup restore, DST, partial migration and fail-safe recovery.

## One single cutover
- [ ] Run shadow validation (no actual messages/submissions) against all three lanes; reconcile historical Notion/SQLite/local application data and outstanding other-agent work.
- [ ] Verify the private repo, secure Notion integration, optional AI API limits, any permitted employer/ATS submission credentials, and no unaudited paid services.
- [ ] Retire both local Codex heartbeat schedules **and** the previous application scheduler, with in-flight jobs completed or reconciled.
- [ ] Take final delta import; enable cloud schedules and verify one central writer. Test both morning tasks and application lane from GitHub Actions with the Mac off.
- [ ] Monitor receipts/status of auto-submitted jobs separately from prepared/attempted/manual-action-needed jobs; ensure ambiguous submissions are never blindly retried.
- [ ] Document rollback, alerting, restoration, retention, monthly usage and a clear list of remaining exceptions.

**Run instructions:** Paste the ENTIRE `ONE_PASS_CLOUD_MIGRATION_PROMPT_V2.md` into ONE new interactive Codex chat opened on the local `narendranathe/apply` checkout. Do not paste the original Codex prompts or the two individual cloud task prompts again; Codex will read them from local files. Do not treat a plan alone as deployment.

## Execution evidence, October 12, 2026

The original checklist remains the deployment contract. This status records what actually passed; registration is separate from activation.

| Gate | Evidence / current result |
| --- | --- |
| Private destination and source audit | PASS; repository private, original workflows/repositories/Notion/source bodies audited |
| Historical preservation and offline comparison | PASS; 719 normalized records, 769 exact restored archive members; offline unchanged354/would-import365/conflicted0/blocked0 |
| Application and outreach engineering | PASS; V2 policy, permitted adapters, fresh dedup, truthful QA, human/manual evidence, suppression and no uncertain retry |
| Hosted engineering tests | PASS;165 tests without skips plus85 legacy; real PDF QA and four-archive restore |
| Default-branch workflow registration | PASS; merged PR1,06:00/06:30 America/Chicago, explicit CLOUD_ENABLED=false |
| Runner Notion credential/read-write/shadow/import | BLOCKED; NOTION_TOKEN absent, operational imports0 |
| Old actors retired / final delta / activation | BLOCKED; both outreach schedules remain ACTIVE; Milo application controller unresolved |
| Provider-permitted application writes | Credential/permission exceptions;0 cloud auto-submissions,1 human-confirmed Goldman submission |
| Fully cloud-hosted while Mac is off | FAIL until hosted Notion validation and the single cutover pass |

See [full migration report](../MIGRATION_REPORT.md) and [cutover runbook](../runbooks/CUTOVER.md) for direct evidence links and exact next actions.

## October 15 superseding checkpoint

The October 12 credential/controller blockers above are resolved: the runner authenticates to Notion, 728 historical records passed fresh exact readback, a complete 986-page source snapshot passed encrypted restore, and the user confirmed Milo is stopped. Additional history and one unverified contact are preserved in append-only pre-cutover archives. These are not a final cutover delta.

Production remains disabled. Final local-state capture, six outstanding coordination claims, desktop writer retirement, a completed populated hosted research validation and actual delivery of the configured 06:00/06:30 Chicago schedules remain readiness gates. No scheduled run had appeared by 08:25 Chicago on October 15. Both desktop schedules remain active. Authorized live LinkedIn access and employer-authorized submission credentials are capability exceptions; history import does not establish either. Follow the current [runbook checkpoint](../runbooks/CUTOVER.md#october-15-preservation-checkpoint), preserving the single-writer and no-uncertain-retry safeguards.
