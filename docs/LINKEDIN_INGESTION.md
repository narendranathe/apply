# LinkedIn evidence ingestion

`cloud/linkedin_ingestion.py` normalizes supplied member exports without reading
files, making network requests, logging content or writing records. The GitHub
Actions central writer remains the only operational writer. The importer cannot
send an invitation, send a message, accept an invitation or check a live inbox.

## Official access research

Research checked October 13, 2026, using LinkedIn's own Help and API documentation.

- Ordinary developer OAuth does not grant personal inbox access. The open
  permissions cover authenticated profile, email and social publishing. Most
  other products require separate LinkedIn approval.
  [Getting Access to LinkedIn APIs](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)
- Both personal Messages and Invitations APIs are restricted to approved
  partners under an API agreement. A listed endpoint or a token is insufficient
  proof of permission for this account and use case.
  [Messages API](https://learn.microsoft.com/en-us/linkedin/shared/integrations/communications/messages),
  [Invitations API](https://learn.microsoft.com/en-us/linkedin/shared/integrations/communications/invitations)
- The Messages API requires a specific member action, affirmative choice and an
  editable proposed draft before sending. Scheduled automated activity is not a
  permitted member action under the documented requirements. Approval to read
  evidence would not authorize scheduled personal messaging.
  [Messages requirements](https://learn.microsoft.com/en-us/linkedin/shared/integrations/communications/messages#requirements)
- The separately approved Member Data Portability product can provide inbox,
  invitations and connection snapshots. The member product is available to
  members located in the EEA and Switzerland; its documentation names the
  self-serve scope `r_dma_portability_self_serve`. The third-party product needs
  reviewed product access and business verification and limits consenting
  members to the EEA. This repository has no evidence establishing account
  eligibility or granted access. The Snapshot API page lists different member
  scope wording, so any future adapter must verify the actual product grant and
  supported scope rather than copy an assumed permission string.
  [Member product](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/member-data-portability-member/),
  [Third-party product](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/member-data-portability-3rd-party/),
  [Snapshot API](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/shared/member-snapshot-api),
  [Supported domains](https://learn.microsoft.com/en-us/linkedin/dma/member-data-portability/shared/snapshot-domain)
- The Compliance product is closed; its purpose is regulated-member compliance
  archiving. It is not a general personal inbox entitlement.
  [Compliance FAQ](https://learn.microsoft.com/en-us/linkedin/compliance/compliance-api/compliance-faq)

No functioning, authorized live LinkedIn connector is configured. The default
`LiveUnavailable` adapter reports `inbox_checked=false` and no outbound capability.
No scraping, cookies, private APIs, browser automation or session transfer is
implemented.

## Official member export

The member requests the archive through LinkedIn's Settings & Privacy → Data
Privacy → Download your data. LinkedIn describes Messages as sent, received and
archived message data, Invitations as sent and received invitation data, and
Connections as first-degree connection data including profile URL and connection
date. The export's coverage depends on selected categories and applicable account
activity. Missing categories are not evidence of an empty inbox or no invitations.
[LinkedIn Download your data](https://www.linkedin.com/help/linkedin/answer/a1339364?lang=en)

A bounded read-only filename search in Downloads and Documents, excluding common
dependency directories and limiting depth to five, found no matching member export
CSV or LinkedIn export ZIP. No private messages were opened. Real member-export
compatibility remains unverified. Tests use synthetic CSV fixtures inline; they
verify parser behavior, not a production import or live LinkedIn access.

The current parser recognizes these English header shapes, case-insensitively:

| File | Required headers | Additional recognized evidence |
| --- | --- | --- |
| Messages.csv | FROM, TO, DATE, CONTENT | CONVERSATION ID, CONVERSATION TITLE, SENDER PROFILE URL, RECIPIENT PROFILE URLS, SUBJECT, FOLDER, optional Direction |
| Invitations.csv | From, To, Sent At, Message | Direction, inviterProfileUrl, inviteeProfileUrl, optional Status/State |
| Connections.csv | First Name, Last Name, Connected On | URL, Email Address, Company, Position |

LinkedIn's Help page documents categories and contents, not a versioned CSV header
contract. Consequently these accepted shapes are parser compatibility targets,
not a claim that a real user archive was validated. Unknown or localized headers
fail into an explicit conflict. Keep the original archive and verify its headers
before adding a new schema adapter. Extra columns remain verbatim in `source_row`.

The CSV reader handles UTF-8 BOM, a Notes preamble before the header, comma/tab/
semicolon delimiters, quoted commas, escaped quotes and multiline cells. A malformed
file is withheld with `malformed_or_unsupported_export`; valid other files still
normalize, and effective completeness becomes partial. This is not authorization
to perform external effects following a storage ambiguity.

## Pure integration contract

```python
from cloud.linkedin_ingestion import AccountIdentity, ExportSource, import_member_export

result = import_member_export(
    {"Messages.csv": supplied_csv_bytes},
    account=AccountIdentity(
        profile_url="https://www.linkedin.com/in/synthetic-owner",
        verified=True,
        evidence_ref="private:verified-export-owner",
    ),
    source=ExportSource(
        evidence_ref="private:original-archive-sha256",
        exported_at="2026-10-12T18:00:00+00:00",  # only if independently documented
        completeness="partial",
        covered_categories=("messages",),
    ),
    existing_records=all_authoritative_and_legacy_events,
    existing_contacts=all_authoritative_contacts_with_suppression,
)
```

Inputs are in-memory text/bytes. The central workflow owns authenticated archive
retrieval, ephemeral staging, exact account verification and a fresh complete read
of relevant Notion and journal context. Do not omit uncertain legacy events to
obtain a cleaner import. Module imports and function calls have no IO side effects.

The result has `schema=apply-linkedin-ingestion/v1` and these fields:

| Field | Meaning |
| --- | --- |
| records | Immutable envelopes for conversation_events, contact_observations and conversation_observations |
| aliases | Exact alias → canonical original event ID associations, including source-row hashes |
| duplicates | Repeated rows or exact existing events; no second event is created |
| conflicts | Reconciliation reasons and private normalized observations when available |
| source | Archive evidence, exact file hashes/counts, supplied/missing categories and explicit historical completeness |

Conflicts can contain message content for private recovery. Never print the result
or exception payloads to GitHub logs. Report only aggregate counts and sanitized
reason codes. Keep original exports and recoverable conflict observations in
authenticated private storage; do not commit them. Persist immutable aliases and
conflicts through the central writer and verify post-write readback. An uncertain
write stops further effects. The library does not write to Notion directly.

### Identity and relationships

Exact public profile URLs receive canonical HTTPS `www.linkedin.com/in/...` aliases
with tracking and fragments removed. URL path case is preserved. Exact existing
profile mappings reuse the existing contact ID. Two existing IDs for one profile
remain a conflict; the importer never selects one. Display names do not identify
contacts. Name-only and ambiguous group recipients retain source text and remain
unresolved. No same-name merge or email-based guess occurs.

`conversation_events` contain `event_id`, `event_type` (message/invitation/connection),
`contact_id` for one resolved counterparty, `contact_ids`, `participants`,
`thread_id`, `source_thread_id`, `direction`, `direction_verified`, `text`, raw
`occurred_at`, parsed `timestamp`, `source_row`, hashes and source evidence. A
native conversation ID creates a stable account-scoped thread ID. Without one,
the importer does not invent a conversation from a shared pair of names.

Direction requires an explicitly verified export owner plus an exported
INCOMING/OUTGOING field or an exact account profile URL in the sender/recipient
fields. Contradiction becomes `direction_conflict`; names do not establish
direction. Missing recipient identities stay unresolved even if direction is
explicit. Archived folder data remains historical folder evidence. Subject,
body, participant fields and timestamps are preserved verbatim in the source row.

ISO timestamps with UTC/GMT/explicit offsets can produce a UTC comparison value.
Unzoned timestamps remain unzoned. Day-only connection dates remain day precision.
English month names can be parsed; numeric dates need explicitly documented MDY
or DMY order. Two-digit years remain source text because no century is implied.
Original timestamps are never replaced with import time, file mtime or invented
timezone. Thread last-message and last-received timestamps are derived only from
comparable explicit UTC values in the supplied historical events, not a claim
that a received message was a professional reply to a specific earlier message.

### Deduplication and uncertain matches

An exact semantic alias requires account identity, event type, resolved participant
profile identities, raw message-content SHA-256, timestamp and verified direction.
Message/invitation aliases require a nonempty body and an explicitly zoned,
comparable instant; unzoned or coarse timestamps stay possible matches across
sources. Connection aliases can retain day precision.
An exact source-row alias also makes repeated unresolved observations idempotent.
Neither alias uses an invented import timestamp. Reexports with different file
hashes can match the same message. Existing SQLite/Notion event IDs remain intact.

`exact_event_alias(payload, account_url)` is reusable by a reviewed legacy adapter.
It requires explicit `event_type`, `direction`, exact `text`/`content`,
`occurred_at`/`timestamp` and `contact_key`/participants. A journal envelope's content
hash is not automatically a message-body hash. The importer does not reconstruct
a message or delivery direction from prose headings. Existing
`payload.ingestion_aliases` can carry independently verified exact associations.

Possible matches, including a verbatim message embedded in a date-only legacy
journal entry for the same profile, become
`possible_existing_event_requires_reconciliation`. Multiple existing event IDs
for an exact alias become `exact_alias_multiple_existing_events`. Those rows are
withheld from new delivery history and preserve private observations for explicit
reconciliation. Matching text at distinct explicit UTC times is retained as
distinct events. A body hash with missing timestamp/direction is a possible match,
not exact proof; a contradictory native thread ID is also held for reconciliation.
The owner's profile alone never establishes matching counterparty identity.
Never retry an external action because an import withheld data.

### Freshness, suppression and outcomes

Every export record sets `historical_only=true`, `live_state_verified=false` and
`history_complete=false`. Even an explicitly complete archive is complete only for
its supplied categories and source cutoff. Import time does not refresh evidence.

An invitation row does not establish current pending/accepted/withdrawn state.
Connections establish historical connection evidence at the export cutoff; they
do not establish a particular invitation's acceptance or a current connection.
Absence from Connections does not prove an invitation is pending or rejected.
Exported source Status/State is retained without becoming live state. The importer
does not infer referrals, interview requests, opt-outs or professional outcomes
from message text. Such outcomes need a separately attributed verified observation.

Existing opt_out, suppressed, no_contact and company_opt_out flags survive in
suppression context. Absence of a flag always remains unknown, never permission
to contact. Historical imports never clear suppression or satisfy an outbound
history gate. The shared projection must enforce newer authoritative suppression
changes across all agents.

## Optional approved live provider

`LinkedInIngestionProvider` is a read-only interface with `capabilities()` and
`fetch_evidence(cursor, limit)`. `LiveUnavailable` implements it without pretending
access exists. A future adapter can be enabled only after all of these are recorded:

1. Specific official product/partner approval for this account, region and purpose,
   a verified grant/scope set, applicable agreement and storage/use restrictions.
2. Least-privilege credentials held as managed secrets, documented member consent,
   and permission to retrieve each requested data category.
3. A successful authorized request demonstrating account binding and actual inbox
   coverage, timestamp precision, pagination, archive limitations and cursor rules.
4. Complete evidence retrieval and dedup reconciliation against immutable history,
   including deleted/edited messages, partial pages, unknown delivery and opt-outs.
5. Sanitized failure behavior: expired permission, incomplete pagination or uncertain
   responses stop live-state claims; an attempt to fetch never means inbox checked.

No future read adapter gains outbound methods from this protocol. Personal messages
and invitations stay human-executed unless a separately permitted integration and
execution path is established. Permitted notification signals and user-confirmed
updates can append scoped observations with exact evidence; they do not substitute
for complete thread history or a full inbox check.

Run the synthetic checks with:

```sh
python3 -m unittest discover -s tests -p test_linkedin_ingestion.py -v
```
