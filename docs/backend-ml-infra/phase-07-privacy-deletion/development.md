# Phase 07 local privacy development

Use the existing [Phase 06 setup](../phase-06-queue-recovery/development.md). This phase
uses the same PostgreSQL queue, Firebase adapter and private local ObjectStorage. It
creates no provider, deployment, public policy, trained model or separate processing runner.

```sh
export PYTHONPATH=apps/api:packages/contracts:packages/font-engine:.
# Export DATABASE_URL, STORAGE_ROOT and a private stable HMAC_SECRET (at least 32 characters).
make migrate
make api
# Separate terminal with the same configuration:
WORKER_MODE=persistent make worker
npm run dev
```

Migration head is `0010_privacy`, with 27 application tables. It adds privacy request
work targets/HMAC receipts, contribution copy lineage, immutable dataset versions and
asset last-use timestamps. Empty up/down/up and seeded prior-release upgrades are tested.
Backfill flushes deferred constraints before DDL. Downgrade refuses once erasure or new
contribution/dataset lineage exists; use forward repair. Do not delete records to permit
an application downgrade.

Privacy is unavailable without a stable `HMAC_SECRET`. Identity/capture demos keep their
existing defaults. Never rotate or discard the HMAC key or independent erasure journal
while receipts exist without a separately verified migration. The journal is stored under
`privacy/erasures/` through the same adapter. API/worker startup verifies it and reconciles
restored targets before serving/processing. A restore that also loses/rolls back this
journal remains an unresolved operational gate. Provider backup expiry stays unknown.

## Explicit user controls

In `/account`, sign in and choose **Open private settings**. No local writing is read or
uploaded. Consent is off by default. A registered owner may explicitly grant
`model-training-v1`; guest grants/contributions fail closed. A separate selected-sample
confirmation copies only that stored sample into the dataset area. Image copies reuse
verified metadata-free PNG derivatives; stroke copies retain geometry without timing/pen
hardware metadata. This is consented data transport, not training or an approved dataset.

Withdrawal closes the active grant, excludes subsequent dataset selection immediately,
and queues dataset-copy cleanup. New explicitly granted consent remains distinct.
Project/account/contribution-only deletion shows exact scope, explicit confirmation,
real step states, automatic retry and completion. Project/account deletion blocks private
access before cleanup. Local demo drafts and explicitly retained local capture backups
remain separate and are not silently uploaded or erased by cloud controls.

Sensitive consent/deletion requests require the existing recent-auth window (600-second
local default, configurable). Token refresh does not renew it; sign out/in to renew
registered authentication. No anonymous reauthentication policy is invented. A private
owner-issued receipt permits status after Firebase deletion; it appears only in a header,
never a URL. The tab retains the receipt in owner-scoped session storage. Keep it private.

## Contracts and operator configuration

- `GET/PUT /api/v1/consent`, purpose `model_training`, explicit boolean/policy version.
- `POST /api/v1/privacy/contributions`, explicit sample/consent/policy and idempotency key.
- `POST /api/v1/privacy/deletion-requests`, scope, optional project/version, confirmation
  and required idempotency key. `GET .../{id}` accepts owner Bearer or scoped receipt.
- Existing project `DELETE` adapts its strong ETag to the same saga and natural retry key.
- `privacy_maintenance` v1 is a system job containing only deletion-request ID. The same
  PipelineWorker claims it with the existing global cap, holder, monotonic attempt and lease.
  Normal owners cannot cancel privacy jobs. Crash/expiry requeues accepted cleanup.
- `PrivacyService.build_dataset(version)` is an offline selection seam, not a public
  training endpoint. It rechecks owner/consent under the same locks as withdrawal and
  stores immutable membership hashes. Writer splits, licensing promotion and training
  remain Phase 08 research gates; no training is started to manufacture evidence.

`system_settings` operator keys:

```json
{"key":"privacy_policy.v1","value":{"version":1,"policy_version":"model-training-v1","guest_contributions":false,"new_requests_enabled":true,"contributions_enabled":true,"retry_base_seconds":2,"retry_cap_seconds":600,"stuck_seconds":3600}}
```

Accepted deletion/withdrawal operations continue when either admission switch is false.
Retry delay is capped; failed dependencies retain completed steps. Workers emit
`privacy_saga_stuck` with a count when the stuck count changes. `PrivacyService.signals()`
provides the content-free count/threshold; provider alert delivery remains Phase 11.

## Retention and reconciliation

Start with an explicit private dry run. The report contains opaque asset/checkpoint IDs,
policy, counts and integrity hash, without source text or raw identity. Apply rechecks
live references/ownership and only removes still-eligible known objects. Unknown objects
remain inventory reports. Snapshot/export/result/revision references are preserved.
Active alias-preservation ledgers belong to accepted erasure and do not expire through
ordinary abandoned-upload cleanup.

```sh
mkdir -p .local/privacy-reports
PYTHONPATH=apps/api:packages/contracts:packages/font-engine:. .venv/bin/python -m services.worker \
  --retention-report .local/privacy-reports/retention-new.json
# Inspect that exact dry-run report before applying:
PYTHONPATH=apps/api:packages/contracts:packages/font-engine:. .venv/bin/python -m services.worker \
  --retention-apply .local/privacy-reports/retention-new.json
# General inventory remains dry-run only:
PYTHONPATH=apps/api:packages/contracts:packages/font-engine:. .venv/bin/python -m services.worker \
  --reconcile-report .local/privacy-reports/orphans-new.json
```

`retention_policy.v1` has version 1 and configurable seconds for pending uploads,
unused derived assets, terminal checkpoints, staged publications, idempotency receipts,
terminal events and audit. Defaults and retained boundaries are in
[ADR 0017](../../decisions/0017-phase-07-retention-and-restore.md). Retention is an explicit
operator action through the existing worker entry point; no hosted scheduler is provisioned.
Account erasure additionally handles merged ancestry and historical claim keys, with
Firebase identities last. Test privileges are local administrator credentials. Hosted
least-privilege privacy/service identity and provider-scoped grants remain gates.

## Verification and rollback

```sh
# Disposable real PostgreSQL only; local Auth emulator for the separate emulator suite.
TEST_DATABASE_URL=... .venv/bin/python -m pytest -m 'not emulator' -q
TEST_DATABASE_URL=... PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 \
  .venv/bin/python -m pytest -m emulator -q
make lint contracts-check
npm test
npm run typecheck
npm run lint
npm run build
npm start
```

The isolated browser fixture is `scripts/phase07_local_acceptance.py initialize`, API
factory `scripts.phase07_local_acceptance:app` on port 8007, worker mode `worker`, and
frontend port 3070 with the Phase 01 public emulator variables. It uses a named synthetic
schema and dedicated object root. Never point it at real writing. Emulator debug output
can contain disposable credentials; do not copy it into application evidence.

Deploy compatible Phase 07 handlers before enabling new requests. Older Phase 06 reapers
are not advertised as compatible with new system privacy jobs. Rollback pauses new requests
and contributions, retains compatible cleanup workers, journal and key, and drains accepted
operations. No queue truncation, consent resurrection, history rewrite or data restoration
is a rollback shortcut. Existing v1 capture/analysis/export payloads continue to work.

See [evidence](evidence.md). Phase 03/04 browser, Phase 05 OS-gentle and Phase 06 browser
checks remain pending. Phase 07's own ordinary responsive/keyboard evidence does not close
those earlier checks. Hosted Firebase, service identity, App Check, shared rate limiting,
provider backup expiry, real-handwriting quality and public readiness remain unresolved.
Stop before Phase 08; it requires a separate authorization and accepted Phase 07 evidence.
