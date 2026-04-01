# IPTV Edge Agent — 多声道融合提升 ASR 研发任务（给 Claude）

> 依据：**agentv2项目计划最新.txt**、**ASR_Multichannel_Fusion_NoRef_Plan_v0.1.md**（ChatGPT + Ala 测试结论）  
> 工作区根目录：`/Users/ala/工作项目/agent`  
> 主工程：`iptv-edge-agent/`

---

## 0. 审核结论（两文档对齐情况）

- **agentv2项目计划最新.txt**：在 iptv-edge-agent 基础上 **UI 完全不变**，只改麦克风声道使用（当前 1 通道 → 多通道融合），提升 ASR 准确率，构建安装 APK。
- **ASR_Multichannel_Fusion_NoRef_Plan_v0.1.md**：已明确 AMB37 8ch 可用通道、交织格式、**算法 A（Channel Picker）** 为主方案、防抖参数、不做回采、TTS 时 ASR 关闭。
- **对齐点**：两文档一致，均不做回采/参考；只做多声道→1ch 融合后送现有 ASR；UI 不改。  
- **说明**：当前 iptv-edge-agent 使用 **Vosk** 作为 ASR 引擎，本任务要求融合后的 **1ch 流送入现有 Vosk 识别器**，不在此任务中替换为 sherpa-onnx（除非另有要求）。

---

## 1. 目标与约束

### 1.1 目标
- 利用 **AMB37 8 通道** 麦克风，将 **ch1/ch2/ch3（FL/FR/FC）** 通过选路或轻融合得到 **1 路 mono**，再送 Vosk，**降低 ASR 错误率**。
- **不做**系统播放声回采（无 reference）；**TTS 播放时 ASR 关闭**、TTS 结束后再打开（现有逻辑保持）。

