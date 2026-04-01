# HK-ARRAYMIC-V3.2（USB 2ch）→ ASR 提升：结论 + 研发任务包（给 Claude/Cursor）

> 本文档为**结论 + 研发任务包**，Claude/Cursor 照此实现即可，**无需再设计或再测**；测试由你（用户）执行。  
> **Phase-0「干净环境」识别率基线必须通过后，才允许进入 Phase-1（AEC/BF/NS/AGC）**。  
> **§3.7 两条「必须做」补丁（VAD 拖尾 + 静音时长可配）** 已纳入并**已在当前代码中实现**，验收前请确认配置与默认值符合下表。

---

## 0. 当前事实（别争论，先落地）

- 麦克风在系统里枚举为 **Capture 2ch / 48k / 16bit（FL FR）**，本质是「立体声外壳，内容极可能为同一束波束结果的复制」。
- 你已做三组语音（S1/S2/S3）：**L、R、avg、adaptive 的 ASR 文本完全一致且正确**；**pick_mono（逐帧择大）在 S2_side 出错**（「低头思故乡」→「举头思故乡」）。

**一句话裁决**：  
当前 2ch 极可能高度相关；**「自适应择优 + 平滑切换」在现阶段没有收益，还可能像 pick 一样引入抖动/拼接错误**。默认用最稳的 **AVG (L+R)/2**（或直接用 L 单声道）；**pick_mono 明确禁用**。

---

## 1. 本阶段目标与阶段门

| 阶段 | 目标 | 门控 |
|------|------|------|
| **Phase-0** | 环境良好、无视频/TTS 下，输入链路稳定，ASR 在近/中/远、左右站位**接近 100% 正确** | **必须通过 Phase-0 验收，才允许进入 Phase-1** |
| **Phase-1** | AEC / BF / NS / AGC（参考信号、板载算法验证等） | 仅当 Phase-0 通过后开启 |

当前策略：TTS 播放时关 ASR，暂不做系统级 AEC 回声链路，也不搞参考信号。  
**Phase-0 唯一 KPI**：输入音频更稳定、一致（不伪影、不抖动、不拼接错位），让 ASR 在远近、左右站位下「稳如老狗」。

---

## 2. 决策：默认策略怎么选

### 2.1 默认（上线）策略：AVG downmix

- **默认**：`mono = (L + R) / 2`
- 再 **48k → 16k**，喂给 ASR（与现有 sherpa-onnx/sensevoice 流水线一致；你已验证文本表现正确）。

### 2.2 可选（默认关闭）策略：adaptive_mono

- **保留代码开关，但默认关**。原因：
  - 当前 HKMIC 的 L/R 高相关，adaptive 基本退化成 AVG；
  - 若有相位差/噪声差，adaptive 的「跟随」可能带来轻微幅度调制；收益不确定，风险确定（复杂度、调参、边界）。
- 若以后拿到「真实多麦 raw 通道」，adaptive 才可能有意义（下一阶段）。
- 实现参数写死即可：**frame=20ms**，**平滑** `alpha = 0.9*alpha + 0.1*a_new`（不增加更多旋钮）。

### 2.3 禁用策略：pick_mono（逐帧择优）

- **明确禁用**：实测 S2_side 已出现替换错误；逐帧硬切会制造拼接边界，ASR 最怕这种。
- **不提供** `asr.stereo_to_mono.mode=pick`，不纳入测试与默认选项。

---

## 3. Claude / Cursor 研发任务包（照做即可）

### 3.1 配置项（必做）

| Key | 说明 | 默认 |
|-----|------|------|
| `asr.input.sample_rate` | 采集采样率 | **48000** |
| `asr.input.channels` | 声道数 1=mono, 2=stereo | **2** |
| `asr.stereo_to_mono.mode` | **avg** \| **adaptive**（不提供 pick） | **avg** |

### 3.2 AudioRecord 初始化策略（必做）

- **优先尝试**：2ch @ 48k
- **失败回退**：1ch @ 16k（兜底保命）
- **日志**：实际拿到的采样率、声道数、缓冲大小

### 3.3 Stereo → Mono（必做）

- 实现 **StereoToMono.avg(short[] interleavedLR) → short[] mono**
- **adaptive** 作为可选实现（默认关闭），参数写死：
  - 帧长 **20ms**
  - 平滑：`alpha = 0.9*alpha + 0.1*a_new`
