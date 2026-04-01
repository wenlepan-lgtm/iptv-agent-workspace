#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="./AecVideoTestApp/app/src/main/assets/models/sensevoice"

run_one () {
  local wav="$1"
  local tag="$2"
  local out="asr_${tag}_$(basename "$wav" .wav).txt"
  python run_asr_sensevoice.py "$MODEL_DIR" "$wav" > "$out"
  # 统计“有效字符数”：去掉标题线/空白，粗略看转写量
  local n=$(grep -vE '^(=+|sherpa-onnx|$)' "$out" | tr -d '[:space:]' | wc -m | tr -d ' ')
  echo "$tag $(basename "$wav")  chars=$n  -> $out"
}

echo "=== Scan T1 ==="
for f in ch_T1/*.wav; do run_one "$f" "T1"; done

echo "=== Scan T2 ==="
for f in ch_T2/*.wav; do run_one "$f" "T2"; done
