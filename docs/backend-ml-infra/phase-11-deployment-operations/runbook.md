# Phase 11 runbook — deploy, observe, recover, roll back

Nothing here provisions anything. These are the steps an operator follows once the owner
has chosen a provider and approved [ADR 0027](../../decisions/0027-phase-11-environments-and-service-identity.md).
All credential values are placeholders; no real secret is committed anywhere.

## Environment variables

| Variable | Placeholder | Notes |
|---|---|---|
| `APP_ENV` | `preview` / `production` | `local_fs` and `fake` auth are rejected outside `local`/`test` |
| `DATABASE_URL` | `postgresql+psycopg://penforge_app:CHANGE_ME@db.example/postgres` | App role, never the migration role |
| `HMAC_SECRET` | ≥32 random characters | Rotation uses `HMAC_KEY_VERSION` |
| `HMAC_KEY_VERSION` | `v2` | Versions identity/rate-limit hashes; it is not a verification keyring |
| `PRIVACY_HMAC_SECRET` | Existing privacy key, ≥32 characters | Stable tombstone/reference/receipt key. Back up independently of the rotating HMAC key |
| `READINESS_TOKEN` | ≥32 random characters | Also gates `/internal/metrics` |
| `AUTH_BACKEND` | `firebase` | Requires `FIREBASE_PROJECT_ID`; hosted identity is still gated (see below) |
| `STORAGE_BACKEND` | **blocked** | Only `local_fs` exists; a cloud adapter is required first |
| `CORS_ALLOWED_ORIGINS` | `https://app.example` | Origins only, no paths |

## Provisioning checklist (each step needs the owner's action)

1. Create the hosted PostgreSQL project and object-storage bucket.
2. Create **three** roles: `penforge_migrate`, `penforge_app`, `penforge_worker`.
3. Run `infrastructure/migrations/grant_app_role.py` and `grant_worker_role.py` with the
   migration role; never mount the migration credential into API or worker.
4. Seed the quota tier — **without it every upload and export returns
   `503 quota_unconfigured`**:

   Validate a reviewed copy of [the example](../../../infrastructure/deployment/quota-tiers.example.json):

   ```sh
   export PYTHONPATH=apps/api:packages/contracts:packages/font-engine:.
   .venv/bin/python scripts/configure_quotas.py infrastructure/deployment/quota-tiers.example.json
   # DATABASE_URL must explicitly name the intended operator connection:
   .venv/bin/python scripts/configure_quotas.py infrastructure/deployment/quota-tiers.example.json --apply
   ```

   The first command only validates. The example has real `free_guest`/`free_user` tiers,
   rule objects with `window_kind` and `limit_amount`, and global limits. Its numbers are
   proposals. Applying identical rules is idempotent; different limits under an existing
   version are rejected. Existing balance rows are not rewritten by this command. A quota
   policy change requires a reviewed version/balance transition; simply changing a tier
   does not retroactively raise an exhausted balance. No migration auto-seeds quotas.
5. Put the secrets in the provider's secret store, never in the repository.
6. Run the benchmark against the real DSN and commit the JSON:
   `python scripts/phase11_pg_benchmark.py --dsn "$BENCHMARK_DATABASE_URL" --provider … --skip-restore`.
   Use a disposable database and migration-capable benchmark identity, never the production
   app role. The harness creates and finally drops its own UUID schema.

## Deploy

- Expand → migrate → contract. Deploy a worker image **compatible with the new payload**
  before the API that emits it.
- Migration command: `.venv/bin/alembic -c infrastructure/migrations/alembic.ini upgrade head`
  run with the migration role only.
- Smoke check after deploy: `GET /api/v1/health` returns `{"status":"ok"}`;
  `GET /ready` (authenticated) returns 200 only when the Alembic head matches;
  `GET /internal/metrics` (bearer `READINESS_TOKEN`) reports `penforge_scrape_db_ok 1`.
- Never run a destructive downgrade in production automatically. A rollback is a
  redeploy of the previous immutable image; schema rollback requires a verified backup.

## Observe

- `/api/v1/health` is public and returns liveness only.
- `/internal/metrics` (bearer token) exposes request counters/latency, queue depth/age, expired leases, retained retries, live quota windows,
  ready asset-byte metadata, API body transfer, validation outcomes and privacy backlog.
  Font resource fields and per-job wall time are maxima over retained records, not counters.
  The compiler's retained `peak_rss_bytes` is its largest individual-process high-water
  value; the separate benchmark measures sampled simultaneous process-tree RSS.
  Transfer excludes proxy/TLS overhead and future direct cloud-object traffic; stored bytes
  are metadata, not a provider billing inventory. It is absent from the OpenAPI document.
