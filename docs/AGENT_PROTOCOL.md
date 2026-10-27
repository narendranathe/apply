# Universal agent protocol v1

`cloud.agent_protocol.AgentProtocol` provides one agent-neutral library over the existing Notion immutable Cloud records. `scripts/agent_gateway.py` exposes its nine operations over JSON lines and an MCP stdio tools adapter. Neither component performs an external effect or calls `store.put`. No permanent server or additional paid service is required.

The existing Hiring & Outreach data source remains authoritative: `d932083f-8d8a-4570-a027-025bbfffb878`. The central GitHub Actions dispatcher remains the only operational cloud writer. A proposal, local outbox file, assignment, successful HTTP call or click is never proof of a completed application or message.

## Operations

Every successful library response has `schema: apply-agent-protocol/v1`, `operation`, `data` and `usage`. Failure codes are fixed strings in `ProtocolError.code`; the adapter emits sanitized errors without private source text or tracebacks.

| Operation | Arguments | Result / permissions |
| --- | --- | --- |
| `get_campaign_brief` | none | Manifest identity, cursor, counts, compact priorities, freshness and blockers; `read_summarize` |
| `get_ready_tasks` | `agent?`, `capabilities?`, `limit=10` | At most 20 tasks from the publisher's 40-task priority window; caller filters intersect configured grants; `read_summarize` |
| `get_entity_context` | `entity_id`, `depth=2`, `task_id?`, `evidence_ids?` | One projection and at most five meaningful events; exact source access requires depth 3 and a separate trusted source-evidence grant |
| `get_recent_changes` | `since_cursor?`, `limit=20` | Bounded metadata changes and next/latest cursor; expired cursor requires refreshing the brief |
| `get_interaction_history` | `contact_id` | Level 2 conversation metadata, suppression and completeness; original messages are excluded |
| `get_application_state` | `job_id` | Indexed application state and meaningful event metadata; legacy `applied` without receipt is indexed as uncertainty |
| `propose_action` | `action`, `evidence` | Deterministic intake envelope for central validation; capability depends on action type |
| `get_action_result` | `action_id` | Exact acknowledged result metadata, or `unacknowledged`, `verified: false`, `retry_allowed: false` |
| `report_observed_outcome` | `event`, `evidence` | Proposed observation envelope, never an authoritative verification or task completion |

Read-only callers can view appropriately authorized compact state. Retrieving an executable-looking task does not grant its capability. Execution belongs to the dispatcher and its provider gates. LinkedIn message and invitation execution are unsupported by this protocol version; these remain human tasks pending a specifically permitted official connector.

## Trusted identity and permissions

The library receives `principal` and `registry` from its trusted host. A supplied `agent` argument is a consistency check and must equal that principal. A request's claimed capabilities only narrow existing grants. Unknown principals default to `read_summarize`. Disabled principals receive no grants.

Canonical capabilities are:

| Capability | Scope |
| --- | --- |
| `read_summarize` | Compact state, tasks, changes and result metadata |
| `research_propose` | Research, outreach review, interview preparation, referral review and reconciliation proposals |
| `prepare_application` | Application preparation proposals |
| `execute_permitted_application` | Submission proposals for an already eligible application; provider permission and V2 screening remain dispatcher checks |
| `ingest_verified_conversation` | Evidence-backed conversation observations for verification by the central writer |
| `record_verified_manual_action` | Evidence-backed manual-action observations for verification by the central writer |
| `administer_workflow` | Configuration proposals, subject to central policy |

`allow_source_evidence: true` is an additional private-data permission on a registry entry. It does not allow execution. Exact evidence reads also require an explicit selected task from the current manifest's task map, selected entity and at most five evidence IDs present in both that task and the projection. The selected record must be an `agent_tasks` record; another evidence record cannot masquerade as a task.

The local gateway binds a single principal in a deployment-owned `--registry-file`. It accepts only a regular file owned by the process UID with no group/other access. There is no `--principal` switch and no per-request identity credential. This is a local OS trust boundary, not remote authentication: an untrusted user able to replace the launcher or its registry can replace that boundary. A hosted wrapper must bind the authenticated GitHub/host actor to the centrally configured registry and ignore claimed actor/capability values inside intake. Give future agents least-privilege integrations, never place credentials in prompts or records, and never expose this local process as a public HTTP endpoint.

Example **private configuration**, stored outside Git and permissioned `0600`:

