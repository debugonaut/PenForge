#!/usr/bin/env bash
# Owner-run preprocessing comparison for the frozen English R6 volunteer winner.
# `prepare` and `verify` are data-only; only the owner invokes `compare`.
set -euo pipefail
umask 077
if [[ $# -gt 1 ]]; then
  printf 'Usage: bash scripts/compare_english_r6_volunteer_preprocessing.sh [prepare|verify|compare]\n' >&2
  exit 2
fi
penforge_stage="${1:-compare}"
case "$penforge_stage" in prepare|verify|compare) ;; *) exit 2 ;; esac
penforge_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_root"
penforge_pilot_run="${PENFORGE_VOLUNTEER_RUN:-$penforge_root/research/runs/r6-volunteer-pilot-20261022}"
penforge_variant_run="${PENFORGE_VOLUNTEER_PREPROCESSING_RUN:-$penforge_root/research/runs/r6-volunteer-preprocessing-20261022}"
penforge_frozen_run="${PENFORGE_R6_RUN:-$penforge_root/research/runs/r6-owner-20261022-ready}"
export PYTHONPATH="$penforge_root/packages/contracts:$penforge_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 MKL_NUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned .venv is missing; use the locked recognition environment.\n' >&2
  exit 2
fi
penforge_arguments=(
  --run "$penforge_variant_run"
  --pilot-run "$penforge_pilot_run"
  --frozen-run "$penforge_frozen_run"
)
case "$penforge_stage" in
  prepare|verify)
    .venv/bin/python -u -m research.english.volunteer_run variants "${penforge_arguments[@]}"
    ;;
  compare)
    penforge_arguments+=(--owner-run)
    .venv/bin/python -u -m research.english.volunteer_run compare "${penforge_arguments[@]}"
    ;;
esac
