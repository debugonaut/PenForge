# Phase 00 — Foundations and integration contracts

Status: implemented locally on 2026-10-19; Docker image builds and hosted CI await a compatible runner. Source: Part B S0, §§5–7, 10; Part A IC-01–05, §§15–17.  
Entry: frontend complete; user explicitly authorized Phase 00 only. Local acceptance completed; recommended next phase is 01 with separate authorization. See [actual evidence](evidence.md) and [development commands](development.md).

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

A reproducible, independently runnable FastAPI foundation beside the existing root Next.js app, a PostgreSQL baseline migration, strict versioned contracts and a generated TypeScript client. No handwriting is sent to a cloud service in this phase.

## Work packages

1. **Audit and reconcile.** Recheck `src/lib/contracts.ts`, mock services, DrawingCanvas, GlyphShape, capture/upload flows, review/edit panels and Writing Desk font loading. Record current coordinate scales, transformations, statuses, storage envelopes and service dependencies. Convert the index's reconciliation rows into accepted/proposed ADRs with source sections and affected files. Resolve wire casing at a single boundary; do not rename the existing UI types throughout the app.
2. **Additive layout and dependencies.** Add `apps/api`, `services/worker`, `packages/contracts`, `packages/font-engine`, `packages/ml-core`, `infrastructure/migrations`, `infrastructure/docker`, and research manifest/evaluation areas. Preserve the root frontend. Use the source's Python 3.12 baseline in reproducible environments; record any supported local deviation. Isolate API, worker and research dependencies/images. No research frameworks in API dependencies.
3. **Application foundation.** App factory; env-only validated configuration; `/api/v1/health` with liveness only; internal readiness that checks DB/migration head without public diagnostics; request IDs with bounded safe incoming values; typed error envelope and validation pointers; CORS allowlist; JSON logging with a strict field policy and token/URL/content redaction. Never log raw validation input or arbitrary exception strings.
4. **Database baseline.** Translate Part B §7.3 into Alembic `0001_initial` and corresponding models, reviewing cyclic foreign keys, deletion order, project ownership and array codepoints. Include identity, assets/samples, glyph revisions/snapshots/style, model registry, jobs/events, quota/idempotency/settings, exports and consent/deletion/audit structures. If future result/candidate additions remain unsettled, add them in later additive migrations rather than silently rewriting the source baseline. Test guards: approved same-glyph active revision, immutable finalized exports/snapshots/ready asset content, terminal jobs, audit append-only role. Check checksum lengths, outline size and codepoint scalar validity beyond the illustrative DDL.
5. **Canonical contract registry.** Define schema versions independently for REST, glyph outlines, strokes, templates, calibration, ML result manifests, jobs and model entries. Reject unknown fields/versions, nonfinite numbers and unsafe IDs. IC-01 permits multi-contour lines/quadratics/cubics, holes, descenders, metrics and empty space. Define a deterministic hash encoding shared across Python/TS with fixtures. IC-02 preserves stroke order, relative timestamps and optional observed pen metadata. IC-03 pins public template identity and physical geometry. IC-05 hashes the body excluding its hash field and pins children/provenance. Specify rejection/error semantics and configurable validation budgets; unmeasured budgets remain provisional safety controls, not quality thresholds.
6. **Local adapters.** Inject AuthVerifier and ObjectStorage protocols. Fake auth is local/test only and cannot start in production configuration. Local filesystem storage uses private configured roots, safe path resolution, immutable atomic writes, metadata/hash retrieval and deletion; no traversal or symlink escapes. Local upload/download capability semantics mirror future cloud adapters without pretending to select a provider.
7. **Contracts and CI.** Export deterministic OpenAPI and job JSON Schemas; generate TS types/client with an agreed pinned generator. Compile it independently and against mapping fixtures. Add contract drift/breaking-change checks, import boundary checks, formatting/lint/type/unit jobs and PostgreSQL integration/migration jobs. Docker Compose for local PostgreSQL and separate API/worker image recipes; provider deploy scripts wait for phase 11.
8. **Research foundations from day one.** Define dataset manifests, license/provenance fields and writer-disjoint split validation. Add the Devanagari research checklist and shaping corpus schema alongside Latin. Establish benchmark record schemas with empty/unmeasured results, not fabricated runs.

## Planned contracts and frontend touchpoints

`/api/v1/health`, the common error model and schema registry are the runnable public surface. Future endpoint contracts may be documented but must not masquerade as implemented endpoints. Preserve ProjectService/HandwritingService/FontService interfaces until adapters are proven. Existing x/y-only drawings are explicitly legacy; missing timing/pressure cannot be backfilled as observed data.

## Verification and acceptance

- A clean checkout can start the API and PostgreSQL with documented commands; `make test` has explicit frontend/API/integration targets.
- Migration upgrade → downgrade → upgrade works on an empty DB; schema/model drift is empty. PostgreSQL-specific guards are tested on PostgreSQL, never substituted by SQLite.
- Health returns only liveness. Readiness is restricted and cannot leak hostnames, versions or credentials.
- Invalid body/token-like fields never appear in logs or error details. Test nested secrets, signed URL queries, raw outline/image/text fields and malformed request IDs.
- Fake auth is rejected outside local/test. Storage conformance tests cover immutable replay, conflicting writes, hash verification, traversal, missing objects and idempotent deletion.
- Contract fixtures include contour holes, cubic/quadratic paths, negative descenders, whitespace glyphs, non-BMP codepoints, optional pen metadata, wrong versions and malicious/oversized payloads.
- Generated client compiles; existing frontend typecheck/build and visual behavior remain intact. No automatic local-draft cloud migration occurs.
- CI configuration is executable; distinguish locally executed checks from jobs requiring a hosted runner. Record toolchain and test durations as local measurements only.

## Completion evidence and rollback

Deliver migration reports, generated artifacts, contract fixtures, test outputs, dependency/image inventories and ADRs. Stop before Firebase/project feature implementation. Additive files can be reverted without changing local browser drafts. Production downgrade is never automated; establish backup/expand-contract rules now.

## Open gates

Generator resolved: pinned openapi-typescript 7.10.1. Python 3.12 and real native PostgreSQL are reproducible; Docker is not available on this host, so image/Compose execution remains unverified locally. Provider, free-tier, Q7–Q10, model selection, quality gates and ETA remain pending. No public availability or accuracy claim is part of S0.
