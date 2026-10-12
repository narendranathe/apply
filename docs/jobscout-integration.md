# JobScout integration

JobScout (`narendranathe/job-scout`) is the pipeline's sourcing feed. Its existing
GitHub Actions workflow exports `frontend/public/api-data.json` nine times daily.
The export is public and needs no authentication. The October 11 audit found
377 jobs across 19 companies: Greenhouse 316, custom 19, Ashby 9, Lever 7 and
Workday 26. Configured coverage is broader than the actual exported subset;
never report template counts as live coverage. The migration reuses this feed.

## Consuming the feed

```
python3 scripts/jobscout_feed.py [--min-score 0.51] [--max-age-days 7] [--json]
```

Defaults mirror the export's own `high_match` threshold (0.511) and a 7-day window.
Output is a research candidate: title, company, location, URL, ATS, score, age,
sponsorship signal, salary and matched skills. Official role, sponsorship,
compensation and form verification still precede submission eligibility.

## Known feed quirks (checked 2026-10-11)

- `first_seen_at` is unreliable — every job in the export shows <=1 day, likely a
  state-restore artifact in the Actions fallback. **Freshness comes from `posted_at`.**
- The export's `stats.companies_tracked` fluctuates (19–46 observed); the Actions
  fallback may cover a subset of the 130+ configured companies. Treat the feed as
  strong but not exhaustive — the cron may still web-search for target companies
  (JPMC, Visa, Mastercard, Goldman Sachs) that JobScout doesn't track.
- Scores top out ~0.62. The keyword/heuristic component works with no resume loaded;
  the TF-IDF refinement only applies when a resume version is in the vault.

## Candidate facts and scoring

The cloud lane grades current official requirements against the reviewed private
master and ground-truth evidence, independently of the public feed's heuristic
score. The audited local JobScout database contained no resume versions; the live
hosted vault's contents and access controls were not established. This migration
does not upload candidate facts or private resume text to that backend. A future
vault integration requires verified private access and a reviewed exact source.

## Cloud application handoff (V2)

The cloud application lane consumes this existing public export and independently verifies each official employer/requisition. Public feed scores and sponsorship flags are research signals, not authorization. Actual posting date controls freshness; feed-reported applied/unknown history suppresses repeats even when a role no longer passes current filters.

The former extension-only/manual submission rule is superseded by the V2 standing authorization. Eligible truthful packets can use an employer-permitted official integration with scoped cloud authentication and definitive evidence. The laptop extension remains a manual aid where necessary; it is not driven from GitHub Actions. See `pipeline.md` and `docs/ATS_CAPABILITIES.md`.
