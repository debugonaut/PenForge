# Phase 00 acceptance evidence

Date: 2026-10-19. Status: **implemented and locally verified within Phase 00**.
The user authorized no later implementation phase. Docker image/Compose execution and
hosted Actions remain unexecuted host/runner checks; no deployment is claimed.

## Implemented acceptance

| Requirement                                    | Working implementation and evidence                                                                                                                                                                                                                                                                                                                             |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Independently runnable API beside root Next.js | `app.main:create_app`; real HTTP 200 liveness with safe request ID; `/api/v1/contracts` returns registry metadata. Only those two feature paths in OpenAPI. [Commands](development.md)                                                                                                                                                                          |
| Safe foundation                                | Env-only validated config, fake/local adapter rejection outside local/test, bounded fixed/chunked bodies, UUID request IDs, typed errors with safe schema field pointers, explicit CORS, content-free logs. Nested tokens/URLs/content/malformed IDs and sanitized unknown errors tested                                                                        |
| Restricted readiness                           | Separate loopback app, secret required, DB/head check with timeout. Tests verify 404/503; native migrated DB returned authenticated 200 `{"status":"ok"}`                                                                                                                                                                                                       |
| Real PostgreSQL/Alembic                        | All 21 baseline tables; migration up → down → up on fresh isolated PostgreSQL schema; SQLAlchemy metadata diff empty. Database acceptance (`../../../artifacts/phase-00/database-acceptance.md`; local record), final output (`../../../artifacts/phase-00/database-tests-final.txt`; local record)                                                                                           |
| Tested lineage/invariants                      | Same-glyph approved active revision; generated revision begins pending with explicit owner approval evidence; immutable geometry/snapshots/final exports/verified asset content; checksum/size/scalar checks; terminal jobs; deferred owner references; append-only role. Tests include cascade cycles and index applicability                                  |
| Strict independently versioned contracts       | Outline curves/holes/descenders/space/non-BMP, strokes/legacy metadata, templates, calibration, ML results/child hashes, jobs and model entries. Wrong versions/fields/limits rejected. Hash source excludes only root self-hash; unportable JS/Python input and contradictory child references fail                                                            |
| Frontend boundary                              | Generated wire types and explicit mappings into distinct cloud models; actual counts/nullable ETA/cancellation/review preserved. Prototype transforms fail closed as canonical geometry edits; missing legacy timing/pressure/dimensions stay null. Existing demo services and UI remain unchanged                                                              |
| Local adapters                                 | Fixed injected fake principal; private dirfd/no-follow filesystem, atomic immutable publication, replay/conflicting concurrent writes, checksum/size, method/expiry/tamper-bound local capabilities, traversal/symlink/permissions rejection and idempotent deletion tested                                                                                     |
| Generation/compatibility/CI                    | Pinned openapi-typescript 7.10.1, deterministic OpenAPI/JSON Schemas/generated TS, independent compilation and retained v1 compatibility tests. Make targets, import boundaries and Actions PostgreSQL/build jobs authored. [Contracts README](../../../packages/contracts/README.md)                                                                           |
| Reproducibility/isolation                      | Python 3.12.15, uv 0.13.1/uv.lock, retained npm lock. API-only dependency install into clean env succeeds without worker/research/native-PostgreSQL tools. Separate image recipes; native PostgreSQL start/migrate/readiness tested. Toolchain (`../../../artifacts/phase-00/toolchain.json`; local record), API inventory (`../../../artifacts/phase-00/api-dependencies.txt`; local record) |
| Research from day one                          | Governed manifests/license/consent, global writer-disjoint/duplicate/ancestor checks, six independent script/style partitions, benchmark/experiment records and Devanagari starter corpus. Audit (`../../../artifacts/phase-00/research-audit.json`; local record), [research lifecycle](../../../research/evaluation/PROTOCOL.md)                                             |

## Executed verification

- `make lint`: Ruff check/format, mypy across API/contracts/research/migration scripts,
  API dependency/service boundaries, local documentation links and preserved source hashes.
- `make contracts-check`: deterministic Python artifacts, retained-v1 compatibility,
  generated TS drift, independent TS compilation, research schemas and offline metadata audit.
- Python non-DB suite: **112 passed** (54 contract, 32 API/storage/config, 26 research).
- Real PostgreSQL suite: **49 passed** (final standalone run 0.65s); no SQLite substitute.
  Tests isolate/clean their own schema. Initial migration has no previous backend release
  to upgrade; its metadata and frozen v1 contract baseline establish future compatibility.
- `npm test`: **38 passed** (31 existing plus seven mapping/hash/client checks).
- `npm run typecheck`, `npm run lint` and `npm run build`: passed.
- Combined `make check` output is saved in final-check.txt (`../../../artifacts/phase-00/final-check.txt`; local record).
  Test durations are local engineering measurements, not throughput/ETA or model evidence.
