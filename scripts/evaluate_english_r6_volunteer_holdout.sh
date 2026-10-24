#!/usr/bin/env bash
# Data-only preparation/verification; only the owner runs `evaluate`.
set -euo pipefail
umask 077
if [[ $# -ne 1 ]]; then
  printf 'Usage: bash scripts/evaluate_english_r6_volunteer_holdout.sh prepare|verify|evaluate\n' >&2
  exit 2
fi
penforge_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_root"
penforge_stage="$1"
case "$penforge_stage" in
  prepare) penforge_cli_stage=holdout-prepare ;;
  verify) penforge_cli_stage=holdout-verify ;;
  evaluate) penforge_cli_stage=holdout-evaluate ;;
  *) exit 2 ;;
esac
penforge_run="${PENFORGE_VOLUNTEER_HOLDOUT_RUN:-$penforge_root/research/runs/r6-volunteer-holdout-20261023-reviewed-ready}"
penforge_coordinates="${PENFORGE_VOLUNTEER_HOLDOUT_COORDINATES:-$penforge_root/research/runs/r6-volunteer-holdout-intake-20261023/reviewed-coordinates-v2.json}"
penforge_adaptation="${PENFORGE_VOLUNTEER_ADAPTATION_RUN:-$penforge_root/research/runs/r6-volunteer-adaptation-20261023-capture-v2-ready}"
penforge_frozen="${PENFORGE_R6_RUN:-$penforge_root/research/runs/r6-owner-20261022-ready}"
export PYTHONPATH="$penforge_root/packages/contracts:$penforge_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 MKL_NUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned .venv is missing; use the locked recognition environment.\n' >&2
  exit 2
fi
penforge_arguments=("$penforge_cli_stage" --run "$penforge_run" --frozen-run "$penforge_frozen" --adaptation-run "$penforge_adaptation")
if [[ "$penforge_stage" == prepare ]]; then
  penforge_arguments+=(--holdout-coordinates "$penforge_coordinates")
elif [[ "$penforge_stage" == evaluate ]]; then
  penforge_arguments+=(--owner-run)
fi
exec .venv/bin/python -u -m research.english.volunteer_run "${penforge_arguments[@]}"
