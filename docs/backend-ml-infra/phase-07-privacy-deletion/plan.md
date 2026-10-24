# Phase 07 — Consent, retention and coordinated deletion

Status: implemented locally on 2026-10-20; OS-gentle and final account-deletion browser interaction remain unexecuted. Automated, ordinary responsive/keyboard and project/withdrawal workflow acceptance passed. See [actual evidence](evidence.md), [inventory](inventory.md) and [development/rollback instructions](development.md). Phase 08 is not started. Source: Part B S6b, §§6.10, 7.7, 10; Part A §§14–15.  
Depends on phase 06. Mandatory before public M1 launch; privacy enforcement starts in 00–03.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

Private handwriting remains private by default. Users can withdraw training contributions and delete a project/account through a resumable process covering sources, derivatives, jobs, exports and identity.

## Work packages

1. Implement GET/PUT consent for separate purpose model_training and policy version. Product usage is not training consent. Enforce eligibility, active consent and per-sample contribution creation transactionally. No default opt-in.
2. Contribution copies live in a separate dataset prefix after EXIF stripping, with pseudonymous contributor reference, source/consent lineage, dataset/training-run versions and retention state. Dataset builds recheck consent rather than trust a stale export list.
3. Withdrawal marks contributions withdrawn, excludes future builds, deletes active copies where feasible and records removal time. State the honest limitation: withdrawal does not remove influence from already trained weights. Prevent withdrawal racing a dataset build from admitting new data.
4. Implement deletion requests for project, account and contributions_only. Persist step state/evidence; reject illegal scope/owner combinations. Project/account starts by marking deleting and blocking new uploads/jobs/exports. Sensitive account actions verify revocation/recent auth policy.
5. Cancel jobs and wait for terminal state/lease expiry. Fence new publication, revoke asset access/capabilities as supported and terminate/re-authorize streams. Track all assets, including historical guest-prefix captures after claim; never rely solely on current-owner prefix text.
6. Remove original captures, normalized images, crops, outlines, raw/derived strokes, result manifests, checkpoint blobs, style embeddings, fonts and reports. Verify missing metadata/list inventory. Treat storage failures as resumable failures, not deletion success.
7. Withdraw/remove dataset copies, then remove DB children in a tested FK order. Handle cyclic snapshot/active-revision refs explicitly. Keep only minimal content-free evidence with HMAC subject references; clear identifying columns/metadata. Delete Firebase user last, only after jobs/storage/DB cleanup is confirmed.
8. Add retry/reconciler with bounded backoff and operational alerts. Deletion can resume after every crash point. Account claim/delete races lock consistently. Contribution-only deletion does not delete the user's working project.
9. Enforce configurable retention for derived assets, checkpoints, pending uploads, idempotency rows, events and audit. Snapshot/export-referenced revisions persist until permitted project deletion. Orphan cleanup begins with a dry-run report; no indiscriminate bucket deletion.
10. Connect privacy actions to existing account/project controls with clear scope, confirmation, progress, failed/retry and completion states. Surface actual provider backup expiry once selected; do not promise instant backup erasure.

## Acceptance tests

- No contribution without active purpose-specific consent. Grant/withdraw concurrency, policy version and dataset rebuild exclusion pass.
- Private captures are never selected for training by default; withdrawal copy cleanup is verifiable and unlearning copy is accurate.
- Inject a crash before/after every saga step; resume without duplicate deletion or premature completion. Firebase deletion is always last.
- Running/queued jobs and stale workers cannot publish or serve assets after deletion starts. Issued URLs fail within documented revocation/TTL behavior.
- Storage inventory is empty for the deleted scope, including claimed guest work and orphan intermediate objects. Contribution-only purge preserves project assets.
- Database deletes satisfy FK/trigger rules without bypassing approval/export immutability for ordinary writes. Audit/evidence contains counts/codes only, no handwriting text or retained raw user IDs.
- Cross-user request/progress endpoints return 404. Retention preserves snapshot-referenced content and cleanup reports are accurate.
- Backup expiry/restore behavior is tied to the phase 11 provider evidence; deletion remains enforced if restoring an older backup.

## Evidence, rollout and rollback

Deliver a data inventory, consent lineage tests, crash-point deletion report, retention dry-run output and user-facing deletion semantics. Test restore reconciliation for completed deletions. Rollback may stop new contribution collection or new requests while existing sagas continue; it must never resurrect deleted data or re-enable withdrawn consent.

## Open gates

Provider backup expiry and revocation features; credential handling/identity in Q7; guest contribution policy. These gates block public promises, not local saga tests.


## Later owner-approved service policy — 2026-10-24

A separate service-accountability domain now requires registered/verified accounts
and current Terms/rights receipts for creation, edits and raw object transfers.
Existing identity recovery, optional training consent/withdrawal and account-erasure
routes have no new Terms gate. Migration 0014 receipts are removed by the validated
account-deletion saga; new audit admissions use the existing stable privacy HMAC format.
Public legal/operator/provider gates remain open. See
[local implementation evidence](../../accountability/evidence.md). Earlier acceptance
results and pending browser/hosted checks remain unchanged.
