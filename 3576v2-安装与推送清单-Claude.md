# 3576v2 安装与推送机顶盒清单（给 Claude）

> 对比 **iptv-edge-agent**（GitHub：https://github.com/wenlepan-lgtm/iptv-edge-agent）与 **3576v2**（本地：`/Users/ala/工作项目/3576v2/`），列出 ASR、TTS、2D 数字人相关安装与需 push 的文件，供 Claude 推送到机顶盒。

---

## 一、两工程对比摘要

| 项目 | iptv-edge-agent（GitHub 版） | 3576v2（AndroidTVSpeechRecognition） |
|------|------------------------------|--------------------------------------|
| **位置** | `/Users/ala/工作项目/agent/iptv-edge-agent/` | `/Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/` |
| **ASR** | Vosk，模型路径 `/sdcard/vosk-model-small-cn-0.22` 或 assets 解压 | Vosk + SenseVoice/Streaming/KWS 等，模型在 `/sdcard/joctv_models/` 下多目录 |
| **TTS** | TTSManager（系统 sherpa-onnx TTS 引擎） | TTSManager + SherpaOnnxTtsEngine，模型 `/sdcard/joctv_models/tts/` |
| **2D 数字人（DigitalHuman）** | 仅布局有 `tvDigitalHumanAvatar`，无 DigitalHumanManager / DUIX | **DigitalHumanManager + DUIX SDK**，模型 gj_dh_res、Lily 在应用专属目录或 assets 解压 |
| **APK 构建** | `./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk` | 同上；发布需签名（3576v2 根目录有签名文件） |

---

## 二、3576v2 需推送到机顶盒的文件一览

以下路径均为 **机顶盒内路径**；左侧为 **本机 3576v2 源目录**，右侧为 **adb push 目标**。

### 1. APK（安装包）

| 本机路径 | 机顶盒操作 | 说明 |
|----------|------------|------|
| 构建产物：`/Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/app/build/outputs/apk/debug/app-debug.apk` | 不 push，用 `adb install -r` 安装 | Debug 包 |
| 或签名后：`/Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/app-release-signed.apk` | 同上，`adb install -r app-release-signed.apk` | 发布用需先签名 |

### 2. ASR 相关（模型与资源）

| 本机路径（3576v2） | 机顶盒路径 | 说明 |
|--------------------|------------|------|
| `/Users/ala/工作项目/3576v2/vosk-model-small-cn-0.22/` | `/sdcard/vosk-model-small-cn-0.22/` 或 `/sdcard/joctv_models/vosk-model-small-cn-0.22/` | Vosk 中文 ASR 模型（主工程 MainActivity 用 `/sdcard/vosk-model-small-cn-0.22`） |
| `/Users/ala/工作项目/3576v2/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/`（含 model.int8.onnx、tokens.txt 等） | `/sdcard/joctv_models/sensevoice/` | SenseVoice 离线 ASR（若使用 SenseVoiceAsrEngine） |
| `/Users/ala/工作项目/3576v2/streaming-zipformer/`（含 encoder、decoder、joiner、tokens.txt） | `/sdcard/joctv_models/streaming-zipformer/` | 流式 Zipformer ASR（若使用 StreamingAsrEngine） |
| `/Users/ala/工作项目/3576v2/sherpa-onnx-streaming-paraformer-bilingual-zh-en/` | `/sdcard/joctv_models/streaming-asr/` | 流式 Paraformer 中英（若使用 SherpaStreamingAsrEngine） |
| `/Users/ala/工作项目/3576v2/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | `/sdcard/joctv_models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | 关键词唤醒（若使用 SherpaKwsEngine） |

### 3. TTS 相关

| 本机路径（3576v2） | 机顶盒路径 | 说明 |
|--------------------|------------|------|
| `/Users/ala/工作项目/3576v2/models/vits-zh-hf-fanchen-C/`（含 vits-zh-hf-fanchen-C.onnx、lexicon.txt、tokens.txt、*.fst、dict 等） | `/sdcard/joctv_models/tts/` | SherpaOnnxTtsEngine 使用的 VITS 模型（需包含 onnx 及同目录下全部依赖文件） |

### 4. 数字人（DigitalHuman：DigitalHumanManager + DUIX）

| 说明 | 机顶盒路径 | 备注 |
|------|------------|------|
| DigitalHumanManager | 源码在 `joctvagent/.../digitalhuman/`，APK 内已包含 | 负责状态 IDLE/SPEAKING、口型同步等，与 DUIX 协同 |
| DUIX SDK | 已编入 APK（`:duix-sdk`） | 无需单独 push |
| 数字人基础配置 **gj_dh_res** | 应用专属目录：`/sdcard/Android/data/com.joctv.agent/files/duix/model/gj_dh_res` | APK 的 **assets 里已有**，首次启动会从 assets 解压到该路径，一般**无需 push** |
| 数字人形象 **Lily** | 同上：`.../duix/model/Lily` | 同上，assets 有 `Lily/`，首次运行自动复制 |
| 可选预置（避免首次解压） | 先安装 APK 再 push，或先 `mkdir -p` 再 push | 见下方「数字人可选 push」 |

### 5. 其他

| 本机路径 | 机顶盒路径 | 说明 |
|----------|------------|------|
| 视频文件（自备） | `/sdcard/1.mp4` 或 `/sdcard/1.ts` | 应用内写死或尝试 1.mp4 / 1.ts |
| 配置 | 应用 assets 或首次运行生成 | config.properties（web.api.key 等）在应用内配置，一般不 push |

---

## 三、给 Claude：推送到机顶盒的命令示例

以下命令在 **本机** 执行，假设机顶盒已通过 USB 连接且 `adb devices` 可见。

