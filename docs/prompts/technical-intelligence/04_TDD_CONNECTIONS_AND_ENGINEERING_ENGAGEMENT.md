# Prompt 4 — Connections, inbox evidence and meaningful engagement (TDD)

Implement the relationship-engagement layer using the approved PRD, central Notion state, verified technical signals and existing `cloud/outreach.py` lanes. Preserve the distinction between first-touch connection requests (06:30) and accepted-connection replies/follow-ups (06:00). Link contact, company, current team/role, source post, prior invitation/message, application/referral context, and suppression to the same canonical identity.

**Tests FIRST:** Write failing fixtures for pending/accepted/declined/unknown invitations, inbound replies, historical sent messages, a new message arriving after a draft, group conversations, stale company affiliation, duplicated contact aliases, repeated likes/comments, opt-outs, contradictory Notion records, agent handoff, company contact caps, pacing and weekends. Test explicit prevention of follow-up on pending invitations, inaccurate personalization, invented familiarity and ambiguous send retries.

Implement evidence-backed research dossiers and next-action proposals: read a specific engineer's permitted public technical content; identify a genuinely useful question or alternative engineering design; draft a concise substantive comment or connection note where history and platform permissions allow; mark blocked work with a specific reason; and associate the draft with exact post URL and revision/hash. A like/comment is not evidence of relationship progress unless actually verified.

For LinkedIn inbox reading, examine current official access eligibility and supported scopes. Implement a live connector ONLY if the account has the required authorized read access. Otherwise retain `inbox_checked=false`, accept member-authorized LinkedIn archive imports, and allow verified user-reported conversation updates. Do not substitute unauthorized automation or pretend archived messages are live.

Keep engagement authentic: do not run indiscriminate likes, spam, false personal endorsements, or coordinated bulk interactions. Any actual external sending must have a verified platform-permitted path and evidence of the outcome; otherwise the executable action is a reviewable draft/manual task, not an actual send.

**Exit criteria:** Local TDD proof; Notion relationship/engagement state; no-duplicate and opt-out tests; approved access capability report; human-ready, technically insightful example outputs; and a PR. Preserve all existing application functionality and uncertainty holds.
