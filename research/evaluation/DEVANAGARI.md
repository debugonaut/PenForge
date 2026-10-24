# Devanagari research checklist v1

**Later checkpoint (2026-10-21):** R1 (`../LOCAL_RESEARCH_R1.md`; local record) admitted one bounded
IIIT-HW-Dev subset and measured a failed recognition pilot plus weak geometry/reuse
controls. The audit now has nine datasets, one admitted research subset, five measured
benchmarks and four completed stages for one fitted model. R2 (`../PRETRAINED_R2.md`; local record)
prepares a pretrained experiment for owner execution; no TrOCR accuracy exists.
Zero-result statements below preserve the historical Phase 09 boundary. No model
is qualified for the product.

Source: [backend v3 Part B §8.9](../../docs/specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md) and [Phase 09](../../docs/backend-ml-infra/phase-09-devanagari/plan.md). Phase 09 adds engineering contracts and an experimental shaping path; product/production promotion remains gated.

- Complete use/license, contributor-consent and writer-ID review before any handwriting corpus admission. The [DHCD candidate record](../manifests/dhcd-candidate-v1.yaml) remains unresolved isolated-character source metadata, not a shaping corpus or approved benchmark. Phase 09 downloaded and approved no handwriting dataset. Later, on 2026-10-20, the owner supplied written permission from CVIT and four candidate records were added ([IIIT-HW-Dev](../manifests/iiit-hw-dev-v1.yaml), [IIIT-Indic-HW-UC](../manifests/iiit-indic-hw-uc-v1.yaml), [IIIT-INDIC-HW-WORDS](../manifests/iiit-indic-hw-words-v1.yaml), [DohaScript](../manifests/dohascript-v1.yaml)); all remain `candidate`, so no dataset is approved and no build is admitted.
- Preserve writer-disjoint print/cursive/mixed recognition, segmentation and held-out writer-fidelity partitions for later licensed evaluation. Preserve source codepoints and raw captures; any normalization creates explicit derived evidence.
- Derive common conjunct inventory and a 200–500 word regression corpus only from a licensed source with recorded corpus/version/hash, license, tokenization/normalization, sampling method and frequency statistics. The Tier 1 Unicode inventory does not determine Tier 2 or language frequency.
- Keep recognition/glyph-shape generation separate from standards-based shaping. OCR scores and raw character counts cannot pass cluster, matra, reph, conjunct or mark-positioning gates.
- Use the engine's script systems and GSUB/GPOS rules from the primary OpenType and HarfBuzz sources recorded in [ADR 0023](../../docs/decisions/0023-phase-09-unicode-and-shaping.md). HarfBuzz performs Indic analysis/reordering; model output does not invent shaping behavior.
- The source-derived [starter corpus](corpora/deva-starter-v1.yaml) remains `starter_not_evaluated`, with no golden outputs or common-word inventory. The separate engineering shaping trace (`../../artifacts/phase09/shaping-fixture.json`; local record) covers its 11 examples against the pinned OFL Noto engineering font and fixed local toolchain. Its passing result verifies code and fixture compatibility only; it is not human review or handwriting evaluation.
- Future human review must assess visible marks and contextual forms independently from recognition and writer fidelity. Exercise fallback, `.notdef`, normalization/nukta comparisons, source clusters, advances/offsets and real TTF/OTF/WOFF2 output across representative renderers.
- Keep Tier 2, handwriting capability, numeric acceptance thresholds, corpus inventory, writer-fidelity claims and production status unavailable until their independent evidence is admitted and reviewed.

The local research audit remains metadata-only: **8 dataset records, 0 of them approved, plus 0 measured handwriting benchmarks and 0 completed handwriting experiments**. Permission to use a corpus is not a measured capability. The Noto Sans Devanagari bytes and OFL 1.1 text are pinned under `research/fixtures/fonts/noto-deva`; that font is a redistributable engineering reference, not handwriting or a corpus. See [Phase 09 evidence](../../docs/backend-ml-infra/phase-09-devanagari/evidence.md) for the complete boundary and remaining gates.
