#!/usr/bin/env bash
set -euo pipefail

A="T1_with_ref.wav"
B="T1_no_ref.wav"

if [[ ! -f "$A" || ! -f "$B" ]]; then
  echo "Missing wav files: $A or $B"
  exit 1
fi

echo "== ffprobe =="
ffprobe -hide_banner -i "$A" 2>&1 | grep -E "Duration|Audio" || true
ffprobe -hide_banner -i "$B" 2>&1 | grep -E "Duration|Audio" || true
echo

python3 - <<'PY'
import wave, math, numpy as np, os

files = ["T1_with_ref.wav","T1_no_ref.wav"]

def wav_dur(path):
    with wave.open(path, 'rb') as w:
        return w.getnframes() / w.getframerate(), w.getframerate(), w.getnchannels(), w.getsampwidth()

def read_int16_mono(path, target_frames=None):
    with wave.open(path, 'rb') as w:
        ch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        n  = w.getnframes()
        assert ch == 1, f"{path}: channels={ch}, expected 1"
        assert sw == 2, f"{path}: sampwidth={sw}, expected 2"
        if target_frames is None:
            target_frames = n
        target_frames = min(target_frames, n)
        raw = w.readframes(target_frames)
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return x, sr, target_frames

# read durations
meta = {}
for f in files:
    dur, sr, ch, sw = wav_dur(f)
    meta[f] = (dur, sr, ch, sw)

min_dur = min(meta[f][0] for f in files)
sr = meta[files[0]][1]
min_frames = int(round(min_dur * sr))

print(f"Min duration used for fair AB: {min_dur:.3f}s ({min_frames} frames @ {sr}Hz)")
print()

def stats(x):
    rms = float(np.sqrt(np.mean(x*x)))
    peak = float(np.max(np.abs(x)))
    clip = float(np.mean(np.abs(x) >= 32760) * 100.0)  # % of samples near int16 max
    zcr = float(np.mean(x[:-1] * x[1:] < 0) * sr)       # approx zero-crossings per sec
    return rms, peak, clip, zcr

for f in files:
    x, sr, used = read_int16_mono(f, target_frames=min_frames)
    rms, peak, clip, zcr = stats(x)
    print(f"{f}")
    print(f"  used_dur = {used/sr:.3f}s")
    print(f"  RMS      = {rms:.1f}")
    print(f"  PEAK     = {peak:.0f}")
    print(f"  CLIP%    = {clip:.3f}%")
    print(f"  ZCR/s    = {zcr:.1f}")
    print()
PY

echo "Done."
