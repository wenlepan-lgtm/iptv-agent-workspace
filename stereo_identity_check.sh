#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <stereo_wav>"
  exit 1
fi

IN="$1"
if [[ ! -f "$IN" ]]; then
  echo "ERROR: not found: $IN"
  exit 1
fi

TMPDIR="_chk_$(basename "$IN" .wav)"
mkdir -p "$TMPDIR"

# split L/R
ffmpeg -y -hide_banner -loglevel error -i "$IN" \
  -filter_complex "channelsplit=channel_layout=stereo[L][R]" \
  -map "[L]" "$TMPDIR/L.wav" \
  -map "[R]" "$TMPDIR/R.wav"

# make mono + 16k
ffmpeg -y -hide_banner -loglevel error -i "$TMPDIR/L.wav" -ac 1 -ar 16000 "$TMPDIR/L_16k.wav"
ffmpeg -y -hide_banner -loglevel error -i "$TMPDIR/R.wav" -ac 1 -ar 16000 "$TMPDIR/R_16k.wav"

# L-R (如果 L≈R，这个会接近静音)
ffmpeg -y -hide_banner -loglevel error \
  -i "$TMPDIR/L_16k.wav" -i "$TMPDIR/R_16k.wav" \
  -filter_complex "[0:a][1:a]amix=inputs=2:weights=1 -1:normalize=0,volume=2" \
  "$TMPDIR/L_minus_R_16k.wav"

echo "== volumedetect =="
for f in "$TMPDIR/L_16k.wav" "$TMPDIR/R_16k.wav" "$TMPDIR/L_minus_R_16k.wav"; do
  mv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/mean_volume/ {print $2}')
  xv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/max_volume/ {print $2}')
  printf "%-20s mean=%-10s max=%s\n" "$(basename "$f")" "$mv" "$xv"
done

echo
echo "== correlation check (python) =="
python3 - "$TMPDIR/L_16k.wav" "$TMPDIR/R_16k.wav" <<'PY'
import sys, wave, math
from array import array

def read_i16(path):
    with wave.open(path,'rb') as w:
        assert w.getnchannels()==1
        n=w.getnframes()
        sr=w.getframerate()
        data=w.readframes(n)
    a=array('h'); a.frombytes(data)
    return sr, a

_, L = read_i16(sys.argv[1])
_, R = read_i16(sys.argv[2])
n=min(len(L),len(R))
L=L[:n]; R=R[:n]

# corr
meanL=sum(L)/n
meanR=sum(R)/n
num=0.0; denL=0.0; denR=0.0
for i in range(n):
    x=L[i]-meanL
    y=R[i]-meanR
    num += x*y
    denL += x*x
    denR += y*y
corr = num / (math.sqrt(denL*denR)+1e-12)

# avg abs diff (normalized)
absdiff = sum(abs(L[i]-R[i]) for i in range(n)) / n
avgabs  = (sum(abs(L[i]) for i in range(n))/n + sum(abs(R[i]) for i in range(n))/n) / 2
ratio = absdiff / (avgabs + 1e-12)

print(f"corr(L,R) = {corr:.6f}   (1.0 means identical)")
print(f"avg_abs_diff_ratio = {ratio:.6f}   (~0 means identical)")
PY

echo
echo "Outputs in: $TMPDIR"