### 1. 创建机顶盒目录（如不存在）

```bash
adb shell "mkdir -p /sdcard/vosk-model-small-cn-0.22"
adb shell "mkdir -p /sdcard/joctv_models/sensevoice"
adb shell "mkdir -p /sdcard/joctv_models/tts"
adb shell "mkdir -p /sdcard/joctv_models/streaming-zipformer"
adb shell "mkdir -p /sdcard/joctv_models/streaming-asr"
adb shell "mkdir -p /sdcard/joctv_models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
```

### 2. Push ASR 模型

```bash
# Vosk（主工程 MainActivity 用）
adb push /Users/ala/工作项目/3576v2/vosk-model-small-cn-0.22/ /sdcard/vosk-model-small-cn-0.22/

# SenseVoice（若用）
adb push /Users/ala/工作项目/3576v2/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx /sdcard/joctv_models/sensevoice/
adb push /Users/ala/工作项目/3576v2/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt /sdcard/joctv_models/sensevoice/
# 若该模型目录还有其它依赖文件，需一并 push

# streaming-zipformer（若用）
adb push /Users/ala/工作项目/3576v2/streaming-zipformer/ /sdcard/joctv_models/streaming-zipformer/

# streaming-asr / KWS 按需 push 对应目录
```

### 3. Push TTS 模型

```bash
adb push /Users/ala/工作项目/3576v2/models/vits-zh-hf-fanchen-C/ /sdcard/joctv_models/tts/
```

（若 SherpaOnnxTtsEngine 期望的路径是 `tts/vits-zh-hf-fanchen-C.onnx`，则需保证 `vits-zh-hf-fanchen-C.onnx` 及同目录 lexicon、tokens、fst、dict 等在 `/sdcard/joctv_models/tts/` 下与代码一致。）

### 4. 数字人（DigitalHuman）可选 push

应用包名为 `com.joctv.agent`，数字人目录为 `getExternalFilesDir("duix")` = `/sdcard/Android/data/com.joctv.agent/files/duix`。  
**通常不需要**：APK 的 `assets/gj_dh_res`、`assets/Lily` 会在首次启动时复制到该目录。若需**预置**（例如机顶盒无网络、希望减少首次启动时间），可先安装 APK 后执行：

```bash
adb shell "mkdir -p /sdcard/Android/data/com.joctv.agent/files/duix/model/tmp"
adb push /Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/app/src/main/assets/gj_dh_res/ /sdcard/Android/data/com.joctv.agent/files/duix/model/gj_dh_res/
adb push /Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/app/src/main/assets/Lily/ /sdcard/Android/data/com.joctv.agent/files/duix/model/Lily/
adb shell "touch /sdcard/Android/data/com.joctv.agent/files/duix/model/tmp/gj_dh_res"
adb shell "touch /sdcard/Android/data/com.joctv.agent/files/duix/model/tmp/Lily"
```

### 5. 安装 APK

```bash
# Debug（未签名）
adb install -r /Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/app/build/outputs/apk/debug/app-debug.apk

# 或发布签名后（在 AndroidTVSpeechRecognition 目录执行签名脚本后再安装）
# adb install -r /Users/ala/工作项目/3576v2/AndroidTVSpeechRecognition/app-release-signed.apk
```

### 6. 启动应用与日志

```bash
adb shell am start -n com.joctv.agent/.MainActivity
adb logcat -s ASR:V TTS:V DigitalHuman:V
```

---

## 四、安装文件汇总表（便于 Claude 逐项执行）

| 序号 | 类型 | 本机源路径 | 机顶盒目标 | 操作 |
|------|------|------------|------------|------|
| 1 | APK | `3576v2/AndroidTVSpeechRecognition/app/build/outputs/apk/debug/app-debug.apk` | — | `adb install -r <apk>` |
| 2 | ASR | `3576v2/vosk-model-small-cn-0.22/` | `/sdcard/vosk-model-small-cn-0.22/` | `adb push 源 目标` |
| 3 | ASR | `3576v2/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/` 内 model.int8.onnx、tokens.txt 等 | `/sdcard/joctv_models/sensevoice/` | `adb push` |
| 4 | TTS | `3576v2/models/vits-zh-hf-fanchen-C/` | `/sdcard/joctv_models/tts/` | `adb push` |
| 5 | ASR 流式 | `3576v2/streaming-zipformer/` | `/sdcard/joctv_models/streaming-zipformer/` | 按需 push |
| 6 | ASR 流式 | `3576v2/sherpa-onnx-streaming-paraformer-bilingual-zh-en/` | `/sdcard/joctv_models/streaming-asr/` | 按需 push |
| 7 | ASR KWS | `3576v2/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | `/sdcard/joctv_models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | 按需 push |
| 8 | 数字人（DigitalHuman） | APK 内 assets 已有 `gj_dh_res`、`Lily`，首次启动自动解压到 `.../duix/model/` | `/sdcard/Android/data/com.joctv.agent/files/duix/model/gj_dh_res`、`.../Lily` | 一般无需 push；可选预置见「数字人可选 push」 |
| 9 | 视频 | 自备 | `/sdcard/1.mp4` 或 `/sdcard/1.ts` | 自选 push 或拷贝 |

---

**说明**：若实际运行的是 **iptv-edge-agent**（agent 仓库）构建的 APK，则机顶盒上只需 **Vosk 模型** `/sdcard/vosk-model-small-cn-0.22/` 和视频 `/sdcard/1.mp4`，TTS 为系统引擎，2D 数字人当前未集成。本清单以 **3576v2（AndroidTVSpeechRecognition）** 为准，便于 Claude 按 3576v2 推送机顶盒。 
