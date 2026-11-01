# Pinned index custody recovery

This is a one-time recovery for failed run 40050463437 and guard stops 40101990960, 40103832704, 40204362589 and 40204819102. It appends exactly five immutable audit resolutions. It does not replay an application, outreach action, business lane, completion marker or historical import. The old index stays incomplete; its original publication clock and upstream exception stay unverified.

## Current deployment boundary

The implementation is in [PR #28](https://github.com/narendranathe/apply/pull/28). It is not deployed or applied. Production remains disabled and research remains configured. The owner chose to wait for the November 1 Actions allowance reset without changing spending. No actual successful hosted custody receipt exists yet.

Local engineering validated the actual authenticated 2,902-page / 2,655-CORE trusted snapshot and 3,631-page / 3,384-CORE observed snapshot, all 757 historical records, the exact one-field unmanaged mention drift, all candidate index children and the native run/job/log/artifact witnesses. These are offline and read-only proofs, not a completed hosted recovery.

## After Actions capacity returns

1. Confirm that Actions admits jobs. Pass current PR CI, including the push-only scripts/verify_index_custody.py --archives-only actual archive check. Merge normally and pass main CI. Do not bypass blocked checks or rerun an old operational failure.
2. Read back CLOUD_ENABLED=false and CLOUD_RESEARCH_ENABLED=true. Both desktop automations must remain paused; the user-confirmed Milo and Muse stops remain applicable.
3. Run and independently authenticate a current-main all-lane research validation. Its preview remains nonpersistent. Confirm exact reconstruction of physical pages and the logical manifest under current source.
4. Dispatch the **new** index-custody-reconciliation.yml workflow once on main with **mode=reconcile**, its initial/default mode. Do not dispatch verify against an absent cohort: it cannot create one, and the failed invocation remains fenced. The first writing invocation must be the only prior invocation of this workflow.
5. Retrieve the actual successful native run and artifact. Use scripts/verify_index_custody.py --run-id followed by its actual successful run ID, with the existing scoped GitHub credential and backup key supplied securely in the process environment. Never place credentials in the command line, workflow inputs, logs or chat. The tool prepares an actual receipt and exact encrypted complete snapshot under recovery-evidence/. It performs no operational write or activation.
6. Review and commit those actual files through normal CI/deployment. Independently check native run/jobs/artifact rereads, ZIP/cipher/member hashes, original execution binding, whole five-record cohort, exact full-source readback and backup. A simulator, summary, successful upload or full persisted cohort alone is insufficient.
7. Run the strict read-only runtime preflight. Only then assess the remaining production readiness gates. Production enablement is a separate action.

The manual-only workflow requires the private owner/main/attempt-one context and uses hiring-outreach-single-writer with cancel-in-progress false. It has a 180-minute job ceiling and a 150-minute request budget. Recovery performs one complete fresh source scan before appending and one final backup scan. Every append gets exact fresh readback without write retry. No PDF tool installation or provider call is required.

## Failure and later verification

Any proper subset, changed record, unexpected current source, different raw drift, missing history, changed attempt, uncertain write or failed backup stops recovery. Keep production disabled. Do not blindly retry or change an ID to escape suppression.

If all five records exist exactly, mode=verify is read-only and uses the original append binding. It creates no resolutions. It can verify a completed cohort after the original executor failed during backup. A later main commit is allowed only when every protocol source byte matches the original binding. Extra or uncertain maintenance attempts require explicit evidence review; there is no generic override.

The restored final backup must equal the entire observed snapshot plus exactly the five expected audit records. It preserves the ready record, immutable history/suppressions, V3 hold/cohort and both permanently reserved observation absences. The known unmanaged mention display change is pinned by exact page identity/hash/path and both whole-page hashes. No general plain_text normalization is allowed.

## Durable receipt and artifact retention

Independent receipt admission freshly checks full native artifact list/item metadata twice, exact ZIP members/digest, authenticated ciphertext/plaintext-member hashes, complete restored source/cohort equality and successful terminal run/jobs twice.

After admission, runtime uses the separately committed ciphertext and exact artifact witness. It rechecks receipt schema/self-hash, ciphertext hash, original source binding, all peers/holds/reservations, execution and verification run/jobs/attempts, and complete maintenance history. It does not depend on expiring artifact downloads and never treats a 404 as successful verification. Missing or mismatched receipt/ciphertext remains blocked.

The separate maintenance-history fence applies even if an old cloud run disappears from cloud workflow history. A later failed, queued, foreign-trigger or rerun maintenance attempt cannot inherit an old receipt.

## Production gates still pending

Custody verification does not establish current index completeness or authorize an external effect. After actual recovery and receipt deployment, validate a newly published persistent index, independently authenticate its backup, prove same-Chicago-day no-repeat behavior, and execute a real hosted intake/consumer/repeat. Preserve all application/relationship holds and permitted-provider restrictions. Unsupported submissions stay exceptions; LinkedIn sends remain human-executed without a specifically permitted integration.
