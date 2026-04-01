# AMB37（Mic Arry 8 / SmartIC）在 RK3576 上的通道情况确认（给 Cursor/Cursor Review 用）

> 目标：确认 **AMB37 通过 USB 输出的通道数/采样率/布局**，并进一步判定：  
> - 哪几路是“人声主麦通道”  
> - 是否存在“回采/参考(reference)通道”，以及它落在哪几路  
> - 为后续 AEC/beamforming/ASR 输入选路提供确定性依据

---

## 0. 你当前已经观测到的事实（关键）

### 0.1 ALSA 声卡枚举
你已执行：

```bash
adb shell cat /proc/asound/cards
adb shell cat /proc/asound/pcm
```

输出显示 **card2 是 AMB37**：

- `2 [M8]: USB-Audio - Mic Arry 8`

### 0.2 通道/采样率（最关键）
你已执行：

```bash
adb shell cat /proc/asound/card2/stream0
```

得到：

- Capture
- `Format: S16_LE`
- `Channels: 8`
- `Rates: 16000`
- `Channel map: FL FR FC LFE SL SR FLC FRC`

**结论（确定）：AMB37 当前在盒子上枚举为 “8 通道、16k、16bit PCM、7.1 layout”。**

---

## 1. 为什么 tinycap 会 Permission denied

你从 Mac 端直接跑：

```bash
adb shell "tinycap ... "
```

会遇到 `Permission denied`，因为 `/dev/snd/pcmC2D0c` 需要 root 访问。

你测试过 `su` 不支持 `-c` 参数（很多国产 ROM 的 su 就是这种实现），因此要用“交互式 root shell”。

正确方式：

```bash
adb shell
su
# 现在已经是 root
tinycap ...
```

---

## 2. 录制 8ch 原始 WAV（root 下）

### 2.1 录制 8 秒（8ch@16k）
```bash
adb shell
su
rm -f /sdcard/amb37_8ch_8s.wav
tinycap /sdcard/amb37_8ch_8s.wav -D 2 -d 0 -r 16000 -b 16 -c 8 -T 8
exit
exit
adb pull /sdcard/amb37_8ch_8s.wav .
```

> 说明：  
> - `-D 2 -d 0` 对应 `card2 device0 capture`（与你的 `/proc/asound/pcm` 一致）  
> - 必须 `-c 8`，否则会得到错误的参数或 “Invalid argument”。

### 2.2 快速验证文件格式
```bash
ffprobe -hide_banner -i amb37_8ch_8s.wav 2>&1 | grep -E "Duration|Audio"
```

期望看到：
- `16000 Hz, 8 channels, s16`

---

## 3. 拆分 8 通道到单通道 WAV（Mac 端）

由于 layout 是 7.1，最稳的方法是 channelsplit：

```bash
mkdir -p amb_ch
ffmpeg -y -hide_banner -loglevel error -i amb37_8ch_8s.wav \
  -filter_complex "channelsplit=channel_layout=7.1[FL][FR][FC][LFE][SL][SR][FLC][FRC]" \
  -map "[FL]"  amb_ch/ch1_FL.wav \
  -map "[FR]"  amb_ch/ch2_FR.wav \
  -map "[FC]"  amb_ch/ch3_FC.wav \
  -map "[LFE]" amb_ch/ch4_LFE.wav \
  -map "[SL]"  amb_ch/ch5_SL.wav \
  -map "[SR]"  amb_ch/ch6_SR.wav \
  -map "[FLC]" amb_ch/ch7_FLC.wav \
  -map "[FRC]" amb_ch/ch8_FRC.wav
```

---

## 4. 一眼识别“哪几路是主麦/哪几路是参考”的最短实验

> 注意：**不要靠猜**。AMB37 不同固件/模式下通道含义会变。  
> 下面三组实验能在 10 分钟内给出“确定性映射”。

### 实验 A：纯人声（TV 静音、不要接回采线）
- 人在麦前朗读《静夜思》，持续 8 秒  
- 录一段 `A_speech_only.wav`（8ch）

判定规则：
- RMS/mean_volume 明显更高的通道 = **人声主通道**（通常落在 1/2/3 或 1-4）
- 近似 -91dB / -81dB 的通道 = **空/静音/无效通道**

### 实验 B：纯电视（不说话、TV 播放视频）
- 播放新闻，保持音量固定  
- 录一段 `B_tv_only.wav`（8ch）

判定规则：
- 如果 TV 声主要出现在 1/2/3：说明主要是“空气传播回灌”  
- 如果 TV 声主要出现在 7/8：说明存在“参考/回采注入通道”（但要进一步看是否可控/线性）

### 实验 C：TV 静音 + 接回采线（不说话）
- TV 静音  
- 接回采线  
- 录一段 `C_ref_inject_only.wav`

判定规则：
- 若 7/8 依然很大：**回采线不是“电视播放参考PCM”**，更像 “固定注入/电平被放大/接点不对”
- 若 7/8 随 TV 音量变化：才可能作为 AEC reference 候选（还需相位/延迟测量）

---

## 5. 批量计算每路音量（mean/max）

你已用过脚本，这里给一个更干净版本（针对 `amb_ch` 目录）：

```bash
cat > amb_volume_scan.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
dir="${1:-amb_ch}"
echo "=== $dir ==="
for f in "$dir"/*.wav; do
  mv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/mean_volume/ {print $2}')
  xv=$(ffmpeg -hide_banner -nostats -i "$f" -af volumedetect -f null - 2>&1 | awk -F': ' '/max_volume/ {print $2}')
  printf "%-18s mean=%-10s max=%s\n" "$(basename "$f")" "$mv" "$xv"
done
EOF
chmod +x amb_volume_scan.sh
./amb_volume_scan.sh amb_ch
```

---

## 6. 给 ASR 用哪个通道（基于你目前观测到的模式）

你已经观测到类似的模式（示例）：

- ch1/ch2/ch3：人声强（约 -34 ~ -36 dB）
- ch7/ch8：可能是“回采注入”或某种参考（约 -27 dB）
- ch5/ch6：几乎静音

**因此给 ASR 的输入优先选：`ch3_FC`（或在 `ch1/ch2/ch3` 中动态选能量最大的一路）。**  
**不要把 ch7/ch8 直接送 ASR**（它们更像参考/注入通道，会把系统声/注入声带入识别）。

---

## 7. 下一步建议（Cursor/Claude 研发任务）

### 7.1 先做“动态选路”前端（低成本高收益）
- 候选通道：ch1/ch2/ch3
- 每 20ms 计算 RMS
- 选最大输出到 1ch PCM → ASR

### 7.2 再评估“是否能做真 AEC”
真 AEC 的前提：reference 必须满足：
- 与 TV 音量/播放内容强相关（可控）
- 电平合理（不饱和、不固定 0dB）
- 与 mic 通道存在稳定延迟/相位关系（可估计）

如果当前回采线注入的信号不满足上述条件，**不要浪费时间做 AEC**，先用：
- 动态选路 + VAD/KWS 门控
- 再逐步升级 beamforming

---

## 8. 需要你补充给 Cursor 的最小证据（把结果贴回去即可）

1) `adb shell cat /proc/asound/card2/stream0` 全部输出（你已有）  
2) 三个场景 A/B/C 的 `amb_volume_scan.sh` 输出结果  
3) 每个场景任选 1~2 路（比如 ch3、ch7）的 ffplay 听感描述  
4) （可选）ASR 对 ch3 的识别文本（看电视误识别是否下降）

只要这些证据齐了，通道映射就能“定版”，后面代码改动就不会盲人摸象。
