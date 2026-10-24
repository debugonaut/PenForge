#!/usr/bin/env bash
# Preparation and validation integration are separate. Only the owner invokes train/resume.
set -euo pipefail
umask 077
if [[ $# -ne 1 ]]; then
  printf 'Usage: bash scripts/train_english_r6_research.sh prepare|preflight|freeze-source|control|train|resume|select|final-test\n' >&2
  exit 2
fi
case "$1" in prepare|preflight|freeze-source|control|train|resume|select|final-test) ;; *) exit 2 ;; esac
penforge_r6_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_r6_root"
penforge_r6_data="${PENFORGE_DATA:-$HOME/PenForge-data}"
penforge_r6_stage="${PENFORGE_R6_STAGING:-/private/tmp/penforge-r6-staging-20261022}"
penforge_r6_original_stage="${PENFORGE_R5_STAGING:-/private/tmp/penforge-r5-staging-20261021}"
penforge_r6_model="${PENFORGE_PRETRAINED_MODEL:-$penforge_r6_data/models/trocr-base-r2}"
penforge_r6_r5_run="${PENFORGE_R5_RUN:-$penforge_r6_root/research/runs/r5-agent-authorized-20261021}"
penforge_r6_run="${PENFORGE_R6_RUN:-$penforge_r6_root/research/runs/r6-owner-20261022-ready}"
export PYTHONPATH="$penforge_r6_root/packages/contracts:$penforge_r6_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned .venv is missing; use the locked recognition environment.\n' >&2
  exit 2
fi
penforge_r6_arguments=(--staging "$penforge_r6_stage" --r5-staging "$penforge_r6_original_stage"
  --model "$penforge_r6_model" --r5-run "$penforge_r6_r5_run" --run "$penforge_r6_run")
case "$1" in train|resume) penforge_r6_arguments+=(--owner-run) ;; esac
.venv/bin/python -u -m research.english.r6_run "$1" "${penforge_r6_arguments[@]}"
