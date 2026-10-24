# 0007 — Private captures, bounded decoding and the durable verification queue

Status: accepted for the locally implemented Phase 03 slice. Date: 2026-10-19.

## Context

The user authorized closing the three [Phase 02 recovery findings](../backend-ml-infra/phase-02-projects/review.md)
and then implementing [Phase 03 — Capture and uploads](../backend-ml-infra/phase-03-capture-uploads/plan.md)
only. Phases 04–11 remain gated. The accepted source is the complete
[v3 specification](../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md),
Part A FINAL refining Part B. Phase 03 must turn real image bytes and browser strokes
into immutable private source assets without claiming extraction, generated fonts,
model quality, provider selection, deletion or public deployment.

## Decisions

1. **Local filesystem storage remains the only Phase 03 adapter.** The injected
   `ObjectStorage` protocol exposes upload creation, metadata, verification, download
   capabilities, immutable writes and deletion. `LocalObjectStorage` uses owner-private
   descriptor-relative paths, `O_NOFOLLOW`, atomic hard-link publication, exact-size
   streaming, SHA-256 verification and idempotent missing-object deletion. A cloud
   adapter stays a Phase 11 provider decision.
2. **Browser capabilities are opaque, short-lived and scoped.** Local capabilities are
   AES-GCM authenticated tokens carrying object key, method, expiry, declared size and
   optional SHA-256. The HTTP transport exposes `/api/v1/storage/objects/{token}` for
   PUT/GET without logging the token. A token is valid only for its method and object,
   is checked again against live owner/asset state, and is invalidated by rotation,
   completion, expiry, ownership claims or rejection. Tokens are not a substitute for
   authorization. Local capability handling is not a signed cloud-provider URL.
3. **Initialization is owner-scoped and capacity is reserved before issuance.**
   `POST /projects/{id}/uploads` validates declared MIME, size, source type and an
   optional pinned template reference. Asset IDs and storage keys are generated from
   opaque UUIDs, never filenames. User and global `storage_bytes` reservations are
   created in the same PostgreSQL transaction after locking the current user. Missing
   quota configuration fails closed. Per-project file count is bounded. Idempotency
   receipts replay the exact original response; changed bodies fail.
4. **Completion verifies the object and enqueues exactly one durable job.**
   `POST /uploads/{id}/complete` requires live owner/project state, an unexpired pending
   upload and exact stored size. It transitions the asset to `uploaded`, inserts one
   `asset_verify` v1 job with the deterministic key `asset-verify:{asset_id}`, links the
   reservation to that job and clears the upload capability in one transaction. A
   second completion returns the same job rather than creating another.
5. **The queue is PostgreSQL, not an in-memory runner.** Claims use
   `FOR UPDATE SKIP LOCKED`, supported job type/schema versions and a resource profile.
   A claim increments the attempt, records a holder, lease expiry, heartbeat and stage.
   Heartbeats and publication are fenced by job ID, owner, current attempt, holder,
   unexpired lease, cancel state and live account/project state. Publication uses a
   conditional finalize; stale holders, expired attempts, claimed accounts, suspended or
   deleting owners and deleting projects cannot publish. Expired leases are requeued
   with bounded backoff or failed after the attempt limit.
6. **Image verification is isolated, bounded and originals stay immutable.** The worker
   copies the stored bytes into a private per-attempt directory, then decodes in a
   separate subprocess. macOS uses Seatbelt network denial; supported Linux uses a
   seccomp network filter. Missing isolation fails closed. The decoder enforces byte,
   wall, CPU and RSS limits, checks JPEG/PNG/WebP magic, performs a full decode, rejects
   multiple frames and trailing/polyglot data, and enforces dimension and pixel caps.
   It hashes the original bytes and the derivative. Only a separate PNG derivative is
   EXIF-oriented, metadata-stripped and bounded by dimension/size. Original object bytes
   are never rewritten. HEIC and PDF remain unsupported.
7. **Derivative publication is a fenced transaction.** The worker writes an
   attempt-specific thumbnail key with an immutable `put_immutable_stream`, then calls
   the API-side publication service. Publication re-verifies both stored objects, checks
   current asset/job/lease/ownership/project state and inserts one immutable thumbnail
   asset. A lease lost during storage validation cannot publish. Crash cleanup removes
   partial attempt files and known derivatives; accepted thumbnails are retained.
8. **Deduplication is owner-local and race safe.** After verification the service locks
   the owner and searches for a canonical ready `original_upload` with the same SHA-256.
   A duplicate becomes `rejected` with `duplicate_of`; its local bytes remain available
   for retry/cleanup, and a sample can reference the rejected alias only while the
   canonical owner asset is ready with the same hash. Simultaneous identical uploads
   cannot both become canonical. Existing claim aliases, historical storage-key
   ownership and ready bytes remain valid after account claiming. Deduplication is not
   global and does not compare a different user's hashes.
