# Qualified-outcome reporting and experiments

`cloud.reporting.run_reporting(store, config, now, dry_run=True)` reports the existing Notion source records through the same central writer. It has no messaging or submission transport. Dry-run reads the real source but stages all reporting writes in a ShadowStore; it never mutates the base store or configuration.

The returned summary contains aggregate counts, policy, proposed allocation, report/cursor IDs, and zero external messages. Personal names, message bodies, answer fields, and source record IDs stay in private Notion records. Sources with duplicate identities and changed content, ambiguous application revisions, contradictory confirmations, or uncertain write/readback stop reporting.

## Counts and evidence

Jobs and contacts count distinct explicit canonical IDs, then original Notion page IDs, then exact record IDs when stronger identity is unknown. A source observation is not a verified currently open role or contact relationship. Multiple observations of the same identity do not multiply those counts. Applications fold by explicit application ID and revision; distinct official requisitions at the same employer remain distinct.

| Metric | Required evidence |
| --- | --- |
| Application status observations | Latest recorded status only; a stage label does not prove a submission or interview |
| Prepared application observations | An explicit historical prepared/draft_ready/approved stage, distinct from QA readiness |
| QA-ready packet evidence | Structured packet QA verified=true, no missing answers, and PDF/source/ground-truth/answer SHA-256 values; artifact revalidation remains the submission lane's responsibility |
| Jobs with qualification evidence | Official source URL and aware source date, exact JD hash, explicit truthful-fit map, current ground-truth fact IDs, no VERIFY/BLOCKED fact use, and the recorded coverage matches the evidenced requirements |
| Applications attempted | Distinct application IDs with a structured, nonfuture aware attempt date |
| Verified submissions | Stable official receipt ID, HTTPS evidence URL, nonfuture aware confirmed_at, applied_verified receipt status, and a 2xx HTTP result |
| Recruiter screens/interviews/referrals/useful replies | Explicit structured verified confirmation with an exact event ID, HTTPS source, aware nonfuture confirmation date, contact ID, and a supported outcome |

A later rejection/closure does not erase an earlier verified receipt. All immutable application transitions are inspected for historical receipts. Repeated receipt/evidence references count once. The same receipt claimed by different applications, or conflicting contents for one confirmation event, blocks the report. A generic HTTP 200, prose about an interview, copied activity, drafted packet, stage label, or date-only historical observation is not an outcome.

The current Lever adapter's receipt shape is supported without inventing a `verified` flag: `status`, `receipt_id`, `evidence_url`, `confirmed_at`, `http_status`. An unknown Greenhouse response remains unknown. Submission eligibility and employer authorization remain independently enforced by the application lane.

For confirmed relationship outcomes, preserve structured evidence in the originating immutable event:

```json
{
  "contact_id": "contact:fixture",
  "job_id": "job:fixture",
  "assignment_id": "assignment:fixture",
  "confirmation_evidence": {
    "event_id": "provider:exact-event-id",
    "outcome": "interview_confirmed",
    "verified": true,
    "evidence_url": "https://example.test/confirmation/exact-event-id",
    "confirmed_at": "2026-10-12T15:00:00+00:00"
  }
}
```

Supported outcomes are `recruiter_screen_confirmed`, `interview_confirmed`, `referral_offered_confirmed`, `referral_submitted_confirmed`, `useful_reply_confirmed`, `message_sent_confirmed`, and `invitation_sent_confirmed`. The last two establish actual activity for experiment attribution; they are not qualified business outcomes. Offered and submitted referrals remain separate. Reporting consumes explicit human/provider confirmations and does not parse or invent them from prose.

## Hypotheses, assignments, and attribution

Four immutable versioned `experiments` definitions preserve hypotheses for genuine warm introductions versus direct routes, verified manager versus recruiter routes, concise proof versus role-specific curiosity, and broader professional networking versus a role-specific route. These are hypotheses, not claims about response rates. Changing a definition requires a new versioned identity.

Actual assignments are separate `experiments` records with `record_type=assignment`:

