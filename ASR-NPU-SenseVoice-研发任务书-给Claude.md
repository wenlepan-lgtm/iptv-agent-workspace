# ASR NPU 离线方案（VAD + SenseVoice RKNN）研发任务书

**面向**：Claude  
**项目**：iptv-edge-agent（RK3576 机顶盒语音识别）  
**更新**：根据前序结论切换为 NPU 离线 ASR 方案，本任务书为落地研发与测试依据。

---

## 一、前因后果（背景）

### 1.1 原方案与问题

- **原方案**：在 RK3576 Android 14 上使用 sherpa-onnx 的 **Streaming Zipformer RKNN 模型**（`sherpa-onnx-rk3576-streaming-zipformer-small-bilingual-zh-en-2023-02-16`）做实时流式 ASR，推理走 NPU。
- **现象**：模型加载成功、NPU 有负载、`decode()` 多次迭代执行，但 **`getResult().text` 始终为空字符串**。对官方 `test_wavs` 自检同样空结果。
- **根因**：经排查与社区确认，此为 **sherpa-onnx 已知 bug**（GitHub issue #2861）：RK3576 上该 small 流式 Zipformer RKNN 模型存在兼容性问题，输出空文本；与代码逻辑、`librknnrt.so` 版本、`tokens.txt`、`inputFinished()` 等无关。
- **结论**：不再在 RK3576 上使用该 streaming Zipformer small RKNN 模型，需换用其他 **NPU 可用的 ASR 方案**。

### 1.2 业务约束

- **必须使用 NPU**：CPU 模式占用 40%～50%，商用场景发热与续航不可接受；目标为推理全在 NPU（RKNN）上。
- **体验要求**：尽量接近“边说边出字”的流式体验，可通过 **业务层分段 + 增量回显** 实现，而非强依赖模型本身 streaming。

### 1.3 选定新方案

采用 **VAD 分段 + NPU 离线 ASR**：

- **模型**：sherpa-onnx 官方提供的 **SenseVoice RKNN 离线模型**（固定 20 秒输入，中英日韩粤）。
- **流程**：AudioRecord → mono 16k → VAD 切出语音段 → 每段送入 SenseVoice RKNN 做离线识别 → 结果回调；可选在段进行中每 600ms 做一次增量识别作为 partial 回显。
- **优势**：绕过 RK3576 streaming Zipformer 的 bug，推理全在 NPU，稳定可商用；流式体验由分段与 UI 增量展示实现。

---

## 二、目的（研发目标）

1. **实现 NPU 离线 ASR 引擎**：基于 sherpa-onnx OfflineRecognizer + SenseVoice RKNN 模型，封装“输入一段 16k float PCM → 输出识别文本”的接口。
2. **接入 VAD 分段**：在现有 `AsrController` 音频管线基础上，增加/复用 VAD 状态机与双缓冲（ringBuffer + segmentBuffer），在语音段结束时触发离线识别；可选支持段内增量识别。
3. **与现有业务对接**：识别结果通过现有 `AsrListener`（或等价接口）回调，供意图路由、TTS、UI 等使用；不破坏现有唤醒词、TTS 静音等逻辑。
4. **可灰度、可配置**：如 partial 增量开关、VAD 参数、模型路径等可通过配置文件或常量调节，便于验收与迭代。

---

## 三、已准备资源（本地文件）

以下文件已下载并放在 **agent 项目根目录**（与 `iptv-edge-agent/` 同级）：

| 文件 | 说明 | 用途 |
|------|------|------|
| `sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2` | SenseVoice RKNN 模型包（20 秒输入，中英日韩粤） | 解压后得到 `model.rknn`、`tokens.txt` 等，供 OfflineRecognizer 加载 |
| `lei-jun-test.wav` | 官方长语音测试 WAV | 用于离线自测（VAD 切段 + SenseVoice 识别）或整段解码验证 |
| `silero_vad.onnx` | Silero VAD 模型 | 可选：与自研 VAD 对比或替换；首期可用现有 RMS/hangover VAD |

**使用前请将 SenseVoice 压缩包解压**，例如：

```bash
cd /path/to/agent
tar xvf sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2
```

