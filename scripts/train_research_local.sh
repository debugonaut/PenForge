#!/usr/bin/env bash
# Owner-run entrypoint. Agents must not launch this script or the --train command.
set -euo pipefail
umask 077

: "${PENFORGE_STAGING:?Set PENFORGE_STAGING to the audited external staging directory.}"
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_run_dir="${1:-research/runs/manual-r1-$(date -u +%Y%m%dT%H%M%SZ)}"
penforge_training_log="${penforge_run_dir}.log"
mkdir -p "$(dirname "$penforge_run_dir")"

export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2

.venv/bin/python -m research.preparation.check_study --staging "$PENFORGE_STAGING"
penforge_training_args=(--train --staging "$PENFORGE_STAGING" --output "$penforge_run_dir")
if [[ -n "${PENFORGE_HARDWARE_INFO:-}" ]]; then
  penforge_training_args+=(--hardware-info "$PENFORGE_HARDWARE_INFO")
fi
.venv/bin/python -u -m research.evaluation.local_run "${penforge_training_args[@]}" 2>&1 | tee "$penforge_training_log"
printf '\nResults: %s/result.json\nLog: %s\n' "$penforge_run_dir" "$penforge_training_log"