- 交织格式：Android stereo 为 **L,R,L,R,...**，即 `L[i]=buffer[i*2]`, `R[i]=buffer[i*2+1]`

### 3.4 Resample 48k → 16k（必做）

- 先用**简单可靠的 3:1 降采样**（或线性插值）；不要引入第三方库。
- 输出：**16k / 1ch / 16bit** PCM，直接喂现有 ASR 输入路径。

### 3.5 日志与验收（必做）

- **日志**（每 N 帧打印一次）：
  - downmix 模式（avg / adaptive）
  - 语音段粗略音量（mean/max 或 RMS）
- **验收定义**：
  - 不崩、不漏音频、不出现明显「忽大忽小/抽搐」
  - 你用 **S1/S2/S3 同一句口令**，ASR 文本与当前基线一致（即正确文本）

### 3.6 实现顺序建议

1. AsrConfig 增加上述三配置项及默认值  
2. AudioRecord：优先 2ch@48k，失败回退 1ch@16k，打日志  
3. StereoToMono：实现 avg；实现 adaptive（默认关）  
4. Resample 48k→16k（3:1 或线性插值）  
5. 将 16k mono 接入现有 processAudioData / ASR 路径，保持 asrMuted 逻辑  
6. 日志：downmix 模式、周期 mean/max 音量  
7. config.properties.template 补充上述项及注释  

### 3.7 VAD 与静音（必须做 — 否则读长句会被截断）

以下两条**必须满足**，否则 Phase-0 会卡在「读诗/长句老是被截断」的假问题上。**当前代码已实现**，交付前请确认配置与推荐值。

| 补丁 | 要求 | 实现与配置 |
|------|------|------------|
| **1. VAD 拖尾（hangover）** | 不能「一帧低于阈值就判 VOICE_END」；需**连续 N 帧都低于阈值**才判结束说话。 | 已实现：`belowThresholdFrameCount`，仅当 `>= config.vadHangoverFrames` 才置 `isInSpeech=false` 并打 VOICE_END。<br>配置：`asr.vad.hangover.frames`，**默认 4**。2ch 路径每帧约 100ms，故 4 帧≈400ms。读诗/长句可改为 **5–6**。 |
| **2. 静音时长可配置** | 静音超时触发 final 的时长**不能写死 800ms**；长句/诗要 1200–1800ms，短口令 800–1000ms。 | 已实现：`asr.vad.silence.duration.ms`，**默认 1200**。长句/古诗建议 **1200–1800**，短口令 **800–1000**。 |

**验收前检查**：`config.properties` / template 中 `asr.vad.hangover.frames` 与 `asr.vad.silence.duration.ms` 存在且按场景设好；日志中 VOICE_END 仅在句末或长停后出现，不在句中频繁出现。

---

## 4. Phase-0「干净环境」识别率基线测试（必须先过）

**测试目的**：在**环境良好、无视频播放、无回采线**的条件下验证：  
① 输入链路稳定（不丢帧、不抖动、不忽大忽小）；  
② ASR 在近/中/远、左右站位能否**接近 100% 正确**。  

**只有 Phase-0 通过，才允许进入 Phase-1（AEC/BF/NS/AGC）**；否则后续效果无法归因，工程不可控。

### 4.1 测试条件（必须严格一致）

- 房间安静（空调/风扇尽量关）
- 不播放视频、不播 TTS
- HK-ARRAYMIC-V3.2 仅 USB 接入
- 输入参数固定：**48k / 2ch**（或应用内部下采样到 16k mono）
- 识别内容固定（两种都测）：
  - **固定口令一句**（便于「100% 正确」判定）
  - **《静夜思》全诗**（便于长句稳定性）

### 4.2 场景集合（最少 6 条样本）

每条录音 **8～12 秒**，读**相同内容**：

| 场景 | 说明 |
|------|------|
| **S1** | 近距 0.5m 正前方 |
| **S2** | 中距 1.5m 正前方 |
| **S3** | 远距 3～4m 正前方 |
| **S4** | 左侧 1m |
| **S5** | 右侧 1m |
| **S6** | 远距 3～4m + 左侧（或右侧）略偏角度（模拟真实摆放） |

你已有 S1/S2/S3 可复用，建议补录 **S4/S5/S6**。

### 4.3 要测试的「输入融合模式」（只比 2 个）

- **mode=avg**（默认）：mono = (L+R)/2  
- **mode=adaptive**（候选）：自适应权重 + 平滑  

