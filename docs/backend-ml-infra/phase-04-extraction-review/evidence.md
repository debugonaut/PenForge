# Phase 04 acceptance evidence

Date: 2026-10-20. Implemented locally; live browser acceptance remains pending at the
user's choice. Work was completed sequentially after the user's instruction to focus
on one task at a time. No training, provider provisioning, deployment or font compilation.

## Implemented paths and review

- **Structured Latin:** pinned template → verified original image → quality assessment,
  marker rectification, QR/page validation, background normalization, guide dropout,
  authoritative cells → canonical outlines and private crop/mask evidence → candidates.
- **Digital Track C:** immutable normalized StrokeDocument → explicit capture-to-font
  frame → canonical outline → candidate. This path bypasses image segmentation and OCR.
  Pen/mouse provenance, observed time and optional metadata remain intact. Missing
  historical dimensions require a new isolated sample; no metadata is fabricated.
- Owner/lease/attempt-fenced services register immutable manifests/children. Workers
  cannot activate glyphs. Stable candidates preserve alternatives and provenance;
  selection creates a pending revision, separate from explicit owner approval.
- ETag/base-revision edit/approve/reject/rollback APIs, identical save suppression,
  repeatable-read milestones, profile-value freezing, 52/95 coverage and versioned
  isolated calibration follow-ups are implemented. Space uses approved metrics;
  .notdef/alternates do not increase coverage; font validation always remains false.
- The existing opt-in private wizard panel displays actual canonical SVG geometry,
  crop/mask evidence and risk flags. Uniform scale/translation modify real points and
  metrics. Unsupported controls and oversized edits are unavailable. Local demo panels
  remain explicitly demo; Caveat is never presented as an extracted personal font.

Decisions: [pinned templates](../../decisions/0008-phase-04-pinned-latin-template.md),
[canonical geometry](../../decisions/0009-phase-04-canonical-vectorization.md),
[fenced publication/review](../../decisions/0010-phase-04-result-publication-and-review.md).
Run instructions: [development](development.md). Licensing: [dependency evidence](dependencies.md).

## Printable and on-screen template evidence

Two A4 version 1.0.0 pages map 94 drawn ASCII symbols exactly once; space is metric-only.
The shared manifest pins physical cells/guides, ordered markers/quiet zones, public QR,
version/page/hash, printable margins, colors and a 20 mm reference. Generator and
extractor consume the same manifests. Retained legacy fixtures still round-trip.

**68 template/contract tests passed** at this package checkpoint. Deterministic SVG/PNG
masters and PDFs were generated; Poppler rendered both pages at 150 DPI and both images
were visually inspected. PDF page size, all ordered marker locations, exact QR payload
and the 20 mm scale reference passed digital measurements in the
printability record (`../../../artifacts/phase04-core/printability.v1.json`; local record).
[Two-page PDF](../../../packages/contracts/templates/penforge-latin-ascii-1.0.0.pdf),
page 1 proof (`../../../artifacts/phase04-core/print-final-1.png`; local record),
page 2 proof (`../../../artifacts/phase04-core/print-final-2.png`; local record).
Physical-printer measurements and real-camera captures remain unexecuted.

## Reproducible extraction and geometry baselines

The final image run 05 (`../../../artifacts/phase04-core/run-05/baseline.v5.json`; local record) records
40 page/condition/method cases, three repeats each, exact source/config/lock hashes,
archived code and two tracing configurations. A single licensed Caveat family is used
entirely in validation; train/test are empty. Aliases/weights/augmentations cannot cross
splits. Synthetic font controls are not real writers, admitted training data or held-out
handwriting. No private writing was used for training.

| Dimension | Final measured result | Interpretation |
| --- | --- | --- |
| Cell extraction | 749/749 expected nonempty condition instances across 16 accepted cases; zero extras | Repeated synthetic conditions, not 749 unique characters |
| Source-mask fidelity | Worst case-mean IoU 0.953931; worst individual punctuation IoU 0.5875 | Noisy marks still require review; means do not waive failures |
| Boundary fidelity | Worst case-mean F1 at 1 px 0.987045 | Synthetic controls only |
| Outline preservation | Minimum individual extracted-mask/vector IoU 1.0; maximum 477 nodes | Binary reference raster, not a font-engine result |
| Review burden | 749 identity inspections required; 74 extra flagged cell instances | Actual human corrections and time are null |
| Local resources | Case p50 292.3–575.3 ms; process lifetime peak RSS 613.4 MiB | n=3 smoke profile; not stage RSS, throughput, cost or ETA |
| Rejected capture controls | Low-resolution → low_resolution; severe blur → too_blurry | Successful assessment with actionable retake guidance |

Numeric image gates (`../../../artifacts/phase04-core/run-05/gates.v5.json`; local record) use measured
extrema with downward rounding; they apply only to the listed synthetic conditions.
Every character assignment remains `identity_unverified`, and expected template text
is context rather than proof of what was written. There is no recognizer.

