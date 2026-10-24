# 0016 — Explicit consent and resumable erasure

Status: accepted for local Phase 07. Date: 2026-10-20.

The user explicitly authorized Phase 07, sequentially, stopping before Phase 08.
This implements v3 Part A §14 and Part B §§6.10/7.7 using the existing PostgreSQL
queue, identity service and ObjectStorage adapter. No training or provider is introduced.

Consent policy `model-training-v1` is explicit and independent of personal font use.
Grants are immutable; only the withdrawal timestamp can close a grant. Guests cannot
grant or contribute under the default versioned policy. Contribution creation is an
explicit sample action, restricted to verified metadata-stripped image derivatives or
validated raw stroke documents with private capture metadata removed. A durable ledger
precedes copying. Owner locks serialize consent, contribution publication, dataset
selection and withdrawal. Dataset manifests are immutable lists of consented contribution
IDs/hashes; live consent is rechecked at selection. No training runs are launched.

Deletion accepts project, account and contributions_only scopes with durable HMACed
idempotency receipts. Recent authentication (the existing configurable claim age) is
required for consent changes and deletion. Read-only progress remains available while
an account is deleting. No bearer capability can read handwriting after scope revocation.

Order: accept/revoke access and withdraw applicable contributions → cancel/wait for
jobs and lease expiry → preserve any surviving cross-project canonical aliases →
delete/verify known storage and dataset objects → delete database children/parents →
delete Firebase identities last → clear transient identifiers and retain counts only.
The saga is a system job in the existing queue, independent of the deleting identity.
Each step commits separately; effects are idempotent and retries use bounded backoff.
Running workers must lose their existing live/commit fence before cleanup begins.

Project erasure deletes the project parent in one statement, allowing existing cascade
and deferred cycle guards to see the absent parent. Ordinary immutability is retained.
Account erasure includes merged guest identity history, claimed storage keys and original
job idempotency scopes. A narrowly scoped database purge routine authorizes removal of
claim receipts only after storage completion and no remaining live scoped jobs. It does
not expose a general trigger-disable or immutability override.

An integrity-checked, content-free erasure journal lives through ObjectStorage outside
the database backup. HMAC subject/project references contain no raw UID or content.
Restore reconciliation re-applies deleting state and re-enqueues cleanup before serving
requests. Authentication consults account tombstones to prevent recreation; project
reconciliation occurs before API/worker startup. Restoring both the database and journal
to an older state cannot be solved locally; Phase 11 must verify independent backup and
restore procedures. Backup expiry remains unknown. No model unlearning is claimed.

Accepted withdrawals/deletions continue if new requests or contributions are paused.
Rollback stops admission, keeps compatible workers and journal, and uses forward repair;
it never restores revoked consent, removed data or immutable historical outputs.

See the [data inventory](../backend-ml-infra/phase-07-privacy-deletion/inventory.md).
