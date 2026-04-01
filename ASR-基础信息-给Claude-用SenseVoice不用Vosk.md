# ASR 基础信息（给 Claude：用 SenseVoice，不要用 Vosk）

**问题**：当前 iptv-edge-agent 里 MainActivity 还在用 **Vosk** 做 ASR，这是错的。  
**正确方案**：应使用 **Sherpa-Onnx + SenseVoice RKNN**（NPU 离线），模型是下面这个包。

---

## 一、正确的 ASR 模型（必须用这个）

- **模型压缩包**（项目根目录已有）：  
  `sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2`

- **解压后目录名**：  
  `sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17`

- **解压后目录内应有**：  
  - `model.rknn`  
  - `tokens.txt`  
  - `README.md`、`LICENSE` 等

- **部署到设备**：  
  将该目录拷贝到设备（如 `/sdcard/sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17`），代码里用这个路径作为 **SenseVoice 模型目录**（`modelDir`）。

---

## 二、当前错误（不要再这样做了）

- **MainActivity** 里仍在调用 `loadVoskModel()`，加载的是 **Vosk** 模型：  
  - 模型名：`vosk-model-small-cn-0.22`  
  - 使用 `org.vosk.Model`、`StorageService.unpack(..., "model", "model", ...)`  
  - 用 **Vosk 的 Recognizer** 做识别  

- **正确做法**：  
  不要用 Vosk；应使用 **sherpa-onnx OfflineRecognizer + SenseVoice RKNN**，即上面 `sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17` 这个模型。

---

## 三、项目里已有的正确组件（请基于这些改）

- **NPU 离线引擎**（已存在）：  
  - 类：`com.joctv.agent.asr.NpuOfflineAsrEngine`  
  - 封装 sherpa-onnx **OfflineRecognizer**，使用 **SenseVoice RKNN**（`model.rknn` + `tokens.txt`）  
  - 接口：输入 16k float PCM 段，输出识别文本；支持 partial / final 回调  

- **模型目录配置**：  
  - `NpuOfflineAsrEngine` 构造需要 `modelDir: String`  
  - 应指向解压后的 **SenseVoice 目录**，例如：  
    - `/sdcard/sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17`  
    - 或从 assets 解压到应用私有目录后的路径  

- **研发任务书**（方案与步骤）：  
  - 文件：`ASR-NPU-SenseVoice-研发任务书-给Claude.md`  
  - 里面写明了：用 VAD 分段 + **SenseVoice RKNN 离线模型**，不用 Vosk、不用 streaming Zipformer。

---

## 四、总结（直接复制给 Claude 即可）

1. **ASR 模型**：必须用 **sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2** 解压后的目录（内含 `model.rknn`、`tokens.txt`），不要用 Vosk 的 `vosk-model-small-cn-0.22`。  
2. **引擎**：用项目里已有的 **NpuOfflineAsrEngine**（SenseVoice RKNN），不要用 Vosk 的 `Model` / `Recognizer`。  
3. **MainActivity**：不要再 `loadVoskModel()`；应改为检测/加载 SenseVoice 模型目录，并创建、使用 **NpuOfflineAsrEngine**（或基于该引擎的 ASR 流程）。  
4. **详细步骤**：按 `ASR-NPU-SenseVoice-研发任务书-给Claude.md` 中的任务与步骤实施。
