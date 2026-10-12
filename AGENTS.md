# AGENTS.md — multi-agent operating contract

Applies to every agent working this repo: Milo, Codex, Cloud Agent, and any future runner.
Naren owns all approvals. Agents prepare; he decides.

## 1. Sync discipline

The GitHub repo (`narendranathe/apply`) is the shared source of truth for text files.

- **Before starting work:** get the latest. If you have git credentials, `git pull`.
  If you work through the GitHub API (no git auth), read via `get_file_contents` —
  never assume your copy is current.
- **After finishing a unit of work:** sync it back. With git credentials, commit + push.
  Via API: `push_files` for batches, `create_or_update_file` for single files.
- **PDFs never go to GitHub.** The API push path is text-only and `*.pdf` is gitignored.
  PDFs are local build artifacts — compile from the `.tex` with the preamble documented
  in `docs/resume-instructions.md`. The `.tex` is the source of truth.
- Never force-push. Never rebase shared history.

## 2. Dedup is mandatory, not optional

Duplicate applications to the same employer get candidates blacklisted. The registry is the guardrail.

- Before sourcing or preparing ANY role, run `python3 scripts/registry.py check --key <dedup_key>`.
- Dedup key: the canonical job URL if available, else `company:job_id`, else `company:role:location`
  normalized (lowercase, no punctuation). When in doubt, use the posting URL.
- If the key exists in `applications/registry.json`: **stop.** Log nothing, create nothing.
  If you believe it's a genuinely different role at the same company, add a note to the existing
  entry and ask Naren — do not create a second folder on your own.
- Claim immediately: `python3 scripts/registry.py claim --key ... --company ... --role ... --url ... --agent <your-name>`
  This creates the registry entry (`sourced`) and the application folder in one step, so two
  agents racing the same role can't both prepare it. First claim wins.

## 3. Facts

- `docs/ground-truth.md` is the only source of facts for resume content. Nothing ships that isn't in it.
- `[VERIFY]` items don't ship without Naren's confirmation. `[BLOCKED]` items never ship.
- Titles in Experience are HR-verifiable and never adjusted. Routing happens via the summary
  headline and the arc, never via a rewritten job title.

## 4. Resume tailoring

- One JD per application folder. Tailor from `resumes/master/`, never edit the master in place.
- **Experience is strict reverse-chronological, always** (Naren's rule, 2026-10-12): ExponentHR →
  Missouri S&T → C2FO → Udaan → Zomato. Verify in the `.tex` and in the compiled PDF's text
  extraction before any draft leaves `draft_ready`.
- Follow the arc system in `docs/arcs.md`: pick the primary arc from the JD's own language.
- Every tailored resume ships with `changelog.md`: each bullet changed/added/removed vs master,
  with the JD requirement it maps to. Naren approves the changelog, not just the PDF.
- Name variants `resumes/variants/<application-folder>.tex/.pdf` and reference them from the
  application folder. The registry records which variant was submitted.

## 5. Answers

- Draft knockout answers in `answers.md` per application: work authorization, sponsorship needs,
  location/relocation, years of experience, salary expectations, and anything the posting asks.
- Standing answers live in `docs/knockout-answers.md`. Per-posting deviations go in the
  application folder and are flagged for Naren.
- Never misrepresent work authorization or sponsorship needs. When a question is ambiguous,
  draft the honest answer and flag it — don't guess.

## 6. Submission

- **Nothing is submitted without Naren's explicit approval** of the exact resume PDF and the
  exact answers. `approved` status + his words, not an agent's inference.
- After submission, update the registry immediately: `applied`, date, agent, resume variant.
- If a submission fails or a posting disappears, record it — don't silently retry.

## 7. Record-keeping

- Every application folder keeps the JD snapshot (`jd.md`). Postings change; we keep the copy
  we applied against.
- Update `status.json` on every transition. The registry is the index; the folder is the record.