```json
{
  "schema": "apply-agent-registry/v1",
  "principal": "future-research-agent",
  "agents": {
    "future-research-agent": {
      "enabled": true,
      "capabilities": ["read_summarize", "research_propose"],
      "allow_source_evidence": false
    }
  }
}
```

## Index and context schemas

The publisher derives compact projections from existing records and application folding/deduplication. It retains original record identities and source evidence. Projections and manifests are immutable, versioned records; do not overwrite a fixed `projection:<entity_id>` pointer.

The logical v1 manifest has the following payload:

```json
{
  "generated_at": "2026-10-13T12:00:00+00:00",
  "complete": true,
  "conflicts": [],
  "projections": {"job:fixture": "entity-projection:hash"},
  "tasks": ["agent-task:hash"],
  "task_map": {"task:fixture": "agent-task:hash"},
  "changes": ["index-change:hash"],
  "change_cursors": ["cursor:1"],
  "previous_cursor": "cursor:0",
  "source_cursor": "cursor:1",
  "counts": {"jobs": 1},
  "campaign_brief": {"highest_priority": ["Review eligible application"]},
  "action_results": {"agent-intake:hash": "action-result:hash"}
}
```

Large logical manifests use bounded immutable physical storage (`agent-index-manifest/v2`). `cloud.index_manifest.ManifestView` exposes the same logical payload while keeping the actual stored root CORE separate. The root commits its logical identity/hash and bounded section directories; storage pages use `kind: index_changes`, `status: manifest_page`, and `agent-index-manifest-page/v1`. These pages are storage metadata, excluded from logical changes and public change responses. Every page is at most 16 KiB of canonical UTF-8, the root at most 48 KiB, and the publisher checks every complete Notion request against the existing limits before its first write. Semantic children and bottom-up storage pages receive exact readback before the root is published last. Small manifests preserve their original inline CORE.

Agents use the returned physical `manifest_id` in `expected_manifest_id`; they do not substitute the logical hash or a hydrated dictionary under the physical hash. The protocol resolves only selected map paths and change windows, counts those store reads, and discards its page cache for every fresh intake authority check. Missing, tampered or unsupported storage fails closed. Existing first-40 task selection, grants, freshness, evidence and context budgets remain unchanged. A brief needs only the bounded header. An older reader sees no outer `complete: true` on the new root and therefore cannot authorize work from it. Full materialization is for writer rebuilding, backup verification and explicit offline diagnostics, never routine agent startup.

Manifest discovery uses `store.latest_index_manifest()` when supported, or `--manifest-id` for a known immutable snapshot. Both avoid a history scan. The compatibility fallback queries only `index_manifests`, but that fallback scans the manifest family; use the bounded Notion implementation or an explicit snapshot in production. It never queries the entire application/outreach event history for a routine brief. The existing store may make multiple HTTP requests for one validated record, so `store_calls` is a measured logical store-call count, not a claim about raw HTTP count.

Projection payload `kind: entity_projections` contains `entity_id`, `entity_type`, `state`, `last_verified_action`, `source`, `verified_at`, `observed_at`, `next_action`, `due_at`, `blocking_conditions`, `related_ids`, `responsible_agent`, `version`, `evidence_refs`, `confidence`, `freshness`, `suppression`, `history_complete`, `source_complete`, `recent_event_ids` and `application_state` where relevant. The returned `projection_hash` is the canonical SHA-256 of the exact stored projection payload. Missing source/history, suppression, ambiguity and stale evidence remain blockers.

Task payload `kind: agent_tasks` contains `task_id`, `entity_id`, `type`, `status`, `due_at`, `reason`, `priority`, `evidence_refs`, `allowed_capabilities`, `executor`, `responsible_agent` and `blocking_conditions`. Ready selection accepts `open`, `due` and `reconciliation`, excludes blocked tasks, respects agent assignment and requires every task capability to be granted. The manifest priority order is authoritative. `executor: human` remains human even when the agent can review or prepare that task.

Context levels are deliberately different:

1. A routine briefing returns counts, health and priorities without original messages or documents. Normal metadata responses have an 8,000-byte hard ceiling, approximately 2,000 tokens using the disclosed byte estimate.
2. An entity returns one projection and at most five latest meaningful event metadata records. Free-form message bodies, receipts, answers, documents and credentials are excluded from metadata fields.
3. Exact source evidence requires `allow_source_evidence`, explicit task/entity/evidence selection and at most five records. The default evidence budget is 32,000 bytes. Larger records require narrower evidence or the existing authenticated archive access; the protocol does not return a bulk inbox/export.

