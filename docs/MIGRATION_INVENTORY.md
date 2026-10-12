# Migration inventory

Observed October 11, 2026. This is a sanitized inventory, not an authorization
list or a statement that historical relationships/application statuses are current.
The private repository gate was verified by the coordinating agent through the
authenticated GitHub API (`private=true`, zero forks). Previously public tracked
candidate documents may already have been copied; visibility does not retract
them. No shared history was rewritten.

## Local schedulers and actors

| Actor / persisted ID | Observed state / cadence | Migration handling |
| --- | --- | --- |
| `linkedin-accepted-connection-follow-up` | ACTIVE; 09:00 daily, persisted weekly rule lists all seven days | Preserve ACTIVE until validated one-time cutover; cloud target 06:00 America/Chicago |
| `linkedin-connection-request-morning` | ACTIVE; 09:30 daily | Preserve ACTIVE until validated one-time cutover; cloud target 06:30 America/Chicago |
| OpenClaw `343ce407-f9bc-4760-8526-bf6f8dbf0e38`, Memory Dreaming Promotion | Enabled; cron `0 3 * * *`; timezone absent from stored schedule | Unrelated memory job; retain |
| OpenClaw `6ce85427-63ed-419f-a662-f099ae19b9c6`, heartbeat-main | Enabled; every 1,800,000 ms; latest state skipped/no-route | No application task in saved payload; retain |
| OpenClaw `8b9e3738-ed5d-42b7-bf31-e8a95d12c022`, skill-collection-review-main | Enabled; every 604,800,000 ms | Unrelated skill job; retain |

The accepted lane's exact persisted rule is
`RRULE:FREQ=WEEKLY;BYHOUR=9;BYMINUTE=0;BYDAY=SU,MO,TU,WE,TH,FR,SA`.
The first-touch rule is `RRULE:FREQ=DAILY;BYHOUR=9;BYMINUTE=30`.
Their saved prompt SHA-256 values are respectively
`7b53b3c7ec0acf7b79b0e700e05fc8e96aac153e10d08d25ce192320e6fb494d`
and `f6deb61881d4c2ec6727020e966d575482796423759082a225eb999490ea5866`.
Full original prompts, IDs, destination chat IDs and schedules are preserved in
the encrypted source archive. The TOML files have not been changed.

Associated saved chat references are `Run LinkedIn networking outreach`
(`01a0c1c4-eafc-71c1-a75d-af7b30ee3d03`), `Run next LinkedIn outreach`
(`01a0ce1f-1104-7040-a65b-edf778abb551`) and `Codex Workflow Scheduling`
(`6ac93de3-c08c-83e9-9251-95104853f5e3`). Three recent-turn source snapshots
are encrypted with the archive; each connector response has older pages still
available, so these are not represented as complete conversation transcripts.
Older cloud/schedule proposals are references superseded by the V2 instruction.

The Codex automation directory contains exactly two configurations. No
application match appeared in those configurations, the 11 inspected user/system
LaunchAgent/LaunchDaemon plists, or the three actual OpenClaw cron records.
OpenClaw stores cron state in `~/.openclaw/state/openclaw.sqlite`, not the old
`cron/jobs.json` path. Its 79 live indexed transcript messages contained no
application/JobScout references. The coordinator verified no user crontab after
the initial sandbox read was denied. This bounded evidence leaves the previous
application scheduler/cadence **unresolved**; it does not prove that no other
manual, browser-extension, cloud, or historical actor exists.

## Source classifications

