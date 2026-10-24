# Live drawing to font — accepted implementation, 2026-10-24

Status: all four canvas work packages completed locally. See [actual evidence and
remaining gates](evidence.md). The owner subsequently clarified that this is a supporting
primitive: the primary goal is [freeform words/sentences to individually editable font
characters](../design-language.md#freeform-handwriting-to-font-direction--2026-10-24).
That automatic analysis engine and the separate photo import are not implemented here.

The owner approved recording live strokes, deriving faithful outlines, retaining common
font coordinates, explicit shape review and real font export. The existing account,
consent, immutable-source and approved-snapshot paths remain authoritative. No new
training, model execution, private-font rebuild, provider or public release is involved.

## Decision and scope

Use the existing fixed round canvas brush. Add a versioned analytic stroke-outline
converter using the already pinned Skia PathOps library. It expands polylines with
round caps/joins, unions overlaps, clips to the original artboard and converts conic
arcs to canonical quadratic curves within an explicit engineering tolerance. Pressure
and timing remain observed source metadata; fixed-brush geometry does not use pressure.

The alternatives were smoothing pixel-grid contours (retains raster quantisation) and
introducing pressure-sensitive drawing (changes the brush experience). Direct round
stroke geometry matches the accepted current brush. Keep legacy analysis v1 unchanged;
new explicitly requested Latin canvas conversion uses job v2. Old workers accepting
only payload v1 must not claim v2. Existing image paths and Devanagari remain unchanged.

1. Freeze scoped source preimages and legacy fixture hashes. Add v2 contracts and
   explicit dispatch/queue compatibility before connecting the algorithm.
2. Implement bounded curve geometry and strict provenance verification. Test dots,
   holes, crossing/overlapping strokes, pressure independence, clipping, source
   immutability, determinism and old converter compatibility on synthetic fixtures.
3. Connect the existing canvas/account/review client with a shared brush constant and
   a faithful captured-artboard preview. Preserve review/approval; no auto-activation.
4. Exercise capture → worker → review/approval → real font export with synthetic data.
   Run scoped checks and responsive/keyboard browser QA; record actual limits and
   update the checkpoint. Preserve all earlier hosted/research/motion gates.

No per-letter autoscaling, invented characters, cursive joining or typography-quality
claim is part of this package. Existing source and font packages remain frozen. The
separate reviewed-photo-to-account import remains a supporting product package;
the owner's later freeform-analysis direction determines the next priority.
