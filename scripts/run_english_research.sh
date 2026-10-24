#!/usr/bin/env bash
# OWNER-RUN ONLY. Agents may use bash -n, never invoke this wrapper or its entrypoint.
set -euo pipefail
umask 077
if [[ $# -lt 3 ]]; then
  printf 'Usage: bash scripts/run_english_research.sh baseline|finetune|test letters|words NEW_OUTPUT_DIR [flags]\n' >&2
  exit 2
fi
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_mode="$1"
penforge_task="$2"
penforge_output="$3"
shift 3
case "$penforge_task" in letters|words) ;; *) printf 'Choose letters or words.\n' >&2; exit 2 ;; esac
case "$penforge_mode" in baseline|finetune|test) ;; *) printf 'Choose baseline, finetune or test.\n' >&2; exit 2 ;; esac
penforge_data_root="${PENFORGE_DATA:-$HOME/PenForge-data}"
penforge_staging="${PENFORGE_ENGLISH_STAGING:-$penforge_data_root/prepared/english-r3-$penforge_task-v1}"
penforge_model="${PENFORGE_PRETRAINED_MODEL:-$penforge_data_root/models/trocr-base-r2}"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2
penforge_args=("$penforge_mode" --owner-run --task "$penforge_task" --staging "$penforge_staging" --model "$penforge_model" --output "$penforge_output")
if [[ "$penforge_mode" == finetune ]]; then penforge_args+=(--train); fi
exec .venv/bin/python -u -m research.english.run "${penforge_args[@]}" "$@"
