# ADR 0009 — Exact ink boundaries and explicit drawing frames

Date: 2026-10-20. Status: accepted for local experimental Phase 04.

## Decision

`pixel-union-contours-v2` traces the union of binary pixel squares, retaining every
component and hole. Image coordinates refer to pixel centers; boundaries lie at half
pixels. Collinear points may be removed without changing the boundary. The default
trace and curve tolerances are zero. Optional bounded curve fitting stays versioned
and explicit; it is not the selected extraction policy. No speck removal or per-letter
resizing silently changes handwriting. Canonical coordinates remain baseline-origin,
x-right, y-up, even-odd fill as in the existing contracts.

`capture-raster-union-v2` derives a filled outline from immutable normalized strokes.
The explicit capture frame pins canvas dimensions, baseline, uniform units per pixel,
x origin and brush width. A four-times raster supplies a 0.25 capture-pixel grid.
Missing historical dimensions require a new isolated sample rather than guessed
geometry. Pressure affects width only where observed; absent pressure/time remain
absent. This path bypasses image segmentation and OCR. Sources never become revisions.

The review renderer displays actual canonical SVG paths in a shared em frame. Uniform
scale and translation modify every endpoint and curve control point in font units;
unsupported edits must remain disabled. Empty space is metrics-only. Zero-area,
open, nonfinite or excessive outlines fail validation.

## Evidence and limits

Run 04 (`../../artifacts/phase04-core/run-04/baseline.v4.json`; local record) repeats each synthetic
case three times. The selected method reproduced every extracted mask pixel for 749
nonempty cell instances (minimum individual vector/mask IoU 1.0), with up to 477 nodes.
This measures geometry preservation after extraction, not source recognition. Individual
noisy punctuation mask/source IoU still reaches only 0.5875 and requires explicit review.
The measured gates (`../../artifacts/phase04-core/run-04/gates.v4.json`; local record) apply only to
these synthetic regression conditions. Real handwriting and human correction time
remain unmeasured. Binary reference raster equivalence is not a font-engine claim.

Seventeen geometry tests and 55 retained contract tests passed, including random pixel
topologies, holes, descenders, closed curves with coincident endpoints, digital metadata
immutability and invalid frames. Six client tests cover canonical SVG, transforms and
conditional review transport. Mypy passed for the extraction core and contracts.

OpenCV and NumPy licensing is recorded in the [dependency decision](../backend-ml-infra/phase-04-extraction-review/dependencies.md).
Potrace is not bundled. Resource/node/pixel caps remain provisional safety bounds;
container and real-capture performance gates remain open.