- Logs are content-free by construction: only allowlisted events, UUID fields and
  finite numbers leave the process. No handwriting, font bytes, tokens, signed URLs or
  request paths appear anywhere.

Portable provisional rules: [alerts.yml](../../../infrastructure/observability/alerts.yml).
They cover database scrape failures, old queue entries, expired leases, privacy failures,
API 5xx rate and 60% global storage pressure. Load them into a private Prometheus-compatible
scraper with the bearer token. Gauge values can fall after retention/deletion. Scrape health
itself must be monitored. No alert destination, delivery test or operator contact is
configured until the environment owner supplies them. These rules are not deployed alerts.

## Incidents

| Symptom | Action |
|---|---|
| Jobs stuck, oldest age rising | Inspect `penforge_queue_depth`; re-run the worker; leases expire in 60 s and the queue reclaims them. No manual intervention is normally needed |
| `quota_exceeded` (429) | Expected behaviour: the cap held. Raise the tier deliberately, never by editing rows ad hoc |
| `quota_unconfigured` (503) | The tier row may be missing or invalid — validate and restore the reviewed configuration; do not improvise limits |
| Provider outage | Users keep local drafts; nothing is auto-uploaded. Reconcile after recovery |
| Suspected credential leak | Follow the rotation procedure below; rotate DB credentials and revoke exposed service credentials |
| Deletion/reconciliation failure | The HMAC erasure journal is the authority; keep the reconciliation job retrying rather than deleting data by hand |

## HMAC rotation without losing erasure history

1. Before changing any existing key, inject `PRIVACY_HMAC_SECRET` with the **current old
   `HMAC_SECRET` value** into both API and worker via the secret store. Restart and verify
   reconciliation/receipt access. Existing installations fall back to HMAC only until this
   stable key is explicitly pinned. New installations should set an independent privacy key.
2. Back up that privacy key and the immutable erasure journal in separately controlled
   storage. Do not replace them when rotating capability, cursor or rate-limit secrets.
3. Change `HMAC_SECRET` and `HMAC_KEY_VERSION` in API and worker together. Existing signed
   capabilities/cursors can expire immediately; clients must obtain fresh ones. There is
   **no dual-key grace window**. Pause admission/drain work if a rolling mismatch is possible.
4. Verify startup reconciliation and old deletion progress receipts, then new upload/download
   issuance. Never discard the stable key while any tombstone/receipt depends on it.

If the stable privacy key itself is exposed, ordinary HMAC rotation does not repair it.
Disable affected access and perform a separately designed journal/reference migration;
this release has no privacy-key migration/keyring tool. Invalid journal signatures still
fail closed. Runbook correctness does not imply hosted rotation has been rehearsed.

## Backup, restore and RPO/RTO

| Class | Owner | Schedule | Retention | Verified? |
|---|---|---|---|---|
| PostgreSQL logical dump | operator | daily | 7 days | **Locally drilled**; see the pinned [benchmark](benchmarks.md) for current measurements |
| PostgreSQL PITR | provider | depends on plan | Neon Free: 6 h | **Not executed** — no hosted instance |
| Object storage | provider | depends on plan | unknown until selected | **Not executed** |

The measured local logical-restore stages total under one second for 60 synthetic jobs; that is
**not** a production RTO. No provider backup exists until a hosted instance and its
retention policy are verified.

Restore procedure: restore into a **fresh** database, then run the restore-reconciliation
job so the erasure journal re-blocks any account deleted during the outage. A restore
must never resurrect access to deleted data or withdrawn contributions; that path is
exercised by the deletion drills in the evidence file.

Logical backups are an acceptable design if scheduled, off-site, access controlled and
successfully drilled with explicit RPO/RTO; managed PITR is optional. Object bytes and the
erasure journal require independent backup: PostgreSQL dumps contain only object metadata.
Do not restore an older journal over the live deletion authority. The synthetic database
restore benchmark does not itself rehearse object recovery, key restoration or withdrawal
reconciliation; the earlier privacy tests cover reconciliation logic separately. A complete
selected-environment recovery drill remains a release requirement.
