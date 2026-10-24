# Phase 06 local recovery development

Use the existing PostgreSQL queue and private local filesystem services from
[Phase 05](../phase-05-font-export/development.md). No provider is provisioned. The
frontend still uses local demo storage unless the owner explicitly connects private
services. Identity does not migrate local writing.

```sh
uv sync --locked --group dev --group worker --group research
export PYTHONPATH=apps/api:packages/contracts:packages/font-engine:.
make migrate
make api
# Separate shell, same DATABASE_URL, storage root and HMAC configuration:
WORKER_MODE=scheduled WORKER_MAX_RUN_SECONDS=300 make worker
# Alternatively:
WORKER_MODE=persistent WORKER_MAX_RUN_SECONDS=300 make worker
```

Both modes dispatch `asset_verify`, `analyze_samples` and `compile_export` through
one claim/lease/checkpoint/publication protocol. Scheduled mode exits when no eligible
work remains or its budget expires. Persistent mode backs off idle polling and stops
at its configured budget or SIGTERM. A future `run_after` does not keep a scheduled
invocation alive. A supervisor/scheduler remains an external Phase 11 concern.

## Configuration and admission

Migration head is `0009_queue_recovery`, with 26 application tables. It adds checkpoint
references, bounded stream slots, safe immutable event snapshots, asset/job association
and additional live/deferred publication fences. Apply migrations before starting the
new service binaries. Existing v1 job IDs/payloads and terminal records are preserved.
The compatible workers emit no new payload version. Unknown versions/profiles stay queued.

Operator configuration is in `system_settings`, never a caller-controlled priority.
`queue_policy.v1` validates `QueuePolicy`; its `version` selects `quota_tiers.vN`.
Defaults are local safety bounds, not production quotas: global concurrency 4, owner 2,
retry base 30 s/cap 600 s with ±20% jitter, checkpoint reuse TTL 86,400 s, SSE owner/global
slots 3/32 and stream lifetime 60 s. Level 3 reduces owner concurrency to one; level 4
rejects new job admission with Retry-After. Levels 1–2 reserve optional-feature policy;
no new optional feature or inferior algorithm is enabled. Existing reads, corrections,
approvals and validated downloads remain available. Invalid operator config fails closed.

Explicit owner **and** global `quota_balances` are required for storage and compute;
exports also require export count. Missing balances return `quota_unconfigured`.
Optional `quota_tiers.vN` has this shape (values below are test fixtures only):

```json
{
  "version": 1,
  "tiers": {
    "free_guest": {
      "compute_seconds": {"window_kind": "day", "limit_amount": 10000}
    }
  },
  "global": {
    "compute_seconds": {"window_kind": "day", "limit_amount": 100000}
  }
}
```

Use the actual `users.tier` key and configure every required resource. Supported
windows are day/month/lifetime. Provisioning is idempotent and preserves existing
balance limits; changing a tier record does not retroactively rewrite balances.
Reservations lock all relevant balance UUIDs in order, reserve both scopes in the job
transaction and settle once. Compute reservations use bounded stage time × attempts:
verification 60 s/attempt, analysis 30 s/sample/attempt, export 60 s/format/attempt.
These ceilings are safety reservations, not ETA predictions. Checkpoint storage is
also reserved and actual accepted bytes remain charged. Automatic top-up is disabled.
Over-reservation usage prevents success publication; terminal cleanup commits at most
the reserved cap and records actual measured use plus the uncommitted overage. Killed
attempts are explicitly unmeasured; this is not provider billing reconciliation.

## Recovery and diagnosis

Workers checkpoint a completed strict decode, each assessed sample and each fully
validated export format. The identity covers inputs/template/model/configuration/
algorithm/dependency and code hashes. Outputs and manifest are immutable; the database
reference publishes last under holder/attempt/lease/scope fencing. A replacement verifies
all bytes and reconstructs its own attempt-specific result/candidate IDs. Corruption is
`checkpoint_invalid`; retain evidence and investigate rather than editing accepted bytes.
The TTL limits reuse, not historical retention. Accepted history is preserved; general
retention/deletion is Phase 07. Pending ledgers identify crash output for reconciliation.

SIGTERM stops claims and finishes/checkpoints the current bounded unit before requeueing
(or completes a final already-safe publication). Attempts remain monotonic; max_attempts
increases for the replacement so clean termination does not consume a failure allowance.
SIGKILL recovery waits for lease expiry and normal retry backoff. Two identical failures
for the same stage/input/code stop as `poison_suspected`. No failure state reopens a
terminal job. Stage children enforce CPU/wall/RSS limits and poll interruption every 20 ms;
the parent checks the database at most every 0.5 s (faster in explicit test fixtures).
That local control rate has not been qualified against a hosted pooler.

`GET /api/v1/jobs/{id}` is the reliable owner-only baseline: ETag and Retry-After
(queued 5 s, running 2 s, terminal 30 s). Cancellation is
`POST /api/v1/jobs/{id}/cancel`; queued work cancels immediately and running work reports
`cancel_requested` until acknowledged or reaped. `GET /api/v1/jobs/{id}/events` uses the
same durable snapshots, Last-Event-ID, 1.5 s database polling and 15 s heartbeat comments.
It rechecks token/account/project/ownership, bounds slots/lifetime and uses no
LISTEN/NOTIFY. Pass credentials in Authorization, never URLs. Event cursors are job scoped.
The frontend starts with polling, reconnects SSE with its cursor and falls back to adaptive
ETag polling after repeated stream failures. Reload starts from saved server state. ETA
stays null/insufficient_data; no matching benchmark was qualified by this phase.

```sh
# Disposable real PostgreSQL database required; never point tests at real writing.
TEST_DATABASE_URL=... .venv/bin/python -m pytest -m 'not emulator' -q
TEST_DATABASE_URL=... PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 \
  .venv/bin/python -m pytest -m emulator -q
make lint
make contracts-check
npm test
npm run typecheck
npm run lint
npm run build
npm start
```

The queue recovery tests exercise actual SIGKILL/SIGTERM processes, 1/5/20 concurrent
database claimers, reservation races, checkpoint reuse and EXPLAIN plans. The acceptance
test drains all three real handlers and records synthetic stage wall/RSS profiles.
Reports are in `artifacts/phase06/`; see [evidence](evidence.md) for exact results and
unexecuted browser checks. The disposable browser helper is
`scripts/phase06_local_acceptance.py` (`initialize`, then connect a local emulator guest,
then `seed`). Its schema and objects are explicitly synthetic. Browser automation was
disabled before the Phase 06 interactions could be checked. Do not treat seed approval
as Phase 04 human-review acceptance.

## Rollback and remaining gates

Stop new schema emission first (this phase still emits v1); retain compatible workers,
drain queued/running jobs and preserve terminal records/checkpoints. Prefer stopping new
admission or a forward repair over live downgrade. Never truncate the queue or delete
accepted history to make a downgrade pass. Empty up/down/up is tested; 0009 refuses
downgrade when checkpoint references exist. New binaries require the additive schema;
database rollback alone does not provide old-worker compatibility with the new fencing
protocol. Coordinate binaries/schema rather than mixing incompatible releases.

Provider pooler/SSE limits, hosted worker service identity, App Check, shared public
rate limiting, privacy/consent/deletion, real-handwriting accuracy and public readiness
remain unresolved. No hosting cost or production capacity is inferred. Phase 07 requires
separate explicit authorization.
