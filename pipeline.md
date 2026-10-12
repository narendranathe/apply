# Coordinated cloud application pipeline

The private repository hosts code; GitHub Actions runs the common orchestrator; existing Hiring & Outreach Notion records hold operational state. The V2 standing authorization allows truthful, qualifying applications through permitted reliable methods without routine per-application approval.

## Source and qualify

Consume `scripts/jobscout_feed.py` and the existing JobScout public export. Do not rebuild its scraper. Require a fresh export and actual posting date; `first_seen_at` does not prove posting freshness. Import feed-reported applied/unknown history as suppression even for inactive, low-score, or older roles; a feed flag is not a receipt.

Deduplicate using employer/requisition and official URL aliases. Verify the exact live official ATS post, employer identity, JD and complete requested question schema. Snapshot source/evidence dates. Extract explicit required qualifications and map them to ground-truth IDs: minimum 70% documented fit, honest production/project boundaries, three hands-on DE years versus six professional years, US location, no unsupported seniority or sponsorship assumptions. Unknown requirement extraction or screening evidence becomes a research exception. Feed scores prioritize research, never certify eligibility.

## Prepare and verify

Select the primary arc from the official JD and retain the Arc F earlier-career narrative. Tailor a copy of the current master, preserve titles/dates and the frozen preamble, and record every change with the requirement it serves. The reviewed master/fact hashes prevent changed source claims silently inheriting approval. Save JD, TeX, PDF, answer JSON/Markdown, changelog, requirement/fact/claim manifest, and exact hashes.

Screening answers come from documented standing records or verified exact-question overrides. Sponsorship stays truthful. Compensation and demographic preferences are never invented; legal attestations stay human-only. Compile with shell escape disabled, lint source, check budgets and real PDF text/ordering, page count/fill/margins/overfull boxes, and render previews. Unavailable or failed tooling is a block, not a pass.

Artifacts are ephemeral private runner files. Notion stores source packet material, hashes and evidence; a future run regenerates and verifies a packet before an attempt. Plaintext packets are never committed. Immutable private `application_packets` records preserve source, exact answers/changelog and bounded PDF bytes; restore the exact packet with `scripts/registry.py packet`. Packets above the Notion size bound need an approved artifact-storage route. Runner `/tmp` paths are not used as handoff links.

## Eligibility and submission

Check current official post/schema, exact artifact/answer hashes, authoritative history, permission expiry and pacing at action time. The adapter must have independently verified employer-specific submission permission plus a scoped credential. [ATS capabilities](docs/ATS_CAPABILITIES.md) distinguish readable postings from writable application integrations.

Append and read back `submission_eligible`, then `submitting` with the exact payload hashes and attempt ID before making one external request. A documented definitive receipt is `applied_verified`; otherwise record `submission_unknown` for reconciliation. Never automatically retry an unknown effect. Per-role exceptions continue research elsewhere. Uncertain Notion writes, identity conflicts, or failed readback stop the entire writer and keep the run incomplete.

## Scheduling and reporting

Applications run as a distinct phase of the 06:30 America/Chicago job within the shared orchestrator. No separate application schedule was found in checked-in files or accessible Codex automation definitions; unavailable OS scheduler surfaces remain an audit limitation. The default deployment is shadow-only until the one coordinated cutover succeeds.

Daily pacing counts attempts, including unknown outcomes. Report factual counts for discovered/qualified/prepared/attempted/confirmed and exceptions. A draft, fill, attempted POST, or HTTP success with no known receipt never increments confirmed applications. Manual submission evidence is reconciled through the same authoritative history.
