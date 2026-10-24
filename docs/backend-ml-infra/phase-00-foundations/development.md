# Phase 00 local development

The root Next.js app stays where it is. The API runs independently with Python 3.12;
PostgreSQL is required for migrations and integration tests. No frontend request to the
API is enabled automatically. Read [evidence](evidence.md), [contracts](../../../packages/contracts/README.md)
and [research workflow](../../../research/README.md) for current capabilities.

## Install reproducibly

Use Node 22+ and uv 0.13.1. Both lockfiles are committed workspace artifacts. If uv is
not installed, the tested workspace-local bootstrap is:

```sh
python3 -m venv .bootstrap-venv
.bootstrap-venv/bin/python -m pip install uv==0.13.1
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.python"
.bootstrap-venv/bin/uv python install 3.12
make UV=.bootstrap-venv/bin/uv setup
```

With uv on PATH, `make setup` installs the locked Python dev group and runs `npm ci`.
The API-only group contains no research/ML frameworks. `worker` and `research` groups
are deliberately empty until later authorized implementation needs dependencies.
The optional native PostgreSQL group is never part of the API image.

## PostgreSQL and API

Docker path (Docker engine required):

```sh
make db-up
make migrate
# New terminal:
make api
```

Compose binds PostgreSQL only to `127.0.0.1:5433`, using local example credentials.
`make db-down` stops it while keeping the named data volume. The default DATABASE_URL
matches Compose. Do not reuse these credentials in deployment. Create the disposable
integration database once:

```sh
docker compose -f infrastructure/docker/compose.yaml exec postgres createdb -U penforge penforge_test
export TEST_DATABASE_URL='postgresql+psycopg://penforge:penforge@127.0.0.1:5433/penforge_test'
make test
```

No-Docker path, tested on this arm64 Mac:

```sh
.bootstrap-venv/bin/uv sync --locked --group dev --group local-postgres
.venv/bin/python scripts/local_postgres.py start
```

The command prints DATABASE_URL and TEST_DATABASE_URL for its private Unix socket.
Export those exact values, then `make migrate`, `make api` and `make test`.
It creates `.local/postgres-native` and a user-private socket directory in the OS temp
area; TCP listening is disabled and host auth rejected. Local trust applies only to
that private Unix socket. Native runtime is PostgreSQL 16.2 from the pinned tool wheel;
Compose/CI use 16.6. This is development tooling, not a deployment recommendation.
Stop with `.venv/bin/python scripts/local_postgres.py stop`. Existing clusters are not
deleted. Some sandboxes require permission to use PostgreSQL shared memory/sockets.

API is at [localhost:8000](http://localhost:8000/docs):

```sh
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/contracts
```

Only these two `/api/v1` routes exist. Contract metadata marks future schemas explicitly;
projects, auth identity persistence, uploads, processing and font export are later phases.
Fake auth accepts the configured local token through an injected protocol for tests;
there is no public `/me` or token issuance endpoint. Local filesystem capabilities are
in-process method/expiry/key/hash-bound tokens, **not** public URLs or a cloud storage API.
They cannot be used to upload browser drafts.

## Configuration and readiness

Settings read environment variables only, not `.env` automatically. `.env.example` is a
reference. CORS requires explicit origins. Local adapters fail startup outside local/test;
nonlocal foundation images default to disabled adapters. Public liveness never contacts
the DB or reveals versions/credentials. Run management readiness separately on loopback:

```sh
# Supply a unique local secret with at least 32 characters in your shell.
export READINESS_TOKEN='replace-with-a-unique-local-secret-of-at-least-32-characters'
make readiness
# In another terminal with the same secret:
curl -H "Authorization: Bearer $READINESS_TOKEN" http://127.0.0.1:8001/ready
```

This route is absent from the public app. Without the correct secret it returns a generic
404; DB/migration failure returns generic 503, and a matching `0001_initial` returns
`{"status":"ok"}`. It has a three-second dependency timeout. Do not publish the management
port; environment/service identity and ingress rules remain Phase 11 gates.

Migration credentials and runtime credentials must be separate. `grant_app_role.py` applies
least-privilege grants to an operator-created role, including audit mutation denial.
It does not create roles, choose a secrets provider or remove a table owner's inherent
rights. Runtime credentials must not own/inherit the migration schema role.

## Verification and regeneration

```sh
make lint                 # Ruff, mypy and API import/service boundaries
make contracts-check      # deterministic artifacts, retained-v1 compatibility, TS, research
make test-api             # no PostgreSQL required
make test-integration     # requires TEST_DATABASE_URL; never silently substitutes SQLite
make test-frontend
npm run typecheck
npm run lint
npm run build
# Combined acceptance:
make check
# Regenerate after intentional contract changes:
make contracts
```

The 49 integration tests create a fresh isolated schema, exercise migration up/down/up,
compare metadata and remove their schema. They require a disposable DB with schema/role
creation rights; do not point tests at production. There is no previous backend release
schema to upgrade in Phase 00. The committed v1 compatibility baseline is the starting
point for future migrations/contracts and must not be overwritten to silence regressions.

CI mirrors `make check` with a PostgreSQL service and builds separate API/worker foundation
images. It does not deploy. This workspace lacks Git metadata/remote, Docker and a hosted
runner invocation; those jobs were authored/reviewed, not executed locally. Native commands
and real PostgreSQL tests were executed. The worker image exits explicitly because queue
execution starts in Phase 03; it cannot pretend to process jobs.

## Rollback and limits

Frontend components, CSS, assets, drafts and mock services were preserved. Removing the
new foundation files does not migrate or modify browser data. `alembic downgrade base`
destroys backend data and is suitable only for a disposable DB. Do not run destructive
production migrations automatically; use backup verification and expand/migrate/contract.
No generated personal font, recognizer, model quality, hosting cost or readiness is proven.

Technical references consulted: [FastAPI errors](https://fastapi.tiangolo.com/tutorial/handling-errors/),
[FastAPI middleware](https://fastapi.tiangolo.com/tutorial/middleware/),
[SQLAlchemy psycopg](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg),
[PostgreSQL initialization](https://www.postgresql.org/docs/17/app-initdb.html),
and [pgserver source](https://github.com/orm011/pgserver).
