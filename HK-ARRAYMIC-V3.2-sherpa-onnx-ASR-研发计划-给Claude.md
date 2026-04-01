## HK-ARRAYMIC-V3.2：切换 sherpa-onnx ASR 研发计划（给 Claude 执行）

### 0. 目标与边界

- **目标**：在不改动现有音频采集链路（2ch@48k → avg → 16k）的前提下，将 ASR 引擎从 **Vosk** 切换为 **sherpa-onnx**，提升句首鲁棒性和整体识别率。  
- **前提**：  
  - 仍使用 HK-ARRAYMIC-V3.2（USB 2ch@48k）及当前 `AsrController` 中的 downmix/resample/VAD 逻辑；  
  - Phase-0 离线回放框架已经就绪，可用来对比 Vosk vs sherpa-onnx；  
  - 唤醒词/命令窗口/ring-buffer 方案按前一份「执行版 v0.1」推进，这里不重复设计，只关注 ASR 引擎替换。

---

## 1. 架构设计：解耦“音频链路”和“ASR 引擎”

### 1.1 统一 ASR 引擎接口

在 `com.joctv.agent.asr` 包下，引入统一接口，例如：

```kotlin
interface AsrEngine {
    fun start()
    fun stop()
    fun acceptPcm16k(data: ShortArray, length: Int, rms: Double)
    fun reset()
}
```

并约定：

- 所有引擎（Vosk / sherpa-onnx）都只接收 **16k / mono / PCM16**；  
- `AsrController` 负责：录音 + 2ch@48k → avg → 16k → VAD + 状态机；  
- `AsrEngine` 负责：具体的 ASR 调用与 JSON/文本解析；  
- `AsrController.AsrListener`（现有）仍然是对 UI / 业务层的唯一回调通道。

### 1.2 两个具体实现

- `VoskAsrEngine`：封装当前 `Recognizer` 流程（迁移已有代码即可）。  
- `SherpaOnnxAsrEngine`：新实现，封装 sherpa-onnx 的 ASR API。

切换方式：

- 在 `AsrController` 中根据配置选择引擎：

```kotlin
val engineType = props.getProperty("asr.engine", "vosk")
private val asrEngine: AsrEngine =
    if (engineType == "sherpa") SherpaOnnxAsrEngine(listener, context, config)
    else VoskAsrEngine(model, listener, config)
```

---

## 2. sherpa-onnx ASR 接入方案

### 2.1 依赖与模型准备（概念设计）

> 具体版本号按 sherpa-onnx 官方文档选择，这里只描述形态。

- 在 `build.gradle.kts` 中添加 sherpa-onnx Android ASR 依赖（类似现有 TTS 引擎接入方式）：  
  - 参考 sherpa-onnx 官方 Android 示例（ASR 部分），引入所需 AAR/JAR。  
- 在设备 `/sdcard/` 或 `assets` 下放置 sherpa-onnx ASR 模型目录，例如：  
  - `/sdcard/sherpa-onnx-asr/`  
  - 内含 encoder / decoder / joiner / tokens 文件等（具体文件名按所选模型确定）。

### 2.2 `SherpaOnnxAsrEngine` 设计要点

- 负责：  
  - 初始化 sherpa-onnx ASR（加载模型、创建 stream/decoder）；  
  - 暴露 `acceptPcm16k()`，将 16k PCM 分帧送入；  
  - 负责解码循环（在线/流式），将 partial / final 文本通过 `AsrListener` 回调。  
- 行为要求：  
  - 支持 partial（用于 UI 实时展示）；  
  - 支持 final（触发 `onAsrResult(text, isFinal = true)`，并清空内部状态）。  
  - 必须支持 `reset()`（用于 `triggerFinalResult()` 后清空解码器状态）。

### 2.3 与现有 `AsrController` 的集成

- 在 `AsrController.processAudioData(...)` 中：  
  - 将原来直接调用 Vosk `recognizer.acceptWaveForm()` 的逻辑迁移到 `AsrEngine`：  

```kotlin
// 由 AsrController 保证 data 是 16k/mono
asrEngine.acceptPcm16k(srcShortArray, length, rms)
```

