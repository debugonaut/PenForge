#!/usr/bin/env bash
# Private five-writer volunteer pilot for the frozen English R6 pass-2 winner.
# `prepare` and `verify` are data-only; only the owner invokes `evaluate`.
set -euo pipefail
umask 077
if [[ $# -ne 1 ]]; then
  printf 'Usage: PENFORGE_VOLUNTEER_RUN=/path/to/private/run bash scripts/evaluate_english_r6_volunteers.sh prepare|verify|evaluate\n' >&2
  exit 2
fi
case "$1" in prepare|verify|evaluate) ;; *) exit 2 ;; esac
penforge_volunteer_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_volunteer_root"
penforge_volunteer_run="${PENFORGE_VOLUNTEER_RUN:-$penforge_volunteer_root/research/runs/r6-volunteer-pilot-20261022}"
penforge_volunteer_frozen="${PENFORGE_R6_RUN:-$penforge_volunteer_root/research/runs/r6-owner-20261022-ready}"
export PYTHONPATH="$penforge_volunteer_root/packages/contracts:$penforge_volunteer_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 MKL_NUM_THREADS=2
if [[ ! -x .venv/bin/python ]]; then
  printf 'Pinned .venv is missing; use the locked recognition environment.\n' >&2
  exit 2
fi
penforge_volunteer_arguments=(--run "$penforge_volunteer_run" --frozen-run "$penforge_volunteer_frozen")
if [[ -n "${PENFORGE_VOLUNTEER_COORDINATES:-}" ]]; then
  penforge_volunteer_arguments+=(--coordinates "$PENFORGE_VOLUNTEER_COORDINATES")
fi
if [[ "$1" == evaluate ]]; then
  penforge_volunteer_arguments+=(--owner-run)
fi
.venv/bin/python -u -m research.english.volunteer_run "$1" "${penforge_volunteer_arguments[@]}"
