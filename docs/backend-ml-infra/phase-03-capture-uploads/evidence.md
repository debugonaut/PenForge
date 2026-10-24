# Phase 03 acceptance evidence

Date: 2026-10-19. Workspace: PenForge repository root.

Phase 03 is implemented locally. The continuation corrections below have fresh automated
evidence; follow-up browser acceptance remains pending because browser access and Computer
Use permissions are unavailable. This record separates executed checks from hosted,
container and ML gates. Phase 03 itself supplies no extraction, recognition, style model,
generated font or public deployment. The prior [Phase 02 review](../phase-02-projects/review.md)
recovery findings were corrected and verified before this phase.

## Implemented slice

- `ObjectStorage` conformance remains on the local filesystem adapter. Private
  capabilities are opaque, method/object/expiry scoped and enforced on the transport
  routes; originals are immutable, hash/size verified and deleted idempotently.
- Owner-scoped upload initialization validates declared MIME/size/source/template,
  generates opaque asset IDs and keys, and reserves user/global storage atomically.
  Completion verifies the stored object and creates exactly one durable `asset_verify`
  v1 job. Repeated completion returns the same job.
- PostgreSQL queue claims use `FOR UPDATE SKIP LOCKED`, leases, heartbeats, attempt
  fencing and bounded retry. Publication is conditional on current attempt, holder,
  unexpired lease, active owner, non-deleting project and the linked asset state. Account
  claims revoke old capabilities and stale workers; historical storage keys remain
  readable inventory.
- JPEG/PNG/WebP verification runs in an isolated subprocess with network denial,
  byte/wall/CPU/RSS limits, full decode, frame/trailing-data checks and dimension/pixel
  caps. Originals keep their exact bytes and metadata. Only the bounded PNG derivative is
  EXIF-oriented and metadata-stripped.
- Owner-local SHA-256 dedupe is race safe and returns only the owner's canonical asset.
  Duplicate aliases remain valid sample references while the canonical bytes are ready.
- `StrokeDocument` v1 stores normalized coordinates, capture size, stable stroke IDs,
  observed timing and only observed pen metadata. Legacy documents remain explicitly
  `unavailable_legacy`; edits are immutable derived documents with a parent hash.
- Abandoned uploads expire, known objects are cleaned up, reservations settle once, and
  `--reconcile-report` writes a dry-run inventory without deleting orphans.
- The opt-in capture panel preserves the selected original `File` in IndexedDB, shows
  real byte/check progress with unknown ETA, retains retry recovery and enters an explicit
  retake state after terminal verification failure. No draft or preview is uploaded
  automatically.

## Historical implementation commands and results

These results preceded the continuation corrections. They are retained for provenance and
do not prove the stricter decoder, crash cleanup or current frontend workflow.

```sh
make check                       # with TEST_DATABASE_URL
make contracts-check
npm test
TEST_DATABASE_URL=... .venv/bin/python -m pytest -m 'integration and not emulator' -q
TEST_DATABASE_URL=... PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 \
  .venv/bin/python -m pytest -m emulator -q
PYTHONPATH=. .venv/bin/python scripts/measure_capture_decoder.py
```

| Check                                                          | Actual result                                                                                                                                                                     |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| API unit, contract, research and decoder tests                 | **200 passed**, 102 deselected; one Starlette/httpx deprecation warning.                                                                                                          |
| Real PostgreSQL integration                                    | **98 passed**, 204 deselected. Migration upgrade → downgrade → upgrade reached `0004_capture_uploads`; metadata drift is zero; 22 application tables are present.                |
| Real Auth emulator + PostgreSQL                                | **4 passed**, 298 deselected. The emulator suite predates Phase 03 capture routes; capture HTTP authorization is covered by service/DB tests and the browser session below.        |
| Frontend tests                                                 | **78 passed**. Includes the new terminal-verification → retake regression, existing interrupted-init/sample retry tests, IndexedDB original preservation and capture transport scope. |
| Decoder resource measurement                                   | 5 synthetic fixtures, 3 runs each; values below.                                                                                                                                  |
| Ruff, Ruff format, mypy, boundaries, docs links/source hashes  | Passed; mypy checked 57 files; 302 local links and both preserved source hashes passed.                                                                                            |
| Contract generation, drift, compatibility, generated contracts | Passed; `asset_verify_job.v1`, OpenAPI and generated TypeScript refreshed.                                                                                                        |
| `npm run typecheck`, `npm run lint`, `npm run build`           | Passed; all six application routes built.                                                                                                                                         |
| Docker, Linux container limits, hosted CI/providers            | Not executed. Docker remains unavailable and no provider or public deployment was authorized.                                                                                     |

