# answers.json — stable schema for the AutoApply AI extension

Each application folder ships `answers.md` (human-readable) **and** `answers.json`
(machine-readable, this schema) so the AutoApply AI Chrome extension / Answer Vault
can ingest the pipeline's output without parsing prose.

```json
{
  "schema_version": "1.0",
  "application": {
    "company": "Anthropic",
    "role": "Data Engineer, Product",
    "location": "San Francisco, CA",
    "posting_url": "https://boards.greenhouse.io/anthropic/jobs/5448481008",
    "ats": "greenhouse",
    "dedup_key": "https://boards.greenhouse.io/anthropic/jobs/5448481008"
  },
  "resume": {
    "variant_pdf": "resumes/variants/2026-10-11-anthropic-data-engineer-product.pdf",
    "variant_tex": "resumes/variants/2026-10-11-anthropic-data-engineer-product.tex",
    "arc": "E",
    "changelog": "applications/2026-10-11-anthropic-data-engineer-product/changelog.md"
  },
  "work_authorization": "Current H-1B holder working in the United States. Seeking a change of employer petition filed from within the US — no cap-subject lottery selection required.",
  "requires_sponsorship": true,
  "answers": [
    {
      "question": "Are you legally authorized to work in the United States?",
      "answer": "Yes — current H-1B holder working in the United States.",
      "source": "knockout-answers"
    },
    {
      "question": "Will you now or in the future require sponsorship?",
      "answer": "Yes — seeking a change of employer (H-1B transfer) petition filed from within the US.",
      "source": "knockout-answers"
    }
  ],
  "eeo": "ask_user",
  "notes_for_applicant": "Anything the human should double-check before submitting."
}
```

## Field rules

- `source` is one of `knockout-answers` (standing), `ground-truth` (resume fact),
  `generated` (drafted for this JD — review before use), `ask_user` (needs him).
- Nothing in `answers` is ever submitted by the pipeline. The extension fills fields
  while **he** has the application open; he reviews and submits.
- `eeo` is always `ask_user`. The pipeline never answers EEO self-ID.
- `work_authorization` uses the exact G2 text from `docs/knockout-answers.md`.
- Keep answers plain-text, no markdown, no em dashes — they go into form fields.
