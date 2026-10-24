# Penforge research foundations

**Current English-letter evidence (2026-10-22):** the owner completed R5 training and
the single approved final recovery. Independent ID-joined rescoring verified
**1,332/1,664 (80.05%) exact** across 418 writers, **93.45%** case-insensitive shape
accuracy, and 332 errors (223 case-only / 109 other). The original interrupted attempt,
both claims and all execution evidence remain unchanged; no R5 retry is pending.
See R5 closeout (`ENGLISH_R5.md`; local record) and R6 preparation (`ENGLISH_R6.md`; local record). R6 uses the same
training/validation split; no eligible untouched final inventory remains under the
existing coverage rule. The corrected R6 package passed **260 English tests** before
the owner completed its bounded two-pass run. The frozen validation winner scored
**1,364/1,664 (81.97%)**, versus the unchanged R5 validation control at
**1,349/1,664 (81.07%)**. Independent data-only verification reproduced the scores,
hashes and winner. No fresh final test is available, so this is validation-only
research evidence and no product model is qualified. Agents prepare/audit and use
inert/synthetic tests only; real
model training and inference remain owner-run. No product model, sentence OCR or
personal-font fidelity is qualified. The owner is preparing volunteer collection using
the pilot handout (`ENGLISH_VOLUNTEER_HANDOUT.md`; local record); no volunteer dataset has been admitted
and no volunteer-data training or evaluation is authorized by that preparation.

**Historical R3 preparation checkpoint:** English uppercase/lowercase letters and handwritten words first.
All three owner-downloaded NIST/CVL ZIPs pass pinned source checks. The actual NIST
letter study contains 2,080/104/260 writer-separated images with all 52 case-sensitive
letters in each split. The actual CVL word study contains 1,344/96/256 writer-separated
crops, excludes German passage 6, and exact-matches all 99,904 labeled crops to XML.
Private staging passed image/mapping integrity checks. A bounded duplicate search and
spot review found no candidate-pool near duplicates; this does not establish writer
identity or exhaustive label correctness. The CVL license version differs between its
bundled README and Zenodo metadata. NIST source-specific terms, consent/provenance,
global writer aliases and corpus admissions remain unresolved. No English model has
run and no English accuracy is measured. See R3 (`ENGLISH_R3.md`; local record), the
approved plan (`ENGLISH_R3_PLAN.md`; local record), owner scripts (`ENGLISH_R3_SCRIPTS.md`; local record) and
CVL source audit (`CVL_SOURCE_REVIEW.md`; local record).

The separate English launchers remain **owner-run only**. Their preflight stops before
model loading until reviewed admission files exist. Raw archives, prepared images and
private review sheets stay outside Git. R2's training command still targets Hindi.

**Prepared Hindi experiment:** pretrained TrOCR R2 (`PRETRAINED_R2.md`; local record), approved for
preparation only. Exact model pins, Hindi text checks and owner-run baseline/fine-tune/
test commands are supplied. The full 1.33 GB model and durable admitted data are
downloaded/prepared and checksum verified. The owner completed an R2 pilot: baseline
and adapted models each recognized 0/64 validation words exactly, with worse character
errors after adaptation. An audited target-alignment bug compromised v1 training.
The corrected v2 objective is prepared; its quality remains unmeasured. The Hindi
command remains available in that guide, but English preparation supersedes its rerun
as the next task. R1 below is independent historical evidence.

**Historical R1 handoff (2026-10-21): local research R1 (`LOCAL_RESEARCH_R1.md`; local record).** Five archives
were fully verified; only a bounded writer-disjoint IIIT-HW-Dev study is admitted.
The audit now has **9 dataset records, 1 bounded research-approved dataset, 5 measured
benchmark records and 4 completed lifecycle stages**, representing **one** small ridge
fit completed before the latest execution restriction. Recognition generalization was
poor; no personal-font generator, qualified product model or deployment resulted.

**Training is owner-run only.** Agents prepare/audit data and code, validate metadata
and inspect saved results; they must not launch training/fitting scripts or automated
training. Use the handoff's read-only checker and manual command. The retained
observations (`results/local-r1/result.json`; local record) and
lineage (`results/local-r1/lineage.json`; local record) preserve failures and limits.

Phase 00 established enforceable metadata and reproducibility rules without executing ML. Before R1, the expanded inventory had **8 dataset records, none approved, 0 measured handwriting benchmarks and 0 completed handwriting experiments**. Phase 04 separately records measured [synthetic extraction and analytic geometry controls](../docs/backend-ml-infra/phase-04-extraction-review/evidence.md); those remain separate from authentic-writer measurements. Eighteen fake writer identifiers exercise metadata checks; they are not handwriting samples or human participants.

