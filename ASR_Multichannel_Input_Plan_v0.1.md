# 多声道输入给 ASR 的落地方案（AMB37 8ch@16k）— 给 Claude / Cursor

> 你们只需要按本文实现，不需要你们去做现场测试（现场测试由 Ala 执行）。  
> 目标：在 **RK3576 Android 14 + AMB37（USB 8 通道阵列）** 上，把“多声道阵列”转成一条更干净的单声道流式音频，显著降低电视/环境声误识别。

---

## 0. 已知硬事实（无需再争论）

来自设备 `/proc/asound/card2/stream0`：

- **Capture: 8 channels**
- **Rate: 16000**
- **Format: S16_LE**
- **Channel map: FL FR FC LFE SL SR FLC FRC**（7.1 布局）

结合已做的音量扫描（volumedetect）结果特征：

- **ch1_FL / ch2_FR / ch3_FC / ch4_LFE**：有明显有效信号（人声/电视回灌都会出现在这里）
- **ch5_SL / ch6_SR**：基本静音（≈ -91 dB）→ **可忽略**
- **ch7_FLC / ch8_FRC**：在“接回采线”时电平显著升高 → **更像 reference/注入通道**（用于后续 AEC 或回采验证），**不应直接送入 ASR 作为主输入**

> 因此：ASR 主输入候选只在 **{ch1,ch2,ch3}（必要时含 ch4）** 中选；  
> **ch7/ch8 只作为 reference 预留，不进 ASR 输入**。

---

## 1. 为什么“多声道直接喂 ASR”是错的

ASR（SenseVoice / sherpa-onnx 等）通常接受 **1ch 或 2ch**。  
把 8ch 混在一起等价于把噪声/电视声也叠加放大，**SNR 更差**，误识别更高。

正确工程链路是：

**8ch 捕获 → 多声道前端融合（选路/合成/波束形成）→ 输出 1ch clean → ASR**

---

## 2. 推荐落地顺序（最短闭环）

### 2.1 v0.1：动态选路（Channel Picker）✅ ROI 最高、实现最简单
核心思想：每帧（20ms）计算 ch1~ch3 能量，选能量最大的那一路输出。

优点：
- 对“近讲人声方向变化”自适应
- 对电视声（更均匀/分散）有天然抑制
- 只需要 Kotlin，无需 JNI

**结论：v0.1 先做 Channel Picker**（不要一上来做复杂 beamforming）。

### 2.2 v0.2：三路加权合成（Soft Mix / Lite Beamforming）
把 ch1~ch3 按权重相加输出（权重可按能量动态调整），比硬切换更平滑。

### 2.3 v0.3：真阵列算法（MVDR/DS beamforming + WebRTC NS）
高收益但工程复杂（更适合 JNI/C++）。

---

## 3. ASR 输入通道策略（直接写死给你们）

### 3.1 主输入候选集合
- `CANDIDATES = [ch3_FC, ch1_FL, ch2_FR]`
- `OPTIONAL = ch4_LFE`（仅当现场确认 ch4 有明显人声且不脏时再加入）

### 3.2 禁用集合
- `IGNORE = [ch5_SL, ch6_SR]`（基本静音）
- `RESERVED_REF = [ch7_FLC, ch8_FRC]`（reference/注入预留，不进 ASR）

---

## 4. 代码层实现要点（iptv-edge-agent / Kotlin）

### 4.1 AudioRecord 配置（必须拿到 8ch）
- sampleRate = 16000
- encoding = PCM_16BIT
- channelMask：
  - Android Java 层对 8ch capture 支持不一致（ROM 相关）。如果标准 `CHANNEL_IN_7POINT1` 不可用，则走枚举/兜底：
    - 优先尝试 `AudioFormat.CHANNEL_IN_7POINT1`
    - 失败则退回 `CHANNEL_IN_STEREO` 并打印告警（但这会损失阵列价值）

> 你们的实现要把“是否真的拿到 8ch”打印成硬日志：  
> `AudioRecord.getChannelCount()` / `getFormat()` / `getSampleRate()`。

### 4.2 多声道 PCM 解析（deinterleave）
AudioRecord read() 读出来的是 **交织(interleaved)** short[]：

`[ch1_s0, ch2_s0, ..., ch8_s0, ch1_s1, ch2_s1, ..., ch8_s1, ...]`