**pick_mono 禁用，不纳入测试。**

### 4.4 每条样本输出与判定

每条样本需输出：

1. **识别文本**（纯文本）
2. **置信度/打分**（若 ASR 无则空）
3. **音频基本指标**：mean_volume / max_volume / 时长
4. **失败原因标记**：空、漏字、错字、断句异常、杂音串入等

### 4.5 Phase-0 验收门槛（Pass 条件）

- **固定口令**：**6/6 全对 = 100%**
- **静夜思**：允许标点/空格差异，但**关键字不得错/漏**  
  - **关键字清单**：床前、明月光、疑是、地上霜、举头、望明月、低头、思故乡  
  - 任一关键字错或漏 → **Fail**
- **avg vs adaptive**：
  - 两者都过 → 默认上线 **avg**（更稳更简单）
  - avg fail、adaptive pass → 默认上线 **adaptive**
  - **都 fail → 禁止进入 Phase-1**，先排查输入链路/增益/麦克风安装/声学结构

**Phase-0 没过就别谈 AEC/BF/NS/AGC**——否则是在给麦克风厂算法当遮羞布，失去工程可控性。

### 4.6 现实提醒

在「安静环境」下做到 100% 并不离谱，尤其固定口令。若做不到，大概率不是模型问题，而是：**音频链路（增益/失真/门限/采样转换）或麦克风安装/声学结构（密封/避震/距离/朝向）** 有问题。

---

## 5. 下一阶段路线图（边界写死，防止飘）

### 5.1 AEC / 参考信号（以后再开）

- 当前没有「参考信号（播放回采）」，就不要在软件里假装做 AEC。
- 未来要做真 AEC：必须明确**参考声道从哪来**（USB 多端点？主板 I2S 回采？系统 3A 模块？）。

### 5.2 唤醒词（Wake Word）

- 等**输入链路稳定 + ASR 不抖**后再上唤醒词。
- 建议：独立 KWS 流水线（16k mono、低功耗常开），唤醒后再拉起完整 ASR（或提升采样/线程优先级）；细节不在此展开。

---

## 6. 与现有 3ch 逻辑的关系

- 现有工程有 3ch @ 16k 的 `MultiChannelAudioProcessor`（channel_picker / weighted_fusion）。
- **HKMIC V3.2** 仅暴露 2ch @ 48k，且 L/R 同源，因此：
  - **新增**「2ch @ 48k → downmix（仅 avg/adaptive）→ resample → 16k mono」路径，与 3ch 路径**并列**，由配置/设备能力选择。
  - 2ch 路径下**不使用** channel_picker / pick_mono，**不提供** pick 配置项。

---

## 7. 重要但易踩坑（写死给 Claude）

- **麦克风「自带 AEC」对机顶盒喇叭可能无效**：若喇叭不是从 HKMIC 的 USB Playback 出声，板子拿不到参考，AEC 可能未真正生效；TTS 时关 ASR 仍是合理止血。
- **把不确定性从算法层挪到供应商/硬件层**：既然 HKMIC 只给 2ch 且高度相关，就别在软件里硬卷「择优融合」；先把链路做成**稳定、可控、可配置、可回退**，这是工程护城河。

---

## 8. 文件与位置速查

| 内容 | 文件/位置 |
|------|-----------|
| ASR 入口、AudioRecord、多声道分支 | `iptv-edge-agent/app/src/main/java/com/joctv/agent/asr/AsrController.kt` |
| 配置读取、AsrConfig | 同上 + `config.properties` |
| 配置模板 | `iptv-edge-agent/app/src/main/assets/config.properties.template` |
| 3ch 处理器（2ch 路径不复用其 pick 逻辑） | 同上，`MultiChannelAudioProcessor` |

---

**总结**：Claude/Cursor 按 §3 实现 2ch@48k → avg/adaptive → 16k mono → ASR，并打日志；**§3.7 两条必须做补丁（VAD 拖尾 + 静音时长可配）已实现**，验收前确认配置即可。你按 §4 做 Phase-0 六场景测试并判定通过与否；**Phase-0 通过后再考虑 Phase-1（AEC/BF/NS/AGC）**。  
（另：AudioRecord buffer 已按「静夜思测试2-问题与方案」从 80ms 增至 120ms，减少 RecordThread overflow，与 ChatGPT 建议一致。）
