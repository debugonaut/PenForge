# Research and release evidence protocol v1

**Current English evidence (2026-10-22):** R5 closeout (`../ENGLISH_R5.md`; local record) records
completed owner-run training and the single approved final recovery: independently
verified 80.05% exact isolated-letter accuracy on 1,664 letters / 418 writers. The
original interruption, both claims and all evidence remain immutable; no R5 retry is
pending. R6 preparation (`../ENGLISH_R6.md`; local record) uses training/validation only and has no
eligible fresh final inventory at the existing coverage minimum. Agents do not run
real models. Earlier R1/R2 and phase-specific zero-result statements are historical;
no product capability is qualified. Exposed writers never become an untouched final.

Status: Phase 00 research/release protocol remains binding. Phase 04 adds separately reported synthetic engineering baselines and analytic digital controls. Phase 08 implements metric/report infrastructure and separate synthetic font/style resource measurements; no real-writer benchmark, trained model or production pipeline is admitted. Source: [v3 Part A §§8, 13–14 and Part B §§8.6, 8.11](../../docs/specifications/PENFORGE_CODEX_BACKEND_ML_INFRA_CONTEXT_v3.md).

## Reproducible organization

Store dataset/license/consent manifests in `research/manifests`, partitioned benchmarks in `research/evaluation/benchmarks`, and experiment records in `research/experiments`. A real run records an immutable code tree/commit reference, dependency lock hash, algorithm/preprocessing versions, parameter/config hash, random seed, command arguments, dataset manifest digests, split-registry digest, model checksum, input/output schemas, measured hardware/runtime and output artifact digests. Commands are recorded as argument arrays for review; the validator never executes them. Keep raw inputs, private transcriptions, annotations, consent receipts, checkpoints and weights in controlled external storage. No large training dependency is installed in the API environment.

Record each attempt separately; never revise a result in place to conceal failure. A new dataset, label correction, model, preprocessing rule, shaping feature or configuration creates a new version and explicit comparison. Fix evaluation seeds and freeze the test split before tuning; compute CIs with a declared method where applicable. Unit-test metadata success is not an ML experiment.

## Lifecycle gates

1. **Dataset research and admission.** Establish source permission for the particular intended use, license/attribution obligations, contributor privacy/consent, withdrawal lineage, writer identity and duplicate lineage. Curate script/style labels and capture-condition strata; document missing coverage. Obtain separately authorized real handwriting where public sources cannot meet these requirements. Reject unknown writer IDs rather than accepting a sample-level random split. Freeze hashes and train/validation/test allocation policy before comparison.
2. **Baseline comparison.** Compare compatible classical/CV and eligible pretrained methods on the same frozen writer-disjoint partitions and capture conditions. Pin at least two method/config artifacts; record failures and manual correction burden. Synthetic template render ground truth is useful for extraction regression but must be reported separately from authentic held-out handwriting. Do not select a faster method merely because it fits a resource budget. An unavailable qualified method leads to waiting or an honest unsupported state.
3. **Justified training or fine-tuning.** First diagnose baseline errors by dimension and partition. Document why deterministic processing, better capture or the pretrained baseline is insufficient. Admit training data only under its explicitly reviewed training scope and current contribution consent. Select a recipe only after this evidence; pin initial model, preprocessing/augmentations, optimizer/schedule, seed, stopping/selection policy, hardware and measured resource consumption. Use train/validation for tuning. Keep per-person fine-tuning research-only. If no training is justified, record that evidence and continue with the validated baseline.
4. **Held-out evaluation.** After selection, evaluate once on untouched test writers with no training/validation overlap. For same-writer fidelity, compare the test writer's calibration/reference captures with additional authentic held-out glyphs/text from that same test writer; none of that writer enters global training. Report recognition and segmentation errors, geometric/style errors, joining/shaping, technical export validity, human judgments and user effort independently. A changed method after looking at test results requires a new held-out evaluation policy; no test-set tuning.
5. **Candidate registration.** Register an immutable candidate only with reviewed model/data licenses, exact safe-format checksum (ONNX/safetensors/JSON rules), schema/worker/script compatibility, provenance, measured resource profile, model card and baseline/held-out reports. No pickle or executable loader paths. Require independent human review of fidelity and applicable script behavior. Numeric gates must be dated, sourced to measured baselines and approved before promotion; current values remain null. Phase 08 provides read-only registry verification/cache infrastructure, with no handwriting model registration or promotion.
6. **Staged deployment.** Roll out only a qualified capability per script/style/task/input class with explicit operator approval, limited exposure and a retained compatible prior pointer. Preserve approved glyph revisions and immutable exports; a model update only offers sibling candidates. Require job recovery, privacy, capacity and operational launch gates from the relevant engineering phases. Public providers, service identity and costs remain unresolved. Phase 00 does not deploy models.
7. **Monitoring and rollback.** Compare stage duration/RSS, queue/recovery/error rate, input mix, correction/resampling burden and consent/deletion failures with the deployed benchmark profile. Evaluate fidelity/quality regressions with separately authorized opt-in review data; ordinary private handwriting must not enter analytics or training. Record monitoring datasets, incident evidence, pointer changes and rollback reasons. Disable an unqualified capability or return honest unavailability; do not silently substitute weaker output. Retain prior compatible versions and immutable historical exports.