## Continuation corrections and fresh verification

The continuation found two format-validation gaps and one crash-cleanup gap. The fixes
were already present when this closeout resumed and were preserved and freshly exercised:

- JPEG container markers and Pillow's pixel decode do not detect every truncated entropy
  stream. The isolated child now reads all coefficients through the pinned Pillow
  libjpeg dependency with corruption warnings made fatal. Baseline and progressive JPEGs
  truncated before valid EOI are rejected. The adapter supports only the tested libjpeg
  v6b LP64 ABI; incompatible platforms fail closed as `strict_decoder_unavailable`.
- PNG chunk CRCs and a successful pixel decode do not prove that IDAT contains exactly
  one complete zlib image stream. The child validates PNG chunk ordering, bounded Adam7
  scanline geometry, filter bytes, exact stream completion and absence of embedded trailing
  data or a second stream. Valid split-IDAT and interlaced fixtures still decode.
- Derivative staging holds the owner/job fence while writing, removes older attempt
  derivatives before another is staged, and deletes current bytes if its lease expires
  during the write. Cleanup uses the job's persisted attempt budget. Decoder temporary
  files live inside the worker's per-attempt tree, so the lease-aware reaper can remove
  files left by a killed process. Accepted thumbnails survive an ambiguous lost response.
- Additive migration `0005_publication_fence` rechecks the verification lease and live
  owner/project/asset binding at transaction commit. A lease expiring after finalization
  but before commit rolls back publication and quota settlement. The migration preserves
  prior-release completed and queued jobs; its downgrade removes only this new guard.

Executed on 2026-10-19 against disposable real PostgreSQL 16.2 schemas and the local Auth
emulator. The full backend pass used the corrected decoder with actual macOS network
isolation. The first outer-sandbox invocation correctly failed closed when macOS refused
nested `sandbox-exec`; the accepted local execution outside that outer sandbox passed.
No decoder safety check was disabled.

| Fresh check | Actual result and saved output |
| --- | --- |
| `TEST_DATABASE_URL=… .venv/bin/python -m pytest -m 'not emulator' -q` | **325 passed**, 4 deselected, one Starlette/httpx deprecation warning; **213** non-integration and **112** PostgreSQL tests in the collected suite. Output (`../../../artifacts/phase-03/closeout-backend.txt`; local record). |
| `… pytest tests/backend/test_capture_decoder.py tests/backend/test_storage.py -q` | **47 passed**; truncated entropy, embedded PNG data, valid progressive/interlaced inputs, isolation and storage conformance. Output (`../../../artifacts/phase-03/closeout-decoder-storage.txt`; local record). |
| `… pytest tests/backend/test_uploads_database.py tests/backend/test_worker_runtime.py -q` | **26 passed**; owner/global quota races, commit fencing, claims/deletion-state fencing, actual SIGKILL after a real derivative write, lease expiry/retry, exact physical-byte quota settlement, no orphan objects or attempt files. Output (`../../../artifacts/phase-03/closeout-upload-worker.txt`; local record). |
| `TEST_DATABASE_URL=… PENFORGE_TEST_AUTH_EMULATOR_HOST=127.0.0.1:9099 … pytest -m emulator -q` | **4 passed**, 325 deselected. Identity/claim scenarios only; these are not capture UI acceptance. Output (`../../../artifacts/phase-03/closeout-emulator.txt`; local record). |
| `npm test` | **84 passed** in the continuation state before the later wizard UI revision. Includes the Phase 02 storage/recovery regressions and Phase 03 handler/recovery corrections detailed in [the review](review.md). Latest wizard checks are recorded separately by the active UI work. |
| `make lint`, `make contracts-check` | Passed Ruff/format, mypy (59 files), import boundaries, 341 local links and original source hashes, retained-v1 compatibility, schema drift, generated TypeScript and research metadata. Lint (`../../../artifacts/phase-03/closeout-lint.txt`; local record), contracts (`../../../artifacts/phase-03/closeout-contracts.txt`; local record). |
| `npm run typecheck`, `npm run lint` | Passed in the same continuation state. Typecheck (`../../../artifacts/phase-03/closeout-typecheck.txt`; local record), ESLint (`../../../artifacts/phase-03/closeout-eslint.txt`; local record). |
| `PYTHONPATH=. .venv/bin/python scripts/measure_capture_decoder.py` | Five synthetic fixtures, three runs each, after the stricter decoder corrections. Output (`../../../artifacts/phase-03/closeout-decoder-measurement.txt`; local record). |
| Follow-up browser | Attempted through CUA. Browser inventory: **Local browser use is disabled in Work settings**. Native Firefox: **Computer Use permissions are not granted**. No fresh screenshot or interaction was obtained; no settings were changed or alternative browser-control mechanism used. |

