#!/usr/bin/env bash
# Live progress bar (+ resume supervisor) for the Devanagari handwriting archives.
#
# Companion to research/fetch_deva_datasets.py: the same five sources and the same
# published byte counts. A file whose size already equals its expected size is never
# opened for writing — it is reported as complete and left exactly as it is. Only
# short files are resumed with `curl -C -`, which appends at the current offset.
#
# The CVIT server closes each connection near 1.08 GB, so a plain curl --retry gives
# up partway. This script relaunches any transfer that stops without being complete.
#
# Usage:
#   bash research/watch_deva_downloads.sh          # live bar, supervises transfers
#   bash research/watch_deva_downloads.sh --once   # print one frame and exit
#   bash research/watch_deva_downloads.sh --no-resume   # display only
#
# Exactly one instance supervises; extra instances run as read-only viewers, so a
# background copy and a visible terminal can run together without touching the files.
set -uo pipefail

ROOT="${PENFORGE_DATA:-$HOME/PenForge-data}"
DIR="$ROOT/iiit"
LOG="$ROOT/fetch-progress.log"
LOCK="$ROOT/.deva-supervisor.lock"
INTERVAL="${INTERVAL:-2}"
ONCE=0
RESUME=1
for arg in "$@"; do
  case "$arg" in
    --once) ONCE=1 ;;
    --no-resume) RESUME=0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

names=(IIIT-HW-Dev.zip IIIT-HW-Hindi_v1.tar.gz indic-hindi.zip indic-marathi.zip dhcd.zip)
dirs=("$DIR" "$DIR" "$DIR" "$DIR" "$ROOT/deva")
sizes=(1920697925 1822862305 3576032718 3009421257 80417803)
urls=(
  "https://cvit.iiit.ac.in/images/Projects/wordlevel-Indicscripts/IIIT-HW-Dev.zip"
  "https://cvit.iiit.ac.in/images/Projects/wordlevel-Indicscripts/IIIT-HW-Hindi_v1.tar.gz"
  "https://cvit.iiit.ac.in/images/datasets/indic_handwritten/hindi.zip"
  "https://cvit.iiit.ac.in/images/datasets/indic_handwritten/marathi.zip"
  "https://archive.ics.uci.edu/static/public/389/devanagari+handwritten+character+dataset.zip"
)
n=${#names[@]}

# Names listed in manual-downloads.txt were deleted on purpose and are owned by the
# operator's own downloader; supervising them again would silently re-fetch gigabytes.
# Bash 3.2 (macOS) has no associative arrays, so this is a plain lookup.
MANUAL_LIST="$ROOT/manual-downloads.txt"
is_manual() {
  [ -f "$MANUAL_LIST" ] && grep -Fxq -- "$1" "$MANUAL_LIST" 2>/dev/null
}

IS_TTY=0
[ -t 1 ] && IS_TTY=1

file_size() {
  local path="$1"
  [ -f "$path" ] || { echo 0; return; }
  stat -f%z "$path" 2>/dev/null || stat -c%s "$path" 2>/dev/null || echo 0
}

human() { # bytes -> 1.42 GB
  awk -v b="$1" 'BEGIN { if (b >= 1e9) printf "%.2f GB", b/1e9; else printf "%.0f MB", b/1e6 }'
}

rate() { # bytes/second -> 470 KB/s or 1.4 MB/s
  awk -v b="$1" 'BEGIN {
    if (b >= 1e9) printf "%.2f GB/s", b/1e9
    else if (b >= 1e6) printf "%.1f MB/s", b/1e6
    else printf "%.0f KB/s", b/1e3
  }'
}

bar() { # percent width -> filled bar
  local pct="$1" width="$2" filled i out=""
  filled=$(( pct * width / 100 ))
  for ((i = 0; i < width; i++)); do
    if [ "$i" -lt "$filled" ]; then out+="█"; else out+="░"; fi
  done
  printf '%s' "$out"
}

transfer_running() { # name -> 0 if a curl for this file is alive
  pgrep -f "curl .*-o $1" >/dev/null 2>&1
}

