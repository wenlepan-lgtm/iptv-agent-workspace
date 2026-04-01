#!/usr/bin/env bash
set -euo pipefail
for f in hk_ch/L.wav hk_ch/R.wav; do
  echo "== $(basename "$f") =="
  ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | \
    awk -F': ' '/mean_volume|max_volume/ {print $1": "$2}'
done
