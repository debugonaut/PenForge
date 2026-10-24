# Corrected operational benchmarks — 2026-10-21 (Asia/Kolkata)

Local Apple silicon/macOS, PostgreSQL 16.2, psycopg3, migration `0012_devanagari`. No hosted measurement is claimed.

Pinned runs: PostgreSQL (`../../../artifacts/phase10-11-repairs/pg-local-20261021.json`; local record) · worker (`../../../artifacts/phase10-11-repairs/worker-local-20261021.json`; local record). The JSON stores UTC timestamps, so the UTC calendar day can precede the local date.

## PostgreSQL method

Each invocation creates a unique schema, migrates to head, seeds 60 synthetic jobs / 20 owners, measures, and finally drops only its own schema. Native PostgreSQL URLs normalize to psycopg3. Use a disposable database: the operator needs schema/migration permissions. Two runs against the same database also passed; first (`../../../artifacts/phase10-11-repairs/pg-rerun-0.json`; local record), second (`../../../artifacts/phase10-11-repairs/pg-rerun-1.json`; local record).

Claims use `repository.candidates` and a lease update in one transaction; they stop before the complete worker service path. Successful claims and empty polls are separated. Heartbeats check holder, attempt, live lease and state, and extend expiry. Quotas now call **the real `repository.reserve`**, verify both scopes and count reservation records. The benchmark no longer substitutes a simplified balance increment.

| Measurement (p50 / p95 ms) | 1 client | 5 | 20 |
|---|---|---|---|
| Pooled query | 0.177 / 0.264 | 0.544 / 0.611 | 2.138 / 2.382 |
| Successful claim | 3.632 / 26.775 | 17.666 / 33.637 | 91.595 / 130.954 |
| Successful / attempted claims | 10 / 10 | 50 / 50 | 60 / 200 |

Cold connect: **1.663 / 2.138 ms**. Fenced heartbeat: **1.161 / 1.555 ms**, 200 samples. The 20-client claim run has 140 empty polls (p50/p95 49.173 / 67.427 ms); its aggregate rate is not a steady-state capacity claim.

At 20 concurrent 60-second reservations, 10 were rejected and each scope held exactly **600** reserved seconds, with **20** reservation records. At 5 clients both scopes held **300** with no rejection.

Logical dump / restore / first query: **0.048 / 0.109 / 0.013 s**, **60 jobs** restored. This is a small synthetic database drill. No object bytes, hosted failure or production RPO/RTO was measured.

```sh
.venv/bin/python scripts/phase11_pg_benchmark.py --jobs 60 --owners 20 --samples 40
# After provider selection, against a disposable benchmark database:
.venv/bin/python scripts/phase11_pg_benchmark.py --dsn "$BENCHMARK_DATABASE_URL" --provider selected --region selected --skip-restore
```

## Worker method and results

Three synthetic structured-template verifications and isolated extractions, followed by three compile-plus-five-validator runs per font format. Font fixture: 95 varied authored glyphs with **944 nonzero kerning pairs**. The report pins tool versions, input/font hashes and raw timings. Recognition and handwriting fidelity are not measured.

| Format | Compile p50 / p95 ms | Validate p50 / p95 ms | Total p50 / p95 ms |
|---|---|---|---|
| ttf | 37.927 / 53.132 | 311.298 / 341.301 | 364.43 / 377.243 |
| otf | 42.848 / 63.476 | 303.537 / 311.854 | 346.385 / 375.33 |
| woff2 | 52.173 / 69.94 | 320.49 / 356.839 | 390.43 / 409.012 |

Verification p50/p95: **101.417 / 103.595 ms**. Extraction: **617.204 / 619.166 ms**, 49 candidates per run. Child CPU total: **5.846 s**; startup plus all repetitions: **6.039 s**.

Sampled simultaneous process-tree RSS peaked at **398.3 MiB**, under the **768 MiB** supervisor limit, sampled every 20 ms. This can miss between-sample peaks; it is not a cgroup high-water measurement. CI additionally runs the packaged parsers/font pipeline with a Docker memory cap. Docker was unavailable locally, so that gate is not executed here.

Validation failure, missing required layer/format, capture failure, child error or resource-limit breach returns a nonzero exit and preserves a failed report. The earlier Phase 11 compile-only timing and parent-only RSS claims are superseded. Old JSON is retained as historical evidence, not silently renamed into this run.

```sh
PYTHONPATH=apps/api:packages/contracts:packages/font-engine:. .venv/bin/python scripts/phase11_worker_benchmark.py
```

Hosted database/pooler cold wake, network/egress, scheduled worker startup/reliability, SSE limits, container image measurements and complete database/object/journal disaster recovery remain unexecuted.