### 1.2 约束
- **UI 完全不变**：不新增、不删、不改 `activity_main.xml` 及与呈现相关的布局/样式/文案。
- **ASR 引擎**：仍为 Vosk，仅改变“送入 Vosk 的音频”为多声道融合后的 1ch。
- 主工程路径：**iptv-edge-agent/**。

---

## 2. 通道与格式（按 ASR_Multichannel_Fusion_NoRef_Plan_v0.1 定版）

### 2.1 设备与通道
- 设备：AMB37，`/proc/asound/card2/stream0` 为 **8ch@16000Hz S16_LE**，channel map：`FL FR FC LFE SL SR FLC FRC`（对应 Android 下标 0～7）。
- **参与融合的候选通道（写死）**：
  - **CANDIDATES = [ch3, ch1, ch2]** → 即 **FC, FL, FR**，对应 **index 2, 0, 1**。
- **可选**：ch4 (LFE)，本阶段不默认启用；若后续测试确认“更干净”再开。
- **不参与**：ch5/ch6 (SL/SR) 几乎静音；ch7/ch8 (FLC/FRC) 易为回采/注入，本阶段不用于近端融合。

### 2.2 采集格式
- **sampleRate** = 16000  
- **encoding** = PCM_16BIT  
- **channelMask**：优先尝试 **8 通道**（如 `AudioFormat.CHANNEL_IN_7POINT1` 或 `setChannelIndexMask(0xFF)`，视 ROM 而定）。  
- **必须**：初始化后打日志确认 **channelCount == 8**，否则阵列方案不成立，需回退或报错说明。

### 2.3 交织布局（interleaved）
- 每帧 **20ms** → **frameSamples = 320**（每通道）× **8 通道** = **2560 shorts/frame**。
- 布局：`[ch0_s0, ch1_s0, …, ch7_s0, ch0_s1, …, ch7_s1, …]`，即 `interleaved[i*8 + ch]` 为第 ch 通道第 i 个采样。

---

## 3. 融合算法（v0.1 只做算法 A）

### 3.1 算法 A：动态选路 Channel Picker（推荐默认，必做）
- 每 **20ms** 一帧，对 **ch1/ch2/ch3（index 0,1,2）** 计算能量，例如 `energy[k] = sum(s*s)`。
- **选出能量最大的一路** 作为本帧输出通道，该通道的 320 个采样即本帧 **mono** 输出，送 Vosk。
- **防抖（必须）**：
  - **SWITCH_DB = 3 dB**：新通道能量比当前通道高 3dB 才考虑切换。
  - **HOLD_MS = 200 ms**：连续满足“新通道更优”达 200ms 才真正切换，避免乱跳。
- **日志**：约每 1 秒打印一次：当前 selectedChannel、ch1/ch2/ch3 能量（可转为 dB）、是否发生切换。

### 3.2 算法 B：Top2 软融合（可选，仅当 A 有问题再开）
- 在 ch1～ch3 中取能量 **Top2**，按能量比做加权和输出 1ch，并做限幅/归一化。
- 本任务可只实现算法 A；算法 B 留接口或配置开关，不在首版默认启用。

### 3.3 不做
- 不做真正波束形成（Delay-and-Sum/MVDR 等），不做 ch5～ch8 参与融合。

---

## 4. 实现要点（Kotlin / iptv-edge-agent）

### 4.1 AudioRecord 初始化
- 使用 **AudioFormat.Builder**：`setSampleRate(16000)`、`setEncoding(ENCODING_PCM_16BIT)`、**setChannelIndexMask(0xFF)**（8 通道）或 ROM 支持的 8ch mask。
- 若系统不支持 8ch，可尝试 5ch 等并打日志；若仅 1ch，则保持现有单通道逻辑（无融合）。
- **必须**打印：`format`、`sampleRate`、**channelCount**（必须为 8 才走多通道融合）。

### 4.2 缓冲区与解交织
- 每帧读取 **2560 shorts**（8ch × 320）。
- 按 2.3 的 interleaved 布局，从 **CANDIDATES = [2, 0, 1]**（FC, FL, FR）取 3 路能量，执行算法 A，输出 **320 shorts** 的 mono 帧送 Vosk。

### 4.3 与现有 AsrController 的衔接
- 当前 **AsrController** 使用 `buffer: ShortArray(BUFFER_SIZE)`（1600 = 100ms）、单通道、直接送 Vosk。
- 改造方式二选一（任选一种实现即可）：
  - **方式 1**：在 AsrController 内增加“多通道分支”：若 `channelCount == 8`，则按 20ms 帧读 2560，经 Channel Picker 得到 320 shorts，**累积多帧到约 100ms 或按现有 BUFFER_SIZE 对齐**后，再调用现有 `processStateMachine` / `processAudioData`（仍为 ShortArray + read 长度）。
  - **方式 2**：保持 20ms 一帧调用 Vosk（若 Vosk 支持流式短帧）；否则先做 20ms→mono，再在内部组帧（如 100ms）再送 Vosk，保证与现有逻辑兼容。
- **RMS / VAD**：用融合后的 **mono** 帧计算 RMS，供现有状态机与 VAD 使用。

### 4.4 廉价保险（建议实现，不依赖回采）
- **VAD 门控**：能量低于门限时不送帧或送静音帧，减少误触发。
- **短时静音断句**：连续静音 > 约 500ms → flush/endpoint，便于稳定出 final。
- **启动 300ms warm-up**：ASR 启动后前 300ms 不送有效帧，避免链路未稳定。

---

## 5. 交付清单（Claude 完成即算 Done）

- [ ] **iptv-edge-agent** 内实现 8ch 采集（AudioRecord 8ch@16k），并打日志确认 **channelCount == 8**（否则明确回退到 1ch 并说明）。
- [ ] 实现 **算法 A（Channel Picker）**：CANDIDATES = [2,0,1]，防抖 3dB / 200ms，输出 1ch 流。
- [ ] 1ch 流送入 **现有 Vosk 识别器**（不替换 ASR 引擎），ASR 文本仍显示在现有 UI（不改布局）。
- [ ] 日志可观测：约每秒输出 selectedChannel、ch1/ch2/ch3 能量（或 dB）、切换事件。
- [ ] （建议）VAD 门控 + 300ms warm-up；可选静音断句 500ms。
- [ ] **UI 完全不变**；TTS 时 ASR 关闭、TTS 结束再打开逻辑保持。
- [ ] 构建通过，可生成 APK 安装到机顶盒。

---

## 6. 验收与后续（由 Ala 现场验证）

- 在 **iptv-edge-agent** 上安装新 APK，电视播放（TTS/ASR 互斥）场景下，**人声识别率是否明显提升**。
- 是否出现 **通道乱跳/语音断裂**；若有可调大 HOLD_MS 或后续启用算法 B。
- 若 8ch 在设备上拿不到，日志中需明确说明并回退单通道行为。

---

## 7. 参考与伪代码

- **通道与算法细节**：见 **ASR_Multichannel_Fusion_NoRef_Plan_v0.1.md** 第 1～8 节。  
- **Kotlin 伪代码**：见该文档第 7 节（`processFrame(interleaved, chCount, frameSamples)`），可直接改为正式代码接入 AsrController。  
- **工程路径**：  
  - 主工程：`iptv-edge-agent/`  
  - 主逻辑：`app/src/main/java/com/joctv/agent/asr/AsrController.kt`  
  - 不改：`activity_main.xml`、Vosk 引擎、TTS/数字人模块。

---

## 8. 一句话结论

> **本阶段：在 iptv-edge-agent 内实现 8ch 采集 + ch1/ch2/ch3 的 Channel Picker 得到 1ch，送现有 Vosk，UI 与 TTS 逻辑不变，不做回采；通过多声道选路提升 ASR 准确率。**
