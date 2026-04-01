#!/usr/bin/env bash
set -euo pipefail

A="T1_with_ref_eq.wav"
B="T1_no_ref_eq.wav"

if [[ ! -f "$A" || ! -f "$B" ]]; then
  echo "Missing eq wav files. Run ./ab_trim_equal.sh first."
  exit 1
fi

# ====== 你只需要改这里：把下面这行替换成你本机实际可跑的 SenseVoice 识别命令 ======
# 要求：命令最后必须能输出纯文本到 stdout（或你自己重定向）
ASR_CMD_TEMPLATE='REPLACE_ME --wav "{wav}"'
# 示例（举例而已，不一定是你的）：
# ASR_CMD_TEMPLATE='python3 ./sherpa-onnx/python-api-examples/sense-voice/sense-voice.py --model ./sense-voice-small --wav "{wav}"'
# ===========================================================================

run_asr() {
  local wav="$1"
  local out="$2"
  local cmd="${ASR_CMD_TEMPLATE/\{wav\}/$wav}"
  echo "CMD: $cmd"
  eval "$cmd" > "$out"
  echo "Wrote $out"
}

run_asr "$A" "with_ref.txt"
run_asr "$B" "no_ref.txt"

python3 - <<'PY'
import re, pathlib

def norm_count(s: str) -> int:
    # 统计中文/英文/数字（去掉空白与标点）
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    return len(s)

for tag, fn in [("with_ref","with_ref.txt"), ("no_ref","no_ref.txt")]:
    txt = pathlib.Path(fn).read_text(encoding="utf-8", errors="ignore")
    n = norm_count(txt)
    print(f"{tag:8s} chars={n:6d} preview={txt[:80]!r}")

# 简单给出降幅
w = pathlib.Path("with_ref.txt").read_text(encoding="utf-8", errors="ignore")
n = pathlib.Path("no_ref.txt").read_text(encoding="utf-8", errors="ignore")
cw, cn = norm_count(w), norm_count(n)
if cn > 0:
    drop = (cn - cw) / cn * 100.0
    print(f"\nDrop vs no_ref: {drop:.1f}%  (higher is better)")
else:
    print("\nno_ref chars=0, cannot compute drop.")
PY
