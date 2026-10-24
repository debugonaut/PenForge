# Phase 08 — Personal style, alternates and gated advanced processing

Status: implemented locally on 2026-10-20 within the explicit Phase 08 authorization. Deterministic measurement/authentic alternates are opt-in; advanced paths remain research-only or disabled. No production handwriting capability was qualified. See [actual evidence](evidence.md) and [run instructions](development.md), including remaining browser/OS and data gates. Source: Part B S7/M2, §§8.5–8.6, 8.10–8.11; Part A CAL-01–03, ML-26–32.  
Depends on M1 phases 00–07. Dataset/evaluation preparation starts in 00. Each capability has an independent promotion gate.

2026-10-24 continuation: the owner's clarified natural word/sentence flow is now
tracked in the [freeform implementation sequence](../../freeform/plan.md). Its first
[evidence/source-binding package](../../freeform/evidence.md) is local preparation;
it does not qualify a model, enable normal freeform processing or complete this
phase's recognition/alignment/fidelity gates. The old synthetic connected-region
experiment and isolated-letter model scores remain separate evidence.

Read the complete [v3 source specification](../../specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), [phase index](../plan.md) and project handoff (`../../../PROJECT_STATUS.md`; local record) before implementation. Preserve the accepted [design](../../design-language.md) and [motion](../../motion-design.md) when integrating the frontend.

## Outcome

Fonts retain more of a person's measured writing habits, with authentic alternatives and efficient follow-up captures. Natural handwriting and synthetic candidates become available only where evidence supports their quality.

## Work packages

1. Derive versioned project style profiles from approved references: slant, stroke width, proportions, baselines, widths, spacing and rhythm. Keep measurements separate from unvalidated style embeddings. Immediate adaptation reuses authentic data first; per-user fine-tuning is research only, not a runtime dependency.
2. Add model registry loading with approved/revoked states, artifact checksum/size, input/output schemas, script/style support, compatible worker version, license/provenance and measured resource profiles. Allow ONNX/safetensors/JSON rules only; reject executable/pickle formats and unsafe loader paths. Lazy cache with explicit eviction and integrity rechecks.
3. Build evaluation harnesses on licensed writer-disjoint datasets. Report recognition/CER/top-k/calibration, alignment/split-merge/segmentation, outline/raster error, held-out writer fidelity, blinded human comparisons, corrections per glyph, time to first useful font and resource cost separately. No single confidence score substitutes for these dimensions.
4. Implement script/style-aware routing among specialized interchangeable components. Select the highest qualified method for the input class; feasible scheduling does not authorize a weaker result. No qualified runtime → queued/unsupported/needs-intervention. Preserve hypotheses and evidence; a plausible word cannot overwrite observed geometry.
5. Optimize guided initial passages offline for coverage, repeat/context diversity and effort. Start with 2–3 sentences; adaptive follow-ups request informative words/sentences or isolated characters. Track expected versus observed coverage and mismatches. A prompted phrase remains alignment context, not ground truth.
6. Spike natural/freeform line/word alignment and cursive segmentation behind an experimental flag. Compare against structured fallback and human labels. Preserve uncertain connected regions; request isolated samples when reliable splitting is unavailable. Promote per script/style/input class only after agreed gates.
7. Add authentic alternate selection and a deterministic calt spike with HarfBuzz. Test N=2 and N>2 lookup order on eeeeeeee, hello wellness, run boundaries and mixed text. Features-off fallback must preserve base glyphs. Realism controls enable tested alternate sets, not random unstable outlines.
8. Optional geometric/style-conditioned generation is an explicit opt-in proposal path. Provenance identifies synthetic origin; evidence and quality dimensions are separate/null when unmeasured. Generated/reconstructed shapes cannot activate or enter authentic coverage without explicit owner approval. Immutable originals remain available; reprocessing creates sibling candidate sets.
9. Global learning uses separately consented reviewed contributions, dataset versions, training lineage, offline comparison, approved rollout and pointer rollback. Consent to make a personal font is never reused as training consent.

## API/data integration

Extend style profile, calibration/next, candidate alternatives/selection and model provenance contracts additively. Use the existing review/style UI and adapter boundary; avoid adding a second project workflow. Store context identity separately from base Unicode cmap identity. Advanced jobs advertise unsupported capability rather than returning fake generated previews.

## Acceptance tests

- Registry rejects wrong checksum/schema/version/script, revoked models and pickle artifacts. No arbitrary model path/URL execution.
- Writer-disjoint split validator detects leakage; license/consent manifests are complete. Separate print/cursive and per-script reports are mandatory.
- A faster model with worse writer fidelity is blocked from promotion even if OCR improves. Hardware-incompatible models wait or report unavailability without quality downgrade.
- Approved selections survive new models/retries. Concurrent generated-candidate approval and stale ETag tests protect active revisions.
- calt output is deterministic, covered characters remain correct, features-off fallback works, and adjacent-repeat invariants pass before activation. Document apps that do not enable contextual features.
- Adaptive prompts target actual missing/uncertain evidence; alignment mismatch cannot silently assign a glyph.
- Training withdrawal prevents new dataset inclusion; model rollout/rollback leaves old exports byte-identical.

## Evidence, rollout and rollback

Deliver model cards, license/data manifests, quality/resource reports with confidence intervals where applicable, human-review protocol and capability labels research_only/experimental_opt_in/production_validated/disabled. Stage model pointers per script/task; retain prior compatible versions. Rollback affects future recommendations only and cannot rewrite approved revisions or exports.

## Open gates

Concrete winning models, ranking weights, optimized passage text, numeric thresholds and advanced worker capacity are unselected. These are experiments with acceptance evidence, not promises of universally accurate cursive extraction.

The original packages above retain their source intent. The authorized implementation
does not obtain datasets, train models, optimize the default passage, or run real-writer
evaluation. It provides admission checks/reporters and a synthetic-only freeform spike.
No normal capture routes into that experiment. Global learning remains limited to the
existing consent/withdrawal governance; no training or rollout is performed. Phase 09
requires separate authorization.