The Phase 02 recovery tests remain in the frontend suite: stale editor/list-refresh ETags,
exact interrupted-create body/key restoration, and independent operation keys under actual
storage write/write and remove/write interleavings. The historical two-tab browser evidence
below remains separate from pending current-wizard acceptance.

## Corrected decoder measurements

Synthetic fixtures only; no handwriting, recognition or font quality is measured. The
machine-readable record is decoder-resources.json (`../../../artifacts/phase-03/decoder-resources.json`; local record),
measured after the strict decoder fixes at 2026-10-19 14:19 UTC. The earlier profile is
preserved as decoder-resources-pre-closeout.json (`../../../artifacts/phase-03/decoder-resources-pre-closeout.json`; local record).

| Fixture (synthetic)        | Dimensions | Input bytes | Output bytes | p50  | p95   | Peak RSS |
| -------------------------- | ---------- | ----------- | ------------ | ---- | ----- | -------- |
| ruled-12mp.jpg             | 3000×4000  | 783,959     | 153,216      | 100.5 ms | 102.9 ms | 106.6 MB |
| noise-1mp.png              | 1000×1000  | 3,005,235   | 3,005,235    | 104.0 ms | 121.6 ms | 38.4 MB |
| oriented-alpha-50mp.png    | 10000×5000 | 209,389     | 4,095        | 504.6 ms | 594.5 ms | 421.5 MB |
| bounded-20mib.jpg          | 40×30      | 20,971,520  | 124          | 161.2 ms | 196.0 ms | 94.8 MB |
| plain-1.92mp.webp          | 1600×1200  | 3,488       | 4,052        | 74.2 ms | 97.7 ms | 83.5 MB |

Limits were the provisional defaults: 20 MiB, 50 MP, 12,000 px dimension, 768 MB worker
RSS guard, 10 s CPU and 20 s wall. `n=3` is a smoke profile, not throughput, safe
concurrency, ETA or hosting capacity. Darwin has no finite AS/DATA rlimit; RSS is sampled
approximately every 20 ms and may overshoot. Linux seccomp/container limits remain to be
executed.

## Historical browser acceptance

A disposable PostgreSQL database (`penforge_phase03_browser`) and a separate local object
root were used. The real API, worker, Firebase Auth emulator and Next dev server ran with
local-only settings. A synthetic emulator guest created a cloud project, drew raw strokes,
then uploaded an EXIF/GPS-bearing synthetic 1300×700 JPEG. No hosted account or user data
was touched.