launch() { # index
  local i="$1"
  mkdir -p "${dirs[$i]}"
  (
    cd "${dirs[$i]}" || exit 1
    curl -sSL -C - --retry 3 --retry-delay 3 --speed-limit 2048 --speed-time 45 \
      --max-time 7200 "${urls[$i]}" -o "${names[$i]}"
  ) >>"$ROOT/resume.log" 2>&1 &
}

acquire_lock() {
  [ "$RESUME" -eq 1 ] || return 1
  mkdir -p "$ROOT"
  if mkdir "$LOCK" 2>/dev/null; then
    echo $$ >"$LOCK/pid"
    return 0
  fi
  local pid
  pid=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    VIEWER_OF="$pid"
    return 1
  fi
  rmdir "$LOCK" 2>/dev/null || rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || return 1
  echo $$ >"$LOCK/pid"
  return 0
}

release_lock() {
  [ "${OWNER:-0}" -eq 1 ] || return 0
  [ "$(cat "$LOCK/pid" 2>/dev/null || echo "")" = "$$" ] && rm -rf "$LOCK"
}

trap 'release_lock; exit 130' INT TERM

OWNER=0
VIEWER_OF=""
acquire_lock && OWNER=1

declare -a prev_bytes prev_time speed
now=$(date +%s)
for ((i = 0; i < n; i++)); do
  prev_bytes[$i]=$(file_size "${dirs[$i]}/${names[$i]}")
  prev_time[$i]=$now
  speed[$i]=0
done
last_log=0

render() {
  local t i have want pct total_have=0 total_want=0 total_speed=0 done_count=0
  local line state eta eta_txt arrow total_eta

  for ((i = 0; i < n; i++)); do
    have=$(file_size "${dirs[$i]}/${names[$i]}")
    want=${sizes[$i]}
    [ "$have" -gt "$want" ] && have=$want
    total_have=$(( total_have + have ))
    total_want=$(( total_want + want ))
    [ "$have" -ge "$want" ] && done_count=$(( done_count + 1 ))
    total_speed=$(( total_speed + speed[$i] ))
  done

  t=$(date +%s)
  pct=$(( total_have * 1000 / total_want ))
  if [ "$IS_TTY" -eq 1 ]; then printf '\033[H'; fi
  printf 'PenForge · Devanagari archives — %d/%d complete\033[K\n' "$done_count" "$n"
  printf '%s %3d.%d%%  %s / %s  %s' \
    "$(bar $((pct / 10)) 40)" $((pct / 10)) $((pct % 10)) \
    "$(human "$total_have")" "$(human "$total_want")" "$(rate "$total_speed")"
  if [ "$total_speed" -gt 0 ]; then
    eta=$(( (total_want - total_have) / total_speed ))
    printf '  ETA %dm%02ds\033[K\n' $((eta / 60)) $((eta % 60))
  else
    printf '  ETA —\033[K\n'
  fi
  printf '────────────────────────────────────────────────────────────────────────\033[K\n'

  for ((i = 0; i < n; i++)); do
    have=$(file_size "${dirs[$i]}/${names[$i]}")
    want=${sizes[$i]}
    if [ "$have" -ge "$want" ]; then
      printf '%-24s %s 100.0%%  %s  complete ✓ untouched\033[K\n' \
        "${names[$i]}" "$(bar 100 18)" "$(human "$want")"
      continue
    fi
    if is_manual "${names[$i]}"; then
      printf '%-24s %-18s  %s/%s  manual download — not supervised\033[K\n' \
        "${names[$i]}" "awaiting you" "$(human "$have")" "$(human "$want")"
      continue
    fi
    pct=$(( have * 1000 / want ))
    if [ "${speed[$i]}" -gt 0 ]; then
      eta=$(( (want - have) / speed[$i] ))
      eta_txt=$(printf '%dm%02ds' $((eta / 60)) $((eta % 60)))
    else
      eta_txt='—'
    fi
    if transfer_running "${names[$i]}"; then state=''; else state=' (relaunching)'; fi
    printf '%-24s %s %3d.%d%%  %s/%s  %s  ETA %s%s\033[K\n' \
      "${names[$i]}" "$(bar $((pct / 10)) 18)" $((pct / 10)) $((pct % 10)) \
      "$(human "$have")" "$(human "$want")" "$(rate "${speed[$i]}")" "$eta_txt" "$state"
  done

  printf '────────────────────────────────────────────────────────────────────────\033[K\n'
  if [ "${OWNER:-0}" -eq 1 ]; then
    printf 'supervisor: this process (pid %d) · complete files are never rewritten · curl -C - resumes\033[K\n' "$$"
  else
    local shown
    shown=$(cat "$LOCK/pid" 2>/dev/null || echo none)
    printf 'display only (supervisor pid %s) · complete files are never rewritten\033[K\n' "$shown"
  fi
  printf 'Ctrl-C stops watching. Transfers keep their bytes and resume from the same offset.\033[K\n'
}

