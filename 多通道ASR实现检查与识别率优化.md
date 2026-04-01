# 多通道 ASR 实现检查与识别错误率优化

## 一、当前多通道实现方式概览

### 1. iptv-edge-agent（agent 仓库）

| 项目 | 实现 |
|------|------|
| **多通道** | 支持 **3 通道**（ch0/ch1/ch2 = FL/FR/FC），通过 `AudioRecord.Builder` + `setChannelIndexMask(0x07)` 初始化 |
| **融合策略** | **Channel Picker**：每 20ms 一帧，在 3 个候选通道中选 **能量最大** 的一路作为输出（只输出 1 路单声道） |
| **防抖** | 切换需比当前通道高 **3dB**，且保持 **200ms** 才切换，避免频繁切通道 |
| **VAD** | `MIN_VAD_RMS = 120.0`，静音 **800ms** 触发 final |
| **送 ASR** | 累积到 **1600 采样（100ms）** 后送入 Vosk |
| **暖机** | 代码中 **未发现 300ms 暖机** 逻辑（若需求有，需补） |

**Channel Picker 核心逻辑**（`AsrController.kt` 内 `MultiChannelAudioProcessor`）：

- 候选通道索引：`CANDIDATE_CHANNELS = [2, 0, 1]`（即 FC, FL, FR）
- 交织格式：`[ch0_s0, ch1_s0, ch2_s0, ch0_s1, ...]`
- 每帧计算各通道 RMS，选最大者；切换条件：`dbDiff > 3.0` 且保持 200ms

---

### 2. 3576v2（AndroidTVSpeechRecognition）

| 项目 | 实现 |
|------|------|
| **MainActivity 实际 ASR** | 使用 **AsrController**（Vosk），**单声道** `CHANNEL_IN_MONO` |
| **多通道采集** | **EnhancedAmb37AudioCollector** 存在，且支持 6 通道（4 MIC + 2 REF）概念，但 **当前未接入主 ASR 链路** |
| **AdvancedAsrController** | 使用 EnhancedAmb37 + **mergeMicChannels**（4 路 MIC **简单平均**成 1 路）+ WebRTC APM，但 **MainActivity 没有使用**，仅 AsrController 被使用 |
| **结论** | **3576v2 当前等于单麦 ASR**，多通道和 APM 未参与识别 |

**EnhancedAmb37AudioCollector 的融合方式**（`mergeMicChannels`）：

- 仅对 4 个 MIC 通道做 **逐采样简单平均**，得到 1 路 Float 送 APM；若 APM 可用则回调 `onProcessedAudioData`，否则不送 ASR。
- 且 Android 端 `AudioRecord` 使用 `CHANNEL_IN_STEREO`，**并非真实 6 路硬件映射**，多通道分离依赖的是“假设的”交错格式。

---

## 二、识别错误率高的可能原因

1. **3576v2 未用多通道**  
   主路径是单声道 Vosk，没有利用 AMB37 多麦做融合或波束，远场/噪声场景下识别率会差。

2. **iptv-edge-agent：Channel Picker 只选 1 路**  
   - 若设备实际通道顺序与 `CANDIDATE_CHANNELS = [2,0,1]`（FC, FL, FR）不一致，会经常选到非主说话人通道。  
   - 纯“能量最大”在噪声或反射强的通道上可能选错，且没有多路加权/波束形成。

3. **VAD 与阈值**  
   - iptv-edge-agent：`MIN_VAD_RMS = 120.0` 若偏大，会截断轻声或远场；偏小则易把噪声当语音。  
   - 3576v2 AdvancedAsrController：`VAD_THRESHOLD = 0.01f`（浮点域），和 iptv 的 120（Short 域）量纲不同，需按实际 RMS 分布调。

4. **无 300ms 暖机**  
   iptv-edge-agent 多通道路径下，未发现“前 300ms 不送 ASR 或只做暖机”的逻辑，首字可能不稳定。

5. **AEC/NS 与多通道不匹配**  
   - 3576v2：WebrtcApmProcessor 用的是 Android 系统 AEC/NS/AGC，绑定的是 **单路 AudioRecord**；EnhancedAmb37 的“多通道”在 Android 上实际是 STEREO 假设，真实 6ch 需 USB 等特殊 API。  
   - iptv-edge-agent：3 通道时仍对同一 AudioRecord 做 AEC/NS，若系统对 3ch 支持不好，效果可能打折扣。

