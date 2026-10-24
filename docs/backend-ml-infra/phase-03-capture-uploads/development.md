# Phase 03 local development

Read [Phase 00 setup](../phase-00-foundations/development.md),
[Phase 01 identity setup](../phase-01-identity/development.md) and
[Phase 02 local development](../phase-02-projects/development.md) first. Phase 03 adds
private local capture storage and the first real durable worker. It does not upload
existing local drafts automatically, call a hosted provider, extract glyphs or generate a
font. Default local demos still need no account or storage service.

## Database and migration

Use the Phase 00 Docker Compose or native PostgreSQL setup. The migration head is
`0005_publication_fence` at the Phase 03 boundary. `0004_capture_uploads` adds capture source/template provenance, verify-job links,
capability fingerprints, sample source hashes, quota reservation asset links and
immutability/binding triggers. The migration cycle upgrades from `0003`, downgrades
cleanly on a database with no capture rows, and upgrades again. Downgrade refuses once
capture provenance exists; use forward repair and never delete captures to bypass it.
`0005` adds a deferred verification-publication fence without changing existing jobs.
Its guard rechecks the lease at commit, so expiry after finalization still rolls back the
asset/thumbnail/quota transaction. The upgrade is tested with completed and queued prior
release jobs; downgrade to `0004` removes only the added guard. Later phases may advance
the workspace head; do not downgrade an application database to run this phase's tests.

```sh
export DATABASE_URL='the private local PostgreSQL URL printed by scripts/local_postgres.py'
export TEST_DATABASE_URL='the disposable local PostgreSQL test URL'
make migrate
```

Phase 03 integration tests create a separate schema per test. They do not use SQLite and
do not touch the application schema.

## Storage and quota

The API and worker must point at the same owner-private storage root. Local capabilities
are encrypted with a process secret derived from `HMAC_SECRET`; leave it unset only for a
disposable single-process session, because a restart invalidates outstanding upload and
download tokens. The root must be mode `0700` and must not contain symlinks.

```sh
export STORAGE_BACKEND=local_fs
export STORAGE_ROOT="$PWD/.local/objects"
mkdir -p "$STORAGE_ROOT" && chmod 700 "$STORAGE_ROOT"
```

Uploads fail closed when storage quota is not configured. For local acceptance only,
insert one global and one per-user `storage_bytes` balance. Replace `USER_UUID` with the
internal ID from the local database; this is synthetic setup, not a production quota
policy.

```sql
INSERT INTO quota_balances (id, scope, resource, window_kind, window_start, limit_amount)
SELECT gen_random_uuid(), 'global', 'storage_bytes', 'lifetime', DATE '1970-01-01', 1000000000
WHERE NOT EXISTS (
  SELECT 1 FROM quota_balances WHERE scope='global' AND resource='storage_bytes'
);
INSERT INTO quota_balances (id, scope, user_id, resource, window_kind, window_start, limit_amount)
VALUES (gen_random_uuid(), 'user', 'USER_UUID', 'storage_bytes', 'lifetime', DATE '1970-01-01', 50000000);
```

The defaults are provisional safety limits, not measured capacity:

| Setting                    | Default   |
| -------------------------- | --------- |
| Max upload bytes           | 20 MiB    |
| Max image pixels           | 50,000,000 |
| Max image dimension        | 12,000 px |
| Files per project          | 200       |
| Pending-upload TTL         | 86,400 s  |
| Upload capability TTL      | 900 s     |
| Download capability TTL    | 600 s     |
| Derivative maximum         | 4 MiB / 1,000 px |
| Verify job attempts        | 3         |
| Worker lease               | 60 s      |
| Worker heartbeat           | 20 s      |

## API, Auth emulator and frontend

Follow the Phase 01 emulator setup. Start the API with storage enabled and the frontend
origin allowed. Do not use a hosted Firebase project for local acceptance.

```sh
export APP_ENV=local AUTH_BACKEND=firebase STORAGE_BACKEND=local_fs
export FIREBASE_PROJECT_ID=demo-penforge-identity
export FIREBASE_ALLOW_EMULATOR=true
export FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099
export CORS_ALLOWED_ORIGINS='["http://127.0.0.1:3000","http://localhost:3000"]'
export STORAGE_ROOT="$PWD/.local/objects"
make api
```

In a separate shell, run the same Firebase public emulator variables as Phase 01 and start
Next. On `/create`, choose or draw a sample, then use the explicit **Connect capture
storage** action, select a cloud project that belongs to the current identity, and choose
**Save raw drawing** or **Upload selected original**. Nothing is uploaded before that
action. The selected original image bytes are kept in IndexedDB separately from the
resized JPEG preview; a failed upload, verification error or retake retains them.