需要按 channelCount 做 deinterleave，取出 ch1~ch3 的帧。

### 4.3 帧长
建议 20ms：
- frameSamples = 16000 * 0.02 = **320 samples / channel**
- interleavedShortsPerFrame = 320 * 8 = **2560 shorts**

---

## 5. 动态选路算法（v0.1 直接上）

### 5.1 RMS 能量计算
对候选通道每帧算一次 RMS（或 mean absolute）即可。

### 5.2 防抖 / 滞后（必须有）
避免“每帧乱跳”，加入 hysteresis：

- `SWITCH_DB = 3dB`（新通道能量比当前高 3dB）
- `HOLD_MS = 200ms`（持续满足才切换）

### 5.3 输出
把“当前选中通道”的 320 samples 直接写入 monoBuffer，送入 ASR streaming。

---

## 6. 伪代码（Kotlin 方向，便于你们直接落地）

```kotlin
val candidates = intArrayOf(2, 0, 1) // ch3, ch1, ch2  (0-based index)
var current = candidates[0]
var holdCount = 0

fun processFrame(interleaved: ShortArray, chCount: Int, frameSamples: Int): ShortArray {
    // 1) deinterleave候选通道 + 计算能量
    val energy = DoubleArray(candidates.size)
    for (i in 0 until frameSamples) {
        val base = i * chCount
        for (k in candidates.indices) {
            val ch = candidates[k]
            val s = interleaved[base + ch].toInt()
            energy[k] += (s * s).toDouble()
        }
    }
    // 2) 找最大能量通道
    var bestIdx = 0
    for (k in 1 until energy.size) if (energy[k] > energy[bestIdx]) bestIdx = k
    val bestCh = candidates[bestIdx]

    // 3) hysteresis (简化版本：能量比阈值 + 连续帧计数)
    if (bestCh != current) {
        val ratio = energy[bestIdx] / (energy[candidates.indexOf(current)] + 1e-9)
        val db = 10.0 * kotlin.math.log10(ratio)
        if (db >= 3.0) {
            holdCount++
            if (holdCount * 20 >= 200) { // 20ms per frame
                current = bestCh
                holdCount = 0
            }
        } else {
            holdCount = 0
        }
    } else {
        holdCount = 0
    }

    // 4) 输出 mono
    val out = ShortArray(frameSamples)
    for (i in 0 until frameSamples) {
        out[i] = interleaved[i * chCount + current]
    }
    return out
}
```

> 注意：上面 `candidates.indexOf(current)` Kotlin 不能直接用，需要写一个小函数或维护 `currentEnergyIdx`。这里只是表达思路。

---

## 7. 日志与可观测性（必须做，否则后续扯皮）

每 1 秒打印一次：

- 当前 `selectedChannel`（ch1/ch2/ch3）
- 三路能量（RMS 或 dB）
- 是否发生切换

并在初始化时打印：

- `AudioRecord.getChannelCount()`（确认拿到 8ch）
- `sampleRate / encoding / channelMask`

---

## 8. 与 reference 通道的关系（ch7/ch8 怎么用）

当前阶段（v0.1/v0.2）：
- **ch7/ch8 不进 ASR 输入**
- 仅作为“未来做 AEC”或“回采验证”的 reference 预留

后续若做真 AEC：
- mic = 选路/beamforming 后的近端语音（来自 ch1~ch3）
- ref = ch7/ch8（若被证明确实与播放内容强相关且不饱和）

---

## 9. 交付定义（你们完成什么算 Done）

- [ ] Android 侧能稳定拿到 8ch@16k（日志硬证据）
- [ ] Multi-channel 前端模块实现（Channel Picker v0.1）
- [ ] 输出 mono PCM 流式喂给 sherpa-onnx SenseVoice
- [ ] UI 实时显示识别文本（你们已有 v1 UI 可复用）
- [ ] 日志可观测：当前选路通道随人声方向变化而变化

现场效果由 Ala 测试并反馈；你们不需要自己复现实验场景。

---

## 10. 一句“产品化原则”（别走偏）

> 多声道的价值不是“多喂给 ASR”，而是“用阵列把近端人声做干净，再喂给 ASR”。  
> v0.1 用最简单的 Channel Picker 把闭环跑通；再谈 AEC/beamforming/NPU。

