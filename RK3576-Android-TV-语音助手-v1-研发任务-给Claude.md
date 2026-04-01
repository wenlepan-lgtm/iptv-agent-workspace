# RK3576 Android TV 语音助手 v1 研发任务书（ASR=NPU 交付版）

**面向**：Claude / Cursor  
**工程**：`iptv-edge-agent`（RK3576 + Android 14）  
**目标**：可演示、可商用交付的电视语音助手：唤醒词 + NPU ASR + 意图路由 + TTS + 全双工/回声抑制。

---

## 0. 背景与硬约束

### 0.1 平台与 ASR 约束

- **平台**：RK3576，Android 14。
- **ASR**：必须走 **NPU（RKNN）**。已采用 **VAD + SenseVoice 离线**（`NpuOfflineAsrEngine` + SenseVoice RKNN 20s），识别率已达可交付水平；**不支持**将 CPU ASR 作为主路径商用。
- **TTS**：交付目标为 **NPU 本地 TTS**（自然、温柔）。若 NPU 链路过长，首版可暂保留 CPU TTS 过渡；详见下文 §5 与《TTS-NPU-与-AEC-BF-NS-AGC-方案与确认-给Claude.md》。

### 0.2 业务目标（用户体验）

1. **开机欢迎语**  
   TTS 播报：  
   「您好，我是您的智能服务员，您可以对这电视通过『小聚小聚』呼叫我为您服务。」

2. **唤醒词「小聚小聚」**  
   - 仅说唤醒词 → 回复「有什么可以帮助您？」  
   - 先说唤醒词、再说需求（分开说）→ 正确识别并执行（例：我要退房）。

3. **连说**  
   - 「小聚小聚，帮我送一瓶水」一句话完成唤醒+指令，系统能正确响应（strip 唤醒词后执行「送水」）。

4. **电视背景音 + TTS 时的体验**  
   - 电视在播时仍能识别用户语音（背景音不压麦、不 duck TV 音量）。  
   - TTS 播报时支持 **barge-in**：用户插话能识别；可通过 **仅降低 TTS 音量（TTS ducking）** 提升识别率，不降低电视音量。

---

## 1. 总体架构：状态机与全链路

### 1.1 状态定义（必须做成状态机）

与现有 `StateMachine.kt` 对齐并扩展，建议状态包括：

| 状态 | 说明 | 行为要点 |
|------|------|----------|
| **IDLE** | 常驻待机 | 持续采集麦克风 → 前端降噪/回声处理 → **KWS（唤醒词检测）** |
| **WAKE_DETECTED** | 唤醒命中 | 进入「意图收集窗口」，启动 pre-roll 缓冲（见下） |
| **LISTENING** | 收指令 | VAD 切段 → 段送 **NPU 离线 ASR**（SenseVoice）→ partial/final 文本 |
| **THINKING** | 意图解析 | 本地意图路由（退房/送水/打扫/前台等），决定回复与动作 |
| **SPEAKING** | TTS 播报 | **不停止采集、不停止识别**，实现 barge-in；可做 TTS ducking |
| **ACTION** | 执行业务 | 广播客控、调接口、记录日志等 |

**关键**：**SPEAKING 状态下仍保持采集 + KWS/ASR**，否则无法实现「用户打断也能识别」。

### 1.2 现有工程可复用模块

- **ASR**：`AsrController`、`NpuOfflineAsrEngine`、`VadDetector` / `VAD`、`StateMachine`。  
- **意图**：`intent/IntentRouter.kt` 或 `engine/IntentRouter.kt`，本地 rule-based 路由。  
- **TTS**：`tts/TTSManager.kt`（当前使用 sherpa-onnx TTS 引擎，可后续切 MeloTTS）。  
- **KWS**：`com.k2fsa.sherpa.onnx.KeywordSpotter` 已存在，需接入「小聚小聚」并驱动状态机。

---

## 2. 唤醒词与「连说」处理

### 2.1 唤醒词检测（KWS）

- 使用 **sherpa-onnx Keyword Spotting**（本地、低延迟）。  
- 唤醒词文本：**「小聚小聚」**。  
- 若当前无现成「小聚小聚」模型：可从 sherpa-onnx kws 发布页选取中文 KWS 模型（如 zipformer-wenetspeech），或使用支持自定义关键词的模型/语法；文档：  
  https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html  
- KWS 命中后：**进入 LISTENING（意图收集窗口）**，并启动 **pre-roll 缓冲**（见 2.3）。

