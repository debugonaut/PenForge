# Phase 04 local development

Use [Phase 03 development](../phase-03-capture-uploads/development.md) for PostgreSQL,
Auth emulator, immutable local storage and explicit quota setup. Keep default local demo
services separate. Auth configuration alone never migrates or uploads local writing.

## Setup and execution

```sh
make setup
make migrate
make api
# Separate terminal, same database/storage settings:
make worker
```

The workspace migration head is `0007_analysis_fence`, with 24 application tables.
`0006_extraction_review` adds result/candidate lineage and revision provenance. Empty
migration cycles are tested; downgrade refuses after extraction lineage exists. Use
forward repair on populated databases. Management readiness requires the current head.
The worker now handles both `asset_verify` and `analyze_samples`; no provider is needed.

API and worker require the same storage root and quota balances. Analysis reserves its
private derivatives before writes; the default maximum output is 32 MiB per job. Other
provisional safety caps are 8 samples/job, 4 queued/running analysis jobs/owner, 3 attempts,
24 MB analysis source bytes, 16 MP/6,000 px image extent and 1 MP digital artboard (4x raster).
The child defaults to 768 MiB RSS, 20 CPU seconds and 30 seconds wall time. These are local
bounds, not measured hosted capacity or recognition quality. Analysis uses original
verified resolution, never the display thumbnail. Polling reports assessed sample counts
and no ETA. A `needs_better_input` assessment is successful work with retake guidance.

The existing-role [worker grant helper](../../../infrastructure/migrations/grant_worker_role.py)
was tested with an ephemeral NOINHERIT local role. An operator must provide separate
non-owner credentials without app/migration memberships before hosted execution.
No role, secret, account, provider or public deployment is created by this phase.

## Explicit workflow

1. On `/create`, capture an isolated drawing or choose a photo of the exact downloaded
   template. Connect private capture storage, choose an owned cloud project and explicitly
   save the selected source. Existing local demo drafts stay local.
2. For sheets, select the matching template page before upload. Public PDF/SVG files use
   pinned version/hash geometry, 100% A4 printing and dark neutral ink. Preserve all markers
   and the public QR. Blue ink and monochrome photocopies are unqualified. Physical-printer
   scale and real-camera quality still require separate checks.
3. Open private extraction/review. For a drawing, assign one actual character and provide
   the shared baseline, font scale and brush width. Timing/pressure are not inferred.
   Legacy captures without dimensions require a new drawing. Connected words remain
   experimental; use isolated samples.
4. Analyze the stored source, then inspect actual SVG outlines, crop/mask evidence and
   capture flags. Select a candidate to make a pending revision; inspect and approve it
   explicitly. Recommendations are not approval. Uniform scale/translation edit canonical
   points and metrics; unsupported operations and oversized edits remain unavailable.
5. Propose and approve space metrics separately. Coverage distinguishes core 52 from ASCII
   95, excluding .notdef. Save a milestone to freeze approved mappings. No font file or
   generated personal-font preview is created; the local demo still uses Caveat.

## HTTP surface

Public archived files: `GET /api/v1/templates`, template SVG/PDF downloads and
`GET /api/v1/calibration-definitions/Latn`. Owner-authenticated operations:

- `POST /projects/{id}/jobs`, `GET /projects/{id}/analysis-jobs/{job_id}`.
- Glyph list/detail/candidates, `POST .../select-candidate`, `PATCH .../glyphs/{glyph_id}`
  with `edit`, `approve`, `reject` or `rollback`; conditional mutations require `If-Match`
  and `base_revision_id`. Candidate selection and creates require idempotency keys.
- `POST .../glyphs/space`, milestone snapshot create/get, coverage, capture prompts and
  next calibration follow-up. Prefix private routes with `/api/v1`.

Responses and generated TypeScript are in the [OpenAPI](../../../packages/contracts/openapi.json).
Private evidence downloads use owner-issued expiring capabilities and hash verification.
No private source text/bytes, capabilities or credentials are put in logs or evaluation.

## Reproducible checks

```sh
make lint contracts-check
npm test
npm run typecheck
npm run lint
npm run build
# After build:
npm start
TEST_DATABASE_URL='disposable real PostgreSQL URL' make test-integration
```

The decoder/network-isolation checks need an environment that permits the OS sandbox to
start. A host refusing `sandbox-exec` must be reported as unavailable, not bypassed.
Use the normal authorized execution approval path for local acceptance.

Image baselines: `scripts/evaluate_extraction.py --config <pinned config> --output <new path>`.
Digital controls: `scripts/evaluate_strokes.py --output <new path>`. Never overwrite a prior
attempt. Commands, source/config/lock hashes, exact source licenses, repetitions and limits
are retained in versioned reports. See [evidence](evidence.md) for measured results and
[dependencies](dependencies.md) for licensing. Synthetic controls cannot admit a model or
prove real handwriting accuracy; private writing requires separate training consent.