9. **Raw strokes are versioned immutable sources.** `StrokeDocument` v1 requires
   normalized canvas coordinates, a capture size, stable stroke IDs, ordered points and
   observed timing for new captures. Pen pressure/tilt/twist is stored only when the
   browser observed it. `timing: unavailable_legacy` rejects invented timing, size,
   pointer type or pen metadata. Raw stroke JSON is written with an immutable key and
   SHA-256/document hash; edits require a derivative document with a parent hash.
   Existing local x/y drafts require an explicit preservation choice and remain labeled
   legacy.
10. **Templates and source ownership are checked twice.** A template reference pins
    template ID, version, manifest digest and page index; the service verifies the
    pinned manifest's canonical digest and script against the project before upload or
    sample registration. Publication revalidates the pinned template. Source ownership
    is rechecked during registration and publication, so a claimed or foreign asset
    cannot be attached to another owner's project.
11. **Cleanup is resumable and conservative.** Pending uploads expire after a configurable
    TTL; `reap_abandoned` transitions them to `expired` and deletes only known original/
    derivative keys. Quota reservations settle exactly once and release both user and
    global capacity. `reconcile` is always a dry run and reports orphan keys/missing
    objects; it never deletes unknown objects. This is not Phase 07's coordinated
    deletion saga and grants no account-deletion capability.
12. **The browser keeps the declared demo/cloud boundary.** The capture panel remains
    opt-in. It preserves the selected original `File` bytes in IndexedDB independently
    of the resized JPEG preview, stores one operation with stable idempotency keys, and
    exposes upload progress in real byte/check units with unknown ETA. Terminal
    verification failures enter an explicit retake state and retain the local original.
    Transport interruptions enter retry and reuse the original request keys. No local
    draft, preview or handwriting is uploaded automatically; extraction and future font
    generation remain demos.

## Continuation corrections — 2026-10-19

The closeout probe demonstrated that a structural container check plus Pillow decode
could accept truncated JPEG entropy with valid EOI and embedded bytes after a complete
PNG image stream. Strict validation now reads all JPEG coefficients using the pinned
Pillow libjpeg dependency with warnings fatal, and requires PNG IDAT to contain exactly
one finished zlib stream with complete bounded scanlines and valid filter bytes. The
JPEG adapter supports the tested v6b LP64 ABI and fails closed for another ABI rather
than searching system libraries or accepting partially decoded input. Valid progressive,
split-IDAT and Adam7 inputs retain regression coverage.

Derivative writes now pass through owner/job-fenced staging, which removes older attempt
output before staging another bounded derivative and deletes bytes when expiry occurs
during the write. Terminal cleanup uses the persisted attempt budget, not the runtime's
possibly changed default. Decoder temporary trees are nested under the job attempt so
the lease-aware reaper can inventory them. An ambiguous response after accepted publication
does not delete its accepted thumbnail. A real killed worker proves the recovery path.

Additive migration `0005_publication_fence` checks the verification lease again at
transaction commit, including live owner/project/asset binding. Expiry after conditional
finalization rolls back the publication/quota transaction. Previous-release succeeded and
queued jobs survive unchanged. Its downgrade removes only this additional guard; it is
not a reason to weaken the fence on a running application.

Fresh [closeout evidence](../backend-ml-infra/phase-03-capture-uploads/evidence.md)
records the corrected decoder and actual worker checks. Current-wizard browser closeout
remains pending because supported browser access and Computer Use permissions were
unavailable. Historical browser artifacts do not establish current UI acceptance.

## Consequences and limits

Phase 03 implements real private image/stroke persistence, one durable verification
worker, bounded decoding and owner-local metadata. It does not implement extraction,
recognition, style models, generated fonts, TTF/OTF/WOFF2 output, consent/deletion,
training, public deployment, cloud object storage, hosted queue throughput or shared
rate limiting. Quota amounts, decoder limits, lease timings and retention TTLs are
configurable provisional safety defaults, not measured production capacity. Local
capabilities and encryption keys are process-scoped; a restart invalidates outstanding
transport tokens, which is acceptable for local development and must become a managed
service secret in Phase 11. Linux container/cgroup isolation and hosted resource ceilings
remain unmeasured; the local macOS measurements are recorded separately from ML quality.

The [Phase 03 evidence](../backend-ml-infra/phase-03-capture-uploads/evidence.md) records
executed tests, decoder measurements and browser acceptance, including the retained
Phase 02 recovery regressions. The [development instructions](../backend-ml-infra/phase-03-capture-uploads/development.md)
record the local PostgreSQL, storage, worker, quota and rollback procedure.
