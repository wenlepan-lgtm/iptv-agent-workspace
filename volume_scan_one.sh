#!/usr/bin/env bash
set -euo pipefail
dir="$1"
echo "=== $dir ==="
for f in "$dir"/*.wav; do
  mv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/mean_volume/ {print $2}')
  xv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/max_volume/ {print $2}')
  printf "%-22s mean=%-10s max=%s\n" "$(basename "$f")" "$mv" "$xv"
done | sort -k2
