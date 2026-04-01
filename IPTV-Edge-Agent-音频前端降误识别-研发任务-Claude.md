# IPTV Edge Agent — 音频前端降误识别 研发任务（给 Claude）

> **在 v1.0（iptv-edge-agent）基础上做，且仅做“背景声 / 环境噪音 / 视频声消除”，把 ASR 错误率降下来。**  
> 工作区根目录：`/Users/ala/工作项目/agent`  
> 主工程：`iptv-edge-agent/`（v1 代码，不可改呈现 UI）

---

## 0. 方向与范围（必须遵守）

- **保留 v1 技术栈**：
  - **ASR**：继续用 **Vosk**（不换成 sherpa-onnx ASR）
  - **TTS**：继续用 **sherpa-onnx TTS**
  - **数字人**：继续用 **2D 数字人**（DUIX 或现有实现）
- **本次只做**：**音频前端** —— 背景声音、环境噪音、视频（播放）声音的消除/抑制，从而降低 Vosk ASR 的误识别率。
- **禁止**：
  - 修改 v1 的 **呈现 UI**（布局、控件、样式、文案一律不动）
  - 替换 ASR 引擎为 sherpa-onnx 或其它
  - 用“开始/停止识别”触发播放音量变化的交互方案

---

## 1. 主工程与约束

### 1.1 主工程路径
- **`iptv-edge-agent/`**  
- 若不存在或不全，从 https://github.com/wenlepan-lgtm/iptv-edge-agent 克隆到该路径。

### 1.2 不可改动的部分
- **UI 呈现**：`app/src/main/res/layout/activity_main.xml` 及与界面展示相关的资源（不新增、不删、不改位置/大小/颜色/文案）。
- **ASR 引擎**：仍为 Vosk（AsrController / AsrEngine 等保留，只允许改“输入给 Vosk 的音频从哪来、是否经过前端处理”）。
- **TTS / 2D 数字人**：逻辑与集成方式保持 v1，不在此任务中改。

### 1.3 允许改动的部分
- **音频采集与前端处理**：
  - 采集源（如 `AudioSource`：MIC / VOICE_COMMUNICATION）、
  - 是否启用系统 AEC/NS/AGC、
  - 若 v1 使用 tinycap/FIFO：可改为 `AudioRecord` 并走 VOICE_COMMUNICATION，或保留 tinycap 但在上层增加处理链路，
  - 在送入 Vosk 之前增加：降噪、增益控制、回声消除（若有 reference）等，使“送进 ASR 的音频”更干净。

---

## 2. 目标（本次任务）

- **主要目标**：通过音频前端处理，降低 ASR 错误率。  
  - 减少：**背景声音、环境噪音、视频播放声** 被识别成文字（误触发、误识别）。
  - 提升：**近端人声** 的识别正确率（客房指令等）。
- **不设具体 KPI 数值**：先实现“可验证的闭环”（见下），再根据测试结果迭代。

---

## 3. 实现要点（音频前端）

### 3.1 采集与系统效果器
- 若当前 v1 使用 **tinycap**：评估是否可改为 **AudioRecord**，并选用 **VOICE_COMMUNICATION**，以利用系统 AEC/NS/AGC（若设备支持）。
- 若已用 AudioRecord：检查是否已用 VOICE_COMMUNICATION；若未用，改为 VOICE_COMMUNICATION，并在 **同一 session** 上尝试启用：
  - `AcousticEchoCanceler`
  - `NoiseSuppressor`
  - `AutomaticGainControl`
- 若设备/ROM 不支持上述效果器，在代码中打 log 标明，并保留“仅用 VOICE_COMMUNICATION 采集”的路径。

### 3.2 硬件/多通道（若适用）
- 若设备为 **USB 麦克风且带回采（reference）**：确认驱动是否向 Android 暴露多通道或独立 reference；若暴露，可在应用层或后续阶段考虑双路输入 + 软件 AEC（需单独任务）。
- 本阶段可在文档中说明“当前设备是否有多通道/reference”，不强制在本任务内实现双路 AEC。

### 3.3 不采用的方案（再次强调）
- 不用“用户点击开始识别 → 降低/静音播放音量 → 停止识别 → 恢复音量”作为产品策略。
- 不替换 Vosk 为 sherpa-onnx ASR；不修改 v1 的呈现 UI。

---

## 4. 可验证闭环（建议）

- **T1**：播放视频 + 静默 → 期望 ASR 几乎不输出文字（或误识别明显减少）。
- **T2**：播放视频 + 人说话 → 期望主要识别人声，视频台词被压制。
- **T3**：不播放视频 + 人说话 → 正常识别，作为基线。
- 可保留或新增“标准录音导出”（如 RAW vs VOICE_COMMUNICATION）便于对比；若已有 `AEC_ASR_AlwaysOn_Test_Pack_v0.1.md` 中的测试流程，可对齐其命名与矩阵，便于后续用同一批 wav 做对比。

---

## 5. 交付清单（给 Claude）

- [ ] 在 **iptv-edge-agent** 内，仅改动与“音频采集 + 前端处理”相关的代码（不改 UI、不换 ASR/TTS/数字人）。
- [ ] 采集路径明确：若从 tinycap 改为 AudioRecord，或启用 VOICE_COMMUNICATION + AEC/NS/AGC，在代码与简短说明中写清。
- [ ] 若启用系统 AEC/NS/AGC：在 log 或文档中注明设备上是否可用、是否已 enable。
- [ ] 通过 T1/T2/T3 验证：误识别是否下降、人声识别是否正常；结果写在简短说明或 README 中。
- [ ] 一页说明：本次改了哪些文件、如何编译运行、如何做 T1/T2/T3 验证。

---

## 6. 参考（不要求照抄，仅作上下文）

- 测试与命名规范：`AEC_ASR_AlwaysOn_Test_Pack_v0.1.md`
- 优化方向背景：`IPTV-Edge-Agent-v1-Optimization-Plan-v0.1.md`、`agentv2项目计划.txt`
- 若需对比 RAW vs VOICE_COMMUNICATION 的录音效果，可参考工作区内 `AecTestApp` 的录音与导出方式（逻辑可复用，但不改 iptv-edge-agent 的呈现 UI）。

---

**总结**：在 **iptv-edge-agent（v1）** 上，**只做音频前端**（背景声、环境噪音、视频声消除/抑制），**继续用 Vosk + sherpa-onnx TTS + 2D 数字人**，**不修改 v1 的呈现 UI**。今天之前“换 ASR 为 sherpa-onnx”的改动不再采用，以本任务为准。
