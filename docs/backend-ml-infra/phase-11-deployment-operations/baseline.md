# Phase 11 baseline audit

Historical pre-Phase-11 audit (2026-10-20), preserved for comparison. It is not the current
implementation inventory. See [current evidence](evidence.md) and [repairs](audit-repairs.md).

Date: 2026-10-20. This is the "before" record for Phase 11: what exists, what is
provider-neutral, what is host-only, and which gates were already open. Nothing here is
a deployment claim. Every item was read from the working tree on this date.

## Application surface

| Item | Current value | Source |
|---|---|---|
| API entry point | `app.main:create_app` (factory) | `apps/api/app/main.py` |
| Readiness app | `app.internal:create_internal_app`, separate port, bearer token, not mounted on the public API | `apps/api/app/internal.py` |
| Worker entry point | `python -m services.worker`, sequential `PipelineWorker` over `asset_verify`, `analyze_samples`, `compile_export`, `privacy_maintenance` | `services/worker/__main__.py`, `services/worker/runtime.py` |
| Migration head | `0012_devanagari` | `apps/api/app/db/revision.py` |
| Application tables | 27 | `infrastructure/migrations/schema.sql` |
| Public health | `GET /api/v1/health` → `{"status":"ok"}` only | `apps/api/app/main.py` |
| Readiness | authenticated `/ready`, checks DB connectivity and exact Alembic head | `apps/api/app/internal.py` |

## Configuration

`Settings` (`apps/api/app/core/config.py`) is environment-only and refuses to read a
secrets file. Relevant names, all consumed through pydantic settings:

`APP_ENV`, `DATABASE_URL`, `DATABASE_POOL_SIZE`, `AUTH_BACKEND`, `LOCAL_AUTH_TOKEN`,
`FIREBASE_PROJECT_ID`, `FIREBASE_AUTH_EMULATOR_HOST`, `FIREBASE_ALLOW_EMULATOR`,
`CLAIM_MAX_AGE_SECONDS`, `RATE_LIMIT_BACKEND`, `IDENTITY_RATE_LIMIT`,
`IDENTITY_FIRST_REQUEST_LIMIT`, `IDENTITY_GLOBAL_FIRST_REQUEST_LIMIT`,
`CLAIM_RATE_LIMIT`, `RATE_LIMIT_WINDOW_SECONDS`, `HMAC_SECRET`, `HMAC_KEY_VERSION`,
`STORAGE_BACKEND`, `STORAGE_ROOT`, `UPLOAD_ISSUANCE_ENABLED`, `UPLOAD_MAX_BYTES`,
`UPLOAD_MAX_PIXELS`, `UPLOAD_MAX_DIMENSION`, `UPLOAD_FILES_PER_PROJECT`,
`PENDING_UPLOAD_TTL_SECONDS`, `SIGNED_UPLOAD_TTL_SECONDS`,
`SIGNED_DOWNLOAD_TTL_SECONDS`, `DERIVATIVE_MAX_BYTES`, `DERIVATIVE_MAX_DIMENSION`,
`LEASE_SECONDS`, `HEARTBEAT_SECONDS`, `WORKER_MEM_MB`, `DECODE_CPU_SECONDS`,
`DECODE_WALL_SECONDS`, `WORKER_MODE`, `WORKER_MAX_RUN_SECONDS`, `WORKER_TEMP_ROOT`,
`JOB_MAX_ATTEMPTS`, `READINESS_TOKEN`, `CORS_ALLOWED_ORIGINS`, `REQUEST_MAX_BYTES`,
`IDEMPOTENCY_RETENTION_SECONDS`.

Enforced safety properties already in code:

- `auth_backend=fake` and `storage_backend=local_fs` are rejected outside `local`/`test`.
- The Auth emulator requires an explicit `demo-` project, an explicit loopback host/port
  and `firebase_allow_emulator`; ambient `FIREBASE_AUTH_EMULATOR_HOST` is refused.
- `auth_backend=firebase` is currently rejected outside `local`/`test` because the
  process-local rate limiter is the only implementation. Hosted identity therefore has
  an explicit, tested gate rather than a silent fallback.
- `heartbeat_seconds < lease_seconds`; HMAC secrets must be ≥32 characters.

## Caps and where they come from

