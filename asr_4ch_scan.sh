#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR="./AecVideoTestApp/app/src/main/assets/models/sensevoice"

run_one () {
  local wav="$1"
  local tag="$2"
  local out="asr_${tag}_$(basename "$wav" .wav).txt"
  python3 run_asr_sensevoice.py "$MODEL_DIR" "$wav" > "$out"
  local n=$(grep -vE '^(=+|sherpa-onnx|$)' "$out" | tr -d '[:space:]' | wc -m | tr -d ' ')
  echo "$tag $(basename "$wav")  chars=$n  -> $out"
}

echo "=== Scan ==="
for f in "$@"; do
  run_one "$f" "TEST"
done
