#!/usr/bin/env bash
# DATA PREPARATION ONLY. No model/training entrypoint, download, watcher or scheduling.
set -euo pipefail
umask 077
if [[ $# -gt 1 ]]; then
  printf 'Usage: bash scripts/prepare_english_research.sh [letters|words]\n' >&2
  exit 2
fi
penforge_task="${1:-letters}"
penforge_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$penforge_repo_root"
penforge_data_root="${PENFORGE_DATA:-$HOME/PenForge-data}"
penforge_sources="${PENFORGE_ENGLISH_SOURCES:-$penforge_data_root/english-r3/sources}"
export PYTHONPATH="$penforge_repo_root/packages/contracts:$penforge_repo_root"
case "$penforge_task" in
  letters)
    .venv/bin/python -m research.english.downloads --sources "$penforge_sources" --letters-only --verify
    .venv/bin/python -m research.english.nist --sources "$penforge_sources" \
      --destination "$penforge_data_root/prepared/english-r3-letters-v1"
    ;;
  words)
    penforge_inventory="$penforge_data_root/prepared/english-r3-cvl-inventory-v1"
    penforge_words="$penforge_data_root/prepared/english-r3-words-v1"
    if [[ ! -d "$penforge_inventory" ]]; then
      .venv/bin/python -m research.english.cvl --sources "$penforge_sources"
      .venv/bin/python -m research.english.cvl_inventory --sources "$penforge_sources" \
        --destination "$penforge_inventory"
    fi
    if [[ -d "$penforge_words" ]]; then
      .venv/bin/python -m research.english.cvl_words --check-staging "$penforge_words"
    else
      .venv/bin/python -m research.english.cvl_words --sources "$penforge_sources" \
        --inventory "$penforge_inventory" --destination "$penforge_words"
    fi
    ;;
  *) printf 'Choose letters or words.\n' >&2; exit 2 ;;
esac
printf 'Preparation finished pending actual-file review and admission. No model ran.\n'
