#!/usr/bin/env bash
# OWNER-RUN ONLY. This command prepares data, then runs bounded R4 model checks,
# training, validation selection and exactly one fresh final test. Agents must not run it.
set -euo pipefail
umask 077
if [[ $# -ne 0 ]]; then
  printf 'Usage: bash scripts/train_english_r4_research.sh\n' >&2
  exit 2
fi

penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_data_root="${PENFORGE_DATA:-$HOME/PenForge-data}"
penforge_sources="${PENFORGE_NIST_SOURCES:-$penforge_data_root/english-r3/sources}"
penforge_r3_stage="${PENFORGE_R3_BASELINE:-$penforge_data_root/prepared/english-r3-letters-v1}"
penforge_r4_stage="${PENFORGE_R4_STAGING:-$penforge_data_root/prepared/english-r4-letters-v1}"
penforge_model="${PENFORGE_PRETRAINED_MODEL:-$penforge_data_root/models/trocr-base-r2}"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2

if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned Python environment missing. Prepare it as documented in research/ENGLISH_R4.md.\n' >&2
  exit 2
fi
if [[ -L "$penforge_r4_stage" ]]; then
  printf 'R4 staging path must not be a symlink.\n' >&2
  exit 2
fi
if [[ ! -e "$penforge_r4_stage" ]]; then
  printf 'Auditing local NIST archives and preparing the fresh writer split. No model will run during this step.\n'
  .venv/bin/python -m research.english.r4_study \
    --sources "$penforge_sources" \
    --baseline "$penforge_r3_stage" \
    --destination "$penforge_r4_stage"
fi

printf 'Verifying approved R4 data, exact-case labels, pinned model files and environment.\n'
.venv/bin/python -m research.english.r4_preflight \
  --staging "$penforge_r4_stage" \
  --model "$penforge_model" \
  --summary

mkdir -p research/runs
penforge_attempt="$(mktemp -d "$penforge_repo_root/research/runs/r4-owner-XXXXXXXX")"
chmod 700 "$penforge_attempt"
printf 'Private R4 reports: %s\n' "$penforge_attempt"

for penforge_stage in baseline trocr_full_pass classifier_two_passes; do
  .venv/bin/python -m research.english.r4_run "$penforge_stage" \
    --owner-run \
    --staging "$penforge_r4_stage" \
    --model "$penforge_model" \
    --output "$penforge_attempt/$penforge_stage"
done

.venv/bin/python -m research.english.r4_select \
  --baseline "$penforge_attempt/baseline/report.json" \
  --trocr "$penforge_attempt/trocr_full_pass/report.json" \
  --classifier "$penforge_attempt/classifier_two_passes/report.json" \
  --output "$penforge_attempt/winner-frozen.json"

.venv/bin/python -m research.english.r4_run final_test \
  --owner-run \
  --staging "$penforge_r4_stage" \
  --model "$penforge_model" \
  --winner "$penforge_attempt/winner-frozen.json" \
  --output "$penforge_attempt/final_test"

printf 'Finished. The final report and private predictions are in: %s/final_test\n' "$penforge_attempt"
printf 'These results are research-only; no model was connected to the website.\n'
