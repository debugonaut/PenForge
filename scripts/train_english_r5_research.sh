#!/usr/bin/env bash
# Explicit stages. Preparation/preflight never execute a model. Final test is separate.
# Model stages require the owner's authorization; no stage automatically runs final tests.
set -euo pipefail
umask 077
if [[ $# -ne 1 ]]; then
  printf 'Usage: bash scripts/train_english_r5_research.sh prepare|preflight|baseline|train|freeze|final-test|prepare-final-retry|retry-final-test\n' >&2
  exit 2
fi
case "$1" in prepare|preflight|baseline|train|freeze|final-test|prepare-final-retry|retry-final-test) ;; *) exit 2 ;; esac
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_data_root="${PENFORGE_DATA:-$HOME/PenForge-data}"
penforge_sources="${PENFORGE_NIST_SOURCES:-$penforge_data_root/english-r3/sources}"
penforge_r3_stage="${PENFORGE_R3_BASELINE:-$penforge_data_root/prepared/english-r3-letters-v1}"
penforge_r4_stage="${PENFORGE_R4_STAGING:-/private/tmp/penforge-r4-owner-staging-20261021}"
penforge_r5_stage="${PENFORGE_R5_STAGING:-/private/tmp/penforge-r5-staging-20261021}"
penforge_model="${PENFORGE_PRETRAINED_MODEL:-$penforge_data_root/models/trocr-base-r2}"
penforge_initialization="${PENFORGE_R5_INITIALIZATION:-$penforge_repo_root/research/runs/r4-owner-rNOQvt9w/trocr_full_pass/checkpoint}"
penforge_r4_winner="${PENFORGE_R4_WINNER:-$penforge_repo_root/research/runs/r4-owner-corrected-20261021/winner-frozen.json}"
penforge_run="${PENFORGE_R5_RUN:-$penforge_repo_root/research/runs/r5-approved-20261021}"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned .venv is missing; use the locked recognition environment.\n' >&2
  exit 2
fi
if [[ "$1" == prepare ]]; then
  .venv/bin/python -m research.english.r5_data.prepare --sources "$penforge_sources" \
    --r3 "$penforge_r3_stage" --r4 "$penforge_r4_stage" --destination "$penforge_r5_stage"
  exit
fi
penforge_arguments=(--staging "$penforge_r5_stage" --model "$penforge_model"
  --initialization "$penforge_initialization" --r4-winner "$penforge_r4_winner"
  --run "$penforge_run")
case "$1" in
  prepare-final-retry)
    .venv/bin/python -u -m research.english.r5_eval.recovery "${penforge_arguments[@]}" --prepare-only
    exit ;;
  retry-final-test)
    .venv/bin/python -u -m research.english.r5_eval.recovery "${penforge_arguments[@]}" --owner-run
    exit ;;
  baseline|train|final-test) penforge_arguments+=(--owner-run) ;;
esac
.venv/bin/python -m research.english.r5_eval.snapshot "$1" "${penforge_arguments[@]}"
