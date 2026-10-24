# Phase 10 evidence — measured spacing

Original date: 2026-10-20; corrected 2026-10-21 (Asia/Kolkata). Scope: local, opt-in, one feature shipped end to end. No external
dataset was obtained for this phase, no model was trained or fine-tuned, and no
handwriting-quality, legibility or human-approval claim is made. Phase 11 subsequently
proceeded; its launch work remains partial. See the [audit repairs](../phase-11-deployment-operations/audit-repairs.md).

## What shipped

**Measured pair kerning** (`GPOS` `kern`) derived from the writer's own approved base
outlines, controlled by a new bounded `spacing` option. This changes what the compiled
font does to real text; it is not a preview-only effect.

| File | Change |
|---|---|
| `packages/font-engine/penforge_font_engine/spacing.py` | New. Policy `penforge-spacing-v2`: ink-gap measurement, median target, tolerance, bounded shift, rule ceiling, `kern` emission, shift verification and gap statistics. |
| `packages/font-engine/penforge_font_engine/compiler.py` | Measures spacing for Latin builds, composes `kern` and Phase 08 `calt` in one feature compilation, reports a `spacing` block in coverage and the new warnings. Devanagari is skipped. |
| `packages/font-engine/penforge_font_engine/validation.py` | Adds the `measured_spacing` report to the HarfBuzz layer, with exactness, collision and population-variance invariants. |
| `packages/contracts/penforge_contracts/font_build.py` | New optional `FontOptions.spacing` (`0–100`, default `0`, omitted when zero). |
| `src/components/creation/CloudExportPanel.tsx` | Letter-spacing control in the private export panel, plus the recovery body carrying it. |
| `scripts/phase10_spacing_eval.py` | Reproducible evaluation that writes fonts, reports, specimens and a summary. |
| `tests/backend/test_measured_spacing.py` | 25 tests including spacing + N=2/N=3 alternates, B/C partial coverage with/without space, all three formats, repeated pairs, version identity and determinism. |
| `docs/decisions/0026-phase-10-measured-spacing.md` | The decision record. |

No migration was added. The contract change is an additive optional field, so v1
snapshots, manifests and existing exports keep their serialized form; `contracts-check`
reports `Retained v1 schema compatibility passed`.

### What it does in the actual font

At `spacing 0` the measured `kern` feature is not emitted. Above zero the compiler measures the ink gap of every ordered pair of approved
inked glyphs, takes the median as the target, and emits pair positioning for pairs that
sit more than 2% of the em away from it, clamped to 2% of the em scaled by the control.
Advances, side bearings and contours are never rewritten, because they belong to the
approved revision and are verified against `hmtx`.

## Feature-by-feature status

| Feature | Status | Evidence |
|---|---|---|
| Measured spacing / rhythm | **Shipped, end to end** | `GPOS` `kern` in compiled fonts, five validation layers pass, measurements below |
| Contextual positional forms (begin/middle/end) | **Not implemented** | The product still has no source of positional metadata: approved alternates carry `variant_index` and source lineage but no role. Inventing roles from geometry would fabricate the writer's intent, so the fallback is the documented base form and the existing Phase 08 repeat cycling. Recorded as a data requirement, not a defect. |
| Joins / ligatures | **Not implemented** | No approved join geometry exists in any fixture or product path; substitution without joining geometry would be a false claim. |
| Contour refinement / reconstruction | **Not integrated or qualified for production** | A separately authorized private W03 source-audit/polish experiment was prepared on 2026-10-24; see the note below. The existing proposal → pending revision → owner approval path remains the gate, and no active glyph, snapshot or export is altered. |
| Multi-sample fusion | **Not implemented** | No eligible approved multi-sample pairs exist locally; a synthetic fusion comparison would measure the fixture, not fusion. Left for a phase with real samples. |

## Original verification (2026-10-20; historical counts)

```sh
make lint                                            # ruff check + format, mypy, boundaries, docs
.venv/bin/python -m pytest -m 'not integration' -q   # backend
npm test                                             # frontend
make contracts-check                                 # export/compat/TS/research
npm run typecheck && npm run build
.venv/bin/python scripts/phase10_spacing_eval.py     # artifacts/phase10
```

| Check | Result |
|---|---|
| `pytest -m 'not integration'` | **405 passed**, 214 deselected |
| `npm test` | **118 pass**, 0 fail |
| `make lint` | All checks passed; mypy clean across 137 source files; 638 doc links across 87 files |
| `contracts-check` pieces | Artifacts match, retained v1 compatibility passed, generated TypeScript matches, research schemas/validate pass |
| `npm run typecheck` | passed |
| `npm run build` | passed |
| `test_measured_spacing.py` | **11 passed** |

### Phase 05 validation of an affected output

`validate_font` on the `spacing 100` TTF from the control fixture:

```
status: passed_with_warnings
  structural: passed   ots: passed   fontbakery: passed
  harfbuzz: passed     render: passed
warnings: ['kern_unsupported_in_some_apps']
coverage.spacing.corrected_pairs: 944
```

