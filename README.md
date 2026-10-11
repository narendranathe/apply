# apply

One repo for the whole job hunt. Every role found, every resume version used, every
application submitted — tracked in one place so nothing gets applied to twice and
nothing goes out without Naren's approval.

Run by Milo, Codex, and any future agent, all following the same contract.

## How it works

1. **Find** — the daily run pulls fresh roles from Naren's JobScout feed
   (130+ company career pages) plus targeted searches. Each posting goes through
   a sponsorship/recency/fit gate before anyone touches it.
2. **Prepare** — the agent claims the role in the registry (first claim wins),
   then drafts a tailored resume, a changelog of every bullet changed, and
   knockout-question answers. All saved in the application folder.
3. **Approve** — Naren reviews the changelog + resume + answers. Only his explicit
   "yes" moves it forward. He submits from his own browser, assisted by his
   AutoApply AI extension.

## The one rule

**Before sourcing any role, check `applications/registry.json`.
If the dedup key is already there, stop.** Duplicate applications get candidates
blacklisted. The registry is the guardrail. Full contract: `AGENTS.md`.

## What's where

```
applications/
  registry.json            # the dedup registry — check this first, always
  YYYY-MM-DD-company-role/ # one folder per application
    jd.md                  # the job description snapshot we applied against
    resume.tex/.pdf        # the tailored resume variant
    changelog.md           # every change vs the master resume, and why
    answers.md/.json       # knockout answers (the JSON feeds the browser extension)
    status.json            # machine-readable status
resumes/
  master/                  # the golden master resume — never edit in place
  variants/                # copies of submitted variants, named per application
docs/
  ground-truth.md          # the only source of resume facts (mirrors his Mac)
  arcs.md                  # the six story arcs for tailoring
  resume-instructions.md   # how a tailored resume gets built (gate, budgets, QA)
  knockout-answers.md      # standing answers: work auth, sponsorship, comp, location
  answer-schema.md         # the answers.json format the extension reads
  jobscout-integration.md  # how the JobScout feed works, its quirks, fallbacks
scripts/
  jobscout_feed.py         # fetch + filter fresh roles from the JobScout feed
  new_application.py       # dedup check + claim + folder scaffold
  registry.py              # query/update the registry
pipeline.md                # the daily workflow, step by step
AGENTS.md                  # the multi-agent operating contract — read this first
```

## Statuses

`sourced` → `draft_ready` → `approved` → `applied` → `interview`
...or `rejected` / `withdrawn` / `stale`

- **sourced** — role claimed by an agent. Nobody else touches it.
- **draft_ready** — resume + changelog + answers prepared, waiting on Naren.
- **approved** — Naren approved the exact resume and answers.
- **applied** — submitted. Registry records when, by whom, which resume.