The panel shows real byte progress during upload and real `hash/decode/derivative/publish`
units during verification, with no invented ETA. A terminal image failure shows **Retake
needed** and does not register a sample. A transport interruption keeps the operation and
retries with the same resource keys.

## Worker

The worker is a separate process with the same database and storage root. It supports
`asset_verify` v1 only in this phase.

```sh
export APP_ENV=local STORAGE_BACKEND=local_fs
export STORAGE_ROOT="$PWD/.local/objects"
export WORKER_TEMP_ROOT="$PWD/.local/worker-temp"
export WORKER_MODE=persistent          # or scheduled for one drain pass
export WORKER_MAX_RUN_SECONDS=3600
make worker
```

`WORKER_MODE=scheduled` performs one loop and exits when no work remains, which is useful
for tests and explicit local acceptance. Persistent mode polls with bounded backoff.
Run one or more workers only against a disposable local database during development.
Claims use `FOR UPDATE SKIP LOCKED`; a second worker cannot claim the same attempt.

The worker copies an original into a private per-attempt directory, rejects unsafe input,
and asks the fenced service to stage an attempt-specific derivative before publication.
Staging removes known older-attempt derivatives under the owner/job locks and checks its
lease again after writing. A lost lease, account claim, deleting project or stale attempt
fails publication. Partial decoder files are nested inside the attempt tree and are reaped
only after that attempt is no longer active. Cleanup uses the persisted job attempt budget.
An accepted thumbnail is retained even when its response is lost.

The child rejects truncated JPEG entropy even when valid EOI bytes are present, using
the tested pinned Pillow/libjpeg v6b LP64 ABI with warnings made fatal. Unsupported ABI
builds fail closed; they need a separately tested adapter. PNG validation requires exactly
one finished IDAT zlib stream containing the expected scanlines (including Adam7), valid
filters and chunk ordering. Embedded trailing bytes or a second zlib stream are rejected.

## Reconciliation and expiry

```sh
PYTHONPATH=apps/api:packages/contracts:. .venv/bin/python -m services.worker --reconcile-report .local/phase03-reconcile.json
```

The report is always a dry run: it lists orphan storage keys and missing objects, and
deletes nothing. `reap_expired` handles expired leases; `reap_abandoned` expires pending
uploads and releases their reservations once. Neither is Phase 07's account-deletion saga.

## Acceptance commands

```sh
make lint
make contracts-check
make test-api
# requires a disposable real PostgreSQL TEST_DATABASE_URL
make test-integration
TEST_DATABASE_URL=... PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 \
  .venv/bin/python -m pytest -m emulator -q
npm run typecheck
npm run lint
npm test
npm run build
PYTHONPATH=. .venv/bin/python scripts/measure_capture_decoder.py
# Targeted continuation regressions:
.venv/bin/python -m pytest tests/backend/test_capture_decoder.py tests/backend/test_storage.py -q
TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/backend/test_uploads_database.py tests/backend/test_worker_runtime.py -q
```

On macOS, running inside an outer sandbox may prevent `sandbox-exec` from applying the
child's network-deny profile. The verifier fails closed with `isolation_unavailable`;
execute the authorized local test run where macOS permits installing that profile. Do
not disable isolation to make a test pass. The actual SIGKILL test kills a disposable
worker after its derivative write, expires the lease, and verifies retry removes stale
bytes and settles the user/global quota to the two remaining objects exactly.

For browser acceptance verify at 1440×900, 768×1000, 390×844, 320×700 and a short
1280×600 viewport. Check the opt-in action sends nothing before it is clicked, a successful
original upload becomes ready, a rejected/polyglot image enters retake, and the populated
local drawing draft remains unchanged. Inspect keyboard focus and ordinary/OS-gentle
motion without introducing a global motion toggle.

## Rollback and gates

Stop issuing new uploads first; allow in-flight verification and cleanup to settle. Keep
the API's opt-in capture panel disabled or point storage back to `disabled` if necessary.
Do not downgrade `0004` after captures exist, and do not delete originals or jobs as a
rollback shortcut. Existing local drafts, imported fonts, Writing Desk exports and cloud
project metadata remain available.

Local filesystem capabilities are not signed cloud-provider URLs. Hosted object storage,
service identity, Linux/container isolation and cgroup limits, hosted Firebase/App Check,
shared rate limiting, dependency/provider review, Docker/hosted CI, public deployment and
Phase 07 deletion remain gates. Extraction, training, font generation and ML quality are
later phases. See the [Phase 03 evidence](evidence.md) and
[ADR 0007](../../decisions/0007-phase-03-private-captures-and-queue.md).
