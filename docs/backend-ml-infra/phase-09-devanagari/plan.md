# Phase 09 — Devanagari recognition boundaries and OpenType shaping

Status: Implemented locally with script and shaping evidence. Recognition quality, corpus-derived word coverage, physical printing, human font review, responsive browser acceptance and production promotion remain gated. Source: Part B §8.9 and Part A ML-26/27/30. The user explicitly authorized this phase; this does not authorize Phase 10 or later work. See [evidence](evidence.md) and [local run/rollback notes](development.md).

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md), and project handoff (`../../../PROJECT_STATUS.md`; local record). Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) in product integrations.

## Outcome and limits

Penforge now preserves Devanagari source scalars and grapheme boundaries, offers a versioned first-tier capture/print inventory, carries owner-reviewed Indic geometry evidence into the existing font compiler, shapes through HarfBuzz, and reports script-specific coverage and fallback. The feature is experimental and opt-in. No handwriting recognizer is included or qualified. A Noto Sans Devanagari OFL reference is used only as a labeled engineering fixture to exercise feature generation, output validation and shaping; it is not Penforge handwriting or evidence of recognition/fidelity.

## Delivered work

1. Pinned Unicode 17.0.0 data, UAX #29 grapheme segmentation and derived normalization tables with source hashes and license. Python and TypeScript agree on scalar sequences, grapheme clusters, normalization evidence and sequence hashes. Source input remains unchanged; normalization is recorded only as derived evidence.
2. Derived the bounded `deva-tier1-ucd17-v1` inventory from Unicode properties. It covers 121 Devanagari scalars plus 33 specified interoperability punctuation/space entries. Exclusions are explicit. Tier 2 and a corpus-frequency-derived common conjunct/word list remain unavailable.
3. Added a versioned three-page A4 printable Devanagari calibration template. Each label is shaped; pages retain geometry markers, QR payload and manifest hashes. The local render check passed. Physical print and camera capture remain untested.
4. Added script-specific capture validation, grapheme-aware Writing Desk layout and limits, owner assignment/review support, localized prompts and explicit missing-shape/fallback messages. Unsupported or malformed sequences remain pending/rejected by policy; no code guesses the writer's glyph. No automatic owner approval is added.
5. Added `IndicEvidence` for role, geometry hash, anchors and contextual substitution provenance. The font builder requires approved matching evidence and fails closed on stale geometry, unsupported generated forms and invalid sequence/metric data.
6. Implemented `DFLT` and `dev2` feature systems. The engine emits the reviewed subset of `ccmp`, `nukt`, `akhn`, `rphf`, `rkrf`, `blwf`, `half`, `cjct`, `pres`, `abvs`, `blws`, `psts`, `haln`, `abvm`, and `blwm` when supported by approved source evidence. HarfBuzz performs Indic analysis/reordering. The policy does not emit the obsolete `vatu` or `pstf` targets. Source reconciliation is recorded in [ADR 0023](../../decisions/0023-phase-09-unicode-and-shaping.md), [ADR 0024](../../decisions/0024-phase-09-tier-one-and-templates.md), and [ADR 0025](../../decisions/0025-phase-09-forms-fallback-and-gates.md).
7. Added an 11-sequence shaping harness with scalar-indexed clusters, exact source/derived-normalization records, enabled/disabled feature traces, `.notdef` checks, advances, offsets, visible-mark evidence and deterministic output hashes. The traces pass for the pinned OFL engineering font and tool versions. A missing conjunct uses an explicitly reported half-form or visible-virama fallback, or remains unsupported.
8. Integrated the set ID into cloud projects, capture, jobs, coverage, reviewed outlines, snapshots, compile manifests, private export and Writing Desk handoff. Local Latin, imported-font and legacy migration behavior is covered by regression tests. Phase 09 adds migration `0012_devanagari`; application table count remains 27.
9. Added an explicit future production corpus gate. Synthetic or Noto-derived fixture evidence cannot satisfy writer-disjoint dataset, recognition, writer fidelity, language frequency or human-review requirements.

## Acceptance and remaining gates

The exact local evidence, commands and results are in [evidence](evidence.md). The Phase 09 backend-focused tests pass, the TypeScript suite passes, contracts/research validation, lint/type checks and production build pass. A broader backend run had one intermittent PostgreSQL `Interrupted system call` in an existing queue fairness case after 601 passes; the same test passed on isolated retry. The focused Phase 09 rerun passed all 57 tests.

The starter sequences are regression examples, not corpus-derived language coverage. The checked-in research starter record therefore remains marked unevaluated and has no production word inventory. Penforge has 0 approved handwriting datasets, 0 measured handwriting benchmarks and 0 completed handwriting experiments. No recognition model, real-writer fidelity score, review-effort measure, or user handwriting font has been produced by this phase.

Responsive visual/keyboard acceptance could not be completed because local browser control is disabled in the current Work settings. The previous Phase 03/04 current-wizard checks, Phase 05 OS-gentle check, Phase 06 browser checks, Phase 07 OS-gentle and final account-deletion browser interaction, and Phase 08 OS-gentle/physical-touch checks remain pending as recorded in PROJECT_STATUS.md (`../../../PROJECT_STATUS.md`; local record). The Phase 09 physical print, human review of Devanagari typography, and cross-platform renderer matrix are also pending.

## Rollback and open gates

Disable the Devanagari capability for new work while retaining immutable approved outlines and exports. Migration `0012_devanagari` removes its project field on downgrade only when no project requires the Devanagari set; it does not erase earlier Latin data. Keep recognition, writer fidelity, corpus-derived Tier 2, numeric promotion thresholds, production readiness, provider provisioning and deployment disabled pending separate authorization and evidence. Stop before Phase 10.
