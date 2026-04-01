#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="./AecVideoTestApp/app/src/main/assets/models/sensevoice"

run_one () {
  local wav="$1"
  local tag="$2"   # T1 or T2
  local out="asr_${tag}_$(basename "$wav" .wav).txt"
  python3 run_asr_sensevoice.py "$MODEL_DIR" "$wav" > "$out"
  # 统计有效字符数（排除标题/空行）
  local n
  n=$(grep -vE '^(=+|sherpa-onnx|$)' "$out" | tr -d '[:space:]' | wc -m | tr -d ' ')
  echo "$tag $(basename "$wav")  chars=$n  -> $out"
}

scan_tag () {
  local tag="$1"; shift
  echo "=== Scan $tag ==="
  for f in "$@"; do
    run_one "$f" "$tag"
  done
}

# 用法： ./asr_4ch_ab.sh
scan_tag "T1" ch_T1/ch1_FL.wav ch_T1/ch2_FR.wav ch_T1/ch3_FC.wav ch_T1/ch4_LFE.wav
scan_tag "T2" ch_T2/ch1_FL.wav ch_T2/ch2_FR.wav ch_T2/ch3_FC.wav ch_T2/ch4_LFE.wav

echo "=== Summary (chars) ==="
paste <(ls ch_T1/ch{1..4}_*.wav | xargs -n1 basename | sed 's/.wav//') \
      <(grep -h "chars=" asr_T1_*.txt | awk -F'chars=' '{print $2}' | tr -d ' ') \
      <(grep -h "chars=" asr_T2_*.txt | awk -F'chars=' '{print $2}' | tr -d ' ') \
  2>/dev/null || true