Read operations reuse a manifest within the session to reduce API calls. Intake always refreshes the latest manifest and projection, even when reads were pinned with `--manifest-id`; an older pinned manifest cannot authorize a proposal. Freshness advances with wall time in a normal session. Start a new session or supply a freshly selected manifest when polling for changes. `since_cursor == source_cursor` returns no changes without loading change records; a cursor older than the retained window fails with `cursor_expired_refresh_brief`. Parallel `change_cursors` let readers fetch only the selected page of at most 20 records.

## Intake, idempotency and acknowledgements

Supported proposal types are `research`, `prepare_application`, `submit_application`, `review_outreach`, `prepare_interview`, `review_referral`, `reconcile` and `configure_workflow`. Observation types are `conversation_observed` and `manual_action_observed`; an observation also requires an original timezone-aware `occurred_at`.

The action/event requires nonempty `type`, `entity_id`, `idempotency_key`, `expected_manifest_id` and `expected_projection_hash`. `evidence` is a nonempty list of at most 20 exact immutable evidence record IDs indexed on that projection. Additional action-specific fields are retained privately for central validation. Never invent an answer or receipt to satisfy this schema.

```json
{
  "operation": "propose_action",
  "arguments": {
    "action": {
      "type": "submit_application",
      "entity_id": "job:fixture",
      "task_id": "task:fixture",
      "idempotency_key": "fixture-eligible-submit-v1",
      "expected_manifest_id": "index-manifest:hash",
      "expected_projection_hash": "sha256-of-current-projection"
    },
    "evidence": ["evidence:verified-packet"]
  }
}
```

The library returns a canonical immutable record with `kind: agent_proposals` or `agent_observations`, `status: proposed` and `id: agent-intake:<sha256(principal,idempotency_key)>`. Its payload contains `schema: apply-agent-intake/v1`, `action_id`, `idempotency_key`, `actor`, `action`, `evidence`/`evidence_refs`, `evidence_hashes`, expected manifest/projection identities, `required_capability`, `status: proposed`, `external_effect: false`. Every selected evidence record must exist and validate its hash. Nested mismatched `actor`/`agent` claims fail; capability claims in action content are stripped and never grant authority. Repeating identical intent returns the same envelope; reusing its key with changed content fails. Separate observations/agents still undergo semantic deduplication centrally: an idempotency key alone cannot establish that a contact/application action is new.

The central dispatcher must authenticate the actor independently, recheck the trusted capability registry, read fresh authoritative state, enforce aliases/suppression/history/provider permissions, deduplicate semantic intent, append the intake and execution reservation, and verify exact readback. It must persist `submitting` with exact resume/answer/payload hashes before a permitted application request. A definitive receipt plus persisted readback establishes `applied_verified`. Timeouts, uncertain results or uncertain persistence establish reconciliation, not permission to retry. Notion cannot provide transactional agent locks; the shared Actions concurrency group serializes writers.

Routine truthful eligible submissions retain V2 standing authorization. No blanket `approved` state or per-application human gate is introduced. Unknown personal answers, unsupported provider permissions, legal human-only questions and other explicit exceptions still stop that role. Uncertain storage stops all further effects.

## Adapter and onboarding

The gateway reads one UTF-8 JSON request per line and emits one JSON response per line. It also supports MCP `initialize`, `notifications/initialized`, `ping`, `tools/list` and `tools/call`, using the established initialization protocol revisions through `2025-11-25`. It advertises only tools, with all nine argument schemas. A newer client must accept the returned supported revision; this adapter does not claim the newer stateless MCP protocol. Framing and version negotiation follow the official [stdio transport](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-11-25/basic/transports.mdx), [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) and [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) specifications.

Provision the scoped Notion token in the process's environment without printing it. Launch using a deployment-owned registry:

```text
python3 scripts/agent_gateway.py --registry-file /private/runtime/agent-registry.json --manifest-id index-manifest:hash
```

For a read-only start, omit `--registry-file`. The same onboarding works for Codex, Muse or a future agent:

1. Retrieve `get_campaign_brief` and stop effect proposals when health reports incomplete/stale/conflicted state.
2. Retrieve `get_ready_tasks` for the authenticated principal with its granted capability filter.
3. Select one task; retrieve only that entity's level 2 context. Recognize handled/unknown applications and pending invitations before proposing another action.
4. Request level 3 only when exact source evidence is needed and authorized for that task.
5. Propose supported work with the observed manifest/projection hashes. Preserve the returned action ID and wait for central acknowledgement.
6. Retrieve `get_action_result`. Leave uncertain or missing acknowledgements for reconciliation; hand off by stable entity/task/action IDs and cursor, without chat histories.

