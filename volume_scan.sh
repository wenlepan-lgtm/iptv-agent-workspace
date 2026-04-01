#!/usr/bin/env bash
set -euo pipefail

scan_dir () {
  local dir="$1"
  echo "=== $dir ==="
  for f in "$dir"/*.wav; do
    echo -n "$(basename "$f")  "
    ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | \
      awk '/mean_volume|max_volume/ {printf "%s ", $0} END{print ""}'
  done
}

scan_dir ch_T1
scan_dir ch_T2