解压后目录名：`sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17`，其内应含 `model.rknn`、`tokens.txt` 等。部署到设备时可将该目录拷贝到 `/sdcard/` 或应用私有目录，并在代码中配置 `modelDir`。

---

## 四、研发任务与步骤（实施顺序）

### 4.1 第一阶段：NPU 离线引擎 + 句末 final

1. **解压并确认模型文件**  
   - 在 agent 根目录解压 `sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2`。  
   - 确认解压目录内存在 `model.rknn`、`tokens.txt`（及文档中要求的其它文件）。

2. **实现 NpuOfflineAsrEngine（SenseVoice RKNN）**  
   - 新建或扩展 Kotlin 类，封装 sherpa-onnx **OfflineRecognizer**，使用 **SenseVoice RKNN** 配置（`OfflineRecognizerConfig` 中指定 sense_voice 模型路径、tokens、provider=rknn 等）。  
   - 接口形式：`suspend fun recognize(samples16k: FloatArray): String`（输入 16kHz 单声道 float[-1,1]，返回识别文本）。  
   - 初始化时加载一次，常驻；推理在后台协程执行，避免阻塞主线程。

3. **集成到 iptv-edge-agent**  
   - 在 `MainActivity` 或现有 ASR 初始化流程中：若检测到 SenseVoice 模型目录存在（如 `/sdcard/.../sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17` 或从 agent 解压目录拷贝后的路径），则创建 `NpuOfflineAsrEngine` 实例。  
   - 模型目录可配置（如 `config.properties` 或常量），便于切换环境。

4. **VAD 分段 + 段结束触发识别**  
   - 在现有 `AsrController` 音频管线（2ch@48k → downmix → 16k mono）基础上：  
     - 增加 **ringBuffer**（如保留最近 30～40 秒 16k float），用于 VAD 检测到“语音开始”时回填约 200ms 前缀，避免句首截断。  
     - 增加 **segmentBuffer**：在 VAD 判定为“语音中/结束”期间累积当前段样本。  
   - VAD 参数建议：起始触发连续约 5 帧高能量；结束触发连续约 40～60 帧低能量（约 800～1200ms 静音）；`minSegmentSec` 约 0.3～0.5s，`maxSegmentSec` 约 16～18s（低于模型 20s 上限）。  
   - **当 VAD 判定语音段结束**且时长 ≥ `minSegmentSec` 时：将 segmentBuffer 拷贝为 `FloatArray`，提交给 `NpuOfflineAsrEngine.recognize()`，将返回文本作为 **final** 结果通过现有 `AsrListener.onAsrResult(text, isFinal=true)` 回调。

5. **推理队列与并发**  
   - 使用单任务队列（如 `Mutex` + 协程）：同一时刻只执行一个 `recognize()`，避免 NPU 多任务抢占导致抖动或异常。

6. **不破坏现有逻辑**  
   - 保留唤醒词、TTS 静音、意图路由等；仅将“送进 ASR 并回调结果”的路径从 OnlineRecognizer 改为“VAD 段 → NpuOfflineAsrEngine”；若未启用 SenseVoice 或模型不存在，可回退到原有 Vosk 或其它引擎逻辑（若仍有保留）。

### 4.2 第二阶段（可选）：增量 partial + 去抖

- 在 VAD 处于“语音中”时，每 600ms 将当前 segmentBuffer 内容做一次 `recognize()`，结果作为 **partial** 通过 `onAsrResult(text, isFinal=false)` 回调。  
- 对 partial 做节流（如 UI 更新间隔 ≥300ms）和简单去重/去抖（如与上一句 LCP、重复片段折叠），避免乱跳与叠词。  
- 该能力建议做成配置开关（如 `asr.npu.offline.partial.enabled`），默认可关闭，先保证句末 final 稳定。

---

## 五、测试步骤

### 5.1 模型与引擎自检（建议最先做）

