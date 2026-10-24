# Phase 10 — Advanced realism and contextual writing

Status: **implemented locally on 2026-10-20 for one feature only; audited and repaired 2026-10-21** — measured pair kerning behind a bounded `spacing` control. Contextual positional forms, joins/ligatures, contour refinement and multi-sample fusion remain unimplemented and independently gated; see [evidence](evidence.md). Source: Part B M4, §8.5; Part A context-aware cursive/evidence-first decisions.  
Depends on validated phase 08 capabilities and phase 09 when an affected script requires it. This phase is outside the M1 launch requirement.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

Richer personal rhythm, authentic contextual forms, selected ligatures/joins and improved spacing, with reliable shaping and explicit user control. Each script/style feature ships independently after validation.

## Work packages

1. Define held-out text-run datasets and blinded visual comparisons against authentic handwriting. Establish which limitation is being improved: joins, contextual shape, spacing rhythm, repeated-letter realism or outline fidelity. Do not optimize only generic handwriting aesthetics.
2. Extend contextual glyph identity with explicit beginning/middle/end or script-appropriate context rules, source candidate/revision references and approved state. Keep base cmap separate from feature-only forms. Preserve existing schema versions or add a compatible new version with migrations.
3. Learn spacing/kerning measurements from authentic approved writing where evidence exists. Keep typography bounds and user overrides; classical rules remain an eligible baseline if they meet the same quality gate. Do not run mandatory per-user training.
4. Build selected liga/calt joins and positioning anchors from explicit tested mappings. Contextual substitution alone may not create joining geometry; evaluate shaping and visual continuity together. Script-specific behavior must not assume Latin beginning/middle/end rules apply universally.
5. Evaluate optional contour refinement/reconstruction under the accuracy-first policy. Preserve immutable originals and geometric tolerance. Output remains a proposed synthetic revision until explicitly approved; no hidden repair of active source shapes.
6. Map realism controls to deterministic, tested feature sets and amplitude bounds. Export static base-glyph fallbacks; document platform/app support for enabled features. The same snapshot/options/toolchain yields identical bytes and shaping.
7. Expand multi-sample fusion only where it reduces noise without blurring writer identity. Compare fusion against selecting the best authentic sample, label the method and retain all originals.
8. Integrate controls into the existing style/review adapters only after validated capability exists. Avoid promising a generator by changing preview CSS. Keep the Writing Desk's actual font rendering and document exports consistent with compiled feature support.

## Acceptance tests

- Held-out writer fidelity and human legibility improve on the accepted baseline without recognition/shaping regression. Report joining discontinuity, feature correctness, spacing/proportion errors and correction burden separately.
- Long runs, repeated letters, feature on/off, line/run boundaries, punctuation and mixed scripts shape deterministically without crashes or invalid advances.
- Contextual forms/ligatures cannot corrupt base coverage or substitute unrelated Unicode sequences. Missing forms use documented fallbacks with warnings.
- Generated/refined forms require explicit approval and survive model rollback without altering originals/snapshots. Schema compatibility and review race tests pass.
- Resource usage fits approved caps. Unavailable advanced capacity queues/defer work rather than lowering fidelity silently.
- Fonts pass phase 05 validation layers and representative cross-application rendering with features both enabled and disabled.

## Evidence, rollout and rollback

Deliver feature-specific model/algorithm cards, source lineage, text-run golden suites, human comparison reports, actual font artifacts and compatibility documentation. Promote each feature separately, not a blanket realism badge. Rollback disables new feature generation/pointers; previous exports remain immutable and users retain a static fallback.

### Local status on 2026-10-20

| Work package | Local state |
|---|---|
| 1 — feature evaluation | Text runs, before/after gap statistics, specimens and a summary are in artifacts/phase10 (`../../../artifacts/phase10`; local record). Synthetic only: no held-out writing, no human comparison. |
| 2 — contextual identity | **Open.** No source of positional role data exists yet; approved alternates keep lineage but no begin/middle/end role, so base forms are the documented fallback. |
| 3 — spacing/positioning | **Shipped.** Measured pair kerning within documented bounds ([ADR 0026](../../decisions/0026-phase-10-measured-spacing.md)). |
| 4 — contextual shaping features | **Open.** No justified `liga`/`calt` joins without join geometry; Phase 08 repeat cycling is unchanged. |
| 5 — contour refinement | **Open.** No algorithm justified by current evidence; the existing proposal/review gate is unchanged and no active glyph moved. |
| 6 — deterministic realism controls | **Shipped** for `spacing`: 0–100, defaults and ceilings documented, byte-identical output across order/process, static fallback asserted. |
| 7 — multi-sample fusion | **Open.** No eligible approved multi-sample pairs; no harness built rather than a fabricated comparison. |
| 8 — product integration | **Shipped** for `spacing` in the private export panel; Writing Desk still loads the real compiled font. |

## Decisions intentionally deferred

Concrete learning algorithms, eligible models, join inventory, amplitude bounds and quality thresholds depend on evidence from 08/09. This phase has no promised date, accuracy percentage or paid-compute requirement.
