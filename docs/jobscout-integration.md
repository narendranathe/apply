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

## Resume vault sync (MASTER APPROVED 2026-10-12 — sync BLOCKED, backend down)

JobScout scores jobs against resume versions in its vault. The real endpoint (per
`narendranathe/job-scout:backend/routes/resume_version_routes.py`) is
`POST /api/resume/versions` with `{"version_key": "_AI", "resume_text": "..."}` —
NOT `POST /api/resume` (that path never existed; corrected 2026-10-12). It keeps
old versions — nothing is overwritten.

**2026-10-12: the sync is blocked.** The Render backend (`jobscout-api.onrender.com`)
responds but serves NO routes — `/`, `/api/resume/versions`, `/docs`, `/health` all
return empty 404s. The Flask app in the repo is not running on the live service
(failed/stale deploy or suspended service). Three sync attempts failed (timeout,
then 404 on the documented path, then 404 on the real path). Until the service is
redeployed, the vault still holds the pre-AI-framing resume.

Workarounds, in order:
1. Naren redeploys/fixes the Render service, then re-run the POST.
2. Upload the master text via the JobScout dashboard's resume upload.
3. Otherwise, the daily cron keeps scoring with the old vault resume — keyword
   scoring still works; only the TF-IDF refinement misses the new framing.

Plain text of the approved master is staged at
`workspace/goals/standout-master-resume-for-ai-engineer-transition/hidden_files/master-resume-plain-text-2026-10-12.txt`.

Do NOT sync an unreviewed master — scoring against a draft bakes the draft's
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