### 2.2 连说两种路径（都要支持）

- **路径 A**：先 KWS 再 ASR（常规）  
  - KWS 命中「小聚小聚」→ 进入 LISTENING 窗口 → VAD 切段 → NPU 离线 ASR → 意图解析。

- **路径 B**：一句话含唤醒词（必须做）  
  - 对 **整段 ASR 文本** 做 **WakeWord-stripping**：  
    - 文本开头匹配：`小聚小聚` / `小聚 小聚` / 同音错字容错（可维护小词表）。  
    - 匹配到则 **去掉唤醒词部分**，剩余作为命令文本；若剥离后为空，视为「纯唤醒」，回复「有什么可以帮助您？」。  
  - 实现位置建议：在意图路由前统一做一次 strip（如 `HotwordCorrector` 旁新增 `WakeWordStripper` 或于 `IntentRouter` 入口处理）。

### 2.3 Pre-roll 缓冲（强烈建议）

- KWS 触发时，将 **触发前 300～800ms** 的音频一并送入 VAD/ASR 输入。  
- 目的：避免唤醒词尾音与命令开头被截断（语速快或 KWS 延迟导致丢字）。  
- 实现：在 `AsrController` 或采集链路维护环形缓冲（如 1s），KWS 命中时从当前写指针回溯 300～800ms 作为首段 ASR 输入。

---

## 3. 背景音 / 回声 / 串音（电视 + TTS）

### 3.1 先确认主板是否已做 AEC / BF / NS / AGC

- **确认方式**：  
  - 运行 APK 后执行：`adb logcat -s AsrController:D | grep -E "AEC:|NS:|AGC:"`，看是否出现 `AEC: isAvailable=true`、`NS: isAvailable=true`、`AGC: isAvailable=true` 且 `enabled=true`。  
  - 结合 **麦克风芯片资料**（如 HK-ARRAYMIC-V3.2）查看是否写明芯片/模组内建 AEC/NS/AGC/BF；若有，则主板可能仅透传已处理音频。  
  - BF（波束形成）无 Android 标准 API，多由厂商 HAL 或麦克风固件提供，需查主板/麦克风资料或厂商 SDK。  
- **若主板/系统已支持**：应用层通过现有 `AsrController` 的 Android Effect（AEC/NS/AGC）或厂商 API 启用即可。  
- **若未支持**：需在 **系统层**（HAL/厂商音频库）实现，或应用层用 **WebRTC APM** 兜底；详见《TTS-NPU-与-AEC-BF-NS-AGC-方案与确认-给Claude.md》。

### 3.2 参考信号与 AEC

- **有参考信号**（扬声器正在播放的干净流）：可用 **AEC** 显著消除回声。  
  - **TTS 自身**：TTS 生成的 PCM 可作为 far-end reference，至少消掉 TTS 自回声。  
  - **电视声**：若 ROM 支持（如 AudioPlaybackCapture 或系统级权限），可尝试抓 TV 播放流作参考；否则仅能靠 NS + 波束/单麦 + VAD 增强鲁棒性。  
- 工程上优先：**先用 TTS PCM 做 AEC 参考**，保证「TTS 播报时用户插话」可识别。

### 3.3 音频前端推荐：WebRTC APM（当系统未提供时）

- 引入 **WebRTC Audio Processing Module（AEC3 + NS + AGC）**。  
- 将 **TTS 输出 PCM** 作为 reference 输入 AEC；NS/AGC 对麦克风流处理后再送 VAD/ASR。  
- 若无法拿到 TV 参考，则仅做 TTS 参考 AEC + NS + 更稳健的 VAD 切段。

### 3.4 TTS Ducking（不 duck TV）

- **电视音量**：不修改系统 TV 音量。  
- **TTS 音量**：  
  - 检测到用户开始说话（VAD 有能量/语音段开始）→ **立即降低 TTS 音量**（如 -10dB），便于 ASR 识别。  
  - 可选：TV 背景声较大时适当提高 TTS 音量，避免被淹没。  
- 实现：在 `TTSManager` 或播放层对 TTS 流做音量缩放，由状态机/ASR 事件驱动。

---

## 4. ASR：VAD + SenseVoice（NPU）与「业务层流式」