| File class | Source / count | Destination and constraints |
| --- | --- | --- |
| Versioned source code | Four active local Python scripts, five synthetic test modules and public ATS fixtures | `cloud/legacy/`; per-file provenance hashes; inactive recovery port |
| Safe historical configuration | ATS board seeds and a campaign policy with private alumni facts omitted | `cloud/legacy/outputs/`; historical policy, never the cloud approval policy |
| Private workflow state | Coordination `outputs/`: 45 files, 2,411,665 bytes at audit, excluding bytecode | Encrypted archive; original runbooks, manifests, snapshots/maps, research artifacts and historical script revisions retained |
| Historical SQLite | 354 events, 28 work claims, one metadata row | Consistent SQLite backup API snapshot; encrypted archive and normalized historical Notion records |
| Two full historical logs | Coordination mirror and canonical `2026-09-09/yo/outputs/Outreach_Log.md`; 417,380 bytes each | Encrypt both independently named source copies; never plaintext Git |
| Private Notion export | Complete source rows/body payloads and normalized records when audit finishes | `.migration-private/` during processing; authenticated archive; existing Notion hub remains the operational destination |
| Existing application history | No local `apply` registry/packets; JobScout `backend/jobscout.db` has one removed synthetic fixture, zero real applications | Full database snapshot archived; proven fixture becomes audit evidence, never an application or company suppression |
| Browser/authentication state | LinkedIn/Chrome profiles, cookies, credentials, passwords, API secrets, OAuth tokens | Never copy or archive; use approved service credentials separately |
| Runtime SQLite sidecars | WAL/SHM and `mirror.lock` | Not archived as separate live database files; SQLite backup API captures committed WAL data |

`NOTION_MANIFEST.json`, `START_HERE.md`, `OPERATING_CONTRACT.md`,
`QUEUE_PROTOCOL.md`, `SYNC_RUNBOOK.md`, policy and campaign snapshots were read.
The source manifest points to the existing Hiring & Outreach data source
`d932083f-8d8a-4570-a027-025bbfffb878`. Muse's pause is a user-reported source
observation, not a newly verified process state. Manual, Codex, Claude and Muse
actor labels are retained without inference or source-message replay.

## Historical fidelity and uncertainty

The two full log SHA-256 values match:
`ea4b07c03e425008dbe2f46a70c70a658abcaa71dddfb51a3c83fdffc2b54cf7`.
The consistent source journal export retains every original event payload, ID,
content hash, actor, evidence and delivery flag. Timestamp text remains exact:
221 records have date-only precision and 133 contain a timestamp. No midnight or
timezone is fabricated for date-only observations. The journal reported zero
unacknowledged Notion mirror URLs, which is not proof of a fresh remote read.

| Source delivery flag | Events |
| --- | ---: |
| Historical observation | 220 |
| Not applicable | 46 |
| Sent | 50 |
| Pending | 3 |
| Not sent | 2 |
| Not attempted | 11 |
| Approved for send | 6 |
| Sent verified | 15 |
| Received | 1 |

261 source events say `Historical import`; their individual real-world actor is
not inferred. The other source labels are Codex 68, Muse 14, Naren 2, Claude 9.
Three claims remain marked active and all three leases were expired at audit.
They become historical audit records, never renewed cloud locks. Historical
`Sent`, `Pending`, `Approved for send`, and even `Sent verified` describe the
source observation at its time; they do not authorize a new effect or claim a
fresh relationship check. Embedded older log blocks remain verbatim content and
their original IDs are retained as source aliases without inventing absent hashes.

The private normalized local export contains **390 records**: 354 original
outreach events, 28 historical claim audits, one proven JobScout test artifact
and seven workflow snapshots. The JobScout database has zero jobs, zero scrape
runs, zero resume versions, and one removed application row. Its exact
external-ID/company/URL and note match `backend/tests/test_applications_api.py`;
its `applied_at` is test activity, not a real application receipt. The original
row remains intact in historical audit evidence. `apply/applications/registry.json`
does not exist and no local application folders were found.
Source normalization completed with **zero blocked records**. Full Notion
pagination/body verification read **592 of 592 bodies** and verified all **354
event payloads/content hashes/property hashes**: 352 exact local matches and two
previously approved additional-metadata variants. The variants were independently
checked against the existing scoped correction-proof implementation and the
same proof payloads in the fresh remote audit; zero unresolved conflicts remain.
Both original local and remote forms/hashes are encrypted and preserved. The
merged import input uses the verified existing remote forms without overwriting
old rows.