- 在 `triggerFinalResult()` 中：  
  - 对 Vosk：保留 `recognizer.finalResult` 的处理（仅在 Vosk 引擎存在时使用）；  
  - 对 sherpa-onnx：通过 `AsrEngine.reset()` 触发内部 `final` 解码与清空。  
  - 为简化，可以在 sherpa 模式下：由 `SherpaOnnxAsrEngine` 自己在静音结束/窗口结束时触发 final，并直接回调 `AsrListener`，`triggerFinalResult()` 内只在 Vosk 模式调用。

---

## 3. Phase-B 测试与 A/B 对比（Vosk vs sherpa-onnx）

### 3.1 离线回放对比

利用已经实现的 **离线回放模式**：

- 配置切换：  
  - `asr.engine=vosk` 与 `asr.engine=sherpa` 各跑一轮。  
- 测试集：  
  - `S1_steady_*.wav`（近距正对）  
  - `S2_side_left/right_*.wav`（侧向）  
  - `S3_far_*.wav`（远距）  
- 对比指标：  
  - 《静夜思》四句关键字是否识别正确；  
  - 句首是否出现“朕叶子/歌房钱/行情呢着”等错误前缀的频率；  
  - 延迟和 CPU 占用（粗略观测）。

### 3.2 APK 实时 A/B

在离线回放确认 sherpa-onnx 明显优于 Vosk 后：

- 在相同环境下，使用相同文本（固定口令 + 《静夜思》），对比：  
  - `asr.engine=vosk` 实时结果；  
  - `asr.engine=sherpa` 实时结果。  
- 日志中记录：  
  - VOICE_START / VOICE_END 位置；  
  - final 文本；  
  - 溢出次数（RecordThread overflow）；  
  - 若已实现唤醒/命令窗口，则记录窗口内的识别表现。

### 3.3 Phase-B 验收标准

- 在 **干净环境、近/中/远** 下：  
  - 固定指令集（例如 20 条命令 × 每条近/中/远各 10 次）意图识别正确率 ≥ 98–100%；  
  - 《静夜思》：  
    - 关键字：床前、明月光、疑是、地上霜、举头、望明月、低头、思故乡 **不漏不错**；  
    - 允许极短语气词前缀，但不接受长串无关前缀。  
- 若 sherpa-onnx 在以上指标上明显优于 Vosk，则：  
  - 将 `asr.engine` 默认值从 `vosk` 改为 `sherpa`；  
  - Vosk 可保留为备选或移除以减小体积。

---

## 4. 兼容配置与回退策略

### 4.1 配置项补充

在 `config.properties.template` 中增加：

```properties
# ASR 引擎选择: vosk | sherpa
asr.engine=vosk

# sherpa-onnx ASR 模型目录（示例路径）
asr.sherpa.model.dir=/sdcard/sherpa-onnx-asr
```

并在代码中读取该路径，初始化 sherpa-onnx 模型。

### 4.2 回退策略

- 若 sherpa-onnx 初始化失败或在某些设备上性能/稳定性不达标：  
  - 记录错误日志；  
  - 自动回退到 Vosk 引擎（或提示用户检查模型路径）。  
- 所有唤醒词 / 命令窗口 / ring-buffer / VAD 逻辑均保持与引擎解耦，便于后续继续更换或升级 ASR 引擎。

---

## 5. Claude 执行 Checklist（照此拆任务）

1. **重构 `AsrController` 与 ASR 引擎解耦**：  
   - 引入 `AsrEngine` 接口；  
   - 将现有 Vosk 逻辑迁移到 `VoskAsrEngine`。  

2. **接入 sherpa-onnx ASR**：  
   - 添加 Android 依赖；  
   - 实现 `SherpaOnnxAsrEngine`，支持 `acceptPcm16k()` / partial / final / reset。  

3. **配置与引擎选择**：  
   - 支持 `asr.engine` 配置；  
   - 支持 `asr.sherpa.model.dir` 配置；  
   - 加入回退逻辑（sherpa 初始化失败时回到 Vosk）。  

4. **离线回放 A/B 测试**：  
   - 用 S1/S2/S3 wav 对比 Vosk vs sherpa-onnx；  
   - 输出简明统计（正确率 + 典型错误样例）。  

5. **实时 APK 测试**：  
   - 在干净环境下对比两种引擎的实际效果；  
   - 若 sherpa-onnx 明显更好，则在配置模板中将默认引擎改为 `sherpa`。  

完成以上步骤后，再结合前一份「唤醒词 + ring-buffer + Phase-0/Phase-A 执行版」一起演进到 AEC/降噪阶段。

