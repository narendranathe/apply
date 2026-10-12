# AGENTS.md — multi-agent operating contract

Applies to every agent in this private repository. The October 11, 2026 [V2 migration decision](docs/migration/ONE_PASS_CLOUD_MIGRATION_PROMPT_V2.md) supersedes the former blanket per-application approval rule.

## Sync and one writer

- Pull before work; push a completed unit. Never force-push or rebase shared history.
- The GitHub Actions orchestrator is the only operational cloud writer. Every business lane uses its common concurrency group. Do not run independent live writers or submitters.
- Notion is authoritative. Application records and audit events are immutable; append transitions, verify fresh state and post-write readback. Conflicts or uncertain writes stop the run for reconciliation.
- `applications/registry.json` is an ignored compatibility import/export view. `scripts/registry.py check` reads Notion and fails closed when unavailable. Local `claim`/`set` and `new_application.py` mutations are retired.

## Dedup before preparation and submission

- Check canonical employer plus exact requisition ID and all known official URL aliases before preparing anything. Strip referral tracking, retain identity query parameters, and preserve history.
- Applied, submitting, or unknown prior submissions suppress another attempt. Historical `applied` without receipt means `submission_unknown`, never verified success.
- A genuinely different requisition can proceed through its own qualification gates. An employer-only legacy record with ambiguous role identity blocks until reconciled. Never create an alternative URL to evade suppression.

## Truthful preparation

- `docs/ground-truth.md` is the only resume fact source. Exclude unresolved `[VERIFY]` and `[BLOCKED]` claims; a specifically documented corrected fragment can ship only through its reviewed claim manifest.
- Keep HR titles and employment dates unchanged. Independent AI projects stay in Projects, and incomplete work stays labeled in progress.
- Tailor from `resumes/master/` into private ephemeral packets; never tailor the master in place. Follow `docs/arcs.md` and preserve the frozen preamble.
- Keep the exact official JD snapshot, requirement-to-fact map, every changed/removed/reordered bullet in the changelog, screening answer provenance, source and artifact hashes, and meaningful compile/text/layout/render QA. A changed master or fact source requires a new manifest review.
- Use only documented standing answers or exact question-specific verified answers. Unknown compensation, sponsorship, location commitments, demographic/EEO facts, legal attestations, or other material facts become exceptions. Never guess or select a convenient answer.

## Standing submission authorization

Routine truthful applications may be submitted automatically when all V2 gates pass: currently open official employer/requisition, no duplicate or uncertain prior attempt, supported role/experience/location/work-authorization/compensation screening, exact verified PDF and requested answers, a permitted reliable provider method, and a uniquely recorded central-writer attempt with exact hashes.

No mandatory `approved` state or per-application human review applies to routine eligible applications. Ask Naren for unknown personal facts, prohibited/unclear permissions, legally material human-only questions, or a new paid service/credential. Prepare the concrete packet before requesting a decision.

- Supported application APIs need verified employer-specific permission and scoped credentials. Public job-reading access is not submission permission.
- Never bypass CAPTCHA, anti-bot measures, rate limits, or site restrictions; never migrate browser sessions or drive a laptop extension as a cloud runner.
- Never automatically sign legal attestations, commit to contracts, accept offers, pay fees, take assessments, or send personal emails/LinkedIn messages.
- Persist `submitting` with resume/answer/payload hashes before the single external request. A receipt/portal record, not a click or attempt, is required for `applied_verified`.
- Timeouts, ambiguous receipts, or failed receipt persistence become `submission_unknown`; never blindly retry. A blocked role becomes a durable exception while other roles continue. A storage ambiguity stops all further effects.

## States and evidence

`discovered → qualified → prepared → submission_eligible → submitting → applied_verified`, with `needs_user_fact`, `submission_blocked`, `manual_action_needed`, `submission_unknown`, `closed`, `rejected`, `withdrawn`, and `interview` exceptions/outcomes.

Legacy `sourced → discovered`, `draft_ready → prepared`, `approved → prepared` (historical approval retained as evidence), and `applied → applied_verified` only with definitive confirmation; otherwise `submission_unknown`. All transitions retain source dates, actor, stable identity, exact status and evidence. Report confirmed submissions separately from attempted submissions.
