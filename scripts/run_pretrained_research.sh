#!/usr/bin/env bash
# OWNER-RUN ONLY. Agents must not invoke this wrapper or the neural entrypoint.
set -euo pipefail
umask 077
: "${PENFORGE_STAGING:?Set PENFORGE_STAGING to the audited external data staging directory.}"
: "${PENFORGE_PRETRAINED_MODEL:?Set PENFORGE_PRETRAINED_MODEL to the verified complete model bundle.}"
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2
if [[ $# -lt 2 ]]; then
  printf 'Usage: bash scripts/run_pretrained_research.sh baseline|finetune|test NEW_OUTPUT_DIR [additional flags]\n' >&2
  exit 2
fi
penforge_mode="$1"
penforge_output="$2"
shift 2
penforge_args=("$penforge_mode" --owner-run --staging "$PENFORGE_STAGING" --model "$PENFORGE_PRETRAINED_MODEL" --output "$penforge_output")
if [[ "$penforge_mode" == finetune ]]; then
  penforge_args+=(--train)
fi
exec .venv/bin/python -u -m research.pretrained.run "${penforge_args[@]}" "$@"
