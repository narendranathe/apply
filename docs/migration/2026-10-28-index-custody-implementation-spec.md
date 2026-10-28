# October 28 index custody implementation specification

Status: local implementation and synthetic validation. Hosted reconcile, hosted verify, authenticated durable receipt publication and current healthy-index proof remain pending. This maintenance protocol never activates production, retries a business operation or completes the original publication.

## Scope and fixed inputs

The protocol addresses exactly runs 40050463437, 40101990960, 40103832704, 40204362589 and 40204819102 under schema `apply-pinned-index-custody-writer-resolution/v1`. It preserves the original unknown publication clock, unknown failure cause, incomplete index, unresolved native delivery/history, permanent reserved observation identities and existing V3 hold/cohort. Its only permitted writes are five new immutable audit records, using the existing central writer lock.

`cloud/index_custody_evidence.py` owns reviewed static native/source/archive pins. The exact reviewed diagnosis is `docs/runbooks/evidence/2026-10-26-authenticated-incident-diagnosis.json`, SHA-256 `32b14333869d3cd3e03b994c73242c9e3b976d47f459cb19af17b944b9ff6037`. No caller, environment or workflow input supplies incident, archive or evidence pins. Native evidence readers receive only explicit read-only GitHub operations. Raw captures, keys, decrypted records and diagnostic bodies are absent from the protocol's summaries and new tracked source.

The two AES archives are authenticated with the existing restore helper in private ephemeral staging. Each exact archive/member hash, complete snapshot fingerprint, source page count and CORE record count must match reviewed pins. Both raw snapshot versions remain intact. The pinned post-failure fingerprint commits all 729 added COREs and all raw source bodies; the coordinator cannot substitute an aggregate count for that commitment.

## Pure state validation

`build_plan(before, after, exported)` requires every prior CORE and raw page, identical source identity/schema/permissions/parent metadata under existing edit-stamp exclusions, exact baseline hash and import, ready record, frozen V3 cohort, hold and reserved-ID absence. It independently checks exact added kinds/counts, four predecessor-named guard audits, no new provider attempt/receipt/outreach signatures and no new manifest.

The only raw-page exception is one unmanaged page with reviewed identity hash, reviewed before/after whole-page hashes and one changed scalar at `blocks[3].paragraph.rich_text[1].plain_text`. Both values are strings, both rich-text objects remain mentions, structured mention fields remain unchanged and `text` remains absent. No field is deleted, normalized or generally ignored. A second drift, managed-page drift or structured-field change fails closed.

The frozen semantic builder reproduces the candidate child set in memory using canonical COREs. It checks source/child commitments, every matching child, absence of a candidate manifest and the independently measured old-format capacity defect. The candidate clock remains explicitly diagnostic; no expected original manifest identity/hash is emitted as original evidence. Neither a builder call nor any validator can publish an index or execute a lane.

`reviewed_plan()` returns a bounded safe commitment derived only from reviewed pins. `build_plan()` returns that same deterministic value after validating actual authenticated inputs. Runtime may reconstruct the safe plan without decrypting or reading a key.

## Deterministic cohort

`resolution_records(plan, binding)` creates four peers followed by the lead. Every marker retains false values for external-effect reconciliation/retry, original clock/failure-cause proof, original publication completion, index completion, native delivery/history completeness and activation. Only managed immutable Cloud custody is verified.

Peer hashes cannot recursively contain each other's final whole CORE hashes. The protocol therefore uses a noncircular commitment: all five markers contain the same exact template CORE hash map, computed before commitment fields are added; the lead additionally commits all four final peer whole CORE hashes. Runtime reconstructs all five entire COREs from the reviewed plan and independently verified original binding, so changing any peer, commitment, status, flag or plan fails exact comparison.

The lead payload uses `correction_binding`. The original binding is fixed for the first append run and retained forever. A later hosted verification uses its own current binding while reconstructing all records with the original binding. Its selected verification head may differ from the original executor head after documentation-only changes, provided every protocol source hash matches exactly at both heads and the local reviewed protocol; commit equality alone is not the source contract. A complete cohort does not permit re-appending anything.

