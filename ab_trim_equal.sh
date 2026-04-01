#!/usr/bin/env bash
set -euo pipefail

A="T1_with_ref.wav"
B="T1_no_ref.wav"
OUTA="T1_with_ref_eq.wav"
OUTB="T1_no_ref_eq.wav"

# Get durations (seconds) via ffprobe
durA=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$A")
durB=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$B")

# Use min duration
minDur=$(python3 - <<PY
a=float("$durA"); b=float("$durB")
print(f"{min(a,b):.6f}")
PY
)

echo "Trim both to min duration: ${minDur}s"

ffmpeg -y -hide_banner -loglevel error -i "$A" -t "$minDur" -c copy "$OUTA"
ffmpeg -y -hide_banner -loglevel error -i "$B" -t "$minDur" -c copy "$OUTB"

echo "Wrote:"
echo "  $OUTA"
echo "  $OUTB"

echo "Verify durations:"
ffprobe -hide_banner -i "$OUTA" 2>&1 | grep -E "Duration|Audio" || true
ffprobe -hide_banner -i "$OUTB" 2>&1 | grep -E "Duration|Audio" || true
