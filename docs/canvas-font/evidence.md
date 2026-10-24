# Live canvas conversion — local evidence, 2026-10-24

The approved [canvas package](plan.md) is implemented locally. New Latin account
drawings request analysis payload **v2**, `drawing_mode: fixed_round_v1`. The existing
round **3.3 capture-pixel** brush expands directly into curved filled outlines instead
of tracing a raster grid. This needs no trained model. It does not infer missing letters.

## Implementation and boundaries

- [DrawingCanvas](../../src/components/creation/DrawingCanvas.tsx) and
  [capture code](../../src/lib/stroke-capture.ts) share the brush width. A click or
  keyboard dot renders as a circle even when capture contains duplicate points.
  Account drawing previews preserve their first capture frame's aspect ratio and
  scale the displayed brush uniformly when resized. Raw normalized points, original
  dimensions and actual timing/pen metadata remain unchanged.
- [Round-stroke geometry](../../packages/extraction/round_strokes.py) uses pinned
  Skia PathOps 0.9.2 with round caps/joins, overlap union and artboard clipping.
  Conic arcs become canonical quadratics with a 0.01 capture-pixel engineering
  tolerance. The [versioned policy](../../packages/contracts/policies/canvas-round.v1.json)
  bounds source points, artboard area and output complexity; normal outline node,
  contour and extent limits still apply. These are safety/approximation controls,
  not measured handwriting-fidelity thresholds. Border contact remains reviewable.
- Fixed-width drawing saves observed pressure without applying it to geometry,
  matching the brush the person sees. This is not pressure-sensitive ink or a
  generic contour-refinement capability. Image extraction and Devanagari keep
  their existing converter; their earlier quality gates remain open.
- [Contracts](../../packages/contracts/jobs/analyze_samples_job.v2.json), API admission,
  worker configuration, sequential dispatcher and shared queue support v2 explicitly.
  Every v2 sample needs an isolated Latin assignment. Payload v1 and its JSON schema
  remain unchanged. The old raster converter's public synthetic source/outline
  hashes are identical. New code/dependency fingerprints can invalidate old worker
  checkpoints; this preserves the original converter rather than reusing stale bytes.
- Publication checks exact source/outline/transform, algorithm, pressure policy,
  library version and geometry-policy hash. Candidate selection creates a pending
  revision; only explicit approval activates it. Existing approved snapshots and
  ready exports remain immutable. Source upload, quotas, Terms and ownership guards
  continue to apply. No automatic upload/import of local demo work was introduced.

## Verification performed

**105 focused backend checks** passed across
[canvas geometry/versioning](../../tests/backend/test_canvas_geometry.py),
[legacy geometry](../../tests/backend/test_extraction_geometry.py), contracts and
foundation tests. Independent capsule-distance comparisons cover diagonals, dots,
duplicate-point dots, crossings, open counters and disconnected strokes. Tests also
cover clipping, descenders, reversible offsets, pressure independence, source
immutability, repeatability, budgets, toolchain failure and retained v1 request hashes.

**33 real PostgreSQL integrations** passed across the
[new canvas workflow](../../tests/backend/test_canvas_font_database.py), existing
analysis, extraction/export HTTP tests and queue recovery tests. Synthetic verified
principals and test-only schemas were used. The actual sequential dispatcher ran
the isolated converter and compiler. Exports failed before approval, then produced
ready validated **TTF, CFF OTF and WOFF2** files; FontTools read their expected cmap
and table type, downloads matched their hashes, and another principal could not
download them. A v1-only claimant left v2 queued; forged geometry policy evidence
could not publish. Existing crash/retry/fencing and legacy analysis paths passed.

**171 frontend tests**, TypeScript, ESLint, scoped Ruff/mypy, architecture boundaries,
generated contract drift, retained v1 compatibility and the production build passed.
The build reports existing CSS `align-items: start` support warnings. PostgreSQL
tests report the existing Starlette/httpx deprecation warning. The first broad queue
invocation omitted exported PYTHONPATH, so its two subprocess tests could not import
the app; the corrected complete invocation passed. Initial outputs are retained.

Browser checks used the actual drawing component with a temporary gated loopback
synthetic bridge to the real converter. Mouse crossings/dot and keyboard loop matched
the SVG outline; the source stayed 460 × 290 capture pixels at desktop/tablet/mobile/
narrow sizes, with displayed widths 460/354/350/280. Keyboard continuation after resize,
Escape cancellation, Tab focus, scrolling away/back and visible click dots passed.
The ordinary-motion wizard was also checked at **1440×900, 768×900, 390×844 and
320×568**, without horizontal overflow, including keyboard capture, undo and redo.
The completed production build restored the synthetic local draft and rendered a new
keyboard dot at desktop/mobile sizes. No runtime console errors were observed.

Local-only screenshots, logs, preimages and the synthetic source/outline/provenance
are in `artifacts/canvas-font-20261024/`. The temporary proof routes were removed
before the production build; fixture copies remain only as local evidence. Temporary
servers/tabs were closed and viewport settings restored. The owner's original
Writing Desk tab/document was left untouched. No account/provider or private source
was used in browser QA. Do not publish the local evidence directory wholesale.

## Limits and next package

This establishes local fixed-brush geometry and the synthetic authenticated pipeline,
not independent writer-fidelity, polished typography or full live-account browser
acceptance. Original artboard advance/baseline/scale rules are preserved; per-letter
spacing and comfortable font size still need review on real drawings. Unsaved live
account strokes remain transient until saved; this package does not add account
capture-draft restoration. The demo still simulates extraction/export and uses Caveat.

OS-gentle, physical touch/pen, configured provider/email/account UI and all earlier
phase/hosted/legal gates remain open. Cursive joining, model qualification and missing
character generation are unproven. No training, inference, repeated research evaluation,
frozen private-font rebuild, new dependency, provider action, push or deployment occurred.
The separate next product package is reviewed-photo-to-account import, alongside
human review of real drawing scale/spacing. Additional datasets retain their intake
and owner-run training restrictions.
