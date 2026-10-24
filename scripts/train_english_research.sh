#!/usr/bin/env bash
# OWNER-RUN ONLY. Agents must never run this, including help/dry runs. bash -n is safe.
set -euo pipefail
umask 077
if [[ $# -gt 1 ]]; then
  printf 'Usage: bash scripts/train_english_research.sh [letters|words]\n' >&2
  exit 2
fi
penforge_task="${1:-letters}"
case "$penforge_task" in letters|words) ;; *) printf 'Choose letters or words.\n' >&2; exit 2 ;; esac
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_data_root="${PENFORGE_DATA:-$HOME/PenForge-data}"
export PENFORGE_ENGLISH_STAGING="${PENFORGE_ENGLISH_STAGING:-$penforge_data_root/prepared/english-r3-$penforge_task-v1}"
export PENFORGE_PRETRAINED_MODEL="${PENFORGE_PRETRAINED_MODEL:-$penforge_data_root/models/trocr-base-r2}"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'The prepared Python environment is missing. See research/ENGLISH_R3.md.\n' >&2
  exit 2
fi
printf 'Checking audited English %s inputs before any model execution.\n' "$penforge_task"
.venv/bin/python -m research.english.preflight --task "$penforge_task" \
  --staging "$PENFORGE_ENGLISH_STAGING" --model "$PENFORGE_PRETRAINED_MODEL" --summary
mkdir -p research/runs
penforge_attempt="$(mktemp -d "$penforge_repo_root/research/runs/r3-$penforge_task-owner-XXXXXXXX")"
printf 'Private English reports: %s\n' "$penforge_attempt"
bash scripts/run_english_research.sh baseline "$penforge_task" "$penforge_attempt/baseline"
if penforge_reason="$(.venv/bin/python -m research.english.handoff "$penforge_attempt/baseline/report.json")"; then
  printf '%s\n' "$penforge_reason"
  bash scripts/run_english_research.sh finetune "$penforge_task" "$penforge_attempt/finetune" \
    --parent "$penforge_attempt/baseline/report.json" --reason "$penforge_reason"
  printf 'Comparison: %s/finetune/report.json\n' "$penforge_attempt"
else
  penforge_decision_status=$?
  if [[ "$penforge_decision_status" -eq 3 ]]; then
    printf 'No errors observed on this validation subset; fitting was skipped.\n'
  else
    exit "$penforge_decision_status"
  fi
fi
printf 'No held-out test, product promotion or second task starts automatically.\n'
