# Phase 02 acceptance evidence

Date: 2026-10-19. Workspace: PenForge repository root.

Phase 02 is implemented locally. The evidence below separates checks run against native
PostgreSQL and the Firebase Auth emulator from checks that require Docker or hosted
providers. Project persistence is metadata-only; no handwriting upload, glyph processing,
personal font generation, or deletion purge is claimed.

## Executed acceptance

| Area                           | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guest boundary and idempotency | Real PostgreSQL tests exercise many concurrent creates at the third-creation boundary, parallel same-key requests, exact replay after quota exhaustion, changed-body rejection, identity scope, receipt expiry, interrupted requests, and rollback-safe usage conservation.                                                                                                                                                                     |
| Owner isolation and lifecycle  | PostgreSQL tests cover foreign project IDs, owner-bound/tampered cursors, pagination, redacted conflict details, suspended/deleting accounts, guest-to-owner claim transfer, stale guest rejection, ETag preconditions, deletion capability response, and no hard delete.                                                                                                                                                                       |
| Concurrent mutation            | Two independent PostgreSQL connections save from the same revision; one wins, one receives a safe 412, and the local patch can be rebased using the latest ETag. A regression test verifies that rebasing a name-only edit does not overwrite newer style fields from the other tab.                                                                                                                                                            |
| Auth emulator                  | The Phase 01 identity emulator suite now creates three cloud projects, replays a key at the quota boundary, rejects the fourth create, transfers the guest and its history through the existing claim flow, replays the claim, then verifies destination access and stale guest rejection.                                                                                                                                                      |
| HTTP and contracts             | HTTP tests cover strict fields, wire DTO types, timezone normalization, ETag/If-Match behavior, CORS, typed deletion unavailability and OpenAPI precondition documentation. Generated OpenAPI/TypeScript contracts are checked for drift and retained-v1 compatibility.                                                                                                                                                                         |
| Browser                        | The step-by-step browser acceptance trace (`../../../artifacts/phase-02/browser-acceptance.md`; local record) records the opt-in flow, synthetic identity upgrade, same-owner two-tab conflict, local patch recovery, and a field-level merge that retained another tab's newer style value. The local demo collection remained empty and untouched. Import and duplicate are visibly disabled with reasons; cloud cards disclose font processing unavailable. |
| Responsive UI                  | The default local Workbench and cloud panel were inspected at 1440×900, 768×1000, 390×844, 320×844, and 1280×600. The layout remained scrollable without observed horizontal overflow; approved stationery, typography, palette, pen/quill characters, and motion were not changed.                                                                                                                                                             |

The browser's local test identity was linked from an emulator guest to a synthetic
`@penforge.test` account to share one identity across tabs; it is not a user credential or
hosted provider account. The native PostgreSQL test schema was disposable and isolated from
the application's default schema; the browser-only schema was dropped after stopping the
local API. No hosted account was created. No live user's data was read or changed.

## Commands and results

The final local verification commands and results are:

```sh
make contracts
make contracts-check
make check
TEST_DATABASE_URL='postgresql+psycopg://penforge@/penforge_test?host=/private/tmp/penforge-pg-501&port=55432' \
  .venv/bin/python -m pytest -m 'integration and not emulator' -q
TEST_DATABASE_URL='postgresql+psycopg://penforge@/penforge_test?host=/private/tmp/penforge-pg-501&port=55432' \
PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 \
  .venv/bin/python -m pytest -m emulator -q
```

Migration `0003_project_cursor` was applied to a disposable PostgreSQL schema before browser
acceptance. The real database migration cycle upgraded, downgraded, and upgraded back to
`0003_project_cursor`; planner checks reached `projects_owner_cursor_idx` for the stable
owner pagination ordering.