The authority is [backend v3](../docs/specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md), especially Part A §§8, 13–14 and Part B §§8.6, 8.9, 8.11. The engineering work follows [Phase 00](../docs/backend-ml-infra/phase-00-foundations/plan.md); local extraction is recorded in [Phase 04](../docs/backend-ml-infra/phase-04-extraction-review/plan.md). [Phase 08 adaptation](../docs/backend-ml-infra/phase-08-personalization/evidence.md) now includes deterministic measurement, authentic contextual fonts and gated registry/evaluation/planning infrastructure. [Phase 09 Devanagari](../docs/backend-ml-infra/phase-09-devanagari/plan.md) now has a local Unicode/shaping implementation and engineering-fixture evidence; handwriting recognition and production remain gated in its [evidence](../docs/backend-ml-infra/phase-09-devanagari/evidence.md).

Phase 08 adds pure metric/report runners in evaluation/harness.py and evaluation/run.py,
using the existing BenchmarkRecord and admission validators. Conditions and evidence
domains are explicit. Missing metric observations do not increase that metric's sample
size; human metrics need actual blinded review. Synthetic style/font resource controls
are retained separately in artifacts/phase08, not promoted to handwriting benchmarks.
At the Phase 08 checkpoint, no external data/model or training run existed, and its
audit reported zero approved datasets/measured ML benchmarks/completed experiments.
The later R1 work and current owner-only execution rule are recorded above.

## Run the checks

From the repository root, using the backend's pinned environment:

```sh
.venv/bin/python -m research.validate
.venv/bin/python -m research.schemas --check
.venv/bin/python -m pytest -q tests/backend/test_research.py
```

After changing `research/models.py`, regenerate with `.venv/bin/python -m research.schemas`. JSON Schemas are versioned under [schemas/v1](schemas/v1/). `python -m research.validate` reads local metadata only; it does not download sources, accept dataset terms, execute a recorded command or load model artifacts. Failures return a content-free message so private fields cannot enter CI logs.

`*.yaml` records use the JSON-compatible subset of YAML 1.2. This permits deterministic parsing with the standard library and avoids YAML tags or implicit scalar coercion. Dates are ISO strings; unsupported versions and extra fields fail validation. Research artifact references hash canonical UTF-8 JSON with sorted object keys, compact separators, unescaped Unicode and rejected nonfinite numbers. The whole normalized research record is hashed; raw source files use their byte SHA-256. This scheme is separate from font-outline canonical serialization. Dataset references use `dataset_id:version` plus a digest; experiment/benchmark references use `record_id` plus a digest.

## Data admission and splits

[Dataset contracts](models.py), [manifests](manifests/) and the global writer registry (`manifests/writers-v1.yaml`; local record) retain provenance, source/license review, independently reviewed consent, script/style, sample/ancestor hashes, duplicate groups, writer groups, version and split. Dataset source bytes and model weights belong in separately controlled private storage, outside the repository. No personal names, contact data, handwriting text or raw imagery belong in this registry.

Resolve local writer identifiers to one globally pseudonymous writer group across every dataset before admission. A curator must establish alias evidence; hashing images cannot identify a person. Unknown writers stay quarantined. A writer's crops, pages, scripts and styles share one split. All weights and variants from one synthetic font family share one surrogate writer group. The same exact hash, recorded near-duplicate group, source group or ancestor hash cannot cross train/validation/test boundaries, even under different writer IDs or datasets. Quarantined records are excluded from split counts and rejected by the build admission check.

`validate_datasets` checks these constraints globally. `assert_build_eligible` additionally requires approved manifests and explicit permission for the intended use; metadata fixtures, planned synthetic sources, pending licenses and unresolved/withdrawn consent cannot enter a real build. `validate_dataset_references` prevents measured benchmarks from presenting metadata fixtures as real handwriting data. Withdrawal blocks future inclusion; it does not promise to remove influence from existing model weights. Consent receipts and identity-resolution evidence remain private; committed references use pseudonymous IDs.

The validator detects declared aliases and duplicates. R1 adds exact/normalized-exact
discovery across the inspected corpora and bounded near-duplicate checks inside its
admitted subset. Independent identity adjudication, signed consent verification and
global non-exact cross-corpus discovery remain incomplete. Frozen models prevent
attribute reassignment but do not make nested Python dictionaries a secure immutable
store. Versioned files/content references preserve lineage; production storage and
backend approval enforce immutability later.

