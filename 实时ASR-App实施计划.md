# 实时 ASR App 实施计划（参考 v1 UI，看到实时文字识别）

**目标**：在现有 AecVideoTestApp 上增加**实时语音识别**，边说边在界面显示识别文字；**严格按照 V1.0 的 UI 布局，不许改变**。

**前提**：Phase 1 已完成，sherpa-onnx + SenseVoiceSmall INT8 在 PC 端验证通过（CER 0%，25x 实时率）。

---

## 一、UI 约束（必须遵守）

### 1.1 严禁改动

- **不得新增任何控件**（不增加按钮、TextView、LinearLayout 等）。
- **不得修改现有 layout**：`activity_main.xml` 与 V1.0 保持一致，禁止改结构、顺序、id、文案。
- V1.0 布局即当前 AecVideoTestApp 的：标题 → 视频区（含播放/暂停/停止、音量）→ 分隔线 → 录音控制（录 Raw / 录 Voice）→ 录音状态 → ASR 识别结果（`tv_asr_result`）→ 底部（拉取文件、清空结果）。

### 1.2 实时识别仅通过现有 UI 实现

- **显示位置**：实时识别结果**只**显示在现有 **`tv_asr_result`** 中（覆盖或追加，由实现决定；若为实时模式，可整块刷新为该段识别文字）。
- **触发方式**：不增加新按钮，只能复用现有操作，例如：
  - **长按「录 Voice」(AEC+NS+AGC)**：长按进入/退出实时识别模式（推荐）；
  - 或 **长按「录 Raw」** 作为实时识别开关；
  - 或 在「清空结果」上通过长按切换实时模式（若产品更倾向该逻辑）。
- **状态反馈**：通过现有 **`tv_record_status`** 显示“实时识别中…”或“就绪”等，不新增控件。

---

## 二、实时识别技术方案

### 2.1 方案选择

| 方案 | 说明 | 实时性 | 实现难度 |
|------|------|--------|----------|
| **A. 流式/模拟流式** | 每 20–60ms 送一帧给引擎，引擎返回部分结果并刷新 UI | 最好 | 需 sherpa-onnx Android 流式 API + JNI |
| **B. 分块离线** | 每 1–2 秒录一块，用离线引擎识别一块，结果追加到 UI | 接近实时（1–2s 延迟） | 仅需离线 API，较易 |

**建议**：先做 **B（分块离线）**，在机顶盒上跑通「边说边出字」；若后续有 sherpa-onnx 官方 Android 流式示例或预编译 so，再替换为 **A**。

### 2.2 分块离线实现要点（方案 B）

1. **线程与循环**
   - 单独线程：`AudioRecord` 按 16kHz、单声道、16bit 连续读。
   - 每累积 **1.0 秒**（16000 样本）或 **1.5 秒** 的 PCM，交给**离线识别器**识别一次；识别结果在主线程刷新 **`tv_asr_result`**（实时模式下可覆盖为当前段文字，或追加；不新增控件）。

2. **识别器**
   - 使用 sherpa-onnx **OfflineRecognizer**（SenseVoiceSmall INT8），与 Phase 1 同一模型。
   - 输入：一段 float/short PCM，长度 = 1~2 秒；输出：识别文本。

3. **资源**
   - 模型放在 **assets**（如 `models/sensevoice/` 下 `model.int8.onnx` + `tokens.txt`），首次启动拷贝到 `getFilesDir()` 或 `getCacheDir()`，Recognizer 从该路径加载。

---

## 三、sherpa-onnx Android 集成步骤

### 3.1 获取 native 库与模型

1. **预编译库**（二选一）
   - 从 [sherpa-onnx Releases](https://github.com/k2-fsa/sherpa-onnx/releases) 下载 **sherpa-onnx-v*-android.tar.bz2**（或带 arm64-v8a 的包），解压得到 `libsherpa-onnx.so` 等，放入 `app/src/main/jniLibs/arm64-v8a/`。
   - 若官方包无 SenseVoice 示例，则从 [sherpa-onnx Android 文档](https://k2-fsa.github.io/sherpa/onnx/android/build-sherpa-onnx.html) 按文档用 NDK/CMake 编译，产物同样放到 `jniLibs`。

2. **模型**
   - 使用 Phase 1 的 **sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17**：将 `model.int8.onnx`、`tokens.txt` 放入 `app/src/main/assets/models/sensevoice/`（或同名目录），运行时拷贝到 `getFilesDir()/models/sensevoice/` 再创建 Recognizer。

### 3.2 JNI / Java 调用

- 若预编译包内带 **Java 或 Kotlin 封装**（如 `SherpaOnnx.java`），直接在 `MainActivity` 或单独 `AsrHelper` 里调用。
- 若无，需自行写 JNI 封装：  
  - 创建 `OfflineRecognizer`（传 model 路径、tokens 路径、num_threads 等）；  
  - 提供 `recognize(short[] samples)` 或 `recognize(float[] samples)`，内部调 C++ API，返回 String。

### 3.3 在 AecVideoTestApp 中的调用流程

1. 应用启动：检查 `getFilesDir()/models/sensevoice/` 是否存在模型，不存在则从 assets 拷贝。
2. **长按「录 Voice」** 进入实时识别：
   - 创建 `AudioRecord`（VOICE_COMMUNICATION，16k，MONO，16bit）；
   - 创建 sherpa-onnx OfflineRecognizer（仅创建一次，可复用）；
   - 启动识别线程：循环 `audioRecord.read()` → 累积 1~2 秒 → `recognizer.decode(samples)` → `runOnUiThread` 更新 `tv_asr_result.setText(result)`（或 append）；`tv_record_status` 显示“实时识别中…”。
3. **再次长按「录 Voice」** 退出实时识别：置标志位停止循环，释放 `AudioRecord`；`tv_record_status` 恢复“就绪”。可选：将本次整段结果保留在 `tv_asr_result`。

---

## 四、交付与验收

- [ ] **UI**：**严格保持 V1.0 布局不变**：不新增、不删除、不修改任何控件或 layout；实时识别通过长按「录 Voice」触发，结果仅展示在现有 `tv_asr_result`，状态仅用现有 `tv_record_status`。
- [ ] **模型与 so**：SenseVoice INT8 模型与 sherpa-onnx Android so 正确放置并加载，无崩溃。
- [ ] **实时效果**：长按「录 Voice」后说话，1–2 秒内能在「ASR 识别结果」区域看到对应文字更新；识别准确率与 Phase 1 相当。
- [ ] **可选**：短按「录 Raw」/「录 Voice」录音完成后，用同一离线引擎对刚录的 WAV 做识别，结果写入 `tv_asr_result`，替代当前“模拟 ASR”占位逻辑。

---

## 五、参考链接

- sherpa-onnx Android：https://k2-fsa.github.io/sherpa/onnx/android/index.html  
- Android 构建：https://k2-fsa.github.io/sherpa/onnx/android/build-sherpa-onnx.html  
- SenseVoice 预训练模型：https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html  
- v1 仓库（UI 参考）：https://github.com/wenlepan-lgtm/iptv-edge-agent  

完成上述步骤后，即可在现有 App 上看到**实时文字识别**，并保持与 v1 一致的 UI 结构。
