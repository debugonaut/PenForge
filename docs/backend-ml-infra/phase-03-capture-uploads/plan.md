# Phase 03 — Private capture, uploads and the first durable worker

Status: implemented with continuation decoder/crash corrections freshly verified on
2026-10-19; current-wizard browser closeout remains blocked by unavailable browser access
and Computer Use permissions. Source: Part B S3, §§6.5, 7.5, 9; Part A IC-02/03/05.  
Depends on phase 02. Unlocks phase 04 after closeout. This phase creates the real queue interface; no throwaway in-memory job runner. See [evidence](evidence.md), [development](development.md), [review](review.md) and [ADR 0007](../../decisions/0007-phase-03-private-captures-and-queue.md). The latest user request explicitly authorizes closing Phase 03 and then implementing Phase 04 only.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

Images and browser strokes become verified immutable private source assets with provenance. Unsafe files are rejected, and verification runs through a durable PostgreSQL job with leases.

## Work packages

1. Implement ObjectStorage conformance tests before a cloud adapter: createUpload, getMetadata, verifyUpload, getDownloadUrl and deleteObject. Use local_fs first. Provider-specific code stays in the adapter; cloud implementation waits for phase 11 selection. Capability flags describe resumability honestly.
2. Add upload initialization under an authorized project. Validate declared MIME/size and per-project file limits; reserve user/global storage before granting capacity. Generate keys from opaque IDs, never filenames. Return scoped short-lived upload capability and asset ID; upload_id equals asset_id.
3. Complete verifies metadata existence and exact declared size, moves pending_upload → uploaded and enqueues one asset_verify job idempotently. Expired/foreign/missing objects cannot be promoted. Never trust content-type metadata alone.
4. Implement minimal real queue claim via FOR UPDATE SKIP LOCKED, supported payload schemas/types, attempt fencing, lease and heartbeat. Asset verification must use the same interface that extraction/export will use. Even before S6 hardening, stale attempts cannot publish and retries cannot duplicate accepted assets.
5. Decode in a bounded isolated subprocess: streaming hash, JPEG/PNG/WebP magic consistency, strict full decode, single frame, size/dimension/pixel limits, CPU/memory/wall limits, per-job temp cleanup. Preserve original bytes. EXIF-orient and strip identifying metadata only in derived files. HEIC/PDF remain unsupported pending review.
6. Enforce per-owner original dedupe after verification, handling simultaneous identical uploads. Return a safe duplicate reference only to that owner. Ready original hash/key/size cannot change. Reject conflicting rewrites in storage and DB.
7. Add versioned stroke submission. New capture records normalized coordinates, dimensions, stable stroke IDs, timing and only actually observed pen metadata. Validate point limits and monotonic times. Preserve raw capture immutable; edit operations are derived revisions. Legacy x/y-only drafts remain labeled legacy and require an explicit conversion/resample policy.
8. Pin template ID/version/manifest digest and source type when registering a sample. Unknown/mismatched templates get typed recovery guidance, not guessed character assignments. Source ownership is checked before registration and on worker publication.
9. Add abandoned-upload expiry/reaping with storage/DB reconciliation, dry-run orphan reports, quota release and resumable cleanup. Worker events contain only safe IDs/stages/counts.

## API and persistence

`POST /projects/{id}/uploads`, `POST /uploads/{id}/complete`, owner-only asset download capability, sample registration and job polling contracts. Reconcile exact stroke-registration route with v2 rather than inventing duplicate APIs. Use assets, samples, jobs/job_events and quota reservations from S0; additive source hash/manifest fields as needed.

## Acceptance tests

- Wrong declared size/MIME/hash, malformed/truncated/polyglot files, bombs, huge dimensions and animated inputs cannot become ready.
- Safe original bytes/hash remain identical; derived output has no EXIF GPS. No shell interpolation or unrestricted decoder network access.
- Two workers cannot claim the same attempt; expired lease publication fails. Crash/retry verification is idempotent and partial temp files are cleaned.
- Cross-user init/complete/download/sample references return 404. Signed capabilities are method/object/expiry scoped and never logged.
- Duplicate original detection is owner-local and race safe. Abandoned objects expire and storage reservations return once.
- Strokes round-trip with absent pressure still absent; editing does not alter original hash. Bad timestamps/coordinates/versions fail.
- Local storage passes the future adapter conformance suite. Hash and memory/time measurements are recorded for representative fixtures, with no quality-accuracy claim.

## Rollout and rollback

Add upload progress/retake states through existing capture UI, preserving its layout. Keep local capture recovery until server confirmation. Disable new upload issuance on rollback, allow existing verification/cleanup to settle, and retain originals. No user data is deleted as a rollback shortcut.

## Open gates

Safety limits from v2 are provisional configurable defaults, reviewed through resource tests. Cloud provider selection and service identity remain in 11. Production template geometry is phase 04; this phase validates the contract without claiming reliable extraction.

## Implementation record — 2026-10-19

All nine work packages are implemented locally. `ObjectStorage` conformance remains on
`local_fs`; owner-scoped initialization and completion use PostgreSQL reservations and
one `asset_verify` job; the worker uses `FOR UPDATE SKIP LOCKED`, leases, heartbeats and
fenced publication; the isolated decoder preserves originals and writes only bounded,
metadata-stripped derivatives; owner-local dedupe, versioned raw strokes, template/source
validation, abandoned cleanup and the dry-run reconciler are present. The existing
capture UI exposes opt-in upload, real progress, retry and retake while keeping original
bytes in IndexedDB until confirmation.

Executed: 200 API/unit/decoder tests, 98 disposable-PostgreSQL integration tests, 4 Auth
emulator tests, 78 frontend tests, lint/mypy/boundaries, contract drift/compatibility,
typecheck, ESLint, production build, five synthetic decoder measurement fixtures and a
real local browser session (raw drawing, verified EXIF/GPS JPEG, metadata-free derivative,
and a polyglot retake). Docker/Linux container limits, cloud storage, hosted Firebase/App
Check, shared rate limiting, hosted CI and public deployment remain unexecuted. See
[evidence](evidence.md).

Continuation verification preserves the existing strict JPEG/PNG and fenced staging
corrections and reaches additive migration `0005_publication_fence`. Fresh runs: 325
backend tests (213 non-integration plus 112 PostgreSQL), four Auth-emulator tests,
47 focused decoder/storage tests and 26 focused upload/actual-worker tests. The real
SIGKILL/retry regression proves no orphan derivative/temp files or quota mismatch. Strict
decoder resource measurements were rerun and the earlier profile preserved separately.
Frontend continuation tests passed 84 before the later user-directed wizard revision;
current visual/reload/retry acceptance is explicitly pending in [the review](review.md).
