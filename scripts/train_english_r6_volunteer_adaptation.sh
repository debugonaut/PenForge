#!/usr/bin/env bash
# Owner-run bounded volunteer adaptation of the frozen English R6 pass-2 winner.
# `prepare`, `verify`, `holdout-prepare` and `holdout-compare` are data-only.
# Only `train` loads a model, and only the owner invokes it.
set -euo pipefail
umask 077
if [[ $# -lt 1 || $# -gt 2 ]]; then
  printf 'Usage: PENFORGE_ENGLISH_STAGING=/private/staging \\\n  bash scripts/train_english_r6_volunteer_adaptation.sh prepare|verify|train|resume|holdout-prepare|holdout-compare\n' >&2
  exit 2
fi
penforge_stage="$1"
case "$penforge_stage" in
  prepare|verify|train|resume|holdout-prepare|holdout-compare) ;;
  *) exit 2 ;;
esac
penforge_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_root"
penforge_pilot_run="${PENFORGE_VOLUNTEER_RUN:-$penforge_root/research/runs/r6-volunteer-pilot-20261022}"
penforge_adaptation_run="${PENFORGE_VOLUNTEER_ADAPTATION_RUN:-$penforge_root/research/runs/r6-volunteer-adaptation-20261023-capture-v2-ready}"
penforge_adaptation_config="${PENFORGE_VOLUNTEER_ADAPTATION_CONFIG:-$penforge_root/research/configs/english-volunteer-adaptation-3gb.json}"
penforge_holdout_run="${PENFORGE_VOLUNTEER_HOLDOUT_RUN:-$penforge_root/research/runs/r6-volunteer-holdout-20261022}"
penforge_frozen_run="${PENFORGE_R6_RUN:-$penforge_root/research/runs/r6-owner-20261022-ready}"
penforge_staging="${PENFORGE_ENGLISH_STAGING:-}"
if [[ -z "$penforge_staging" ]]; then
  printf 'PENFORGE_ENGLISH_STAGING must point at the admitted R6 train-validation staging directory.\n' >&2
  exit 2
fi
export PYTHONPATH="$penforge_root/packages/contracts:$penforge_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 MKL_NUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned .venv is missing; use the locked recognition environment.\n' >&2
  exit 2
fi
penforge_adaptation_arguments=(
  --run "$penforge_adaptation_run"
  --pilot-run "$penforge_pilot_run"
  --staging "$penforge_staging"
  --frozen-run "$penforge_frozen_run"
  --adaptation-config "$penforge_adaptation_config"
)
case "$penforge_stage" in
  prepare)
    .venv/bin/python -u -m research.english.volunteer_run prepare-adaptation "${penforge_adaptation_arguments[@]}"
    ;;
  verify)
    .venv/bin/python -u -m research.english.volunteer_run verify-adaptation "${penforge_adaptation_arguments[@]}"
    ;;
  train|resume)
    penforge_train_arguments=("${penforge_adaptation_arguments[@]}" --owner-run)
    if [[ "$penforge_stage" == resume ]]; then
      penforge_train_arguments+=(--resume)
    fi
    .venv/bin/python -u -m research.english.volunteer_run adapt "${penforge_train_arguments[@]}"
    ;;
  holdout-prepare)
    penforge_holdout_arguments=(--run "$penforge_holdout_run")
    if [[ -n "${PENFORGE_VOLUNTEER_HOLDOUT_COORDINATES:-}" ]]; then
      penforge_holdout_arguments+=(--holdout-coordinates "$PENFORGE_VOLUNTEER_HOLDOUT_COORDINATES")
    fi
    .venv/bin/python -u -m research.english.volunteer_run holdout-prepare "${penforge_holdout_arguments[@]}"
    ;;
  holdout-compare)
    if [[ -z "${PENFORGE_HOLDOUT_BASELINE_PREDICTIONS:-}" || -z "${PENFORGE_HOLDOUT_ADAPTED_PREDICTIONS:-}" ]]; then
      printf 'Set PENFORGE_HOLDOUT_BASELINE_PREDICTIONS and PENFORGE_HOLDOUT_ADAPTED_PREDICTIONS.\n' >&2
      exit 2
    fi
    .venv/bin/python -u -m research.english.volunteer_run holdout-compare \
      --run "$penforge_holdout_run" \
      --baseline-predictions "$PENFORGE_HOLDOUT_BASELINE_PREDICTIONS" \
      --adapted-predictions "$PENFORGE_HOLDOUT_ADAPTED_PREDICTIONS"
    ;;
esac
