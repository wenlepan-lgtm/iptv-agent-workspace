# microphone_test_report.md 审核意见

**审核对象**: Claude 执行结果 `microphone_test_report.md`  
**对照**: v0.2 方案 Task-1、mic_params_extracted.md（YD1076S）  
**日期**: 2026-02-04

---

## 1. 报告做得好的地方 ✅

- **硬件识别**：USB 设备 Yundea 1076S 识别正确，与 YD1076S 一致；Card 2 / Device 0、16kHz、S16_LE 记录清楚。
- **策略与线程**：AudioPolicy 与 AudioFlinger 输入线程状态有具体数据（如 Frames read: 200783），说明确实在录音。
- **预处理状态**：正确指出 AEC/NS/AGC 在系统配置中未启用，并指出 `libaudiopreprocessing.so` 存在及需改配置。
- **下一步与附录**：给出了方法 1/2/3 和参考命令，便于你本地用 ADB 复测。

---

## 2. 与 v0.2 Task-1 的差距（重要）

**Task-1 要求**（v0.2）：  
- 验证 **USB 6 通道采集**（或至少多通道：MIC + Reference）  
- 确认通道数、采样率、通道顺序  
- 输出原始多通道 WAV（含 **time-aligned reference**）  
- 验收：Reference 通道可用且同步；MIC 无严重削波/爆音  

**报告当前结论**：  
- 声道为 **MONO（单声道）**，采样率 16000 Hz。

**问题**：  
- 若设备在 Android 上**仅暴露 1 路**，则无法满足「多通道 + Reference」的 Task-1 要求，AEC 的参考信号也无法在应用层拿到。  
- 若 YD1076S 实际有多路（例如 1 MIC + 1 Ref 或 4 MIC + 2 Ref），但当前只看到 MONO，则可能是：  
  - USB 描述符只配置了单路；或  
  - 驱动/策略只打开了单路；或  
  - 需要特定 UAC2 多通道配置才出现多路。  

**审核结论**：  
- **Task-1 已用 ADB 闭环**（见下节）。结论：设备仅暴露**单声道**，无独立 Reference 通道到 Android。

---

## 2.1 ADB 验证结果（机顶盒实测 2026-02-04）

以下为 `adb shell cat /proc/asound/...` 与 `dumpsys media.audio_*` 的结论摘要。

| 项目 | 结果 |
|------|------|
| **声卡** | card0=es8388, card1=hdmi, **card2=Y1076S (USB-Audio, full speed)** |
| **card2 下的流** | 仅有 **stream0**（无 stream1），即只有一条采集流 |
| **stream0 (Capture)** | Format: S16_LE；**Channels: 1**；Rates: 16000；Channel map: **MONO**；Endpoint: 0x83 (ASYNC IN) |
| **AudioPolicy - USB Device In** | Port ID 9；采样率 16000；channel masks: 0x000c, 0x0010, 0x80000001（策略支持多种 mask，但实际打开的流为 1 通道） |
| **AudioFlinger Input (AudioIn_26)** | 16000 Hz；**Channel count: 1**；Channel mask: 0x10 (front)；Audio source: MIC；Frames read: 200783 |
| **USB audio module** | No input streams（dump 时无活跃录音） |

**结论**：  
- YD1076S 在当前机顶盒上**仅向 Android 暴露 1 路采集**，无第二路（无独立 Reference 流）。  
- Task-1 的「6 通道 / 多通道 + Reference」**不适用**本设备；可按「单声道 + 板内或应用层 AEC」推进。

---

## 3. 需要澄清的两点

### 3.1 通道数：MONO 是否就是全部？【已确认】

- ADB 实测：**card2 仅有 stream0，Channels: 1 (MONO)**；无 stream1，策略中该设备实际打开的也是 1 通道。  
- 因此：  
  - 要么接受「**板载 AEC 后只输出 1 路**」的方案（回采在板内，不传到 Android）；  
  - 要么查 YD1076S 厂商说明，看是否有固件/模式可暴露多路（MIC+Ref）到 USB。

### 3.2 应用层 VOICE_COMMUNICATION 用的是什么 AEC？

- 报告推荐用 **AudioSource.VOICE_COMMUNICATION** 启用 AEC/NS/AGC。  
- 在 Android 上，VOICE_COMMUNICATION 通常走的是 **系统软件预处理**（如 libaudiopreprocessing），不一定是 **YD1076S 硬件 AEC**。  
- 若 YD1076S 在 USB 上只给 1 路已处理好的音频，则「硬件 AEC」在板内完成，Android 侧只是用或不用软件效果。  
- 若 YD1076S 能提供多路（MIC + Ref），则应用层可以自己拿 Ref 做 AEC（例如 WebRTC APM），不依赖 VOICE_COMMUNICATION。  

**建议**：在报告中加一句说明——「当前推荐的 VOICE_COMMUNICATION 路径对应的是 Android 软件 AEC/NS/AGC，是否使用 YD1076S 硬件 AEC 取决于设备是否在板内做 AEC 并只输出单路」。

---

## 4. 与 mic_params_extracted.md 的对应

- **YD1076S**：回采 MX1.25 4P、咪头 MX1.25 2P；支持 AEC/NS/AGC。  
- 报告已体现「硬件支持 AEC/NS/AGC」「回采接口需正确连接」，与文档一致。  
- 文档未写 USB 向主机暴露的通道数，因此「MONO vs 多通道」需以实测和厂商说明为准，报告可补充「待确认：USB 是否支持多通道及通道定义」。

---

## 5. 补充动作（已完成）

已用你提供的 ADB 输出完成验证，结论见 **§2.1**。无需再执行额外命令。

---

## 6. 总结表

| 项目 | 报告结论 | 审核意见（含 ADB 验证后） |
|------|----------|---------------------------|
| 硬件识别 | ✓ YD1076S 识别、16k S16_LE | 正确，与文档一致 |
| 通道数 | MONO（1 声道） | ✅ **已确认**：仅 1 路，无 stream1，无 Reference 到 Android |
| 基础录音 | ✓ 有数据流入 | 正确（Frames read: 200783） |
| AEC/NS/AGC | 未启用，给出启用方式 | 正确；需区分软件路径 vs 板内/硬件 AEC |
| Task-1 闭环 | 未显式说明 | ✅ **已闭环**：按「单声道、无 Ref」验收；不适用 6 通道方案 |
| 下一步建议 | 测试应用、系统修改、ASR/TTS | 合理；按「单路 + VOICE_COMMUNICATION 或板载 AEC」推进 |

**总体**：报告描述正确；ADB 验证后确认 **YD1076S 仅暴露单声道**，Task-1 按「单路 + 板内或应用层 AEC」执行即可，无需再追求多通道/Reference 到 Android。