update_speed() {
  local i t dt now_bytes
  t=$(date +%s)
  for ((i = 0; i < n; i++)); do
    now_bytes=$(file_size "${dirs[$i]}/${names[$i]}")
    dt=$(( t - prev_time[$i] ))
    if [ "$dt" -gt 0 ]; then
      speed[$i]=$(( (now_bytes - prev_bytes[$i]) / dt ))
      [ "${speed[$i]}" -lt 0 ] && speed[$i]=0
      prev_bytes[$i]=$now_bytes
      prev_time[$i]=$t
    fi
  done
}

supervise() {
  local i have
  [ "${OWNER:-0}" -eq 1 ] || return 0
  for ((i = 0; i < n; i++)); do
    have=$(file_size "${dirs[$i]}/${names[$i]}")
    [ "$have" -ge "${sizes[$i]}" ] && continue
    is_manual "${names[$i]}" && continue
    transfer_running "${names[$i]}" && continue
    launch "$i"
  done
}

if [ "$ONCE" -eq 1 ]; then
  # One short second sample so the single frame reports a real rate instead of 0.
  sleep 1
  update_speed
  render
  [ "$IS_TTY" -eq 1 ] || echo
  release_lock
  exit 0
fi

mkdir -p "$ROOT"
echo "[$(date -u +%H:%M:%SZ)] watcher start pid=$$ supervisor=$OWNER" >>"$LOG"

while :; do
  update_speed
  supervise
  # A non-interactive run keeps only the 60-second log line, so an unattended
  # supervisor does not grow its output file with thousands of ANSI frames.
  [ "$IS_TTY" -eq 1 ] && render
  if [ "$IS_TTY" -eq 0 ]; then
    total_have=0
    for ((i = 0; i < n; i++)); do
      have=$(file_size "${dirs[$i]}/${names[$i]}")
      [ "$have" -gt "${sizes[$i]}" ] && have=${sizes[$i]}
      total_have=$(( total_have + have ))
    done
    now=$(date +%s)
    if [ $(( now - last_log )) -ge 60 ]; then
      speeds_kb=""
      for ((i = 0; i < n; i++)); do
        speeds_kb+="$(( speed[i] / 1024 )) "
      done
      echo "[$(date -u +%H:%M:%SZ)] $total_have/10409432008 kB/s: ${speeds_kb% }" >>"$LOG"
      last_log=$now
    fi
  fi

  done_count=0
  for ((i = 0; i < n; i++)); do
    have=$(file_size "${dirs[$i]}/${names[$i]}")
    [ "$have" -ge "${sizes[$i]}" ] && done_count=$(( done_count + 1 ))
  done
  if [ "$done_count" -eq "$n" ]; then
    [ "$IS_TTY" -eq 1 ] && printf '\n'
    echo "all $n archives complete; run: python3 research/fetch_deva_datasets.py --verify"
    echo "[$(date -u +%H:%M:%SZ)] all complete" >>"$LOG"
    release_lock
    exit 0
  fi
  sleep "$INTERVAL"
done
