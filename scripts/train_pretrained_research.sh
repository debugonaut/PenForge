#!/usr/bin/env bash
# OWNER-RUN ONLY. Agents must not invoke this launcher, even for help/dry runs.
# Syntax checking with bash -n is allowed. No training starts until the owner runs it.
set -euo pipefail
umask 077
if [[ $# -ne 0 ]]; then
  printf 'Usage: bash scripts/train_pretrained_research.sh\n' >&2
  exit 2
fi
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_data_root="${PENFORGE_DATA:-$HOME/PenForge-data}"
export PENFORGE_STAGING="${PENFORGE_STAGING:-$penforge_data_root/prepared/local-r1}"
export PENFORGE_PRETRAINED_MODEL="${PENFORGE_PRETRAINED_MODEL:-$penforge_data_root/models/trocr-base-r2}"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'The prepared Python environment is missing. See research/PRETRAINED_R2.md.\n' >&2
  exit 2
fi
mkdir -p research/runs
penforge_attempt="$(mktemp -d "$penforge_repo_root/research/runs/r2-owner-XXXXXXXX")"
printf 'Private reports: %s\n' "$penforge_attempt"
printf 'Checking local files, then measuring unchanged TrOCR and running the fixed CPU pilot.\n'
.venv/bin/python -m research.pretrained.check \
  --staging "$PENFORGE_STAGING" --model "$PENFORGE_PRETRAINED_MODEL" \
  --report "$penforge_attempt/preparation-check.json"
bash scripts/run_pretrained_research.sh baseline "$penforge_attempt/baseline"
if penforge_reason="$(.venv/bin/python -m research.pretrained.handoff "$penforge_attempt/baseline/report.json")"; then
  printf '%s\n' "$penforge_reason"
  bash scripts/run_pretrained_research.sh finetune "$penforge_attempt/finetune" \
    --parent "$penforge_attempt/baseline/report.json" --reason "$penforge_reason"
  printf 'Pilot finished. Comparison: %s/finetune/report.json\n' "$penforge_attempt"
else
  penforge_decision_status=$?
  if [[ "$penforge_decision_status" -eq 3 ]]; then
    printf 'No errors were observed in this validation subset; fine-tuning was skipped.\n'
    printf 'Baseline: %s/baseline/report.json\n' "$penforge_attempt"
  else
    exit "$penforge_decision_status"
  fi
fi
printf 'No product promotion or test-set run is automatic.\n'
