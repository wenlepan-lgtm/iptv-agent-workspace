# IPTV Edge Agent v2 研发任务（给 Claude）

> 在 **v1.0（iptv-edge-agent）基础上** 做优化，不推翻现有工程。  
> 工作区根目录：`/Users/ala/工作项目/agent`  
> 参考文档：`agentv2项目计划.txt`、`IPTV-Edge-Agent-v1-Optimization-Plan-v0.1.md`、`AEC_ASR_AlwaysOn_Test_Pack_v0.1.md`

---

## 0. 你需要用到的代码与资源（请先确认）

### 0.1 主工程（v1 基础，必须存在）
- **路径**：`iptv-edge-agent/`
- **内容**：v1 完整工程（Kotlin），包含：
  - `app/src/main/java/com/joctv/agent/`：MainActivity、**asr/**（AsrController、VAD 等）、**tts/**（TTSManager）、**intent/**（IntentRouter）、**web/**（WebAnswerClient）、**engine/**、**utils/**
  - `app/src/main/res/layout/activity_main.xml`：v1 正式 UI 布局（**不得改布局结构**）
  - `app/build.gradle.kts`：当前依赖含 Vosk、OkHttp 等

**若 `iptv-edge-agent/` 不存在或不全，请用户从 https://github.com/wenlepan-lgtm/iptv-edge-agent 克隆到该路径。**

### 0.2 v1 的 ASR / TTS / 数字人 —— 是否需要你“复制”？
- **ASR**：v1 使用 Vosk（`AsrController` + `AsrEngine`）。v2 将 **替换** 为 sherpa-onnx + SenseVoice，不需要你“复制 v1 ASR”，但需 **保留 v1 的 AsrListener 接口与 MainActivity 回调用法**，方便只换实现。
- **TTS**：v1 已有 `TTSManager`（sherpa-onnx TTS 引擎）。v2 在 **保留并沿用** 该模块的基础上再做“升级”（更自然、延迟优化）。**只要 iptv-edge-agent 目录完整，TTS 代码已在项目内，无需额外复制。**
- **数字人**：README 提到 DUIX SDK 与 2D 数字人，当前仓库的 `app/build.gradle.kts` 中未见 DUIX 依赖。
  - **若你本地有 DUIX SDK（aar / so / 或源码）**：请放到 `iptv-edge-agent/app/libs/` 或按 SDK 文档集成，并在本任务中说明已放置路径。
  - **若暂无 DUIX**：Phase 5 可先做“占位 View + TTS 播报状态联动”，后续再接 2D/3D 资源。

**结论：只要保证 `iptv-edge-agent/` 是完整 v1 克隆，ASR/TTS/路由/UI 都在；只需在“需要 3D/2D 数字人 SDK”时，由用户把 DUIX（或其它数字人 SDK）复制到 `app/libs` 或指定目录并告知你路径。**

---

## 1. 目标与 KPI（必须达成）

| 维度 | 目标 | 说明 |
|------|------|------|
| ASR 指令成功率 | ≥ 98%（先 90% 再冲 98%） | 本地客房控制指令 |
| ASR 误触发 | ≤ 10 字/15 秒（或 ≤1 次/分钟） | 播放视频 + 静默时 |
| 端到端延迟 | ≤ 500ms～1s（先可用再优化） | 说完到出字 |
| TTS | 更清晰、更自然，起声 < 300ms | 相对 v1 提升 |
| 数字人 | 先 2D/占位 + TTS 状态联动，再 3D 口型 | P2 |

---

## 2. 硬性约束（禁止违反）

- **UI**：以 v1 的 `activity_main.xml` 为准，**不得新增、删除、改位置/大小/颜色/文案**；只允许在“同一布局”下接新 ASR 输出（如 partial/final 仍显示在原有 TextView）。
- **ASR 常驻**：不得依赖“用户点击开始识别 → 降低播放音量 → 停止识别 → 恢复音量”的交互；ASR 应后台常驻或由配置开关控制。
- **主工程**：所有功能在 **iptv-edge-agent** 内完成；可参考 `AecVideoTestApp`、`AecTestApp` 的代码与脚本，但不要在主工程里复制一套“测试用 UI”。

---

## 3. Phase 1：音频前端与 AEC 验证（P0）

**目标**：确认设备在“播放视频 + 静默/朗读”场景下，RAW 与 VOICE_COMMUNICATION 的差异，以及是否具备可用 AEC 前提。

### 3.1 交付物
- 在 **iptv-edge-agent** 或 **AecTestApp** 中实现 **ForegroundService 常驻录音**（不依赖“点开始识别”）：
  - 两种 `AudioSource`：RAW、VOICE_COMMUNICATION（可配置切换）
  - 固定格式：PCM16, mono, 16k（若仅 48k 则记录在元数据并注明）
  - 按 `AEC_ASR_AlwaysOn_Test_Pack_v0.1.md` 的命名：`{mode}_{vol}_{scenario}_{timestamp}.wav`，并输出对应 json（source、sampleRate、RMS、clipping%、duration、设备信息）
- 导出与打包：将 wav + json 可打包为 zip，便于 PC 批处理。

### 3.2 测试三刀（必须跑）
- **T1**：播放视频 + 静默  
- **T2**：播放视频 + 朗读（近 0.5m / 远 1.5m）  
- **T3**：不播放视频 + 朗读  

矩阵：RAW / VOICE_COMMUNICATION × 音量（如 v10/v40/v70）× T1/T2/T3，最小集 6 段（如 raw_v40_t1/t2/t3, vc_v40_t1/t2/t3）。

### 3.3 判定与下一跳
- **T1 误识别极少**：认为当前链路具备可用 AEC/门控 → 进入 Phase 2。
- **T1 仍大量视频台词**：当前无有效 AEC/reference → 文档中明确结论，并给出“硬件回采排查”或“PlaybackCapture 评估（需授权）”或“唤醒+VAD 兜底”的下一步建议。

---

## 4. Phase 2：v1 内接入“实时字幕（ASR 输出）”（P0）

**目标**：在 v1 的 **同一 UI** 上稳定显示识别结果（partial + final），交互与 v1 一致（无需点“录音”才出字）。

### 4.1 实现要求
- 以 **iptv-edge-agent** 为主工程。
- **替换 ASR 引擎**：用 sherpa-onnx + SenseVoiceSmall INT8 替代 Vosk。
  - 参考 `AecVideoTestApp` 的集成方式：`SherpaOnnxHelper`（或 Kotlin 版）、AAR `sherpa-onnx-1.12.23.aar`（路径：`/Users/ala/工作项目/agent/sherpa-onnx-1.12.23.aar`，请复制到 `iptv-edge-agent/app/libs/` 并配置 `flatDir` + `implementation(name: 'sherpa-onnx-1.12.23', ext: 'aar')`）。
  - 模型：`model.int8.onnx` + `tokens.txt` 放在 `app/src/main/assets/models/sensevoice/`，启动时拷贝到 filesDir 再加载。
- 新增或改造 **AsrController**：
  - 保持与 v1 的 **AsrListener** 接口兼容（partial / final 回调）。
  - 音频：`AudioRecord(VOICE_COMMUNICATION, 16k, mono)`，分块（如 1～1.5 秒）送 sherpa-onnx 识别，结果映射到 partial/final 回调。
- **UI**：不新增控件；用 v1 已有的 `tvAsrPartial`、`tvAsrFinal`、`tvReplyText` 等显示；`btnPauseResume` 仍为“暂停/恢复 ASR”，不是“开始一次录音”。

### 4.2 验收
- 编译通过，安装到机顶盒可运行。
- 布局与 v1 完全一致。
- 开机后 ASR 自动初始化，说话时 partial 实时更新、final 进入路由与 TTS；无需点“录音”按钮。

---

## 5. Phase 3：ASR 模型与 AB 对比（P0）

**目标**：在“客房指令”场景下把识别成功率做上去，并有可复现对比。

### 5.1 交付物
- 使用 Phase 1 产出的标准 wav（及后续补充样本）做 **同一套录音、多模型/多参数** 的批量识别。
- **PC 端脚本**（Python/Bash）：对指定目录 wav 调用 sherpa-onnx（或已有多模型）输出 `*.asr.txt`，并生成汇总表（csv/md）：T1 误识别字数、T2/T3 指令命中率等。
- 文档中给出“主力模型 + 备选”建议及 AB 结果摘要。

---

## 6. Phase 4：TTS 升级（P1）

**目标**：更自然、延迟更低，并与现有 UI/数字人状态联动。

### 6.1 实现要求
- 保留 v1 的 **TTSManager** 与播报流程；可替换底层引擎或参数（如更换 sherpa-onnx TTS 模型、调节 chunk 与起声策略）。
- 与 **MainActivity** 的 TTS 回调（onTTSStart/onTTSDone 等）及 ASR mute/unmute 逻辑保持一致。
- 若有数字人占位或 DUIX：TTS 播报时驱动“说话/待机”状态。

### 6.2 交付物
- 3～5 句标准播报样本的延迟与听感说明（可选简短对比表）。
- 代码中 TTS 与 UI/数字人状态联动的注释或说明。

---

## 7. Phase 5：数字人（2D 占位 → 3D）（P2）

**目标**：先跑通“文字 + TTS + 口型/状态”演示；2D 可过渡，3D 为目标。

### 7.1 实现要求
- 若用户已提供 **DUIX 或其它数字人 SDK**（在 `app/libs` 或指定路径）：在此基础上做 2D/3D 集成，TTS 播报驱动口型或状态。
- 若暂无 SDK：实现 **占位 View**（如 v1 layout 中的 `tvDigitalHumanAvatar` 区域），TTS 播报时更新状态（说话中/空闲），并预留接口便于后续接入 3D 模型与口型数据。

### 7.2 交付物
- 至少：TTS 播报时数字人区域有明确状态反馈。
- 若有 3D 资源：最小可用的口型或能量驱动动画说明/代码位置。

---

## 8. 交付清单（给 Claude 的 checklist）

- [ ] **Phase 1**：ForegroundService 常驻录音（RAW/VC）、标准命名 wav + json、导出 zip；三刀 T1/T2/T3 跑通并记录；结论（AEC 是否可用）与下一跳写进文档。
- [ ] **Phase 2**：iptv-edge-agent 内 ASR 替换为 sherpa-onnx + SenseVoice；实时 partial/final 在 v1 UI 上显示；不新增控件、不依赖“点录音”；AAR 与模型路径配置正确。
- [ ] **Phase 3**：PC 批量识别脚本 + 汇总表；AB 结果与主力/备选模型建议。
- [ ] **Phase 4**：TTS 升级（在保留 TTSManager 接口前提下）+ 与 UI/数字人状态联动。
- [ ] **Phase 5**：数字人占位或 DUIX 集成；TTS 驱动状态；若有 3D 则最小口型/状态演示。
- [ ] **README 或一页说明**：如何编译、如何跑 Phase 1 测试、如何导出与判定 Pass/Fail、如何跑 PC 脚本；若用户需复制 DUIX 等，写清放置路径。

---

## 9. 参考文件与路径速查

| 用途 | 路径 |
|------|------|
| v1 主工程 | `iptv-edge-agent/` |
| v1 布局（不可改结构） | `iptv-edge-agent/app/src/main/res/layout/activity_main.xml` |
| v1 主界面逻辑 | `iptv-edge-agent/app/src/main/java/com/joctv/agent/MainActivity.kt` |
| v1 ASR（将被替换） | `iptv-edge-agent/app/src/main/java/com/joctv/agent/asr/` |
| v1 TTS（保留并升级） | `iptv-edge-agent/app/src/main/java/com/joctv/agent/tts/TTSManager.kt` |
| sherpa-onnx AAR | 工作区根目录 `sherpa-onnx-1.12.23.aar` → 复制到 `iptv-edge-agent/app/libs/` |
| sherpa 集成参考 | `AecVideoTestApp/app/src/main/java/com/joctv/agent/SherpaOnnxHelper.java`、MainActivity 实时 ASR 逻辑 |
| SenseVoice 模型 | `AecVideoTestApp/app/src/main/assets/models/sensevoice/`（可复制到 iptv-edge-agent 同路径） |
| AEC 测试任务包 | `AEC_ASR_AlwaysOn_Test_Pack_v0.1.md` |
| 优化方案说明 | `IPTV-Edge-Agent-v1-Optimization-Plan-v0.1.md`、`agentv2项目计划.txt` |

---

## 10. 若你需要 v1 的 ASR/TTS/数字人“复制到项目目录”的结论（给用户）

- **ASR**：不需要单独复制；v1 的 AsrController 等已在 `iptv-edge-agent` 内，你将用 sherpa-onnx **替换** 实现，保留接口即可。
- **TTS**：不需要单独复制；v1 的 TTSManager 已在 `iptv-edge-agent` 内，你只需在该工程内 **保留并升级**。
- **数字人**：若 v1 已集成 DUIX（当前仓库未看到依赖），只需保证工程完整；**若你有单独的 DUIX SDK（aar/so/资源）**，请复制到 `iptv-edge-agent/app/libs/`（或按 SDK 要求放置），并告知 Claude 路径。若无，则先做占位 + TTS 状态联动，后续再接 2D/3D。

**你只要保证 `iptv-edge-agent/` 是完整 v1 克隆；若有 DUIX 等数字人 SDK，再按上面说明复制到 app 目录即可。**