- Raw drawing: one `raw_strokes` asset reached `ready` with SHA-256 and document hash;
  the historical session registered two `canvas_drawing` samples from two explicit save
  actions. The artifact reports three total samples after the subsequent image save.
  This is not evidence of a single sample across separate user-initiated operations. The UI reported **Source confirmed · local backup
  retained**.
- Uploaded JPEG: original asset reached `ready` with SHA-256
  `a99f1dc4a830d2fe426a9b02cdfed5abf9c4755177f132be4b06885e9dfe529e`, exactly the uploaded
  fixture hash and 54,276 bytes. The stored original remained an EXIF-bearing JPEG.
- Derivative: one immutable PNG thumbnail (13,596 bytes, 538×1000 after orientation
  correction) had zero EXIF entries and empty `info`; the synthetic author/GPS/description
  metadata was absent. The original file on disk retained its metadata.
- Job: one `asset_verify` job reached `succeeded`/`verified`, attempt 1, progress `1`,
  checkpoint `4/4`, no error. Exactly one verify job existed for the upload.
- Retake: an appended-polyglot PNG produced `failed`/`image_decode_failed`, a `rejected`
  asset and **Retake needed** in the UI. The local IndexedDB original count remained one;
  no sample was registered for the rejected asset.
- Layout: 1440×900, 768×1000, 390×844, 320×700 and 1280×600 reported no horizontal page
  overflow. Screenshots and machine-readable results are in `artifacts/phase-03/`.
- Page errors and failed non-storage requests were empty.

Artifacts: upload acceptance (`../../../artifacts/phase-03/browser-acceptance.json`; local record),
retake acceptance (`../../../artifacts/phase-03/browser-retake.json`; local record),
desktop upload (`../../../artifacts/phase-03/browser-upload-confirmed-1440.png`; local record),
retake (`../../../artifacts/phase-03/browser-retake-1440.png`; local record),
mobile 390 (`../../../artifacts/phase-03/browser-upload-390.png`; local record),
narrow 320 (`../../../artifacts/phase-03/browser-upload-320.png`; local record).

The two-tab Phase 02 recovery acceptance was rerun in this Phase 03 closeout against the
current API. The result remains: stale editor after list refresh received 412 and rebased;
an interrupted create restored its exact body/key after reload; two unresolved tab saves
produced two operation keys, and resolving one retained the other; the populated local
drawing draft stayed byte-identical; no page errors were reported. See the
recovery browser result (`../../../artifacts/phase-02/recovery-followup-browser.json`; local record),
two-tab screenshot (`../../../artifacts/phase-02/recovery-followup-two-tabs.png`; local record) and
[Phase 02 review](../phase-02-projects/review.md). The 78-test frontend suite preserves
these regressions.

## PostgreSQL race and security coverage

The integration suite covers, among others:

- interleaved initialization/completion replay, exact object size/hash and one job;
- parallel quota initialization at the configured user/global boundary;
- wrong holder, wrong attempt, wrong owner, expired lease and post-reassignment
  publication/heartbeat rejection;
- claim racing an in-flight upload and worker publication, including old capability
  revocation, retry under the destination owner and historical storage-key readability;
- simultaneous identical owner uploads producing one ready canonical asset and one
  duplicate alias, with sample binding and exactly-once cleanup;
- template digest/version/script mismatches rejected before upload and again on publish;
- lease expiry during storage validation rolling back publication;
- expired-attempt cleanup, reservation release and dry-run reconciliation;
- raw-stroke legacy metadata absence, derived parent lineage and immutable originals.

## Remaining gates

Fresh current-workflow browser checks remain open: upload/retry after reload, copied-image
reselection, keyboard drawing, account changes, ordinary/OS gentle motion and affected
responsive/short viewports. The tooling block is recorded above and in [the review](review.md).

Hosted Firebase/App Check, shared multi-instance rate limiting, provider/dependency review,
Docker/Compose and hosted CI, Linux/container decoder limits, cloud object storage,
service identity, public deployment and Phase 07 deletion remain unexecuted. Decoder
resource numbers are synthetic engineering measurements, not ML quality or production
capacity. Extraction, review, font generation, training and scientific accuracy remain
later phases.
