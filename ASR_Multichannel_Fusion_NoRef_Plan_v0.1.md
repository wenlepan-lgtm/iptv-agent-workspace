# 多声道融合 → 1ch → ASR（无回采 / TTS 时关闭 ASR）执行方案 v0.1  
给：Claude / Cursor（实现用）；测试由 Ala 负责

> 约束已确认：**不做系统播放声回采**（无 reference）；**TTS 播放期间 ASR 关闭**，TTS 结束再打开。  
> 所以本阶段目标就是：利用 **AMB37 8ch 阵列**，把“近端人声”做得更干净，再喂给 ASR，提高识别率。

---

## 1) 哪些通道能用（直接定版，不让你们去测试）

设备：`/proc/asound/card2/stream0` 显示 `8ch@16000Hz S16_LE`，channel map 为：  
`FL FR FC LFE SL SR FLC FRC`

结合你已有的 volumedetect 特征（多次观察一致）：

### ✅ 作为“近端人声输入候选”的通道
- **ch1 = FL**
- **ch2 = FR**
- **ch3 = FC**
- **ch4 = LFE（可选，不默认启用）**

理由：这几路有稳定有效电平，承载主要声音能量（人声/环境）。

### ❌ 不参与融合的通道
- **ch5 = SL、ch6 = SR：几乎全静音（≈ -91dB）→ 直接忽略**
- **ch7 = FLC、ch8 = FRC：经常出现“回采/注入特征”（接线时电平显著变化）→ 本阶段不用于 ASR 近端融合**
  - 它们更像“参考/注入预留通道”，没 reference 算法时混进来只会污染人声。

> **结论（写死给实现）：**  
> `CANDIDATES = [ch3, ch1, ch2]`  
> `OPTIONAL = [ch4]`（只有 Ala 测试确认“更干净”再加）  
> `IGNORE = [ch5, ch6, ch7, ch8]`

---

## 2) 融合算法怎么选（别上来做复杂 beamforming）

没有 reference / 不做回采的情况下，阵列增益的最短闭环是：

### 算法 A（推荐默认）：**动态选路 Channel Picker（Pick-1-of-3）**
每 20ms 帧计算 ch1~ch3 的能量（RMS/mean-abs），选能量最大的一路作为输出 1ch。

- ✅ 代码最简单，收益最大  
- ✅ 不会“把噪声叠加放大”  
- ✅ 对“你说话更近某个 mic”的阵列非常有效  
- ⚠️ 需要**防抖**（hysteresis），避免乱跳

> 这是 v0.1 主方案：**先把它做出来并上线**，识别率通常立刻改善。

### 算法 B（可选升级）：**软融合 Soft Mix（Weighted-2-of-3）**
不是三路全混，而是：  
- 选能量 Top2 两路  
- 按能量比做权重加和输出（并做归一化/限幅）

- ✅ 输出更平滑，不容易切换断裂  
- ⚠️ 权重不稳会把环境噪声也加大（比算法 A 风险高）

### 算法 C（不建议 v0.x 做）：真正波束形成（Delay-and-Sum/MVDR）
需要阵列几何/延迟估计/方向估计，工程量大，且在“房间混响 + 电视残留”场景未必稳赢。  
——先别做，别把项目拖进学术坑里。

---

## 3) 关键实现细节（Kotlin / Android）

### 3.1 AudioRecord 必须拿到 8ch
- sampleRate = **16000**
- encoding = **PCM_16BIT**
- channelMask：优先尝试 `CHANNEL_IN_7POINT1`（不同 ROM 行为不一）
- 初始化后必须打印：
  - `audioRecord.format / sampleRate / channelCount`
  - **channelCount 必须是 8**（否则阵列方案失效）

### 3.2 数据是 interleaved（交织），必须按帧解交织
每帧 20ms：
- frameSamples = 16000 * 0.02 = **320 samples / channel**
- interleavedShorts = 320 * 8 = **2560 shorts**

布局：
`[ch1_s0, ch2_s0, ..., ch8_s0, ch1_s1, ..., ch8_s1, ...]`

---

## 4) 算法 A：动态选路（推荐默认）—参数写死

### 4.1 能量计算
对候选通道 ch1/ch2/ch3 计算：
- `energy = sum(s*s)`（或 mean(abs(s))）

