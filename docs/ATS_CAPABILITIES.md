# ATS capabilities and permission evidence

Checked against primary official documentation on October 11, 2026. Discovery uses `verify(..., require_schema=False)` for live role metadata and JD; that result explicitly has `schema_verified: false` and cannot certify submission eligibility. An API's existence is not permission to submit to an employer. No employer-scoped ATS credentials or employer permission records were found in this checkout; default config enables no live writes.

| ATS | Official read / application path | Implemented behavior |
|---|---|---|
| Greenhouse | Public Job Board GET supports requisition and live questions; application POST needs the employer Job Board Basic Auth key. The server does not enforce required fields. | Verify employer, exact ID, JD, question/consent schema and deadline; validate every required answer locally. Permission and credential gates precede POST. No documented stable applicant receipt contract is assumed: an unrecognized response becomes `submission_unknown`. |
| Lever | Public v0 Postings reads omit custom questions. Employer-authenticated v1 `/postings/:id/apply` returns complete live form. v0 POST needs an employer-generated Postings API key and documents `ok` plus `applicationId`. | Read live official form through a separately scoped credential. Only fully supported basic forms can use v0 writer; required custom, EEO or URL fields block. A 200 with `ok=true` and `applicationId` is a verified receipt. Timeout/uncertain/rejected responses are not blindly replayed. |
| Ashby | Public Job Postings API lists jobs; `applicationForm.submit` is authenticated and requires `candidatesWrite`. | No write adapter enabled. Route to a prepared manual exception until an employer-authorized integration and full schema/receipt adapter are verified. |
| Workday / SmartRecruiters / BambooHR / custom | The feed can contain these postings, but no current employer-specific permitted write integration was verified. | `submission_blocked` with official source link; continue other candidates. No undocumented endpoints, session migration, browser-extension automation, or bot bypass. |

Official sources: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html), [Lever Postings API](https://github.com/lever/postings-api), [Lever authenticated form/API documentation](https://hire.lever.co/developer/documentation), [Ashby application submission](https://developers.ashbyhq.com/reference/applicationformsubmit), [Ashby authentication](https://developers.ashbyhq.com/docs/authentication).

## Employer configuration

Under `applications.employers`, use a normalized employer key with `ats`, exact `board_token`, allowed official hosts for employer-hosted Greenhouse aliases, and secret **environment-variable names**, never credential values. Lever additionally requires `schema_credential_env` and `employer_verification` (`company`, HTTPS evidence URL, source verification timestamp) binding the board to the employer.

`permission` must contain `permitted: true`, `scope: application_submission`, HTTPS `evidence_url`, timezone-aware `verified_at`, and unexpired `expires_at`. This represents separately verified employer/integration permission; the ATS developer documentation alone cannot fill it. Every external write rechecks it. Revoke configuration immediately when permission ends.

A supported form also needs current sponsorship/authorization/location/compensation screening, exact verified answers, a QA-passed PDF and payload hash, authoritative dedup, action-time verification, and central-writer claim.

`screening.sponsorship_evidence` preserves the existing recent-filings gate: `verified: true`, `recent_h1b_filings: true`, HTTPS `source_url`, timezone-aware `verified_at` within 90 days, and the supported `fiscal_years` from the last two completed fiscal years. Filings remain evidence of past filings, not a guarantee about the employer's current hiring decision; the official JD must not prohibit sponsorship. Feed booleans never supply this gate.

For Dallas/Addison, the documented standing minimum is $175,000 annual base; the official USD annual range must reach it. Elsewhere, configure a reusable location-specific `screening.compensation_policy` with a verified standing source and `minimum_base_salary`. No cost-of-living conversion is invented. Unknown location policy or official salary range is an exception. A compensation screen never fills an unsupported salary answer into a form. Missing keys/permissions are credential or manual exceptions, never invented configuration.

## Durable manual packets and reconciliation

One immutable private `application_packets` Notion record stores the JD, exact answer fields/provenance, source, changelog, claim/requirement evidence and PDF bytes as a bounded base64 representation. State transitions reference the packet ID and hashes. A packet above 190 KB blocks for an approved storage route; no new paid service is provisioned. Restore with `python3 scripts/registry.py packet --application-id <canonical-id> --output applications/review-packet`. The exporter verifies source/PDF/answer hashes and creates private review files; do not commit them.

An attempt is persisted before the POST. Record a receipt or `submission_unknown`; daily pacing counts both. There are no automatic POST retries, including after timeout, 429, 5xx, or uncertain Notion writes. Reconciliation requires independent provider receipt/portal evidence and an explicit append-only transition. Sandbox transport fixtures test submission behavior; they never establish real employer permission or a live application count.