MCP example after initialization:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_ready_tasks","arguments":{"agent":"future-research-agent","capabilities":["research_propose"],"limit":5}}}
```

Stdout never includes proposed messages/answers/intake envelopes. It returns only action ID, intake hash, `staged` and `acknowledged: false`. The reusable library returns the envelope privately to its trusted host. For explicit local intake staging, add `--private-outbox` with a directory under system temporary storage. The adapter requires ownership and `0700` permissions and writes immutable `0600` files; differing same-key bytes stop. Upload those files only through the authorized central-writer intake workflow, then securely remove private staging. No outbox is created by default. A staged file is not a Notion acknowledgement.

## GitHub intake integration and deployment status

Current October 26 status: production is disabled, research is configured, and both morning runs were blocked before code by exhausted included Actions minutes and a $0 Actions budget. The bounded storage correction is under review in [PR #28](https://github.com/narendranathe/apply/pull/28); no complete current persistent index or actual hosted intake/consumer/repeat has been verified. The older successful production evidence below remains historical. See the [current cutover checkpoint](runbooks/CUTOVER.md#october-26-failed-validation-authenticated-capture-and-actions-capacity-block).

The implementation includes [agent-intake.yml](../.github/workflows/agent-intake.yml), [scripts/agent_intake.py](../scripts/agent_intake.py) and [config/agents.json](../config/agents.json), deployed on the private default branch. [October 17 production](https://github.com/narendranathe/apply/actions/runs/39044020158) published a complete 937-record index; independent authenticated backup restore verified every generated CORE record and the read contract against all 757 historical records. The brief from that actual snapshot was 4,953 UTF-8 bytes, an estimated 1,239 tokens, with one logical store read and no provider call. This is an offline read of authenticated production data, not a live API latency or actual tokenizer measurement. A hosted intake dispatch has not been exercised against a real task; fixture and dispatcher tests cover its queue behavior. The manual workflow accepts one bounded encrypted private metadata envelope, requires the private repository's default branch and shares `hiring-outreach-single-writer` with the scheduled workflows. The runner authenticates `GITHUB_ACTOR` against the checked-in registry rather than trusting actor or capability claims in the envelope.

The current registry binds GitHub owner `narendranathe` to principal `naren-github`, with `read_summarize`, `research_propose`, `prepare_application`, `record_verified_manual_action` and `ingest_verified_conversation`. It grants no application execution or workflow administration capability, and `allow_source_evidence` is false. An agent using this owner-authorized route must use that bound principal; writing “Codex” or “Muse” into a request does not create a separate authorized identity. Additional agents need explicitly scoped trusted registry entries and appropriate private credentials provisioned outside prompts.

Workflow inputs contain only private metadata, IDs, hashes and evidence references. Never include credentials, original messages, resumes, screening answers or documents in an input. Retrieve authorized exact evidence from Notion through the scoped level 3 operation when necessary. Neither workflow nor runner prints the envelope or its evidence content.

The central dispatcher appends the exact intake, an immutable semantic reservation, a queue record and an acknowledgement, checking every readback. Its result is `queued`, with `external_effect: false`. A queued acknowledgement means that intake was persisted. The scheduled orchestrator's bounded consumer can subsequently verify official jobs, prepare truthful application packets, or derive an observation from an independently confirmed source. It cannot send messages or submit applications. The scheduled application engine separately evaluates its own eligible pipeline and provider permission gates. Partial writes or conflicting reservations require reconciliation; identical complete replays still require current actor/capability authorization.

The publisher marks the matching current task `queued` and removes it from ready work. Its business state and source projection remain unchanged. Task visibility has its own incremental cursor event, so another agent can see the handoff without a fabricated application/message completion. A reservation against an older source projection does not suppress a genuinely fresh task. An incomplete current intake or reservation blocks that task for reconciliation.

### Bounded central consumer

The consumer runs only in live mode after activation, authenticated historical coverage and previous-writer checks. The deployed orchestrator supplies its trusted registry, actual run ID and common concurrency reservation; a request cannot grant itself authority. Research-only and dry-run modes skip the consumer completely. `agent_worker.max_actions` defaults to three and has a hard maximum of ten per wake.

Before any work, the consumer verifies every queue's complete intake/reservation/acknowledgement chain and rejects uncertain previous executions. It then rechecks the current principal's capabilities, source projection, exact evidence hashes, freshness and suppression. Publishing a newer manifest is acceptable only when the admitted projection hash remains identical. Revoked grants, changed sources and unsupported actions produce durable blocked results.

Each action appends an immutable execution intent followed by a separate terminal result. Both link to the original queued acknowledgement and exact source/output hashes. The acknowledgement remains unchanged. A partial execution stops the writer for reconciliation; it cannot be retried merely because its lease is old. The index exposes `completed` only after validating the full chain and action-specific output evidence. Future agents retrieve that terminal result with `get_action_result`.

Official research uses GET-only ATS reads and retains the verified response hash and provenance. Preparation restricts the fresh JobScout feed to the exact target while retaining full-feed historical suppression, uses the existing truthful preparation/QA pipeline, sets submission capacity to zero and independently forbids provider writes. Observations require a matching immutable confirmed source, event identity, entity, outcome and timestamp; caller assertions alone cannot create verified business outcomes. Repeated confirmed events are not counted again. LinkedIn actions and unsupported submissions remain human exceptions. Hosted exercise of an actual admitted task is recorded separately from fixture tests and does not prove provider submission capability.

Future-agent onboarding:

1. Start with the default read-only gateway and a scoped Notion read credential.
2. Keep all credentials outside prompts, source control and workflow inputs.
3. Ask the trusted host to bind the real authenticated actor to one principal.
4. Provision a private local registry matching only that principal's grants.
5. Read the current brief and respect its freshness/completeness blockers.
6. Select ready work and retrieve only the relevant entity context.
7. Request exact source evidence only with an explicit trusted scope grant.
8. Submit only bounded metadata and evidence references through authorized intake.
9. Treat `queued` as persisted intake; wait for a separately verified work result.
10. Hand off stable entity/task/action IDs and the latest cursor, without chat history.

## Verified fixture benchmark and limits

`python3 -m unittest tests.test_agent_protocol -v` exercises three independent agents without chat history, handled application suppression, human outreach, recruiter reply priority, stale/partial/conflicting indexes, missing or corrupted evidence, failed reads/writer acknowledgement, duplicate proposals, opt-outs, incomplete history, source freshness, blocked capabilities, exact evidence privacy, cursor expiry and private staging. The dispatcher/index integration tests separately verify authoritative queue/append behavior.

Measured synthetic fixture session on October 13, 2026, using one fresh protocol and a bounded latest-manifest method:

| Operation | Context bytes | Estimated input tokens | Logical store calls |
| --- | ---: | ---: | ---: |
| Campaign brief | 365 | 92 | 1 |
| Ready tasks, one candidate | 729 | 183 | 1 |
| Application state | 545 | 137 | 1 |
| Contact history, one event | 849 | 213 | 2 |

These are UTF-8 JSON data bytes divided by four and rounded up, not model tokenizer measurements. No provider was called; `actual_input_tokens` is `null`. `record_input_usage({"input_tokens": n})` accepts actual usage only from a provider report. Changes, larger task windows and exact evidence incur their bounded selected record reads. This fixture benchmark proves context/API-call bounds, not production Notion access, live LinkedIn inbox access or ATS delivery capability.

## Encrypted hosted intake transport

Workflow inputs and non-secret environment values may appear in Actions logs. Never dispatch a plaintext envelope. `scripts/encrypt_agent_intake.py` seals the private local outbox using `config/agent-intake-public-key.json` (X25519, HKDF-SHA256 and AES-256-GCM). Only ciphertext is passed as `envelope_ciphertext`. The separate private key is an Actions secret and secure Keychain item; agents never receive it or the migration recovery key. The runner decrypts in memory and prints only acknowledgement status. Encryption does not grant authority: the authenticated GitHub actor still needs current registry grants and exact fresh evidence.

```sh
python scripts/encrypt_agent_intake.py --input /private/path/intake.json --output /private/path/sealed-dispatch.json
gh workflow run agent-intake.yml --repo narendranathe/apply --ref main --json < /private/path/sealed-dispatch.json
```

The sealed input is at most 48 KB and the decrypted envelope at most 32 KB. Tampering, wrong keys, unknown actors, incomplete indexes, stale projections or ambiguous storage all fail closed. Queuing is the only effect of this workflow.

Production publication can become complete only through cutover-bound authenticated baseline restoration and exact fresh source coverage. A configuration flag or a successful query is insufficient. The baseline manifest hash, readback counts and every original immutable record must match; the application lane independently repeats archived-application coverage before preparation and before any provider request. Missing history blocks effects and ready tasks. Preview and research-only modes never take this authority path.
