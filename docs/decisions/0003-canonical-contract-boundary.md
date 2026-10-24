# 0003 — Versioned wire, geometry, capture and hashing boundary

Status: accepted. Date: 2026-10-19. Scope: Phase 00 contracts.

## Context and decision

[v3 Part A IC-01–05 and §§10/17](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md)
fix semantics but leave field casing proposed. The current UI has camelCase prototypes,
`FontProject.isDemo: true`, x/y-only strokes, CSS rotation/scale/crop, and simulated jobs.

Use snake_case wire DTOs with explicit mappings in `src/lib/api-boundary.ts`; preserve
existing UI contracts and services. Cloud metadata has a separate view model and never
claims usable font availability. Server cancellation, actual unit progress, unknown ETA
and user review remain separate facts. Service mode defaults to demo, with no automatic
requests, draft migration or handwriting upload.

Pydantic v2 is the contract source. Commit deterministic OpenAPI, independent JSON Schemas
and generated TypeScript via pinned `openapi-typescript@7.10.1`. A small typed fetch client
uses only the implemented health/registry routes; future DTOs have no invented endpoints.
CI checks byte drift, independent TS compilation and conservative compatibility against
a frozen v1 baseline. Breaking semantics require an explicit new version and review.

Canonical outlines use baseline origin, y-up units, line/quadratic/cubic closed contours,
even-odd fill and explicit metrics. Space has metrics and no contour; contextual sequences
are distinct from single-scalar cmap mappings. Negative descenders/overshoots are allowed.
Safety defaults (8 em extents, 8,192 nodes, 100,000 total stroke points) are provisional
validation controls, configurable through validation context where supported, **not
measured fidelity/quality thresholds**. FontTools/SVG fidelity validation belongs later.

New-format stroke contracts preserve observed order/timing/optional pen metadata. Existing
x/y-only captures map only through an explicit opt-in legacy preservation function, with
null timing, pressure, pointer type and dimensions. The current canvas is unchanged and
continues recording legacy strokes. Missing observations are never reconstructed as fact.
Capture/font coordinate inversion requires supplied physical dimensions, baseline and a
shared scale; prototype CSS transforms fail closed as canonical edits.

Use the versioned deterministic tagged encoding documented in
[contracts README](../../packages/contracts/README.md): normalized binary64 number bytes,
UTF-8 hex strings/keys, sorted keys and tagged containers, with SHA-256. Reject nonfinite,
unsafe integer and unpaired surrogate values. Materialize model defaults before hashing.
ML result hashes exclude only the root artifact_hash field; referenced child checksums
and sizes are explicit. Python/TS fixtures compare exact encoded bytes and hashes. This
is intentionally versioned, not an undocumented claim of RFC 8785 compliance.

## Audit facts and consequences

`DrawingCanvas` records normalized top-left x/y; `GlyphShape` draws into 100×100 SVG and
uses CSS preview transforms. Upload previews are resized to 1,000px and JPEG quality 0.72;
they cannot be treated as immutable original uploads. Local drafts/recovery stay in their
existing version-1 envelopes. Review assignment currently accepts one scalar, not full
Devanagari clusters; future script integration must address that without losing evidence.
Writing Desk imports actual fonts through FontFace and uses one SVG renderer for real
exports. None of these existing paths is changed by Phase 00.

See [contract tests](../../tests/backend/test_contracts.py),
[mapping fixtures](../../tests/api-boundary.test.mjs) and
[phase evidence](../backend-ml-infra/phase-00-foundations/evidence.md).
