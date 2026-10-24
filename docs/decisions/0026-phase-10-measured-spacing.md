# 0026 — Measured pair kerning from approved outlines

Status: accepted for local opt-in Phase 10. Date: 2026-10-20.

## Problem

Phase 08 made repeated letters vary, but spacing was still whatever each approved
glyph happened to carry. The visible result is uneven rhythm: gaps between letters
range from touching to visibly detached, and the widest gaps cluster in the same
words. Reducing that spread is a measurable, script-independent improvement that
needs no new data collection, because the writer's own approved outlines already
contain the evidence.

## Decision

Derive `GPOS` pair kerning from the approved base outlines and gate it behind a new
`spacing` option (`0–100`, default `0`). Policy `penforge-spacing-v1`:

1. Only approved `cmap` base glyphs with real ink take part; `space` and pending or
   alternate rows are excluded.
2. The target gap is the **median** natural gap over every ordered pair of those
   glyphs. A median resists one unusually tight or loose writing habit.
3. A pair is corrected only when its gap differs from the target by more than 2% of
   the em. The shift is clamped to 2% of the em scaled by the control, so the ceiling
   is 10 units at `spacing 50` and 20 units at `spacing 100` on a 1000-unit em.
4. A correction may not produce a non-positive advance, nor remove more than half of
   the first glyph's own advance.
5. At most 4096 rules are emitted, chosen by largest absolute correction and then by
   codepoint order, so output is independent of row order. Correcting everything
   beyond tolerance produced 4121 candidates on the control fixture, so the ceiling
   is a real bound and the remainder is reported as `uncorrected_outliers`.
6. Advances, side bearings, contour geometry and `hmtx` are never rewritten. Kerning
   is positioning, not metrics.

`spacing 0` emits no `GPOS` at all, so existing exports and manifests are unchanged.

## Why kerning rather than recalculating advances

`advance_width` and `left_side_bearing` are part of an approved revision and are
verified against the compiled `hmtx` (Phase 05 structural layer). Rewriting them to
improve rhythm would invalidate that lineage and make a stored revision disagree with
its own compiled font. Pair positioning changes layout without touching the approved
artifact, and it survives the same immutable snapshot/export rules.

## Evidence and its limits

The measurement is geometric: ink gaps in font units, reported before and after as
count, median, standard deviation, minimum, maximum, collisions and disconnections.
On the synthetic control fixture the population standard deviation falls from 12.614
to 10.945 at `spacing 50` and 10.096 at `spacing 100`; the rendered runs fall from
11.249 to 10.31 and 10.538. Run-level numbers are not monotonic in the control because
they measure a subset around its own mean, so only the population figure is used for
the claim.

Nothing here measures legibility, correction burden or resemblance to the writer's
hand. Those need held-out authentic writing and human review, which Phase 10 did not
have. The control fixture is authored synthetic geometry; it validates engineering
behaviour, not handwriting quality.

## Compatibility and rollback

`kern` is supported by HarfBuzz, CoreText and DirectWrite, and is enabled by default in
horizontal text. Applications that ignore `kern` fall back to the unchanged base
metrics, which the validator asserts pair-by-pair. Setting `spacing` back to `0`
produces a font without `GPOS`, byte-identical to the pre-Phase-10 output; existing
exports are never rewritten.

Devanagari is untouched: its positioning belongs to the Phase 09 `dev2`/`DFLT`
generation, and the compiler skips Latin kerning for that script.


## 2026-10-21 audit amendment

The shipped policy is now `penforge-spacing-v2`, with all ordered pairs including repeats,
no newly introduced negative gap, a single feature build for `kern` + `calt`, and validation
against actual partial coverage and alternate states. The current [evidence](../backend-ml-infra/phase-10-advanced-realism/evidence.md)
supersedes the original statistics above. Compiler `1.0.1` is pinned in the existing
`tool_versions` dictionary, so repaired output has a new manifest/build identity. Old
exports are never rewritten. Omitting spacing disables its feature; it no longer promises
pre-repair font bytes because versioned name/build metadata changes. Old queued toolchains
fail explicitly instead of silently producing changed bytes under an old build ID.
