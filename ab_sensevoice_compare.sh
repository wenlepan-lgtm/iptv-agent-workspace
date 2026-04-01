#!/usr/bin/env bash
set -euo pipefail

# Inputs
A="T1_with_ref.wav"
B="T1_no_ref.wav"

# Outputs
AEQ="T1_with_ref_eq.wav"
BEQ="T1_no_ref_eq.wav"

MODEL_DIR="./AecVideoTestApp/app/src/main/assets/models/sensevoice"

if [[ ! -f "$A" || ! -f "$B" ]]; then
  echo "Missing input wav files: $A or $B"
  exit 1
fi
if [[ ! -d "$MODEL_DIR" ]]; then
  echo "Missing model dir: $MODEL_DIR"
  echo "Fix MODEL_DIR in this script or run from the correct project root."
  exit 1
fi

echo "== 1) Check durations =="
durA=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$A")
durB=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$B")
minDur=$(python3 - <<PY
a=float("$durA"); b=float("$durB")
print(f"{min(a,b):.6f}")
PY
)
echo "A: ${durA}s   B: ${durB}s   -> Trim both to ${minDur}s"
echo

echo "== 2) Trim to equal length (fair AB) =="
ffmpeg -y -hide_banner -loglevel error -i "$A" -t "$minDur" -c copy "$AEQ"
ffmpeg -y -hide_banner -loglevel error -i "$B" -t "$minDur" -c copy "$BEQ"

echo "Verify eq wavs:"
ffprobe -hide_banner -i "$AEQ" 2>&1 | grep -E "Duration|Audio" || true
ffprobe -hide_banner -i "$BEQ" 2>&1 | grep -E "Duration|Audio" || true
echo

echo "== 3) Run SenseVoice ASR =="
# IMPORTANT: use python3 if your env requires it
python3 run_asr_sensevoice.py "$MODEL_DIR" "$AEQ" > with_ref.txt
python3 run_asr_sensevoice.py "$MODEL_DIR" "$BEQ" > no_ref.txt
echo "Wrote: with_ref.txt, no_ref.txt"
echo

echo "== 4) Count chars & compute drop =="
python3 - <<'PY'
import re, pathlib

def norm_count(s: str) -> int:
    # keep CJK/letters/digits; drop spaces/punct
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    return len(s)

w = pathlib.Path("with_ref.txt").read_text(encoding="utf-8", errors="ignore")
n = pathlib.Path("no_ref.txt").read_text(encoding="utf-8", errors="ignore")

cw, cn = norm_count(w), norm_count(n)

print(f"with_ref chars={cw}  preview={w[:120]!r}")
print(f"no_ref   chars={cn}  preview={n[:120]!r}")

if cn > 0:
    drop = (cn - cw) / cn * 100.0
    print(f"\nDrop vs no_ref: {drop:.1f}%  (higher is better)")
    if drop >= 50:
        print("=> Verdict: REF likely effective (>=50% reduction). Keep investing in ref/AEC path.")
    elif drop >= 20:
        print("=> Verdict: REF maybe helps (20~50%). Check ref level/series resistors/config.")
    else:
        print("=> Verdict: REF not effective (<20%). Probably not used or level/config wrong; consider KWS/VAD.")
else:
    print("\nno_ref chars=0 -> cannot compute drop; increase T1 duration or volume.")
PY

echo
echo "Done."