- Native optional PostgreSQL command starts a real private socket-only PostgreSQL 16.2
  cluster. `make migrate` applies `0001_initial`; authenticated readiness returns 200.
- Both preserved specification hashes match [provenance](../../specifications/README.md).
  Frontend components/CSS/illustrations/local service/storage implementation were not edited.

Fresh browser review on production localhost:3001 verified CSS widths 1440, 768, 390 and
320 plus 1280×720 short viewport. Existing browser zoom was accounted for; recorded
`innerWidth`/`scrollWidth` match in layout evidence (`../../../artifacts/phase-00/browser-layout.json`; local record).
Large attached hero characters, title/CTA clearance and hand/nib composition remain.
Pause/Resume/Replay work; navigating to the desk suspends the offscreen film. Letter/card/
note changes retain existing words, and original note format was restored. Keyboard Tab
focus was inspected. Re-entry returns visible content. Console collection returned no errors.

Full motion was reviewed. Existing regression tests cover OS gentle selection; the actual
OS preference and hidden-tab timing were not freshly manipulated. This phase changes no
motion source. Imported-font tests pass; no fresh manual font import was performed.
The downloaded stationery SVG was independently parsed: valid XML, 780×680, embedded
font and OFL, retained body, 106,989 bytes. PNG export remains covered by existing tests;
new PNG bytes were not separately inspected.

Screenshots: desktop hero (`../../../artifacts/phase-00/hero-1440.png`; local record),
tablet (`../../../artifacts/phase-00/hero-768.png`; local record), mobile (`../../../artifacts/phase-00/hero-390.png`; local record),
narrow (`../../../artifacts/phase-00/hero-320.png`; local record), short viewport (`../../../artifacts/phase-00/hero-1280.png`; local record),
letter (`../../../artifacts/phase-00/desk-letter.png`; local record),
note/export (`../../../artifacts/phase-00/desk-note-export.png`; local record).

## Findings, limitations and unresolved gates

The offline research audit validates **15 metadata records** with **0 approved datasets,
0 measured benchmarks and 0 completed experiments**. The 18 fake writer identities are
metadata fixtures, not a handwriting dataset. Caveat font/OFL provenance is verified;
IAM/DHCD remain blocked research leads. No ML performance, personal-writer fidelity,
shaping quality, model recipe, threshold, memory/time requirement, ETA or cost is invented.

Independent review identified and fixed malformed DB URL handling/readiness sanitization,
missing migration lint coverage, coarse validation pointers, sparse/accessor/non-JSON hash
input and contradictory child references. Regression checks cover these cases.

Docker and PostgreSQL executables were absent initially. The optional pinned native tool
resolved real local PostgreSQL acceptance. Docker/Compose image builds and hosted CI are
**not executed** because this host has no Docker and the workspace no Git remote/runner.
CI uses PostgreSQL 16.6; native evidence uses 16.2. A separate clean API-only environment
verified dependency isolation but does not substitute for image execution.

The test client emits one upstream deprecation warning for httpx. The captured
npm audit (`../../../artifacts/phase-00/npm-audit.json`; local record) reports **seven dependency findings
(six high, one moderate)** in the existing Next/PostCSS and ESLint/glob dependency chains;
reported remedies include major version changes. No forced framework upgrade was mixed
into this phase. Review these before public release. No security/production readiness
claim is made by green functional tests.

Provider selection/free-tier viability, Infra Q7/undefined Q8–Q10, production secret/service
identity, Firebase compatibility, dataset rights/consent/writers, model selection and all
scientific gates remain pending. Template print geometry, extraction, queue execution,
actual canonical renderer/compiler fidelity and usable TTF/OTF/WOFF2 are later phases.
Current UI extraction/jobs/style/generated-font previews remain demos. Font import and
stationery output remain real. No local drafts or handwriting were uploaded/migrated.

## Decisions, rollback and next phase

[ADR 0001](../../decisions/0001-phase-00-boundaries.md) records source reconciliations;
[0002](../../decisions/0002-postgresql-invariants.md) ratifies schema/guards;
[0003](../../decisions/0003-canonical-contract-boundary.md) freezes mapping/hash policy;
[0004](../../decisions/0004-research-governance.md) records research gates.

Foundation files are additive. Removing them leaves existing browser drafts/services
intact. Alembic downgrade destroys backend data and is for disposable local databases;
production requires verified backup and explicit operator authorization. Future migrations
use expand/migrate/contract. Models/results cannot rewrite approved revisions/exports.

Recommend **Phase 01 — Identity** next, after a new explicit request. It adds real Firebase
token verification and identity/account claiming; it must not automatically migrate local
drafts. No Phase 01 work, training or cloud provisioning was started.
