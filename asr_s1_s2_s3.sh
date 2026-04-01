#!/usr/bin/env bash
set -euo pipefail
MODEL_DIR="./AecVideoTestApp/app/src/main/assets/models/sensevoice"

for s in S1_steady S2_side S3_far; do
  echo "==== $s ===="
  # 直接用 mk_mono_variants 产物
  for v in L_mono_16k R_mono_16k avg_mono_16k adaptive_mono_16k pick_mono_16k; do
    wav="mono_${s}/${v}.wav"
    out="asr_${s}_${v}.txt"
    python3 run_asr_sensevoice.py "$MODEL_DIR" "$wav" > "$out"
    # 粗略统计有效字符（不含标题线）
    n=$(grep -vE '^(=+|sherpa-onnx|$)' "$out" | tr -d '[:space:]' | wc -m | tr -d ' ')
    printf "%-28s chars=%s -> %s\n" "${s}_${v}" "$n" "$out"
  done
  echo
done
