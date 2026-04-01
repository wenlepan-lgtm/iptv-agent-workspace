#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <input_stereo_wav>"
  exit 1
fi

IN="$1"
if [[ ! -f "$IN" ]]; then
  echo "ERROR: input not found: $IN"
  exit 1
fi

BASE="$(basename "$IN" .wav)"
OUTDIR="mono_${BASE}"
mkdir -p "$OUTDIR"

echo "== 1) Split L/R =="
ffmpeg -y -hide_banner -loglevel error -i "$IN" \
  -filter_complex "channelsplit=channel_layout=stereo[L][R]" \
  -map "[L]" "$OUTDIR/L.wav" \
  -map "[R]" "$OUTDIR/R.wav"

echo "== 2) Make mono L/R (keep 48k) =="
ffmpeg -y -hide_banner -loglevel error -i "$OUTDIR/L.wav" -ac 1 "$OUTDIR/L_mono.wav"
ffmpeg -y -hide_banner -loglevel error -i "$OUTDIR/R.wav" -ac 1 "$OUTDIR/R_mono.wav"

echo "== 3) Fixed downmix (L+R)/2 =="
ffmpeg -y -hide_banner -loglevel error \
  -i "$OUTDIR/L_mono.wav" -i "$OUTDIR/R_mono.wav" \
  -filter_complex "[0:a][1:a]amix=inputs=2:weights=0.5 0.5:normalize=0" \
  "$OUTDIR/avg_mono.wav"

echo "== 4) pick_mono & adaptive_mono (python, 20ms frame) =="
python3 - "$OUTDIR/L_mono.wav" "$OUTDIR/R_mono.wav" "$OUTDIR" <<'PY'
import sys, wave, math
from array import array

L_path, R_path, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
eps = 1e-9

def read_wav_i16(path):
    with wave.open(path, 'rb') as w:
        ch = w.getnchannels()
        assert ch == 1, f"expected mono, got {ch}"
        sr = w.getframerate()
        n = w.getnframes()
        data = w.readframes(n)
    a = array('h')
    a.frombytes(data)
    return sr, a

def write_wav_i16(path, sr, samples):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        a = array('h', samples)
        w.writeframes(a.tobytes())

def rms(frame):
    if not frame:
        return 0.0
    s = 0.0
    for x in frame:
        s += x*x
    return math.sqrt(s/len(frame))

def clamp16(x):
    if x > 32767: return 32767
    if x < -32768: return -32768
    return int(x)

srL, L = read_wav_i16(L_path)
srR, R = read_wav_i16(R_path)
assert srL == srR, "L/R sample rate mismatch"
n = min(len(L), len(R))
L = L[:n]; R = R[:n]
sr = srL

frame = int(sr * 0.02)  # 20ms
if frame <= 0: frame = 960

# A) pick_mono: 每帧选 RMS 更大的那路（无平滑，容易“抽风”，但可做上限对照）
pick = []
for i in range(0, n, frame):
    lf = L[i:i+frame]
    rf = R[i:i+frame]
    if rms(lf) >= rms(rf):
        pick.extend(lf)
    else:
        pick.extend(rf)
pick = pick[:n]
write_wav_i16(f"{outdir}/pick_mono.wav", sr, pick)

# B) adaptive_mono: alpha 自适应 + 平滑（厂家推荐路线）
alpha = 0.5
adaptive = []
for i in range(0, n, frame):
    lf = L[i:i+frame]
    rf = R[i:i+frame]
    rL = rms(lf)
    rR = rms(rf)
    w = (rL - rR) / (rL + rR + eps)   # -1..1
    if w > 1: w = 1
    if w < -1: w = -1
    a_new = 0.5 + 0.5 * w
    alpha = 0.9 * alpha + 0.1 * a_new  # smoothing
    for j in range(len(lf)):
        m = alpha*lf[j] + (1-alpha)*rf[j]
        adaptive.append(clamp16(m))
adaptive = adaptive[:n]
write_wav_i16(f"{outdir}/adaptive_mono.wav", sr, adaptive)

print("Generated pick_mono.wav & adaptive_mono.wav in", outdir)
PY

echo "== 5) Resample everything to 16k mono for ASR =="
for f in L_mono.wav R_mono.wav avg_mono.wav pick_mono.wav adaptive_mono.wav; do
  ffmpeg -y -hide_banner -loglevel error -i "$OUTDIR/$f" -ar 16000 -ac 1 "$OUTDIR/${f%.wav}_16k.wav"
done

echo "== 6) Quick volume stats (16k files) =="
for f in "$OUTDIR/"*_16k.wav; do
  mv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/mean_volume/ {print $2}')
  xv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/max_volume/ {print $2}')
  printf "%-26s mean=%-10s max=%s\n" "$(basename "$f")" "$mv" "$xv"
done

echo "DONE -> $OUTDIR"