1. 在 **PC 或设备上** 使用 sherpa-onnx 官方命令行（若已安装）对解压后的 SenseVoice 目录做一次解码验证，例如（路径按实际修改）：
   - 使用官方文档中的 `sherpa-onnx-vad-with-offline-asr` 示例命令，指定 `--sense-voice-model=.../model.rknn`、`--tokens=.../tokens.txt`、`--provider=rknn`，输入 `lei-jun-test.wav`。  
   - 若命令行能正常输出文本，说明模型与 runtime 正常，再在 Android 上做集成。

2. **在 Android 应用内自检**（推荐）：  
   - 在 `NpuOfflineAsrEngine` 初始化成功后，增加一次“离线自检”：读取 `lei-jun-test.wav`（可从 assets 或 `/sdcard/` 拷贝），转换为 16k float，截取前 20 秒（或按模型要求 padding/截断），调用 `recognize()`。  
   - 在 Logcat 中打印识别结果；若为非空且与预期大致相符，则认为引擎与模型在设备上工作正常。

### 5.2 实时麦克风 + VAD 段结束

1. 部署 APK 到 RK3576 设备，确保 SenseVoice 模型目录已放到设备（如 `/sdcard/...`）并配置正确。  
2. 打开应用，进入 ASR 就绪状态（确保使用 NPU 离线方案分支）。  
3. 说一句完整话（如“打开客厅的灯”），保持句末约 1 秒静音，等待 VAD 判定段结束。  
4. **预期**：约 1～2 秒内收到 final 识别结果并展示；Logcat 中可见一次 `recognize()` 调用及返回文本。  
5. 多次测试不同句式与长度（短句、长句接近 15 秒），确认无崩溃、无长时间无响应、无空结果。

### 5.3 长文件离线测试（lei-jun-test.wav）

1. 若应用支持“离线 WAV 测试”入口：选择 `lei-jun-test.wav`，执行 VAD 切段 + 逐段 SenseVoice 识别（或先整段 20s 内解码）。  
2. **预期**：能输出多段或整段文本，与官方 CLI 结果相近；无闪退、无空结果。

### 5.4 与现有业务联调

1. 确认 final 结果正确进入意图路由、TTS 播报、UI 展示等现有流程。  
2. 确认唤醒词、TTS 期间 ASR 静音等逻辑仍生效，无冲突。

### 5.5 性能与稳定性

1. 观察 NPU 占用（如 `cat /sys/kernel/debug/rknpu/load` 或系统监控），确认推理时 NPU 负载明显、CPU 较流式方案低。  
2. 连续运行 10 次以上“说话 → 静音 → 出结果”循环，无内存持续增长、无 ANR、无空结果异常增多。

---

## 六、验收标准（简要）

- **功能**：在 RK3576 上，使用 SenseVoice RKNN 模型，对麦克风实时语音做 VAD 分段并在段结束时输出正确 final 文本；可选 partial 增量回显。  
- **性能**：句末静音后 1～2 秒内出结果；NPU 参与推理，CPU 占用较原 CPU 流式方案显著降低。  
- **稳定**：无崩溃、无 ANR；长句与多次连续识别正常。  
- **兼容**：不破坏现有唤醒词、TTS、意图路由等逻辑；模型路径可配置，便于部署与灰度。

---

## 七、参考与约束

- **sherpa-onnx RKNN 文档**：  
  https://k2-fsa.github.io/sherpa/onnx/rknn/models.html  
  （SenseVoice 固定时长、VAD 切段、decode 示例）
- **Android 工程**：`iptv-edge-agent/`，现有 ASR 相关类：`AsrController`、`SherpaOnnxAsrEngine`、`MainActivity` 等；JNI 使用 sherpa-onnx 带 RKNN 的 so。
- **不采用 CPU 流式**：本方案仅使用 NPU 离线 SenseVoice（及可选 silero_vad），不依赖 CPU 版 streaming 模型作为主路径。
- **签名与部署**：agent 目录下已有签名工具（如 `3576android14签名文件/`），若涉及系统库或特殊部署再使用；当前为 APK 内集成模型与 JNI，按常规安装即可。

---

**请按上述顺序完成第一阶段（引擎 + VAD 句末 final），再视需要做第二阶段（增量 partial）。测试步骤 5.1～5.5 建议全部执行并记录结果，便于验收与问题定位。**