The separate digital baseline (`../../../artifacts/phase04-core/digital-run-01/baseline.v1.json`; local record)
uses newly authored analytic loop/descender/dot controls for mouse and observed-pen
captures, with three isolated-process repetitions each. Against independent constant-
width analytic capsule masks, IoU is 0.925827 or greater; maximum bounds error is
0.125 capture pixels on a 0.25-pixel raster grid. Four contours preserve the loop hole,
descender and disconnected dot. Sources/metadata remain immutable; segmentation/OCR
are false. Digital gates (`../../../artifacts/phase04-core/digital-run-01/gates.v1.json`; local record)
are synthetic geometry regressions, not handwriting or hardware-pressure fidelity gates.
Per-repeat child RSS/wall measurements are recorded separately in that report.

Historical run 03 recorded vector expansion as low as 0.504762 for noisy punctuation.
Pixel-square union tracing fixed that discrepancy in run 04; run 05 confirms it with
current code. Earlier attempts remain unchanged for comparison and are not current
acceptance. The noisy **source** mask limitation remains unresolved.

## Automated and runtime acceptance

**406 backend tests and 93 frontend tests passed** in the final runs. The four emulator
scenarios retain prior Phase 03 evidence and were not rerun. Ruff, mypy, contracts,
TypeScript, ESLint and the production build passed. Command outputs and pinned hashes are retained in
acceptance summary (`../../../artifacts/phase04-acceptance/summary.json`; local record).
The final suite includes the following distinct properties:

- Both actual isolated paths reach pending candidates; pen and mouse captures pass
  source → outline → explicit selection → approval → immutable milestone. New result
  sets preserve existing approvals and snapshots.
- Forged attempt/source/model/origin/mapping/child/policy artifacts fail registration.
  Owner isolation, claim/deleting-state revocation, stale attempts and lease expiry
  during publication pass. A real SIGKILL after staged extraction output recovers
  without orphan objects or quota imbalance; Phase 03 crash regressions also remain.
- A separate ephemeral NOINHERIT worker role registers candidates successfully;
  PostgreSQL denies revision insert, active-glyph update, candidate approval and
  snapshot insert. Hosted execution still requires separate non-owner credentials.
- Concurrent edit conflicts, identical autosave suppression, rollback as a new pending
  revision, generated-proposal review, immutable lineage, profile-value freezing,
  snapshot revision-pruning protection and truthful 52/95/space coverage pass.
- Actual HTTP workflow/authorization, template PDF/SVG downloads, retained v1 schema
  compatibility, generated TypeScript, API dependency boundaries, migration cycle,
  24-table metadata drift, and Phase 02 recovery regressions pass.
- Detailed-outline regression covers a compact JSON object below 32,000 bytes whose database
  representation exceeds 32 KiB. It uses an immutable outline asset, SQL NULL for the
  absent inline source, and compact receipts that rehydrate immutable revision geometry.
  Replay retains original pending metadata even after approval. The API's explicit
  `outline_edit_supported` flag disables oversized edits. The 76 affected database/
  workflow checks passed after this correction; initial failures remain archived.
- Production build and local `npm start` serve all five app routes with HTTP 200:
  startup smoke (`../../../artifacts/phase04-acceptance/startup.json`; local record). HTTP startup is
  not live interaction or visual acceptance.

An initial sandboxed decoder run could not start macOS Seatbelt and failed closed;
its output is retained. The normal approved execution path subsequently ran the actual
network-denied children successfully. One later approval attempt hit a usage limit;
it executed no command. The user's continuation allowed the same normal path to resume.

## Remaining gates and recommended next phase

Current wizard browser checks at 1440/768/390/320 px, short viewports, keyboard/touch,
ordinary/gentle motion and section re-entry remain **pending**. The user chose to defer
live verification; a later browser attempt was rejected by automatic approval review for
that reason. No workaround or settings change was used. PDFs were inspected separately.

Real handwriting production accuracy, recognition, human correction time, unconstrained
natural/cursive segmentation, blue-ink/photocopy qualification and variable-pressure
hardware calibration remain unmeasured. Physical printing, Linux/container limits,
hosted Firebase/App Check, cloud storage/providers, shared rate limiting, dependency/
privacy review, consent/retention/deletion and hosted CI/deployment retain their gates.
Synthetic regression gates do not authorize a production capability or model promotion.

The recommended next implementation at this original checkpoint was **Phase 05 — Font
export**. It was subsequently implemented; see the current handoff for its evidence.

## Separately authorized canvas follow-up — 2026-10-24

The [fixed-brush canvas package](../../canvas-font/evidence.md) now emits explicit
analysis v2 for isolated Latin account drawings. Direct curves replace raster-grid
outlines for these requests while retaining v1/schema/legacy output compatibility,
immutable sources and explicit approval. The actual sequential queue/isolated worker
and approved TTF/CFF OTF/WOFF2 pipeline passed synthetic integration checks. Scoped
ordinary-motion drawing/wizard checks passed at 1440/768/390/320 px with keyboard,
short viewport and undo/redo checks. These do not close the original full-phase
browser, gentle-motion, physical-device or handwriting-quality gates. Image extraction,
Devanagari and generic Phase 10 refinement stay within their previous boundaries.
