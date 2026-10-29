# PenForge private beta — deployment

One container runs the FastAPI service **and** the durable scheduled worker; the
Next.js frontend is a separate deployment; PostgreSQL and private object storage are
managed services. Every component is on a free tier that needs no payment method.

| Piece | Where | Free-tier facts we rely on |
|---|---|---|
| Frontend (Next.js) | Vercel Hobby | No card for Hobby; production HTTPS domain |
| API + worker | One Docker service (`beta.Dockerfile`) | Free host with ≥1 GB RAM and no card; the image runs both processes |
| PostgreSQL | Managed, external | Connections use `sslmode=require`; idle compute may scale to zero |
| Object storage | Private S3-compatible bucket | Presigned PUT/GET; nothing private on the container disk |
| Identity | Firebase Spark (`penforge-handwriting`) | Email/password enabled through `firebase deploy --only auth`; no billing account |

The measured full worker pipeline peaks at **398 MiB** sampled RSS
([benchmark](../../../docs/backend-ml-infra/phase-11-deployment-operations/benchmarks.md)),
so a 512 MiB instance is not enough. The beta needs a ≥1 GB container.

Why one container instead of a separate worker service: every no-card free tier we
verified either has no free background-worker plan (Render) or charges per service
(Northflank Sandbox allowance). Running both processes in one service costs one slot
and keeps the worker able to see the same image and temp filesystem.

## Environment

`cp .env.beta.example .env.beta`, then fill in the values. Two of them are secrets:

```sh
openssl rand -hex 32   # HMAC_SECRET
openssl rand -hex 32   # PRIVACY_HMAC_SECRET
```

`APP_ENV=production` makes the API refuse `local_fs` storage, the `fake` auth backend
and the in-memory rate limiter at startup, so a misconfigured deploy fails loudly
instead of serving with local adapters.

## Build

```sh
docker build -f infrastructure/deployment/beta/beta.Dockerfile -t penforge/beta .
```

The image installs `uv.lock` with the `worker` dependency group, runs as uid `65532`,
and starts `infrastructure/deployment/beta/start.py`, which supervises:

* `uvicorn app.main:create_app` on `$PORT` (8080 by default), and
* `python -m services.worker` in scheduled passes.

Until the 21st of October 2026 this machine had no Docker daemon, so the image itself
must be validated on the host that builds it (or on a machine with Docker) before the
beta is called working.

### Worker cadence and the free database

`start.py` runs a full 300 s worker pass while the API has seen traffic in the last
`WORKER_WAKE_GRACE_SECONDS` (30 s). When the site is idle it rests and only wakes for a
30 s safety pass every `WORKER_IDLE_POLL_SECONDS` (600 s). The API touches
`$WORKER_WAKE_FILE` on each request, so a new job starts within seconds of a user
action, and the managed database is not kept awake (and inside its free compute-hours)
by polling.

## First deploy

1. Create the database and the private bucket, then put their values in `.env.beta`.
2. Create the service from this repository's Dockerfile, attach `.env.beta` values as
   environment variables/secrets, and expose the HTTP port.
3. Run migrations once with `BETA_MIGRATE=upgrade` in the environment (or
   `alembic -c infrastructure/migrations/alembic.ini upgrade head` with the same
   `DATABASE_URL`), then remove that variable so restarts do not re-run them.
4. Provision the quota tier, which every upload and export needs:

   ```sh
   python scripts/configure_quotas.py infrastructure/deployment/quota-tiers.example.json          # dry run
   python scripts/configure_quotas.py infrastructure/deployment/quota-tiers.example.json --apply
   ```

5. Set `CORS_ALLOWED_ORIGINS` to the exact frontend origin and redeploy the API.
6. Check health: `GET /api/v1/health` returns 200 and
   `GET /api/v1/contracts` returns the pinned contract version.

## Frontend (Vercel)

```sh
vercel login                                   # owner action, browser
vercel deploy --prod --build-env NEXT_PUBLIC_PENFORGE_AUTH_BACKEND=firebase \
  --build-env NEXT_PUBLIC_PENFORGE_ENV=production \
  --build-env NEXT_PUBLIC_PENFORGE_API_ORIGIN=https://<api-host> \
  --build-env NEXT_PUBLIC_FIREBASE_API_KEY=... \
  --build-env NEXT_PUBLIC_FIREBASE_PROJECT_ID=penforge-handwriting \
  --build-env NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=penforge-handwriting.firebaseapp.com \
  --build-env NEXT_PUBLIC_FIREBASE_APP_ID=...
```

`.vercelignore` keeps `public/fonts/local-demo` (owner-only) and the Python workspace
out of the upload; `next.config.ts` refuses a production build if that folder is
present.

## Smoke checks after deploy

1. `POST /api/v1/identity/claim` with a Firebase ID token returns 200.
2. Create a project, upload a synthetic image, and confirm the presigned PUT works.
3. Queue a job and confirm `start.py` logs a worker pass that claims it.
4. Download an export and confirm the earlier exports are unchanged.

## Rollback

* Frontend: `vercel rollback <deployment-url>` (or redeploy the previous commit).
* API/worker: redeploy the previous image tag; the container is stateless.
* Database: migrations are additive; restore the provider's snapshot for a bad data
  change. Never point the beta at a database another environment writes to.
* Storage: published objects are immutable; delete only objects created by the failed
  release after checking the export lineage.

## Known limits (do not claim otherwise)

* Ordinary-photo processing is assisted: the user selects and labels regions. No
  automatic word/sentence recognition or cursive segmentation is qualified.
* Missing-character warnings stay visible; a partial font is not full coverage.
* Hosted timings must be measured on the deployed host. Local numbers (≈0.55 s
  analysis, ≈1.73 s three formats) do not transfer.