The warning is deliberate: it tells the user that an application ignoring `kern` shows
the unkerned base spacing, which the validator asserts pair by pair.

### Measured spacing results

From `artifacts/phase10/summary.json`, synthetic control geometry, evidence domain
`synthetic_control`:

| Control | Pairs | Amplitude | Max shift | Population σ before → after | Run σ before → after | Font bytes |
|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | disabled | 11.249 → 11.249 | 9,048 |
| 50 | 944 | 10 | 10 | 12.602 → 10.95 | 11.249 → 10.31 | 10,612 |
| 100 | 944 | 20 | 20 | 12.602 → 10.11 | 11.249 → 10.538 | 10,612 |

Population σ is the claim-bearing number: every correction moves a gap strictly toward
the median, so spread cannot grow. The run-level figure is reported but is not monotonic
in the control, because it describes a small subset around its own mean; no monotonic
claim is made from it. Collisions and disconnections are counted separately in the
report rather than folded into a score, and `legibility` and `handwriting_fidelity` are
`null` because nothing measured them.

Determinism: identical bytes across row order and across a separate process for TTF, OTF
and WOFF2 (`test_output_is_byte_identical_across_row_order_and_process`). Shifts are
verified against shaped output for every run pair; a mismatch raises
`spacing_contextual_shift_mismatch` instead of shipping.

## Remaining gaps and pending checks

- **No authentic held-out writing** was used: all numbers are engineering measurements
  on authored synthetic outlines. Handwriting-quality, legibility and correction-burden
  evidence remain unmeasured.
- **No human review** of the rendered specimens has been recorded; owner approval has
  not been given and is not implied by these artifacts.
- **Positional forms, joins/ligatures, contour refinement and fusion** are unimplemented
  as described above; each stays independently gated.
- **Browser inspection** of the new export control at 1440/768/390/320 with keyboard and
  gentle motion was **not executed** in this phase; local browser control is disabled in
  the current Work settings. This is pending, not passed.
- Earlier pending checks (Phase 03/04 wizard, Phase 05 OS-gentle, Phase 06 browser,
  Phase 07 OS-gentle/account deletion, Phase 08 OS-gentle/physical touch, Phase 09
  responsive visual/keyboard) remain pending. Phase 10 does not make them pass.

## Rollback and compatibility

- Set `spacing` to `0`, or omit it: no measured `kern` feature is emitted. Other
  positioning features may still exist. Zero-valued options remain omitted in v1 serialization.
- Applications that ignore `kern` render the base spacing; base coverage, glyph ids and
  advances are untouched, and no unrelated sequence can be substituted.
- Existing snapshots, exports and manifests are never rewritten; new options create new
  exports with their own manifest hash.
- Devanagari geometry/shaping policy is unchanged. It retains `DFLT`/`dev2`; the
  compiler version stamp changes build identity, including default builds.
- Compiler **1.0.1** is now pinned in `tool_versions`. Older queued manifests lacking
  that stamp fail `toolchain_mismatch`; create a new request under the new toolchain or
  run the retained compatible worker. Ready immutable exports remain downloadable.
- Policy v2 includes identical pairs and prevents newly introduced negative ink gaps.
  Base kerning never transfers to an unmeasured alternate: authentic alternate metrics
  remain intact. Validators check both `calt` states and every emitted rule.
- Regenerated summaries include real SHA-256 values, `gpos`, actual validation strings
  and separate specimen strings. The five-layer report was regenerated with the fonts.

## Current next work

The accepted measured-spacing scope is repaired locally. The final repair acceptance
is recorded [here](../phase-11-deployment-operations/audit-repairs.md). Complete the
retained browser/human review and genuine handwriting evaluation separately; none of
the unimplemented realism features is implied by this closeout.

## Separate private polish experiment — 2026-10-24

The owner authorized polishing the existing W03 draft and auditing every character,
specifically an incomplete `f`. The experimental [proposal module](../../../research/font_polish.py)
uses bounded subpixel reconstruction of reviewed image masks, with topology, sampled
area and boundary guards. The research evidence (`../../../research/PAGE_TO_FONT_POLISH.md`; local record)
separates source resegmentation from smoothing: geometric guards cannot establish that
the original mask contains the correct handwriting. All 92 drawn observations were
visually compared with source; 85 use new source masks and seven retain the prior
private polish. Space is unchanged; two missing ASCII marks remain excluded.

The focused geometry suite passed 40 tests; 184 canonical-normalization winding checks
passed at proposal closeout. The owner subsequently approved the shapes for a private
build. A separate polished font (`../../../research/PAGE_TO_FONT_POLISHED_BUILD.md`; local record)
now passes all five validator layers for TTF/CFF OTF/WOFF2, deterministic repeat builds,
native CoreText and browser checks. Historical pending proposal inventories are unchanged.
Human fidelity/legibility remain independently unmeasured and rule crossings stay
flagged. Original source, model, snapshot and export evidence is unchanged. This is
a private development experiment, not Phase 10 refinement acceptance, a model evaluation,
account integration or evidence of automatic ordinary-photo extraction. Earlier phase
gates remain open.