| Cap | Value | Source of truth |
|---|---|---|
| Upload bytes | 20 MiB | `Settings.upload_max_bytes` (hard upper bound in the field) |
| Upload pixels / dimension | 50 Mpx / 12000 px | `Settings` |
| Request body | 1 MiB default | `Settings.request_max_bytes` |
| Files per project | 200 | `Settings.upload_files_per_project` |
| Pending upload TTL | 24 h | `Settings.pending_upload_ttl_seconds` |
| Capability TTL | 900 s upload / 600 s download | `Settings` |
| Lease / heartbeat | 60 s / 20 s | `Settings` |
| Worker memory admission | 768 MB default, per-job `resource_profile` | `Settings.worker_mem_mb`, job payload |
| Decode CPU / wall | 10 s / 20 s | `Settings` |
| Job attempts | 3 | `Settings.job_max_attempts` |
| Derivative bytes / dimension | 4 MiB / 1000 px | `Settings` |
| Storage/compute quotas | **not seeded in the repository** | `system_settings.quota_tiers.v{version}` |

The quota finding is a real deployment blocker, not a documentation gap:
`configured_balances` reads an optional `quota_tiers.v{n}` row and deliberately invents
nothing. A fresh database therefore returns `503 quota_unconfigured` for storage and
compute instead of applying a default allowance. Provisioning that tier is an operator
action that Phase 11 must specify; the repository contains no seed, fixture default or
migration that creates one.

## Egress and abuse controls already implemented

- Per-identity and global first-request limits, claim limits and a fixed-window limiter.
- PostgreSQL-backed idempotency receipts with bounded retention.
- Upload capability hashing, owner scoping, dedupe and immutable publication.
- Strict fenced queue transitions; workers cannot activate glyphs or snapshots.
- Content-free structured logs restricted to an allowlist of events and numeric fields.

## Provider-neutral vs host-only

| Provider-neutral (works on any PostgreSQL + object storage) | Host-only today |
|---|---|
| Schema, guards, migrations, queue/leases, quotas, idempotency | `LocalObjectStorage` (filesystem, dirfd, in-process capabilities) |
| Storage protocol (`ObjectStorage`) and capability model | `make_async_engine` defaults plus a local socket DSN |
| Worker loop, checkpoint store, fencing, retention/erasure | Worker startup refuses anything but `local`/`test` + `local_fs` |
| Font engine, validation, exports | Docker images exist but were never executed here (no Docker on this host) |
| Research/benchmark harnesses | CI is one workflow (`phase-00.yml`), never run on a hosted runner |

There is **no cloud object-storage adapter**. `storage_backend` accepts only
`local_fs|disabled`, and `services/worker/__main__.py` exits unless the environment is
local/test with `local_fs`. That is the single largest M1 gap: the public English path
cannot run on a hosted runtime without a storage adapter and a hosted-capable worker
entry point.

## CI and release tooling

`.github/workflows/phase-00.yml` is the only pipeline. It runs `make setup` / `make check`,
a Firebase Auth emulator pass, and builds both Dockerfiles. It has no PostgreSQL
integration job beyond the service container used by `make check`, no migration drift
check, no image size/dependency budget, no secret-leak scan, no staging smoke, and no
promotion gate. `make check` = lint, contracts-check, frontend/API tests, typecheck, lint,
build.

Host capability at audit time: no Docker, no `psql`, no `pg_ctl` on `PATH`. The Python
environment does ship `pgserver` with bundled PostgreSQL binaries, which is what the
Phase 03–09 integration evidence used and what the Phase 11 benchmark harness uses.

## Outstanding gates inherited from earlier phases

Phase 03/04 current-wizard browser checks, Phase 05 OS-gentle browser check, Phase 06
browser/responsive acceptance, Phase 07 OS-gentle and final account-deletion browser
interaction, Phase 08 OS-gentle/physical touch, Phase 09 responsive visual/keyboard, and
the entire Phase 10 browser check of the spacing control. None of these become passing
because infrastructure work happens; they are carried forward verbatim.

## Baseline commands and observed results

| Command | Result on 2026-10-20 |
|---|---|
| `make lint` | passed (ruff, format, mypy, boundaries, docs) |
| `.venv/bin/python -m pytest -m 'not integration' -q` | 405 passed, 214 deselected |
| `npm test` | 118 passed |
| `make contracts-check` | passed, retained v1 compatibility reported |
| `npm run typecheck` / `npm run build` | passed |
| Docker build / compose | **not executed** — Docker is not installed on this host |
| Hosted CI, staging, production | **never executed** — no remote, runner, provider or credentials |

These are the numbers Phase 11 changes must be compared against.
