# English Letter Accuracy Improvement Design

**Status:** Owner-approved for local research implementation on 2026-10-21. The owner
runs every model evaluation and training command personally.

## Goal

Improve recognition of isolated, handwritten English letters with exact
case-sensitive scoring: `A` and `a` are different labels. Use the downloaded NIST SD19
corpus and the locally pinned TrOCR base model. Compare a more complete TrOCR
fine-tune with a direct 52-class image classifier built on TrOCR's pretrained visual
encoder. Select model settings on validation data and measure the selected model once
on fresh, writer-disjoint test writers.

The result remains a research measurement. This work does not add the model to PenForge,
claim product accuracy, process English words/lines, publish dataset images or weights,
or deploy anything.

## Evidence and motivation

The first NIST pilot used 2,080 training crops, 104 validation crops and 260 test crops.
Its training recipe presented 400 unique crops, leaving 1,680 training crops unseen.
Validation contained only two examples per case-sensitive letter.

The unchanged TrOCR model scored 12/104 (11.54%) exact accuracy on validation. The
bounded adaptation scored 70/104 (67.31%) on the same rows and 168/260 (64.62%) on the
original test. Of the adapted validation model's 34 errors, 22 were case-only errors.
The pinned model card describes text-line OCR, while this task provides one character
per crop. The first test has already been inspected and must not guide later model or
hyperparameter selection.

The downloaded NIST archive identifies 3,596 source writers. The first study used 159;
there are hundreds of unused writers in its school-writer partition and thousands in
its other writer partitions. A new split can therefore avoid all writers from the first
study.

## Data and evaluation design

Use only the existing, checksum-verified NIST archives. Create a new private study and
exclude every writer used in the first study from all new splits. Keep writer IDs,
crops, mappings, predictions and checkpoints outside Git.

Build training, validation, and **primary test** writer groups from the available
Census-origin NIST partitions HSF0, HSF1, HSF2, HSF3, HSF6 and HSF7, stratifying each
split within every partition so the primary score does not accidentally measure a
train-to-test domain shift. The pinned `by_write.zip` inventory has no HSF8 entries;
do not create an empty HSF8 split. Keep unused HSF4 school-writer records as a separately
reported **cross-domain test** only if the audit meets the same coverage rule. Do not
combine the two test scores. Select distinct writers per letter and quarantine duplicate
or ambiguous images before splitting. Prepare up to 64
training examples per class and up to 32 validation and primary-test examples per
class; require at least 16 examples per class in each primary evaluation set. If the
archive cannot meet those counts after the audit, stop and report actual coverage rather
than weakening writer separation. Report the final counts before model evaluation.

The prior 260-image test set is historical evidence only. Do not use it for training,
checkpoint selection, preprocessing choices, debugging against labels, or comparisons
in this work. Freeze fresh test mappings before any model evaluation. Select settings
and checkpoints only from training and validation. Freeze one winning candidate, then
run one final evaluation session against the fresh primary test and, if available, the
separate HSF4 cross-domain test. Do not inspect fresh test predictions before freezing
the candidate or use either fresh test result to make another training choice.

Primary metric is raw, exact, case-sensitive accuracy across the 52 labels. Also report
uppercase accuracy, lowercase accuracy, case-folded shape accuracy, per-letter and
per-writer results, confusion pairs and writer-clustered confidence intervals. Do not
strip text, correct predictions with a dictionary, or use English context to change a
letter. Report latency and peak memory separately from recognition quality.

## Model comparison

1. **Original TrOCR control.** Evaluate the unchanged pinned checkpoint on the new
   validation partition only.
2. **Expanded TrOCR fine-tune.** Train the existing aligned token-loss approach for
   exactly one deterministic pass over every selected training crop, with the encoder
   frozen and the current decoder/embedding trainable scope. Use the existing four-crop
   gradient accumulation, handling the last partial group without repeating samples.
   Keep the existing 1,800-second wall, 3,600-second CPU, 6,000-MB RSS and 2-GB output
   ceilings. If a ceiling stops the pass early, record it as incomplete and do not call
   it a full-data result. Do not add encoder-unfreeze variants or other hyperparameter
   searches after seeing validation results. Choose the checkpoint using validation
   only.
3. **Direct 52-class visual classifier.** Use the pretrained ViT encoder's loaded CLS
   token feature with a new linear classifier head for the exact 52 labels. Do not use
   the encoder pooler that the pinned checkpoint reports as newly initialized. Train
   the head for one deterministic pass over every selected training crop, then continue
   for one pass with the final encoder block unfrozen. Save both phase checkpoints and
   compare them on validation. The classifier output is restricted to the 52 classes.
4. **Classical reference.** If the existing HOG feature and ridge code can score the
   letter crops without changing frozen R1 results, add it as a low-cost diagnostic
   baseline. It does not replace the two primary neural comparisons or compete for
   final model selection. Compare every neural candidate on validation; do not run the
   fresh test separately for each candidate.

All model runs use the existing pinned CPU environment, fixed seeds, safe tensor
checkpoints, resource limits and offline model files. No new model downloads or paid
compute are included. Select among the neural candidates by highest validation exact
case-sensitive accuracy. If they tie, prefer lower validation character error, then
lower inference latency. Freeze the winner and its configuration before the fresh test
run. Do not define a product acceptance threshold from this experiment.

## Implementation boundary and checks

Implementation will add the new split/admission lineage, a direct classifier runner,
bounded recipe settings and result reporting without overwriting the original R3
study or its reports. Tests will cover writer exclusion, split disjointness, exact
52-class target mapping, deterministic selection, loss/gradient scope, checkpoint
integrity, metrics and resource guards. Synthetic fixtures may test the mechanics but
will never count as model accuracy evidence.

After static checks, the owner will run bounded training and model evaluations locally.
The new tests are evaluated only after validation selects and freezes the candidate.
Reports and model bytes remain private and ignored. No
website integration, public commit, push, deployment or word-recognition run is part
of this design.

## Limits

This design measures isolated NIST letters, not words, text lines, camera photographs,
or diverse everyday user writing. A model pretrained on IAM may have undocumented
pretraining overlap; the study cannot establish independence from all pretraining
data. A stronger score on this test does not qualify a PenForge product model. English
word recognition remains a separate study.