The schema checks record lineage and data eligibility. Operators still need actual scientific review, approved numeric gates and the later production security/privacy/deployment checks; a valid JSON record does not perform or approve a release.

## Independent dimensions and partitions

Each report identifies exactly one script/style and dimension. The initial partitions are `Latn/print`, `Latn/cursive`, `Latn/mixed`, `Deva/print`, `Deva/cursive`, and `Deva/mixed`. Shaping uses text-run cases independent of recognizer success. Do not aggregate these into one advertised accuracy value.

| Dimension              | Evidence to gather                                                                                                                |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Recognition            | CER, top-k/character accuracy, calibrated ECE and competing-hypothesis errors; prompt text is alignment context, not ground truth |
| Alignment/segmentation | Cell hit rate, IoU/boundary F1, split/merge and wrong-assignment errors against human/exact synthetic ground truth                |
| Outline quality        | Contour validity, winding/holes/descenders, path complexity, geometry and raster comparison                                       |
| Writer fidelity        | Held-out same-writer slant/weight/proportion/spacing errors and blinded authentic-reference comparisons                           |
| Cursive behavior       | Joining discontinuities, contextual glyph correctness, deterministic feature-on/off behavior and human legibility                 |
| Script shaping         | Own-font GSUB/GPOS, HarfBuzz cluster/order/offset/advance/golden tests and human-reviewed renders                                 |
| Font export            | Re-open/checksum/metrics/cmap, deterministic bytes, sanitizer and cross-renderer install/render checks                            |
| User burden            | Resamples, manual corrections per glyph and first usable partial/full export                                                      |
| Operations             | Observed per-stage p50/p95, RSS, input/output bytes, queue time, recovery success and actual cost                                 |

Measure clean and adverse camera conditions separately: perspective/rotation, blur, lighting/shadow, low resolution, clipping, faint ink, absent cells and mismatched template versions. Synthetic transformations preserve ancestor hashes and writer/font groups across all variants. Define any perturbation values in the versioned run config; no severity range is established by Phase 00.

## Human fidelity protocol

Use consenting reviewers and licensed/consented authentic references. Randomize and blind method identity and ordering. Reviewers compare multiple samples from the held-out writer and judge shape identity, proportions, spacing/rhythm, joins, readability and unwanted normalization separately. Record rubric version, reviewer count, exclusions, agreement and uncertainty without personal data. A more plausible word or prettier generic glyph cannot replace visual evidence of the person's writing. Do not report a human score until a review has actually occurred.
