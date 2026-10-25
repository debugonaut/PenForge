# Phase 11 evidence — portable implementation; launch incomplete

Original implementation: 2026-10-20. Audited and repaired: **2026-10-21 (Asia/Kolkata)**.
The original closeout overstated measurement coverage and feasibility. The
[audit and repair record](audit-repairs.md) is the current acceptance source.

## Current result

Provider-neutral tooling exists and its repaired local paths pass acceptance:

- **447** backend unit tests, **215** real PostgreSQL integration tests, **118** frontend
  tests; contracts, lint/type/build and source/documentation checks pass.
- Spacing plus authentic alternates and small partial fonts pass all formats and validators.
- Quota provisioning, stable privacy-key rotation and the real protected metrics scrape
  are verified against the migrated database.
- [Benchmarks](benchmarks.md) now include real reservation code, success/empty claim
  distributions, fenced heartbeats, logical restore, verify/extract, compile and validation
  timings, sampled process-tree memory, input/font hashes and failure gates.
- Docker payloads include required Unicode data. Actual capped image smoke/security gates
  are configured in CI, but Docker and hosted CI have not run here.

No provider resources, credentials, public endpoint or training run were created.
[Q7 remains proposed](../../decisions/0027-phase-11-environments-and-service-identity.md).
The [baseline](baseline.md) is historical; [providers](providers.md), [budget](budget.md)
and [runbook](runbook.md) contain the corrected current guidance.

## Subsequent CI repair — 2026-10-25

[Draft PR #1](https://github.com/debugonaut/PenForge/pull/1) repairs runner argument
quoting and gives the capped worker an init parent, preserving its decoder guard.
Clean-checkout fixes align test setup and ORM metadata with the existing migration
head, authenticate fixture requests and preserve passwords in ephemeral test URLs.
No migration, authorization rule or validation assertion was relaxed.

GitHub PostgreSQL acceptance, benchmarks, credential checks, image builds, size
budgets and networkless capped API/worker smoke have now passed on the repair branch.
Contract generation also has a regression for Linux temporary-directory ignore rules
and host formatter configuration. Print-raster checks compare every decoded pixel,
mode, dimensions and metadata while allowing lossless PNG compression differences.
Negative controls reject a single changed pixel and metadata/layout changes; compact
hash diagnostics avoid pytest's unbounded binary-difference rendering. Foundation
checks stop at the first failure, report stalled stacks and have a 30-minute job cap.
The local public suites pass with the patched
dependencies. The PR records the current head's full workflow results; this is not an
all-green or production-readiness claim. HIGH/CRITICAL image scans remain blocking.
Some Debian and pip-vendored findings still have no patched stable release; no blanket
ignore, suppressed severity or bypass was added.

Brotli is updated to 1.2.1 and the current toolchain policy records that version.
Compiler 1.0.1 and spacing policy v2 remain unchanged. Frozen manifests using Brotli
1.2.0 fail with `toolchain_mismatch`; request a fresh build with the patched toolchain.
Existing ready exports remain immutable. No private handwriting or generated personal
font is part of the PR, and no provider provisioning or deployment occurred.

## Work-package status

| Package | Actual state |
|---|---|
| 1 — environments/service identity | Q7 decision brief and local key/role boundaries exist; hosted decisions pending |
| 2 — provider verification | Dated comparison corrected; selected-region/complete topology still untested |
| 3 — PostgreSQL candidates | Real local migrated-schema benchmark/restore passes; hosted poolers/wake tests pending |
| 4 — storage | Local protocol conformance passes; selected cloud adapter missing |
| 5 — worker runtime | Local verify/extract/font measurements and image payload checks pass; containers/scheduler/SSE pending |
| 6 — resource budget | Local inputs and assumptions separated; a complete ₹0 deployment remains unproven |
| 7 — least privilege | Existing local role regression passes; hosted service identities/grants not provisioned |
| 8 — operations | Bounded metrics + real DB/HTTP acceptance + portable alert rules; live worker CPU/RSS and alert delivery/contacts pending |
| 9 — CI/CD | Portable locked jobs, blocking scans, image budgets and capped smoke configured; hosted CI and environment-specific manual promotion pending |
| 10 — recovery | Small DB logical restore measured, existing privacy reconciliation tests pass; complete hosted DB/object/journal and deployment rehearsals pending |
| 11 — release evidence | Local evidence/runbook repaired; no release/promotion readiness claim |

**The public M1 launch gate remains open.** No cloud object-storage adapter or hosted
identity/shared limiter exists. Fresh databases have no automatic quota configuration;
the operator now has a validated, idempotent provisioning path. Managed PITR is optional:
a documented off-site logical-backup solution can qualify after an actual restore and
deletion reconciliation drill. No production RPO/RTO is inferred from 60 synthetic jobs.

## Compatibility and rollback

No migration, public API schema or job payload changed during these repairs. The new
privacy key is optional for compatibility, but must be pinned before general-key rotation.
Existing journal signatures still fail closed when invalid. Compiler 1.0.1 changes new
build identities; retain compatible workers for old queued manifests or submit fresh
requests. Ready immutable exports, approved source geometry and local demo behavior remain
intact. Deployment remains separately authorized.

Prior Auth-emulator evidence is retained, not rerun as part of this repair. All earlier
browser/touch/gentle-motion and authentic-writing acceptance remains pending as listed in
[audit-repairs.md](audit-repairs.md). Numbered implementation work does not establish ML
quality, full phase acceptance or public readiness.