---

## 三、改进建议（可交给 Claude 实现）

### 1. 3576v2：让主 ASR 走多通道路径（可选）

- 若设备能提供真实多通道（如 USB 麦克风 6ch），可改为 MainActivity 使用 **AdvancedAsrController** + 当前 EnhancedAmb37 的 **mergeMicChannels**，这样至少是 4 路平均后再送 ASR，比单麦稳。
- 若暂时无法用 AdvancedAsrController，可先在现有 AsrController 上增加“可选 3 通道 + Channel Picker”（从 iptv-edge-agent 移植），保证同一套 Vosk 能吃到多通道融合后的单声道。

### 2. iptv-edge-agent：多通道融合增强

- **方案 A**：保留 Channel Picker，但增加 **可配置通道顺序**（如从配置或设备属性读取 CANDIDATE_CHANNELS），避免设备通道与假设不符。  
- **方案 B**：在现有“选 1 路”之外，增加 **加权融合**：如对 FC/FL/FR 做加权平均（例如 FC 权重大），或按能量加权混合，再送 Vosk，减少“选错一整路”的风险。  
- **方案 C**：若硬件/性能允许，可考虑简单波束形成（如 delay-and-sum）替代纯 Channel Picker。

### 3. 增加 300ms 暖机（与需求一致）

- 在 **多通道路径** 开始送 Vosk 之前，先累积 **300ms** 数据只做 VAD/内部状态更新，不调用 `acceptWaveForm`；300ms 后再正常送 ASR。  
- 避免首包电平或通道切换导致首字识别错误。

### 4. VAD 与静音参数可调

- 将 `MIN_VAD_RMS`、`SILENCE_DURATION_MS`、`COOLDOWN_MS` 等做成 **可配置**（如 SharedPreferences 或 config 文件），便于在不同房间/设备上调节，降低误切和截断。

### 5. 通道与 RMS 日志

- 在多通道路径打日志：每帧/每 N 帧输出 **各通道 RMS** 和 **当前选中通道**，便于确认通道顺序是否与预期一致，以及 VAD 阈值是否合理。

---

## 四、代码位置速查（方便 Claude 修改）

| 功能 | 工程 | 文件与位置 |
|------|------|------------|
| 3 通道 + Channel Picker | iptv-edge-agent | `AsrController.kt`：约 689–790 行 `MultiChannelAudioProcessor`；约 189–192、230–274 行多通道读与送 ASR |
| VAD 阈值 / 静音时长 | iptv-edge-agent | `AsrController.kt` 常量：`MIN_VAD_RMS`、`SILENCE_DURATION_MS`、`COOLDOWN_MS` |
| 多通道送 Vosk 的入口 | iptv-edge-agent | `AsrController.kt`：`processAudioData`（约 537 行），多通道时用 `accumulatedMonoData` 转 Byte 送 Vosk |
| 3576v2 主 ASR | 3576v2 | `MainActivity.kt`：`asrController = AsrController(...)`，未使用 AdvancedAsrController |
| 多通道平均融合 | 3576v2 | `EnhancedAmb37AudioCollector.kt`：`mergeMicChannels`（约 212–231 行）；`AdvancedAsrController.kt`：`onProcessedAudioData` 里 `asrEngine.feedAudio(processedAudio)` |
| AdvancedAsrController VAD | 3576v2 | `AdvancedAsrController.kt`：`VAD_THRESHOLD = 0.01f`，`updateVadState`（约 213–244 行） |

---

**总结**：  
- **iptv-edge-agent** 的多通道是 **3 路 Channel Picker（选能量最大 1 路）**，无 300ms 暖机，VAD 固定 120。  
- **3576v2** 主路径是 **单声道 Vosk**，多通道（EnhancedAmb37 + 平均融合）未接入，识别率未享受多麦优势。  
- 识别错误率优化可从：**3576v2 接入多通道/融合**、**iptv 增加融合或可配置通道与 VAD**、**统一加 300ms 暖机** 和 **可调 VAD/静音参数** 几方面入手。