### 4.2 防抖（必须）
- `SWITCH_DB = 3 dB`（新通道能量比当前高 3dB 才考虑切）
- `HOLD_MS = 200 ms`（连续满足 200ms 才切换）
- 每 1 秒打印一次：
  - 当前 selectedChannel
  - 3 路能量（dB）
  - 是否发生切换

### 4.3 输出
输出 monoBuffer = 选中通道的 320 samples，送入 ASR streaming。

---

## 5) 算法 B：Top2 软融合（仅在 A 有问题才开）

### 5.1 规则
1. 在 ch1~ch3 中找到能量 Top2：`a, b`
2. 权重：
   - `wa = sqrt(Ea) / (sqrt(Ea)+sqrt(Eb)+eps)`
   - `wb = 1 - wa`
3. 输出：
   - `out[i] = clamp16( wa*sa[i] + wb*sb[i] )`
4. 最后加一个轻微归一化（避免削波）

### 5.2 防爆（限幅）
- 用 `softclip` 或简单 `clamp16`  
- 记录 `clip%`（削波率），超过 0.05% 就要调低权重/整体增益

---

## 6) 强烈建议加的“廉价保险”（不涉及回采）

这几个几乎白捡识别率，成本极低：

1) **VAD 门控（能量门限）**  
- 没人说话就不往 ASR 喂帧（或喂静音帧）  
- 目的：减少噪声触发、减少无意义识别

2) **短时静音检测 / 断句**  
- 连续静音 > 500ms → flush / endpoint  
- 识别文本更稳定

3) **启动 300ms warm-up**  
- 刚开 ASR 时先丢掉 300ms 帧（避免音频链路未稳定）

> 注意：这些都不需要 reference，也不会碰播放声回采那一坨复杂约束。

---

## 7) Kotlin 伪代码（可直接翻成正式代码）

```kotlin
val candidates = intArrayOf(2, 0, 1) // ch3,ch1,ch2 (0-based)
var current = candidates[0]
var holdFrames = 0

fun processFrame(interleaved: ShortArray, chCount: Int, frameSamples: Int): ShortArray {
    // energy
    val e = DoubleArray(candidates.size)
    for (i in 0 until frameSamples) {
        val base = i * chCount
        for (k in candidates.indices) {
            val ch = candidates[k]
            val s = interleaved[base + ch].toInt()
            e[k] += (s * s).toDouble()
        }
    }

    // best
    var bestK = 0
    for (k in 1 until e.size) if (e[k] > e[bestK]) bestK = k
    val bestCh = candidates[bestK]

    // hysteresis
    if (bestCh != current) {
        val curK = candidates.indexOf(current) // implement your own
        val ratio = e[bestK] / (e[curK] + 1e-9)
        val db = 10.0 * log10(ratio)
        if (db >= 3.0) {
            holdFrames++
            if (holdFrames * 20 >= 200) { // 20ms per frame
                current = bestCh
                holdFrames = 0
            }
        } else holdFrames = 0
    } else holdFrames = 0

    // output mono
    val out = ShortArray(frameSamples)
    for (i in 0 until frameSamples) out[i] = interleaved[i * chCount + current]
    return out
}
```

---

## 8) 交付标准（Done 的定义）

实现侧（Claude/Cursor）完成以下即算 Done：

- [ ] Android 端确认拿到 **8ch@16k**（硬日志）
- [ ] 实现 **算法 A（Channel Picker）**，输出 1ch PCM
- [ ] 1ch 流式喂给 sherpa-onnx SenseVoice（或你后续选的模型）
- [ ] ASR 文本实时显示（你已有 UI 可复用）
- [ ] 日志可观测：每秒输出 selectedChannel + energies + switch events

现场验证（Ala）执行并回传结果：
- 电视播放（但 TTS/ASR 互斥）情况下，人声识别率是否显著提升
- 是否出现乱跳/断裂（若有则启用算法 B 或调大 HOLD_MS）

---

## 9) 一句结论（避免跑偏）

> **没有 reference 的阶段，阵列提升识别率的正确姿势是：选路/轻融合把近端人声变干净，再喂 ASR。**  
> 别把 8 路音频直接混进 ASR，那是“把噪声也一起升级”。