- 保持当前方案：**VAD 切段 + `NpuOfflineAsrEngine`（SenseVoice RKNN 20s）**，多语种（中/英/日/韩/粤）不变。  
- **业务层流式体验**：  
  - 段内每 **500～600ms** 对当前 segment 做一次离线识别，结果作为 **partial** 回显；  
  - 静音超过阈值后输出 **final**，触发意图解析；  
  - partial 做节流（如 ≥300ms 才刷新 UI）与简单去重（LCP/重复片段折叠），避免乱跳与叠字。

---

## 5. TTS 选型（NPU + 温柔自然 + 多语言）

- **交付目标**：**NPU TTS**（自然、温柔）。  
- **探索结论**（详见《TTS-NPU-与-AEC-BF-NS-AGC-方案与确认-给Claude.md》）：  
  - sherpa-onnx **无 TTS RKNN 后端**；RK3576 无现成 RKNN TTS 模型或官方 Demo。  
  - **可落地方案**：选用自然温柔模型（**MeloTTS** 或 **VITS** 等）→ 导出 **ONNX** → **rknn-toolkit2** 转 **RKNN** → Android 侧用 **RKNN Runtime** 推理；不支持的算子需在转换阶段做等价替换或简化。  
  - 首版若 NPU 链路过长，可暂保留 **CPU TTS**（如 sherpa-onnx TTS 或 MeloTTS CPU）过渡，不阻塞闭环演示，但交付目标仍为 NPU。  
- **多语言与粤语**：MeloTTS 支持中/英/西/法/日/韩等；粤语可用 MMS TTS 或普通话兜底。  
- **当前**：若已集成 sherpa-onnx TTS 引擎，可先保留；Milestone D 中增加多语言路由与「温柔自然」参数；NPU 替换按上述 ONNX→RKNN 链路单独立项推进。

---

## 6. 研发任务拆解（Milestone A/B/C/D）

### Milestone A：可演示闭环（1～2 天）

| 任务 ID | 内容 | 实现要点 | 验收 |
|--------|------|----------|------|
| A1 | 开机欢迎语 | 应用启动后（或首次进入主界面）触发一次 TTS：「您好，我是您的智能服务员，您可以对这电视通过『小聚小聚』呼叫我为您服务。」 | 冷启动后能听到完整欢迎语 |
| A2 | KWS「小聚小聚」触发 | 接入 sherpa-onnx KWS，关键词「小聚小聚」；命中后进入 LISTENING，TTS 回复「有什么可以帮助您？」 | 5 次纯唤醒 ≥4 次成功 |
| A3 | VAD 切段 + SenseVoice 识别 | 在 LISTENING 窗口内，用现有 VAD + `NpuOfflineAsrEngine` 做离线识别，结果打印或展示（暂不意图） | 说「我要退房」等能出正确文本 |
| A4 | 连说：唤醒词 strip | 对 ASR 整句做开头匹配「小聚小聚」等，剥离后剩余作为命令；若为空则按「纯唤醒」回复 | 5 次「小聚小聚帮我送水」≥4 次正确得到「送水」类命令 |
| A5 | Pre-roll 缓冲 | KWS 命中时，将前 300～800ms 音频拼入首段 ASR 输入 | 连说时句首不丢字（人工抽查） |

**交付物**：唤醒 → 回复 / 连说 → 命令文本正确；状态机 IDLE ↔ LISTENING ↔ THINKING/SPEAKING 清晰。

---

### Milestone B：意图路由与业务动作（2～4 天）

| 任务 ID | 内容 | 实现要点 | 验收 |
|--------|------|----------|------|
| B1 | 本地意图路由 | 基于 `IntentRouter` 扩展：退房、送水、打扫、呼叫前台、音量、返回等，rule-based（JSON/代码均可） | Top 4 意图：退房、送水、打扫、前台 端到端跑通 |
| B2 | TTS 播报确认话术 | 根据意图播报确认句，如「好的，我帮您通知送一瓶水。」 | 执行后能听到对应 TTS |
| B3 | 动作执行 | 广播给客控 App / 调 HTTP 接口 / 写日志；与现有 `IntentRouter`、`RouteResult` 对接 | 至少 4 个意图有明确执行动作或日志 |
| B4 | 失败兜底 | 识别为空或置信度低时，TTS「没听清，请再说一遍」类提示，不瞎编意图 | 乱说或静音时无错误执行 |

---

### Milestone C：全双工与回声抑制（4～10 天）

