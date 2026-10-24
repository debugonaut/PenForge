# Phase 04 — Structured Latin extraction, review and coverage

Status: implemented locally with automated acceptance; live browser checks remain pending at the user's choice. See [actual evidence and limitations](evidence.md) and [development instructions](development.md). Source: Part B S4, §§6.6, 8.4–8.5; Part A IC-01/03/04/05 and calibration.  
Depends on phase 03. Unlocks phase 05. Natural/freeform cursive remains experimental until independently validated.

2026-10-24 follow-up: the separately approved [fixed-brush canvas package](../../canvas-font/evidence.md)
is implemented with versioned round-curve conversion and scoped responsive/keyboard
checks. It does not close the earlier full-phase browser, physical-device, handwriting
quality or hosted gates below.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

A pinned printable/on-screen Latin template becomes authentic editable glyph candidates with source evidence. Users can select, correct and approve them; coverage identifies precisely what a partial font will contain.

## Work packages

1. Finalize a versioned template manifest after printability tests: public template/version/page QR only, marker geometry and ordering, quiet zones, physical units, extraction cells/line regions, guides and Unicode mapping. Generator and extractor consume the same archived manifest/hash. Produce PDF/template metadata without private IDs or credentials. No guessed marker coordinates are promoted from the illustrative JSON.
2. Build a synthetic template generator from verified OFL fonts with exact ground truth, controlled rotation/perspective/blur/noise/shadows/scale and blank/missing cells. Pin font/license/dataset/config hashes. Split evaluation by source writer/font and document synthetic-domain limitations; collect separately authorized representative real captures for production validation.
3. Implement quality assessment, orientation/perspective correction, illumination normalization, dropout-guide removal, safe thresholding and reassessment. Record measurements independently. Derive quality gates from baseline runs; return actionable too_blurry/low_resolution/page_not_found/etc. `needs_better_input` is a successful assessment result with retake reasons.
4. Structured extraction detects pinned markers, rectifies page geometry and crops authoritative cells. Detect border contact, components, faint/clipped ink and empty cells. Known expected characters are context, not proof that the user wrote them. Keep ambiguous crop evidence private and flag review; never confidently assign a wrong shape because the manifest expects it.
5. Add a swappable vectorizer using compatible contour tracing/curve fitting and a mandatory M1 digital-drawing Track C. Track C consumes the versioned normalized stroke document, bypasses image quality/CV segmentation, resamples non-destructively and derives canonical outlines with an explicit capture-to-font transform. Preserve observed pressure/order/timing without fabricating absent metadata. Preserve natural proportions with shared baseline/x-height normalization, holes, winding and descenders. Keep originals immutable. Record algorithm/tolerance/config versions. Potrace is not bundled until its license decision is recorded.
6. Register immutable result manifests and child outlines through the API under valid job/attempt fencing. Validate owner, source/template/model hashes, schema, child sizes and content before candidate registration. Workers cannot write active revisions. Distinguish authentic extraction, manual drawing, reconstruction and generation; unmeasured evidence scores remain null.
7. Implement stable ranked candidates and alternatives. Recommendation is strongest validated authentic evidence, not an approved active glyph. User selection persists across retries/model upgrades. Capture selected candidate provenance in a new immutable revision; synthetic candidates always require explicit approval.
8. Implement glyph GET/list, candidates, select-candidate and v2 PATCH actions (edit/approve/reject/rollback), preserving base_revision_id and ETag semantics. Same outline+metrics hash suppresses autosave noise. Rollback creates a new revision. DB guards reject cross-glyph revisions and pending/generated activation without owner approval.
9. Implement consistent milestone snapshots in repeatable-read transactions. Freeze glyph revision mapping, style/profile/model versions and manifest hash. Implement Latin core 52 and printable ASCII 95 coverage, excluding .notdef from counts and handling space by metrics. Distinguish observed/recommended/approved/generated-pending/missing/excluded.
10. Add calibration definitions and targeted completion endpoints/contracts. Preferred natural passage is 2–3 sentences followed by informative follow-ups, but initial text is an unoptimized example until evaluated. Ask isolated characters when segmentation is uncertain. Never imply arbitrary connected words can already be split accurately.
11. Connect existing review/editor controls with minimal adapter changes. Prototype transforms must have a tested canonical coordinate mapping. Unsupported destructive/geometry controls remain unavailable rather than silently becoming fake backend edits.

## Verification and acceptance

- Distorted/rotated synthetic sheets map to correct pinned codepoints. Wrong version/hash/page/marker ordering fails with recovery guidance.
- Golden extraction reports cell hit rate, IoU/boundary/false-glyph errors, vector/raster fidelity, correction burden and stage resources by input class. Record baseline and then thresholds, with no invented production accuracy.
- Canonical round trips preserve curves, holes, multiple contours and descenders within declared tolerance; NaN/invalid/open/excessive geometry is rejected. Stroke originals and optional metadata remain intact.
- An end-to-end digital capture fixture passes normalized strokes → immutable source → canonical outline candidate → explicit review → snapshot, without image CV or an OCR dependency. Mouse captures without pressure and pen captures with observed metadata both preserve provenance and source hash.
- Every candidate/result/revision endpoint is owner-scoped; forged child hashes, stale attempts, deletion races and incompatible models cannot register/promote.
- Approval persists after retries and new result sets. Synthetic candidate cannot become active through API or direct DB updates without approved metadata and explicit transition.
- Concurrent edit conflicts, autosave suppression, rollback and immutable snapshots pass. Snapshot-referenced revisions cannot be pruned.
- A 52-letter project truthfully reports missing ASCII characters; complete status requires all 95 eligible mapped codepoints and later export validation.

## Evidence, rollback and gates

Deliver versioned templates, synthetic fixtures, real-capture evaluation provenance, quality reports, canonical adapter fixtures, migration/tests and updated contracts. Production capability remains gated if only synthetic performance is known. Roll back an extractor/model pointer without altering approved revisions; retain immutable result siblings for comparison. Devanagari research continues, but Latin test success does not validate it.
