# Phase-0 抓 log 与识别率调参说明

## 一、你本机执行的 adb 命令（抓 log 时用）

机顶盒 USB 连接后，在**本机终端**执行（读静夜思前先运行，读完后 Ctrl+C 停）：

```bash
# 只看 ASR / 音频相关（推荐，便于看 meanVol 和 VAD）
adb logcat -c && adb logcat | grep -E "ASR|StereoToMono|AudioRecord|ASR_Controller|Vosk"
```

若要**保存到文件**（方便事后发给别人看）：

```bash
adb logcat -c && adb logcat | grep -E "ASR|StereoToMono|AudioRecord|ASR_Controller|Vosk" | tee ~/Desktop/asr_phase0_$(date +%Y%m%d_%H%M%S).txt
```

抓 log 时：**安静环境，你读整首《静夜思》**，看终端里 `meanVol`、`VOICE_START`/`VOICE_END`、以及最终识别出的文本。

---

## 二、log 里重点看什么

| 内容 | 含义 |
|------|------|
| `StereoToMono: mode=avg, meanVol=xxx, max=xxx` | 当前帧音量；若 meanVol 长期 &lt; 120，容易被判成静音 |
| `ASR_STATE=VOICE_START rms=xxx threshold=120` | 判定成“开始说话”；若读诗过程中很少出现或中途出现 VOICE_END，可能是 VAD 把句中当静音 |
| `ASR_STATE=VOICE_END rms=xxx threshold=120` | 判定成“说完”；若在句中出现多次，会**提前触发 final**，长句被切成多段，出现“意思 简历 有”这类碎片 |
| `Warmup: xxxms/300ms` | 前 300ms 不送 ASR；若你开口太早，首字可能被吃掉 |

**结论**：若 log 里读诗时 **meanVol 经常 &lt; 120**，或句中频繁出现 **VOICE_END**，多半是 **VAD 阈值 120 偏高**，导致“句中判静音 → 800ms 后 final → 长句被截断、识别成碎片”。

---

## 三、识别率低的可能原因与对应措施

### 1. VAD 阈值偏高（最常见）

- **现象**：整句/静夜思被拆成多段，出现“意思 简历 有”“调 往 里头 下”等。
- **原因**：`asr.vad.threshold=120` 对远讲或 downmix 后电平偏低时，容易把**句中**判成静音，800ms 后触发 final，后续又重新 VOICE_START，形成多段。
- **操作**：在 `config.properties` 里**降低阈值**，例如：
  ```properties
  asr.vad.threshold=80.0
  ```
  或先试 `100.0`。然后重新装 APK/重启应用，再读静夜思抓 log，看 meanVol 是否多数 &gt; 新阈值、VOICE_END 是否减少。

### 2. 静音判定 800ms 过短

- **现象**：读诗时自然停顿（如“床前明月光”和“疑是地上霜”之间）就被当成“说完了”，触发 final。
- **原因**：`asr.vad.silence.duration.ms=800`，句中停顿超过 800ms 就出结果。
- **操作**：适当加大，例如：
  ```properties
  asr.vad.silence.duration.ms=1200
  ```
  长句（如静夜思）可再试 1500。

### 3. 暖机 300ms 吃掉首字

- **现象**：第一句或第一个字经常错/漏。
- **原因**：前 300ms 不送 ASR，开口太早时首字在暖机内。
- **操作**：可略减暖机（例如 200），或读诗时稍晚 0.5 秒再开始：
  ```properties
  asr.multichannel.warmup.ms=200
  ```

### 4. 3:1 重采样较粗糙

- **现象**：调低 VAD 后仍有个别字错/糊。
- **原因**：48k→16k 用“每 3 个取 1”的简单抽取，无低通，可能有轻微混叠。
- **操作**：属次要因素，先优先把 VAD/静音时长调稳，再考虑后续换线性插值或简单低通再抽。

---

## 四、建议的调试顺序

1. **先抓一轮 log**：用上面 adb 命令，安静环境读静夜思，看 meanVol 分布和 VOICE_START/VOICE_END 出现时机。
2. **若 meanVol 经常 &lt; 120**：把 `asr.vad.threshold` 改为 **80** 或 **100**，再测。
3. **若句中多次 VOICE_END**：在改阈值基础上，把 `asr.vad.silence.duration.ms` 改为 **1200** 或 **1500**，再测。
4. **再抓一轮 log**：确认 meanVol 多数高于新阈值、VOICE_END 只在句末出现，且识别整句/静夜思关键字是否明显改善。

---

## 五、config.properties 示例（优先试）

```properties
# 降低 VAD 阈值，避免句中误判静音（远讲/小声时重要）
asr.vad.threshold=80.0
# 句中自然停顿不急于 final（长句/古诗）
asr.vad.silence.duration.ms=1200
# 其余保持
asr.input.sample_rate=48000
asr.input.channels=2
asr.stereo_to_mono.mode=avg
asr.multichannel.warmup.ms=300
```

修改后需**重新打包或确保 config 随 APK/数据更新到机顶盒**，再测静夜思并抓 log。
