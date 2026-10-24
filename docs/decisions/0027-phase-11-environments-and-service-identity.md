# 0027 — Environments, service identity and promotion (Infra Q7)

Status: **proposed, awaiting owner decision.** Nothing in this record provisions an
environment or grants a credential. Date: 2026-10-20.

## What is already decided by the code

These are implemented and tested, not proposals:

- Four environment names exist: `local`, `test`, `preview`, `production`
  (`app_env`). `fake` auth and `local_fs` storage are rejected outside `local`/`test`.
- Hosted Firebase identity is currently refused outside `local`/`test` because only a
  process-local rate limiter exists. This fails closed deliberately.
- Two least-privilege database roles are scripted: `grant_app_role.py` (SELECT/INSERT/
  UPDATE/DELETE on tables, no `audit_events` mutation, no `alembic_version`, no
  `model_versions` mutation) and `grant_worker_role.py` (selected reads, narrow inserts,
  column-limited updates, no revision/snapshot/activation rights).
- Secrets are environment-only; `Settings` never reads a file and redacts `SecretStr`
  values from errors. HMAC secrets require ≥32 characters and carry a rotation version.

## What Q7 still needs the owner to decide

| Question | Recommended answer | Why |
|---|---|---|
| Where do production secrets live? | Provider secret store (Neon connection string, HMAC key, readiness token, Firebase credentials) injected as env vars at deploy | The code already reads only env vars; a secret file would be a new mechanism |
| How many environments exist before launch? | `local` + one hosted `preview`, and `production` only at promotion | A separate staging tier costs money and adds a second credential set for a solo project |
| Who promotes? | One manual action by the owner, after a green CI run and a smoke check | Matches "production promotion is manual" |
| Are API and worker separate DB roles in production? | **Yes** — `penforge_app` and `penforge_worker` | Already scripted; reusing one role would silently grant workers revision rights |
| Is the migration role separate? | **Yes** — a third credential used only by the migration job, never mounted into API or worker | `grant_*` scripts assume a non-owner runtime role |
| Secret rotation | Pin `PRIVACY_HMAC_SECRET` to the existing privacy key before rotating `HMAC_SECRET`; roll API/worker together and accept expired capabilities/cursors. Rotate DB credentials through provider-supported overlapping credentials | A stable privacy key protects tombstones and receipts. There is no general HMAC dual-key overlap mechanism |

## Consequences of the recommendation

- **Blocked until decided:** creating the hosted project, setting the first secrets, and
  running any hosted benchmark. The harness accepts a `--dsn`, but no credential exists.
- **Not blocked:** all provider-neutral work, the conformance suite, local benchmarks,
  CI, and the runbooks.
- A single hosted environment means a bad migration reaches the only public database.
  The mitigation is the expand → migrate → contract rule plus a verified restore drill,
  not a second environment.

## Alternatives considered

1. **Three environments (local/staging/production).** Safer migrations, but doubles
   provider cost and credentials for a project targeting ₹0. Rejected for the first
   release; revisit when real users exist.
2. **One environment.** Cheapest, but a bad deploy is unrecoverable without a restore
   drill. Rejected as the sole plan.
3. **Provider-managed secret vault with per-service tokens.** Best isolation, but no
   provider has been selected and it adds a dependency that the free tiers may not offer.
   Deferred until a provider is chosen.

## Exact next action this unlocks

Once the owner approves the table above and names a provider, the sequence is:

1. Create the hosted PostgreSQL project and object-storage bucket (owner action).
2. Put `DATABASE_URL`, `HMAC_SECRET`, `PRIVACY_HMAC_SECRET`, `READINESS_TOKEN` and the Firebase service
   credentials into the provider's secret store.
3. Create `penforge_app` and `penforge_worker` roles and run the two grant scripts.
4. Provision `system_settings.quota_tiers.v1` — without it every upload returns
   `503 quota_unconfigured`.
5. Run `scripts/phase11_pg_benchmark.py --dsn … --provider …` and record the JSON.

Steps 1 and 2 need the owner's approval and credentials; steps 3–5 are blocked by them.