## Initial source inventory checked on 2026-10-19, extended 2026-10-20

The following table preserves discovery-stage findings. Current archive verification,
bounded admission, exclusions and observed results are in the R1 handoff above; old
download/unopened/zero-result statements below describe the earlier checkpoint.

| Source               | Evidence recorded                                                                                                                                   | Admission status                                                                                                                           |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Existing Caveat font | Actual [font](../public/fonts/CaveatVariable.woff2) and [bundled OFL 1.1](../public/fonts/OFL-Caveat.txt) inspected and byte-hashed                 | Development/evaluation synthetic-source inventory only; no rendered corpus, training grant or evaluation run                               |
| IAM                  | [Official database portal](https://fki.tic.heia-fr.ch/databases) describes English handwriting and noncommercial research access                    | Candidate only; full use terms, product scope, contributor provenance and global writer identity remain gates                              |
| DHCD                 | [UCI repository page](https://archive.ics.uci.edu/dataset/389/devanagari+handwritten+character+dataset) lists CC BY 4.0 for isolated character data | Candidate only; complete review, consent/provenance and usable writer IDs remain gates; published split is not accepted as writer-disjoint |
| Noto Sans Devanagari | Pinned [Google Fonts source commit](https://github.com/google/fonts/tree/2bc5d431b584d89e646214bf6cfa75494e76ceea/ofl/notosansdevanagari), OFL 1.1 text and byte hashes in [Phase 09 evidence](../docs/backend-ml-infra/phase-09-devanagari/evidence.md) | Licensed reference-font engineering fixture only; not handwriting, a dataset or a recognition benchmark |
| [IIIT-HW-Dev](manifests/iiit-hw-dev-v1.yaml) | CVIT archive metadata; no public licence text is identified in the source listing | Candidate only; authorization and redistribution scope are not established in this public record |
| [IIIT-Indic-HW-UC](manifests/iiit-indic-hw-uc-v1.yaml) | CVIT archive metadata; no public licence text is identified in the source listing | Candidate only; authorization, archive structure and writers remain unverified |
| [IIIT-INDIC-HW-WORDS](manifests/iiit-indic-hw-words-v1.yaml) | CVIT source and a public mirror without a named licence or writer field | Candidate only; authorization and redistribution scope are not established |
| [DohaScript](manifests/dohascript-v1.yaml) | The paper describes academic/research-only participant consent; a public data licence is not established | Candidate only; no PenForge training or production use is asserted |

No dataset archives, handwriting bytes, writer identity records, or private permission documents are included. Public source URLs are research leads only; they do not grant PenForge permission to access, train on, generate fonts from, or redistribute the data. The fetch tools verify transport hashes only and confer no dataset rights or admission. Any future use requires current, reviewable authorization and the applicable participant-consent scope. Source visibility is not approval, and institutional permission is not participant consent. Caveat has one font-family surrogate and cannot by itself form three disjoint writer sets. Synthetic font renders can support exact template ground truth later, but cannot prove real handwriting fidelity or camera robustness.

## Research path and gates

[Evaluation protocol](evaluation/PROTOCOL.md) preserves dataset research → baseline comparisons → justified training/fine-tuning when needed → untouched held-out evaluation → candidate model registration → staged deployment → monitoring and rollback. The original [experiment records](experiments/) remain planned; separate `r1-*` records contain actual observations and linked completed stages. Training is not a product runtime dependency and execution belongs to the owner. Completed stages pin code, environment, config, seed, output hashes and parents; baseline comparisons require multiple pinned methods. Lifecycle validation checks evidence, not scientific/release approval or model loading.

Latin print, cursive and mixed writing remain separate partitions, as do Devanagari print, cursive and mixed writing. No chosen model, winning architecture, calibration text, ranking weights, numeric quality thresholds, GPU requirement, memory budget, ETA or hosting cost has been established. Recognition, outline fidelity, personal writing fidelity and standards-based shaping require independent evidence.

The [Devanagari checklist](evaluation/DEVANAGARI.md) and [starter corpus](evaluation/corpora/deva-starter-v1.yaml) retain source-derived regression sequences and remain marked unevaluated as a research corpus. A separate HarfBuzz engineering trace (`../artifacts/phase09/shaping-fixture.json`; local record) records local test outputs against the pinned OFL reference font. It supplies no common-word inventory or handwriting evidence. Production Devanagari remains gated on licensed writer-disjoint data, corpus-derived inventory, real recognition/fidelity evaluation and human-reviewed font/render evidence.