## Append boundary and one-shot fence

`reconcile(store, exported, github, context, key, mode='reconcile')` validates context before native/archive access: owner and triggering owner, exact private repository/default main, current deployed commit, GitHub-hosted Ubuntu runner, attempt 1, workflow dispatch, exact mode, production disabled, research enabled, reviewed source equality and common lock. Native run/job/step/source proofs are independently reread before writes.

The coordinator obtains one fresh complete preappend source snapshot under the common lock and compares it to the authenticated observed snapshot after removing only an already-complete exact cohort. The wrapper obtains one final complete backup snapshot, authenticates its restored contents and validates exact observed-state-plus-cohort equality. These are the two complete scans per hosted operation; scans are not repeated before every row. Native binary evidence is fully authenticated once during reconciliation, followed by fresh native run/job/source and current-context rereads before appends. Exact immediate row readbacks cover each append boundary. No global source atomicity claim is made.

All five records must be absent or all present and exact. Every proper subset, immutable conflict, unrelated new source record or raw drift blocks. First execution also requires complete native history for the new workflow across all trigger types, containing only the current run. Any other maintenance attempt blocks further automatic appends, including a foreign trigger or an ambiguous prior execution whose record has not become visible. No event filter may hide an attempt. This strict rule prevents retry by starting a fresh run.

Every `put` has exactly one immediate fresh `get`; both returned and observed COREs must match exactly. The first exception, absence or mismatch stops without sleep, retry, new epoch or remaining puts. Known Notion errors retain only the sanitized HTTP status, closed error code and operation through their base diagnostics implementation; upstream bodies, strings and arbitrary overrides are excluded. The lead is last. The workflow defaults to `reconcile`, which is the first operation for an absent cohort. `verify` is used only after all five exact records exist; it never puts and cannot serve as a first operation on an absent cohort.

## Durable verification and runtime clearance

Core success is provisional until the hosted wrapper authenticates and validates the complete final backup. Independent receipt admission requires a successful hosted `verify` run, fresh complete artifact listing and direct artifact-item metadata, exact run/head/member bindings, an authenticated ZIP download and authenticated AES restore with exact source/cohort equality. Native run/jobs/history and artifact list/item witnesses are reread to reject changes during admission. Admission must finish while the artifact is available; a missing, expired or unreadable artifact fails closed.

After admission, the authenticated receipt and ciphertext are separately committed as durable evidence. Runtime verifies those durable files, their exact hashes, cohort commitments and protocol sources, together with fresh complete native run/job/history witnesses. Runtime has no dependency on historical artifact retention and makes no artifact-list/item/download request for clearance. This is a deliberate use of previously admitted durable evidence, never treating a 404 or any other failed request as success. A persisted cohort alone, a local synthetic test or a maintenance click never clears the incident. Missing durable receipt is the expected fail-closed state before hosted capacity returns.

Known IDs and this schema family route exclusively through the new typed custody matcher before foreign startup/generic fallbacks. The matcher reconstructs exact COREs from reviewed commitments, verifies old V3 preservation, rechecks all five incidents including later attempts and requires the durable verification proof. Existing cutover, deduplication, provider permission, truthful preparation, central-writer, intent/receipt and quarantine gates remain independent.

## Local evidence and remaining work

The synthetic core suite covers real `SnapshotState`, actual AES-GCM create/restore helpers, exact mention drift, baseline/CORE preservation, real frozen semantic reconstruction, all-or-none cohorts, original binding reuse, native/context changes, one fresh full snapshot, no retry on ambiguous writes and no automatic second execution.

Local passing tests do not prove current persistent index health, an actual hosted no-repeat result, provider intake or production readiness. Those remain pending until owner-authorized hosted work after capacity returns. No billing, repository variable, cloud dispatch, Notion record or production flag is changed by this implementation.