| 任务 ID | 内容 | 实现要点 | 验收 |
|--------|------|----------|------|
| C1 | SPEAKING 时继续采集与识别 | 状态机在 SPEAKING 不关麦、不关 ASR；TTS 播放期间仍送 VAD/ASR，支持 barge-in | 播 TTS 时用户说「我要退房」，10 次 ≥8 次正确识别 |
| C2 | WebRTC APM（AEC/NS/AGC） | 集成 WebRTC APM，**TTS 输出 PCM 作为 AEC reference**；麦克风流经 AEC→NS→AGC 再送 VAD/ASR | 插话识别率达标，无严重回声 |
| C3 | TV 背景声策略 | 若可拿到 TV 播放 reference → 一并送 AEC；否则 NS + VAD 强化 + 更稳的切段参数 | 电视播放时用户正常说话，10 次 ≥8 次正确识别 |
| C4 | TTS ducking | 检测到用户说话（VAD/能量）时立即降低 TTS 音量（如 -10dB），不改 TV 音量 | 插话时 TTS 明显变小，ASR 更稳 |

---

### Milestone D：多语言 TTS 与 ASR 一致（2～6 天）

| 任务 ID | 内容 | 实现要点 | 验收 |
|--------|------|----------|------|
| D1 | 语言识别 | 用 SenseVoice 输出语言标签或简易 heuristics（字符集/词典）判断中/英/日/韩/粤 | 能区分主要语种 |
| D2 | TTS 引擎路由 | zh/en/ja/ko → MeloTTS（或现有引擎）；yue → MMS 或普通话兜底 | 各语种能播报自然句，无乱码 |
| D3 | 音色与温柔参数 | 统一语速、停顿、音量曲线，偏「温柔自然」 | 主观听感可接受 |

---

## 7. 工程化必须项

- **全链路日志**：KWS 命中时间戳、VAD 切段起止、ASR 耗时、TTS 耗时、意图与执行结果；便于售后与线上排查。  
- **录音回放（debug）**：Debug build 下可保存最近若干秒环形缓冲，触发按钮或条件后写出 WAV，便于复现问题。  
- **参数可配置**：VAD 阈值、静音判定时长、pre-roll 长度、ducking 强度、TTS 音量曲线等放入配置文件或 `config.properties`，支持热更新或重启生效。

---

## 8. 接口与模块约定（供 Claude 实现时对齐）

- **状态机**：沿用/扩展 `StateMachine.kt` 的 `State` 与 `StateListener`；在 `MainActivity` 或统一 Controller 中驱动 IDLE → WAKE_DETECTED → LISTENING → THINKING → SPEAKING → ACTION → IDLE。  
- **KWS**：`KeywordSpotter`（sherpa-onnx）输出「命中」事件 → 回调中调用 `stateMachine.onWakewordDetected()` 并注入 pre-roll。  
- **ASR**：`AsrController` 在 LISTENING 下送 VAD 段给 `NpuOfflineAsrEngine`；partial/final 通过现有 `AsrListener.onAsrResult(text, isFinal)` 上报；在路由前对 `text` 做 WakeWord-stripping。  
- **意图**：`IntentRouter.route(text)` 返回 `RouteResult`；根据结果播 TTS、发广播、调接口。  
- **TTS**：`TTSManager.speak(text)`；在 SPEAKING 状态内根据 barge-in 事件调节 TTS 音量（ducking）。  
- **AEC**：在采集链路上插入 WebRTC APM，reference 输入来自 TTS 播放 PCM（若可拿到 TV 再增加一路）。

---

## 9. 验收总表（交付标准）

| 项目 | 标准 |
|------|------|
| 欢迎语 | 开机后播放指定欢迎语 |
| 纯唤醒 | 5 次「小聚小聚」≥4 次回复「有什么可以帮助您？」 |
| 连说 | 5 次「小聚小聚 + 指令」≥4 次正确识别并执行 |
| 意图 | 退房、送水、打扫、前台至少 4 个端到端可用 |
| Barge-in | TTS 播放时用户插话，10 次 ≥8 次识别正确 |
| 电视背景 | 电视播放时用户说话，10 次 ≥8 次识别正确 |
| 多语言 TTS | 中/英/日/韩（及可选粤语）能自然播报，无乱码 |

---

请按 **Milestone A → B → C → D** 顺序实现；每完成一个 Milestone 做一次验收再进入下一阶段。ASR 保持 NPU（SenseVoice）；TTS 交付目标为 NPU（见 §5），首版若 NPU 未就绪可暂用 CPU TTS 过渡。AEC/BF/NS/AGC 先按 §3.1 确认主板是否已支持，未支持则在系统层或应用层（WebRTC APM）实现。