The merged private input contains **703 records**: 354 outreach events, 29 audit
events, seven historical workflow snapshots, 154 contacts, 18 jobs, 75 companies,
38 queue observations and 28 other Notion observations. None of the 154 imported
contacts is claimed identity/history verified: 153 need verification and one is
suppressed. The source's 214 display-property differences remain explicit audit
evidence rather than fresh delivery proof. Export/reconciliation is complete;
operational import and cloud authority are separate deployment gates.

The verified encrypted archive is
`migration-archives/2026-10-11-v2-history.aesgcm`: **682 files**, **22,615,298
restored plaintext bytes**, **2,161,171 ciphertext bytes**, and all **682 restored
SHA-256 values matched**. Ciphertext SHA-256:
`c98be96dc71d26b11ae26ad53e5476c89819ff22b2c6d2c1f0d6fa78686976d1`.
Its sanitized receipt records exact member hashes and key-custody confirmation.
Known credential-pattern scanning of selected source content passed; no browser
or provider authentication files were selected.

## Remaining cutover gates

### October 12 application delta

Main advanced independently to `c9530f2dfc240e712113d9e43dcde01eb7e53953`
with three `draft_ready` folders, three TeX variants and a registry. Every
`applied_at` is null; no receipt or exact PDF is present. Claimed actor labels
are preserved as source observations, not independently verified executors or
cadences. The local scheduler stores still show no corresponding application
job. Its controller must be identified and reconciled before cloud activation.

The original 38 Git source files and one separate unverified identity
observation are preserved in `2026-10-12-c9530f2-application-delta.aesgcm`,
with two normalized inputs: **41 restored members**, every hash matched.
Ciphertext SHA-256 is
`8fad0aadbf02d9ad74e7d4cd788479a4c142e419dce96356ec969b6efbcdd78d`.
The seven additional records are three historical preparations, three incomplete
recoverable source packets, and one fact-source change audit. Combined import
input contains **710 records**, with no source conflicts. The original 703-record
archive is unchanged. Plaintext packets were removed from Git tracking only
after an independent authenticated restore; local ignored copies remain.

The drafts use the older resume and unresolved scale claims. Their self-reported
PASS/QA prose is not a verified artifact or screening result. JPMorganChase's
third-party mirror IDs are source-observed, with `employer_history_unresolved`
holding the employer until official identity reconciliation. Explicit legal-name
and brand dedup follows the [official company source](https://www.jpmorganchase.com/legal);
generic Inc/Co suffix stripping is not used. These records authorize no replay
and require fresh truthful packets before any submission.

Main subsequently advanced to `16de4acc4e66c6b7e09502ff5f4dfb8d3ec873ba`.
Goldman Sachs is reported `applied` in the registry, with a confirmation string
in notes, while its folder remains `draft_ready`. Both exact sources are retained;
no independent receipt was verified. Its next immutable revision is
`submission_unknown`, preventing repeat submission. Two other latest states
remain historical prepared drafts. The additional 42 source files and two
normalized inputs restored as **44 exact members** from a separate archive,
SHA-256 `c383cbfe52ca32e0cd55bdccd9b78728046a22d6119a19e318d54b03e93b0a66`.
Combined input now has **717 records**, preserving every earlier version.

Naren then directly confirmed Goldman was submitted. Gmail supplied the
authenticated employer acknowledgement, with no requisition in its body.
The direct statement, source association and email evidence are preserved
in a separate two-member encrypted archive, excluding the authentication-code email.
An append-only human-confirmed application revision and audit bring input to
**719 records**. Human-confirmed submission count is one; cloud auto-submissions
remain zero. The provider-email evidence scope is explicit and never modeled as
a fabricated ATS HTTP receipt. All **769 archive members** restored exactly.

Runner secrets, complete remote payload reconciliation, actual application actor
and cadence, cloud shadow runs, final source delta and a proven single writer
must pass before local task retirement. Both desktop tasks remain ACTIVE. The
current migration must not enable competing writers or mass-replay source data.