```json
{
  "record_type": "assignment",
  "assignment_id": "assignment:fixture",
  "hypothesis_id": "hypothesis:warm-introductions:v1",
  "arm": "warm_introduction",
  "role_family": "ai_platform",
  "contact_id": "contact:fixture",
  "job_id": "job:fixture",
  "assigned_at": "2026-09-20T14:00:00+00:00",
  "source_event_id": "activity:fixture",
  "source_record_hash": "sha256-of-exact-originating-record-payload"
}
```

The originating event must have structured verified activity evidence, matching contact/job IDs, and the exact payload hash. Assignment time must be at or before the confirmed activity. Role family is explicitly `ai_platform` or `data_platform`; no family is inferred from old prose. An outcome contributes to attribution only when its assignment/contact/job IDs match and its confirmation follows the activity. At most one qualified conversion counts for an assignment, even if both a recruiter screen and an interview are recorded. Separate confirmed events remain visible in funnel counts.

The current bounded implementation rejects assigning the same confirmed human activity to multiple experiments. Multiple dimensions can be valid in a carefully designed experiment, but allowing them here would inflate pooled family denominators. Such a future extension must preserve one independent activity unit and explicit multidimensional attribution. Hypotheses, plans, drafts, unmatched replies, and unverified activities never enter a denominator.

Reporting writes source-dated attribution observations. It does not select contacts for an experiment, assign arms silently, execute invitations/messages, or turn a hypothesis into real activity. Explicit human assignments and subsequent confirmations are required.

## Allocation policy

The default remains 60% applied AI/platform and 40% data/platform research. `outreach.research_baseline_ai_share` records the configured baseline; the next run's effective share is derived from verified outcome evidence. Reporting never changes the configuration file.

These deterministic thresholds can be set under `config.reporting`:

| Parameter | Default |
| --- | ---: |
| maturity_days | 14 |
| min_matured_assignments_per_family | 20 |
| min_qualified_assignments_per_family | 5 |
| min_rate_difference | 0.10 |
| max_mix_shift | 0.10 |

Both families must independently meet the mature assignment and verified qualified-conversion minima. Maturity runs from the confirmed human activity date. Only matched verified screens/interviews count as qualified conversions. Before those thresholds are met, the proposal equals the current allocation, even if one family has many invitations or unverified claimed outcomes. No denominator produces a null rate rather than an invented rate.

After the threshold, reporting compares exact count fractions. A difference of at least 0.10 can produce an allocation proposal of at most 0.10 from the configured baseline toward the family with the higher documented conversion fraction, bounded to 0–1. The next orchestrated run uses `effective_research_share` to apply it to research selection only. It requires a complete readback-verified cursor, a report no more than seven days old, unchanged ground-truth/policy/baseline, and freshly revalidated exact activity, assignment and qualified-outcome sources. It recomputes the cohort evidence and fractions rather than trusting a stored rate. Missing, edited, forged or expired evidence returns the baseline. Repeated identical evidence cannot compound daily shifts because every proposal is bounded from that baseline. No message or application permission follows from an allocation change. Observed fractions describe recorded evidence, not a causal effect, statistical significance, complete inbox coverage, or guaranteed hiring improvement. Unknown outcomes remain unknown.

## Immutable reports and source cursors

`funnel_reports` contains the source-date, source record references, snapshot hash, current ground-truth hash, aggregate evidence, and allocation rationale. `experiments` hypotheses/attribution and the report must each read back exactly before a `sync_cursors` observation is appended. The cursor binds source collection counts, the exact input signature hash, report ID, and observed date. It is a reporting-source cursor, not proof that an inaccessible LinkedIn inbox was checked.

Reporting's generated hypotheses, attributions, reports, and cursors are excluded from their own statistical inputs. The same timestamp and unchanged source set are idempotent. Source changes or a new date append new content-addressed observations. An uncertain write/readback does not advance the cursor and follows the orchestrator's global reconciliation rules.

Tests: `python3 -m unittest discover -s tests -p test_reporting.py -v`. Fixtures cover strict evidence, historical receipts, distinct identity counts, separate stage/QA/qualification evidence, prospective attributable activity, mature cohort thresholds, exact threshold boundaries, no configuration mutation, dry-run safety, idempotency, and cursor ordering after successful readback.