| Check                                                                                 | Result                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Contract generation                                                                   | `make contracts` passed; OpenAPI registry, generated TypeScript and research schema audit refreshed.                                                                                                                                                                                                                   |
| Contract drift, compatibility, TS contract compile, research schema audit             | `make contracts-check` passed; retained-v1 compatibility passed; 15 metadata records validated with 0 approved datasets, 0 measured benchmarks, and 0 completed experiments.                                                                                                                                           |
| Ruff, Ruff format, mypy, architectural boundaries, local Markdown links/source hashes | `make lint` passed; 64 Python files formatted, mypy passed for 44 source files, API boundaries passed, 288 local links and both preserved source hashes passed.                                                                                                                                                        |
| Frontend tests                                                                        | `npm test`: 54 passed.                                                                                                                                                                                                                                                                                                 |
| API unit/contract suite                                                               | `make test-api`: 180 passed, 86 deselected; one Starlette/httpx test-client deprecation warning.                                                                                                                                                                                                                       |
| Real PostgreSQL integration                                                           | `pytest -m 'integration and not emulator'`: 82 passed, 184 deselected; one Starlette/httpx deprecation warning. This includes concurrent guest-boundary/idempotency, concurrent edits, owner isolation, cursor, claims, lifecycle and migration/index checks.                                                          |
| Auth emulator acceptance                                                              | `pytest -m emulator`: 4 passed, 262 deselected; one Starlette/httpx deprecation warning. This used the local Auth emulator at `127.0.0.1:9099` and disposable PostgreSQL. A closeout rerun could not start: PostgreSQL socket access returned `Operation not permitted` and the Auth emulator was no longer listening. |
| TypeScript app check, ESLint, production build                                        | `npm run typecheck`, `npm run lint`, and `npm run build` passed. The final build completed all static routes; ESLint had no warnings after removing an unused type.                                                                                                                                                    |
| Docker Compose/API and hosted Firebase/App Check/CI/deployment                        | Not executed: Docker was unavailable and no hosted provider configuration or public deployment was authorized.                                                                                                                                                                                                         |

The closeout rerun failure occurred during database setup, before emulator tests were
collected; it does not replace the recorded successful emulator run. PostgreSQL and Auth
emulator services were left untouched during closeout.

## Recovery follow-up acceptance — 2026-10-19

- The editor retains its original project/ETag through list refresh. Explicit rebase preserves field-level merging.
- Create recovery is owner/operation scoped, exposes the original body/key, and supports visible restoration and exact retry after reload. Independent creates and identities cannot block each other. A claimed guest's unknown-commit evidence remains inspectable without replay under another UID. Identity changes fence token acquisition and late UI results.
- Recovery writes/removals use independent operation keys. Existing legacy singleton/aggregate bytes remain unchanged; per-operation resolution markers prevent resurrection without rewriting a shared collection.
- `npm test`: **60 passed** at follow-up verification. New tests cover actual interleaved write/write and remove/write storage operations while requests are unresolved, simultaneous creates, stale-after-refresh saves, field merging, reload/body/key recovery, identity/claim errors and legacy-byte preservation. `npm run typecheck` and `npm run lint` passed. Phase 03 preserved these regressions; the current suite is **78 passed** as recorded in [Phase 03 evidence](../phase-03-capture-uploads/evidence.md).
- Fresh browser acceptance used two actual tabs and an existing populated local drawing draft. The stale editor/list-refresh path returned 412 and recovered; interrupted-create reload retried the original body/key; two unresolved saves retained two backups, and resolving one retained the other. The local draft remained byte-identical. Browser result (`../../../artifacts/phase-02/recovery-followup-browser.json`; local record), screenshot (`../../../artifacts/phase-02/recovery-followup-two-tabs.png`; local record). No page errors were reported. This scenario was rerun with the same result against the current API during the Phase 03 closeout. Identity-switch/claim browser acceptance was not rerun; automated tests cover those paths.

## Remaining gates

The later [Phase 02 review](review.md) found three frontend recovery gaps. Those gaps were subsequently corrected under the user's explicit authorization. The correction record below supplements the historical acceptance above.

Hosted Firebase ADC/project/OAuth/certificate/revocation checks, App Check, a shared
multi-instance rate limiter, provider/dependency review, Docker image/Compose execution and
hosted CI remain unverified. The local emulator does not prove hosted authentication or
abuse protection. Cloud deletion handoff and purge belong to Phase 07; import and duplicate
remain unavailable until data and lineage can be preserved. Do not provision providers,
deploy publicly, or extend beyond the separately authorized phase. ML quality,
training recipes, thresholds, resource ceilings and ETA remain unmeasured.
