# Phase 08 — local development and rollback

Read [evidence](evidence.md), [plan](plan.md), the project handoff and ADRs 0018–0022.
This phase adds no dependency or external model. Preserve the existing package lock and
uv.lock. API remains apps/api; Next remains at root src/app. Local demo remains default.

## Migrate and run

Use the existing Phase 04–07 local storage, identity-emulator and worker configuration.
Provide a disposable PostgreSQL database for tests; never a production database.

```sh
make setup
make migrate
make api
# Separate terminals with the same local configuration:
make worker
npm run dev
```

Head is 0011_personalization (27 tables), extending samples, glyph_revisions and
style_profiles and guarding model history. It preserves Phase 07 journals and consent.
Normal capture must not route to the synthetic freeform experiment. No registry artifact
is registered automatically. Application/worker deployment roles must use the existing
grant helpers; local database-owner credentials are not production least privilege.

Connect account storage explicitly in the capture workspace. Open private review,
approve real source revisions, then choose “Measure approved shapes”. An earlier approved
revision from a distinct authentic source can be kept as a pending alternate, separately
approved, paused or re-enabled. Private export variation uses 0/50/100 deterministic
sets. “Compare authentic alternates” appears only for a ready validated calt font and
loads its actual bytes. Missing glyphs use system fallback. Local demo Caveat previews
remain separate.

## Verification

```sh
make contracts
make contracts-check
make lint
npm run typecheck
npm run lint
npm test
npm run build
npm start
# TEST_DATABASE_URL must identify disposable real PostgreSQL:
PYTHONPATH=apps/api:packages/contracts:packages/font-engine:. .venv/bin/python -m pytest -m 'not emulator' -q
# With the existing local Auth emulator running under a demo- project:
PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 make test-identity-emulator
```

The full suite runs registry security cases in an actual network-denied child context
and font validators with the existing isolated runner. Security failures must fail closed;
do not remove isolation to make a test pass. See exact commands/results in artifacts.

scripts/measure_personalization.py measures authored synthetic controls, saves canonical
style bytes and six isolated font reports under artifacts/phase08/measurements and refuses
to overwrite that run. Preserve it; choose a new output directory in a separately versioned
script/config before repeating. These are engineering resources, not an ML benchmark.

The dedicated scripts/phase08_local_acceptance.py supports initialize, seed and worker
modes for the recorded local schema only. It is an explicit synthetic fixture operator,
not production provisioning: hardcoded local socket, port, schema and test-only secret.
Start Auth on 127.0.0.1:9099 with project demo-penforge-identity. Set
FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099 for API/script processes. Its ASGI factory is
scripts.phase08_local_acceptance:app. The browser run used API8008 / Next3080 with
NEXT_PUBLIC_PENFORGE_AUTH_BACKEND=firebase, NEXT_PUBLIC_PENFORGE_ENV=local,
NEXT_PUBLIC_PENFORGE_API_ORIGIN=http://127.0.0.1:8008 and matching demo Firebase config.
Create an emulator guest through /account before seed. The seed is for an empty dedicated
schema; do not run it on user data. It saves opaque fixture references in artifacts.
The existing shared local PostgreSQL service is not reset by this workflow.

## Evaluation and registry boundaries

research/evaluation/run.py takes reviewed local observations and a new output directory:

```sh
PYTHONPATH=. .venv/bin/python -m research.evaluation.run /absolute/reviewed-observations.json /absolute/new-report-directory
PYTHONPATH=. .venv/bin/python -m research.validate
```

It uses the existing DatasetManifest/WriterRegistry and BenchmarkRecord. Real runs need
approved usage/consent, writer-disjoint splits, no duplicate/ancestor leakage, fixed
partitions, reproducibility metadata and real observations. Empty/planned stays unmeasured.
No supplied dataset is currently admitted for a real handwriting run. Do not label
synthetic metrics real handwriting. The CLI never executes a recorded command.

The registry verifies controlled bytes and compatibility. ONNX/safetensors are not an
inference runtime; full graph/operator/resource qualification is still required before a
future runtime. No public register/promote endpoint exists. Revoked rows cannot be
reactivated in place. A new independently reviewed version is required.

## Rollback and privacy

Set realism=0 for future fonts or pause an alternate through the explicit owner control.
Existing approved snapshots/exports stay frozen. Stop routing future work to a model and
revoke its registry row; every cached lookup rechecks live state. Never rewrite old
approved revisions or model contents to simulate rollback. New algorithms create new
profile identities. Personal style measurement needs no training consent.

Migration downgrade refuses when Phase 08 source profiles, alternate lineage or prompt
context exist; it does not discard that history. Up/down/up is verified on an empty
schema, and a 0010 privacy-seeded upgrade verifies unchanged journal bytes and resumable
erasure. Once Phase 08 data exists, prefer application rollback with the additive schema
retained. Any database restore must keep and reconcile the independent Phase 07 erasure
journal and keys before serving or accepting work. Use the established Phase 07 project/
account erasure saga for user data; do not bypass immutability triggers or delete journal
evidence. Hosted backup expiry remains unverified.

No Phase 09/10 work, model training, download/license acceptance, paid compute, provider
provisioning or deployment is authorized by these commands.
