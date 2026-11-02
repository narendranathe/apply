# ADR: Preserve historical custody bindings during technical-intelligence integration

Status: proposed. This decision records constraints for future development; it does not authorize a hosted job, migration, reconciliation, or production activation.

## Context

PR #28 merged to main at `000317e58ea26f798fcfd9e119c49589371d6ed1`. Its custody recovery requires the exact historical source bindings and a complete, authenticated comparison of Notion history. Hosted [reconciliation run 41333025512](https://github.com/narendranathe/apply/actions/runs/41333025512) stopped with `custody_fresh_source_changed`. The code checks that condition before its planned audit appends; the run alone is not sufficient proof of the current source state or the absence of all effects. The failed attempt remains part of the native history and must not be retried blindly.

The technical WIP changes both `cloud/agent_index.py` and `cloud/notion.py`. Main's custody code binds both source files to the original correction run and uses the historical index builder to reproduce historical children. A local trial merge failed main's frozen builder hash test. Altering the expected hash, bypassing source binding, or merely copying the old file under a new path would conceal the incompatibility; a passing synthetic index fixture is not custody proof.

## Decision

1. Preserve the historical builder, Notion adapter, source-binding validation, original artifacts, suppression data and current disabled production flag. Keep the technical work on its separate WIP branch until a reviewed integration can satisfy every existing custody gate.
2. Develop technical behavior in separately versioned modules. A future publisher or projection version must specify how legacy input produces exactly the historical COREs, identities, manifest and cursor behavior, and how newly added technical state is represented without changing the meaning of the historical proof. Keep v1 history immutable; do not infer that a wrapper or separate version is compatible solely because its local fixtures pass.
3. Prepare a separate, explicitly read-only diagnostic for the failed custody attempt. It must authenticate the pinned observed archive using the existing key and pin checks, read a complete fresh source snapshot under the existing central-writer coordination, and separately inspect full native execution and artifact history, expected cohort and reserved identities. Return only sanitized aggregate findings. It must not append records, retry the failed attempt, call the existing reconciliation path, publish an index, modify flags or declare recovery. A match cannot override the prior-attempt fence.
4. Review any hosted diagnostic's own workflow-history classification before use; it must not masquerade as an original custody run or make that run retryable. A guarded transport, caller-supplied evidence, fixture result, repository secret name or local token alone is not authentication, complete-snapshot proof or sole-writer coordination. The diagnostic requires explicitly available scoped credentials and the existing backup key; neither was available in the development shell when last checked.

## Acceptance evidence before operational wiring

- Test exact-byte historical source pins and the original historical reconstruction independently of any new builder version. Verify that historical applications, contact suppressions, unknown outcomes and links are retained byte-for-byte where required.
- Test no mutation at the transport boundary, complete pagination, authenticated archive hashes, source identity, snapshot ordering, readback, reserved IDs and absent, partial, full or conflicting cohorts. Test incomplete history, malformed data, ambiguous writes and changed state between reads as fail-closed. Fixed diagnostics must not emit private record contents or credentials.
- Test full legacy and new-format projection/dossier/cursor parity, response limits, denial and suppression on synthetic paged and nonpaged fixtures. Preserve exact prior records and demonstrate no competing writer, no unauthorized social effect and no altered application authorization.
- Run the required integration and exact-revision hosted checks only when permitted and capacity is verified. Review the failed custody attempt and any new evidence independently; require a verified durable receipt, backup, historical source binding and all existing rollout gates before considering production activation. Preserve the 06:00 and 06:30 America/Chicago schedules.

## Consequences

This keeps the historical evidence contract reviewable and postpones an operational technical publisher. It adds development and provider-read cost and does not itself establish that the current Notion snapshot, the failed attempt or any external publication is safe or reconciled.
