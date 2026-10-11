# JobScout integration

JobScout (`narendranathe/job-scout`) is the pipeline's sourcing feed. It scrapes
130+ company career pages across 6 ATS APIs (Greenhouse, Ashby, Workday, Lever,
SmartRecruiters, BambooHR) and exports `frontend/public/api-data.json` ~9x/day
via GitHub Actions. The export is public — no auth needed.

## Consuming the feed

```
python3 scripts/jobscout_feed.py [--min-score 0.51] [--max-age-days 7] [--json]
```

Defaults mirror the export's own `high_match` threshold (0.511) and a 7-day window.
Output is Gate-ready: title, company, location, URL, ATS, score, age, sponsorship
flag (1 = positive signal), salary, matched skills.

## Known feed quirks (checked 2026-10-11)

- `first_seen_at` is unreliable — every job in the export shows <=1 day, likely a
  state-restore artifact in the Actions fallback. **Freshness comes from `posted_at`.**
- The export's `stats.companies_tracked` fluctuates (19–46 observed); the Actions
  fallback may cover a subset of the 130+ configured companies. Treat the feed as
  strong but not exhaustive — the cron may still web-search for target companies
  (JPMC, Visa, Mastercard, Goldman Sachs) that JobScout doesn't track.
- Scores top out ~0.62. The keyword/heuristic component works with no resume loaded;
  the TF-IDF refinement only applies when a resume version is in the vault.

## Resume vault sync (PENDING — do after he approves the master resume)

JobScout scores jobs against resume versions in its vault (`POST /api/resume`
`{"resume_text": "...", "version": "_AI"}`). The current vault resume predates the
AI-engineer rewrite, so scores still skew toward the old data-engineer framing.

When the master resume is approved:
1. Extract plain text from `resumes/master/Narendranath-Edara-Resume.pdf`.
2. If the Render backend is awake, `POST` it as a new `_AI` version (it keeps old
   versions — nothing is overwritten).
3. If the backend is unreachable (free tier sleeps), do it from his browser via
   the JobScout dashboard's resume upload, or wait for the next deploy.

Do NOT sync the unreviewed master — scoring against a draft bakes the draft's
framing into every future candidate ranking.

## AutoApply AI handoff

Per approved application folder, the pipeline emits:
- `resume.pdf` (+ `.tex`) — the tailored variant, also copied to `resumes/variants/`
- `answers.md` + `answers.json` (schema: `docs/answer-schema.md`)
- `changelog.md` — every bullet changed vs master, mapped to JD requirements

The AutoApply AI Chrome extension consumes these from its Resume Vault / Answer
Vault while **he** has the application open. The pipeline never drives the
extension and never submits anything. Submission stays human, in his browser,
under his review — no exceptions.
