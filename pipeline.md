# Daily application pipeline

Runs every morning via cron. Agents prepare; Naren approves; only approved applications get submitted.

## Phase 1 — Source (agents)

1. `git pull`.
2. Pull the JobScout feed (primary source — his own scraper, 6 ATS APIs, ~9 exports/day):
   `python3 scripts/jobscout_feed.py --min-score 0.51 --max-age-days 7`
   Details, quirks, and the resume-vault sync note live in `docs/jobscout-integration.md`.
   Supplement with targeted web searches for employers JobScout doesn't track
   (JPMC, Visa, Mastercard, Goldman Sachs career sites).
   Prioritize:
   - Companies with H-1B filings in the last two fiscal years (Gate G1).
   - Postings under 72 hours old; deprioritize anything over 14 days (Gate G4).
   - US, relocation-open. Naren is open to relocation anywhere in the US.
3. For each candidate posting, run the Gate:
   - G2: work-authorization line never goes on the resume (free-text fields only).
   - G3: at least 70% of must-haves truthfully evidenced in `docs/ground-truth.md`, else skip.
   - G5: 3 years hands-on data engineering, 6 years professional. "7+ years pipelines" or Staff
     scope → skip unless referred.
   - G6: list the knockout questions the posting implies (work auth, years, location).
4. **Dedup first:** `python3 scripts/new_application.py --company ... --role ... --url ... --agent <you>`.
   If it says STOP, move on. First claim wins.

## Phase 2 — Draft (agents)

For each claimed role:

1. Save the JD snapshot to `jd.md` (postings change; we keep the copy we applied against).
2. Pick the primary arc from the JD's own language (`docs/arcs.md`).
3. Tailor the resume from `resumes/master/` into the application folder (`resume.tex` + compiled `resume.pdf`).
   Copy the variant to `resumes/variants/<folder>.tex/.pdf`.
4. Write `changelog.md`: every bullet changed/added/removed vs master, each mapped to the JD
   requirement it serves. This is what Naren reviews.
5. Draft `answers.md` **and** `answers.json` (schema: `docs/answer-schema.md` — the
   AutoApply AI extension ingests the JSON into its Answer Vault). Knockout answers start
   from `docs/knockout-answers.md`. Flag anything ambiguous — never guess on work authorization.
6. Mark `draft_ready` in the registry.

## Phase 3 — Approve (Naren)

Naren reviews the changelog + resume PDF + answers for each `draft_ready` application.
He approves or requests changes. Agents update and re-present. Nothing moves to `approved`
without his explicit words.

## Phase 4 — Submit (Naren, assisted by his AutoApply AI extension)

- The approved package (tailored `resume.pdf`, `answers.json`) goes into the extension's
  Resume Vault / Answer Vault. When **Naren** opens the application in Chrome, the extension
  fills the fields; he reviews and submits.
- Submission recorded in the registry immediately (`applied`, date, agent, resume variant).
- The pipeline never drives the extension and never submits anything itself.
  No automated/bot submissions, ever.
- Never re-submit, never duplicate. If a submission fails, record it and tell Naren.

## What this pipeline never does

- No automated/bot submissions to career sites (blacklist risk).
- No invented facts, no adjusted job titles, no `[BLOCKED]` claims.
- No second application to an employer already in the registry without Naren's say-so.
