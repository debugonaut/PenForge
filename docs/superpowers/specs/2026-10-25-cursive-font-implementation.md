# Cursive font implementation plan

Approved design: [private rebuild](2026-10-24-cursive-font-design.md).
Execute sequentially. No handwriting asset is published or added to site code.

## 1. Preserve and bind the source

- Inventory and hash the complete frozen first tracing pass before writing a derivative.
- Create a new ignored private run with exclusive file creation. Pin original JPEG and
  decoded RGB hashes, original dimensions, parent inventory hash and recipe hashes.
- Reuse the existing manual character labels; never infer them with a model.

## 2. Audit source-backed ink and metrics

- Inspect every source crop. Compare a stronger chroma threshold with a darker ink
  threshold supported by the blue signal; preserve visible weak stroke sections.
- Save raw and selected masks, all excluded pixels, component choices, thresholds and
  source-space coordinates. Review tiny pieces individually, including dots and quotes.
- Expand bracket/backslash crops where needed, then exclude neighbouring characters
  explicitly. Do not fill gaps or close counters through blanket morphology.
- Keep original blue ink only; ambiguous crossings remain flagged for owner review.
- Use one page scale, row-relative baselines and explicit side bearings. Space is a
  manual metric, since the image supplies no isolated space outline.

## 3. Prepare proposed outlines and legible review sheets

- Run deterministic pixel tracing and the existing bounded subpixel proposer only.
- Require exact mask/pixel-reference round trips. Validate every canonical outline and
  record topology, bounds, pixel differences and rejected smoothing proposals.
- Render source, selected ink and proposed outline together with enough vertical room
  for all ascenders/descenders. Use common scale for comparison; never clip a glyph.
- Audit all 94 drawn characters and space; retain pending status and no owner receipt.
- Recheck frozen-parent hashes and Git exclusions after generation.

## 4. Owner review gate

Present the actual corrected review sheets, uncertain glyphs and proposed metrics.
The design approval does not approve the as-yet unseen contours. Wait for the owner
to approve the specific glyph set before freezing an approved snapshot or compiling.

## 5. Build and test after glyph approval

- Freeze approved outlines/metrics with compiler 1.0.1 and spacing policy v2.
- Build TTF, CFF OTF and WOFF2 privately. Run all required validator layers, printable
  ASCII coverage, cross-format contour/metric checks and byte-identical repeat builds.
- Use the existing Writing Desk font import for real preview, coverage and stationery
  rendering/export checks. Preserve the owner's existing page and use a separate test.
- Report separate-glyph fidelity and any unresolved spacing. Contextual cursive joins
  remain unsupported; the result cannot establish a general handwriting model.

## Verification and closeout

Run recipe syntax/static checks, canonical validation, mask round trips, parent/source
hash preservation and explicit ignored-file checks. Application code is unchanged, so
do not rerun unrelated application tests for private review preparation. Update the
local Cursive handoff and current status with measured results and remaining gates.
No model execution, training, account mutation, commit or push belongs to this package.
