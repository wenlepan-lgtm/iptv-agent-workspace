# TTS NPU 可落地方案 + AEC/BF/NS/AGC 主板确认（给 Claude）

**平台**：RK3576，Android 14  
**工程**：iptv-edge-agent  
**目的**：① 探索芯片可落地的 TTS NPU 方案（自然、温柔）；② 根据麦克风资料或 adb 确认主板是否已做 AEC/BF/NS/AGC，没有则做到系统里或应用层。

---

## 〇、项目约束（必须遵守）

- **绝对禁止任何 CPU TTS**：设备为小盒子，CPU 要留给 **广电高清直播、投屏、电影** 播放，且需 **保持低负载以控制散热**。TTS 必须走 **NPU**。不允许任何以 CPU 为主的 TTS 引擎（如 sherpa-onnx、Android TextToSpeech）、不允许 fallback 或「可开关的 CPU TTS」。
- **目标**：直接落地 NPU TTS，不做「先 CPU 再迁 NPU」的过渡。

---

## 一、TTS NPU 可落地方案（RK3576）

### 1.1 首选路线：Piper(VITS) Streaming + RKNN（迁移 Paroli 思路到 RK3576）

**核心判断**：目前最工程化、可控、可商用的 NPU TTS 路线，是把 **Piper 的 VITS 做 Streaming 拆分**，把"最重的 decoder"图固定化后上 RKNN/NPU。这个路线在 **RK3588 上已有成熟工程实践**：

- **Paroli**：C++ 的 Piper Streaming 实现，带（可选）RK3588 NPU 加速。
- 其作者对"为什么 decoder 更适合上 NPU、如何拆分与流式化"写过完整工程记录。
- 该思路的关键点也被整理成 PDF（encoder 仍不易 NPU，decoder 占大头可 NPU）。

**RK3576 兼容性**：虽然你的芯片是 RK3576（不是 RK3588），但 Rockchip 的 RKNN 生态（toolkit/runtime/C API）对 RK3576 是覆盖的（Model Zoo 明确写支持 RK3576）。

**GPT 讨论结论（与上文一致）**：Paroli / Piper Streaming + 只跑 decoder 是当前最像「工业可用开源方案」的路线：encoder 继续 ONNXRuntime CPU（很轻），decoder 转 RKNN 跑 NPU（最重的部分上 NPU）。Paroli 在 RK3588 上启用 RKNN 后约 **4.3× speedup**，且**设计上就只能把 decoder 放 NPU**（encoder 是动态图，RKNN 不好跑，这也是「全上 NPU」的主要障碍）。Demo 日志给过 **Real-time factor ~0.16**，吞吐富余。结论：**CPU 低负载 + 低温 + 可落地**，这条路命中率最高。

### 1.2 可参考、可落地的方案一览

| 方案 | 说明 | 可落地性 |
|------|------|----------|
| **Paroli（首选参考）** | C++ 实现 Piper Streaming TTS，**仅 decoder 上 RK3588 NPU**，encoder 仍 ONNX/CPU（计算很轻）。实测 RK3588 上 NPU 比 CPU 约 **4.3× 加速**，RTF ~0.15。 | 高：RK3588 已验证；RK3576 用同一套 RKNN Runtime（librknnrt.so）与 rknn-toolkit2 转换即可。 |
| **Piper Streaming + 自建 RKNN decoder** | 用 [mush42/piper 的 streaming 分支](https://github.com/rhasspy/piper/pull/255) 导出 encoder/decoder ONNX，decoder 用 rknn-toolkit2 转 RKNN，在 Android 上 JNI + RKNN C API 跑 decoder。 | 高：与 Paroli 同思路，可直接参考 Paroli 的 `tools/decoder2rknn.py` 与 C++ RKNN 调用方式。 |
| **MeloTTS / 其他 VITS → ONNX → RKNN** | 模型导出 ONNX 后，用 rknn-toolkit2 转 RKNN；若存在不支持的算子需等价替换或简化。 | 中：依赖转换通过与算子兼容，无现成 RKNN 脚本需自研。 |

**Paroli 参考资源（建议直接看）**：
- 代码：[github.com/marty1885/paroli](https://github.com/marty1885/paroli)（CMake、`-DUSE_RKNN=ON`、传 `decoder.rknn` 即用 NPU）
- 工程记录（为什么 decoder 上 NPU、动态 shape 用 pad 解决、RTF 与音质）：[Accelerating Piper TTS on the RK3588 NPU](https://clehaxze.tw/gemlog/2023/12-24-accelerating-piper-text-to-speech-on-the-rk3588-npu.gmi)
- 模型：Paroli 使用 [mush42 的 streaming 分支](https://github.com/mush42/piper/tree/streaming) 导出 ONNX；[HuggingFace 上有 streaming-piper 示例](https://huggingface.co/marty1885/streaming-piper/tree/main)。

**落地要点（Paroli 思路）**：
- 仅 **decoder** 上 NPU；encoder 为动态图，保持 ONNX/CPU，负载很小。
- RKNN 需要静态输入 shape：decoder 输入按 `WINDOW_SIZE + 2*OVERLAP_SIZE` 做 **pad 到最大**，输出再截断（Paroli 已实现）。
- 在 x86 上用 **rknn-toolkit2**（如 1.6.0）跑 `tools/decoder2rknn.py` 把 `decoder.onnx` 转为 `decoder.rknn`，设备端用 **librknnrt.so** + C API 推理。

**RK3576 上落地要点（可直接给 Claude/Cursor）**  
1. **选模型**：先选 Streaming Piper 的中文/多语种 voice（HuggingFace 有现成模型来源）。  
2. **PC 侧转 RKNN**：只转 `decoder.onnx` → `decoder.rknn`（不要碰 encoder）。命令示例：`python tools/decoder2rknn.py decoder.onnx decoder.rknn`，需 rknn-toolkit2 **1.6.0**。  
3. **Android 集成**：把 **librknnrt.so**（RK3576）+ JNI 封装 decoder；encoder 用 **ORT AAR/so**（ONNX Runtime）。  
4. **音频链路**：文本 → phonemize → encoder(CPU) → decoder(NPU) → PCM → AudioTrack。  
5. **与现有体系对接**：TTS 开始触发 ducking（v4.2 已完成）；TTS 播放时继续保持 ASR 静默窗口。

**你关心的「CPU 怎么管住」**  
- **encoder + phonemize** 放到 **低优先级线程**（如 `THREAD_PRIORITY_BACKGROUND`），避免抢播放/投屏的 CPU。  
- **限制并发**：一次只合成一段，流式输出别攒大 buffer。  
- **观测与验收**：统计 RTF、CPU%、温度、是否降频——可用 **Perfetto** + `top -H` + `/sys/class/thermal/*`（盒子有 root/权限更便于排查）。

### 1.3 调研结论摘要

| 项目 | 结论 |
|------|------|
| **sherpa-onnx TTS** | 官方当前 **无 TTS RKNN 后端**；TTS 为 ONNX/CPU，支持 VITS 等模型，嵌入式示例为 RV1106/RV1126 等 **CPU 版**。 |
| **RK3576 NPU** | 6TOPS，支持 ONNX → RKNN（rknn-toolkit2）；**语音合成**在 Rockchip 文档中被列为应用场景，但**无现成 RKNN TTS 模型或官方 Demo**。 |
| **Paroli 路线** | **Piper(VITS) Streaming + RKNN decoder** 在 RK3588 已验证可行；RK3576 可复用该思路（decoder 上 NPU，encoder 轻量 CPU）。 |
| **端侧 TTS 参考** | Bert-VITS2-MNN 为 Android 端侧 TTS，使用 **MNN** 引擎，非 RKNN；可作「自然、温柔」音色与流程参考，不能直接复用 RKNN。 |
| **可行路径** | **首选**：Piper Streaming + RKNN decoder（参考 Paroli）；**备选**：MeloTTS/VITS → ONNX → rknn-toolkit2 → RKNN Runtime C API；若存在不支持的算子，需在转换阶段做等价替换或简化模型。 |

### 1.4 交付形态（Piper Streaming + RKNN）

- **Android App 内置 TTS Engine（JNI）**：
  - 文本 → 规范化/分句 → phoneme/ID → encoder（轻，CPU） → decoder（重，RKNN）→ PCM → AudioTrack
  - decoder 使用 **RKNN Runtime C API**（librknnrt.so）直接推理；JNI 做胶水与缓冲。
  - 支持多语种：优先用 **piper-voices**（声库/语言多）。

### 1.4.1 语言与声色、声调能力（Piper vs MeloTTS）

| 能力 | Piper（Paroli 路线） | MeloTTS |
|------|----------------------|---------|
| **支持语言** | **40+ 种**（piper-voices：ar/bg/ca/cs/cy/da/de/el/en/es/fa/fi/fr/hi/hu/id/is/it/ka/kk/lb/lv/ml/ne/nl/no/pl/pt/ro/ru/sk/sl/sr/sv/sw/te/tr/uk/vi/**zh** 等），含**中文**。 | **约 10 种/变体**：英语（美/英/印/澳）、西、法、**中**（支持中英混合）、日、韩等。 |
| **声色（音色）** | **多音色**：每种语言有多个声库（不同 .onnx 即不同音色），如 en 有 Amy、Ryan、LibriTTS 等；质量档位 x_low / low / medium / high。 | **每语言变体一个优化音色**，通过 speaker 选 EN-US、EN-BR、ZH 等，不是多角色多音色。 |
| **声调/效果** | **语速与停顿**：`length_scale`、`phoneme_duration_scale` 调语速；`sentence_silence` 调句间停顿；`noise_scale`/`noise_w` 微调合成风格。**不提供**整句音高曲线或“声调”级控制。 | **语速 + 韵律**：`speed` 可调语速；有 **tone 参数**（中文对应四声/韵律，英文对应重音模式），声调/韵律控制比 Piper 更显式。 |
| **适合场景** | 多语种 + 多音色选型、离线声库丰富、需「温柔/自然」时靠**选不同 voice 模型**实现。 | 语种略少但质量均衡，需要**显式调声调/韵律**时更合适；中文中英混合友好。 |

结论：**声色**上 Piper 靠「换声库」、MeloTTS 靠「换语言/变体」；**声调/韵律**上 MeloTTS 有 tone 参数更直接，Piper 主要调语速和句间停顿，两者都做不到「任意画音高曲线」级别的细粒度声调。

### 1.5 推荐模型与「自然、温柔」取向

- **Piper(VITS)**：**首选**，已有 Paroli 在 RK3588 的工程实践，Streaming 拆分思路成熟；piper-voices 提供多语种、多音色选择。
- **VITS 系**：VITS、GPT-SoVITS、Bert-VITS2 等，支持 ONNX 导出（如 GPT-SoVITS 有 t2s_encoder / fsdec / vits 等 ONNX）；音色可训练成偏温柔、自然。
- **MeloTTS**：多语种、质量均衡，社区有 ONNX 导出实践；与当前任务书中的「温柔自然 + 多语言」一致，适合作为 **备选模型**。
- 选型时优先：**已有 ONNX 导出文档/脚本** 的模型，便于后续做 RKNN 量化与算子兼容排查。

### 1.6 落地实施步骤（Piper Streaming + RKNN 路线）

**Task A：Piper 模型选型与"可 RKNN 化"评估**
- 选 2–3 个目标 voice（中文/英文优先；你要多语种后续扩）。
- 确认模型输入输出张量形状是否能"静态化/固定 chunk"。（Paroli 路线就是把 decoder 做成更静态的图。）

**Task B：ONNX → RKNN 转换流水线**
- 使用 **RKNN-Toolkit2** 进行 onnx 转 rknn，并做：
  - 量化策略（INT8/FP16，先以稳定为主）
  - 固定输入 shape（避免动态 shape 直接把你搞死）
  - 基准集对比：ONNX 输出与 RKNN 输出误差阈值
  - 参考 Rockchip 官方工具链与模型示例仓库（Model Zoo）。

**Task C：Android JNI 推理封装**
- `RknnTtsDecoder`（C++）：初始化/推理/释放、线程安全、内存复用（避免频繁 malloc/free）；encoder 用 ORT AAR/so。
- Kotlin 层：TTS 队列 + 流式播放 + 可打断（barge-in 时 TTS 音量降低/暂停策略）。
- **CPU 管住**：encoder + phonemize 跑在低优先级线程（如 `THREAD_PRIORITY_BACKGROUND`），一次只合成一段、流式输出不攒大 buffer（见上文「你关心的 CPU 怎么管住」）。

**Task D：性能与温控验收（交付 KPI）**
- **KPI（建议你写死）**：
  - 首包延迟：< 300ms（欢迎语这种短句最敏感）
  - 实时系数 RTF：< 0.5（越小越稳）
  - 长时间（30min 循环播报）温度不触发降频/卡顿
  - 峰值内存：< 600MB（按你盒子 8G 很宽，但别放纵）

### 1.7 与任务书的关系

- 任务书 **TTS 章节** 应改为：**目标为 NPU TTS（自然、温柔），TTS 不占用 CPU**；首选路线为 **Piper(VITS) Streaming + RKNN decoder**（参考 Paroli 在 RK3588 的实践）；**不采用 CPU TTS**，直接落地 NPU 方案。  
- 本文档 §〇（项目约束）与 1.1～1.6 即「TTS NPU 探索结论与落地方案」，可直接摘入任务书「TTS 选型」一节。

---

## 二、AEC / BF / NS / AGC：主板是否已做的确认方法

### 2.1 当前工程现状

- **iptv-edge-agent** 中 `AsrController` 已调用 Android 标准 API：  
  `AcousticEchoCanceler`、`NoiseSuppressor`、`AutomaticGainControl`。  
- 逻辑为：`AcousticEchoCanceler.isAvailable()` / `NoiseSuppressor.isAvailable()` / `AutomaticGainControl.isAvailable()` 为 true 时，对当前 `AudioRecord` 的 `audioSessionId` 创建并 `setEnabled(true)`。  
- **结论**：应用层已「尽量使用系统提供的 AEC/NS/AGC」；是否真正生效，取决于 **主板/ROM 是否实现并暴露** 这些 Effect。

### 2.2 如何确认「主板/系统是否已做 AEC / NS / AGC」

**方法一：运行 APK 看 logcat（最直接）**

设备连接 adb 后，运行带 `AsrController` 的 APK（例如进入语音识别或开始录音），过滤日志：

```bash
adb logcat -s AsrController:D | grep -E "AEC:|NS:|AGC:"
```

- 若看到 `AEC: isAvailable=true` 且 `AEC: enabled=true`，说明 **系统声明支持 AEC** 且应用已启用；同理 NS、AGC。  
- 若为 `isAvailable=false` 或 `create returned null`，说明 **系统未提供** 或该设备/会话下不可用，需在 **系统层** 或 **应用层**（如 WebRTC APM）补齐。

**方法二：adb 查看音频策略与设备能力（辅助）**

```bash
# 音频策略与设备列表
adb shell dumpsys audio

# 与音频相关的系统属性（部分 ROM 会暴露 AEC/NS 支持）
adb shell getprop | grep -i audio
```

可关注：默认录音设备、USB 声卡是否被识别、是否有「echo_cancel」等字样。BF（波束形成）通常 **不在** Android 标准 API 中，多由 **厂商 HAL 或麦克风阵列固件** 实现，需查主板/麦克风芯片资料或厂商 SDK。

**方法三：麦克风芯片资料（HK-ARRAYMIC-V3.2）**

- 若资料中写明 **芯片或模组内建 AEC/NS/AGC/BF**，且输出为「已处理后的 PCM」，则主板侧可能仅做透传，**无需系统再做一次**；应用层只需确保使用该路音频并可选关闭 Android Effect 避免重复处理。  
- 若资料写明 **仅裸麦、无前处理**，则 AEC/NS/AGC（及若需 BF）需在 **系统层**（HAL/厂商库）或 **应用层**（如 WebRTC APM）实现。

### 2.3 麦克风回采接口接线方式（已按厂家指导完成）

**接线方式**（已按厂家指导接好）：
- **回采接口的两个正极**：分别并联到喇叭的 **LP**（左正）跟 **RP**（右正）
- **回采接口的两个负极**：分别并联到喇叭的 **LN**（左负）跟 **RN**（右负）

**目的**：这样可以屏蔽喇叭的声音被麦克风录入（硬件级 AEC 参考信号接入）。

**后续测试**：与 ChatGPT 一起测试 AEC/BF/NS/AGC，结果后续同步。

### 2.4 BF（波束形成）说明

- Android 标准 **没有** 暴露「波束形成」API；多麦阵列的 BF 一般在：  
  - **麦克风模组/芯片固件** 内完成，或  
  - **主板厂商 HAL / 专用 SDK** 中提供。  
- HK-ARRAYMIC-V3.2 为 **2ch@48k**；若芯片资料说明「内置 BF」，则 2ch 可能是 BF 后的单路或双路输出，需以资料为准。  
- 若主板/芯片 **未做 BF**，且产品需要 BF，则需在 **系统层**（厂商驱动/HAL）或 **应用层**（自研/第三方多麦 BF 算法）实现，不在「Android 标准 Effect」范围内。

### 2.5 ADB 核验步骤（按顺序执行，别跳）

详见《RK3576-NPU-TTS-落地-音频前端核验-研发计划-给Cursor.md》§2.2，或执行 `adb-音频前端核验命令清单.sh` 脚本一键核验。

**关键命令**：
```bash
# 音频设备与通道形态
adb shell cat /proc/asound/cards
adb shell cat /proc/asound/pcm
adb shell dumpsys media.audio_policy
adb shell dumpsys audio

# 音频效果（AEC/NS/AGC）检查
adb shell dumpsys media.audio_flinger
adb shell dumpsys media.audio_flinger --list-effects 2>/dev/null
adb shell dumpsys media.audio_flinger --effects 2>/dev/null

# 应用层日志（查看 AsrController 的 AEC/NS/AGC 启用状态）
adb logcat -s AsrController:D | grep -E "AEC:|NS:|AGC:"
```

### 2.6 若主板未做 AEC/BF/NS/AGC：应「做到系统里」的含义与做法

- **系统层实现**（推荐，当可改 ROM/厂商 SDK 时）：  
  - 在 **Audio HAL / 厂商音频库** 中集成 AEC（可选 NS/AGC）；若为多麦且需 BF，在 HAL 或驱动层接入 BF 算法，再对外暴露为「已处理」的录音流。  
  - 这样应用层仅需正常使用 `AudioRecord`，无需关心 AEC/NS/AGC/BF 细节；若系统同时实现了 Android Effect 接口，则 `AcousticEchoCanceler.isAvailable()` 会为 true。

- **应用层实现**（无法改系统时）：  
  - 在 **iptv-edge-agent** 采集链路中集成 **WebRTC APM**（AEC3 + NS + AGC），将 **TTS 播放 PCM** 作为 AEC 参考信号；NS/AGC 对麦克风数据处理后送 VAD/ASR。  
  - 任务书 Milestone C2 已规划此方案；与「做到系统里」不冲突：能系统做就系统做，不能则应用层 WebRTC APM 兜底。

- **任务书中的写法建议**：  
  - 写明：「**先通过 adb/logcat 与麦克风芯片资料确认主板是否已支持 AEC/BF/NS/AGC；若已支持，应用层通过 Android Effect 或厂商 API 启用；若未支持，则在系统层（HAL/厂商库）或应用层（WebRTC APM）实现。**」

---

## 三、建议写入研发任务书的修改点

1. **TTS 章节（当前 §5）**  
   - 将「首版 CPU、TTS 上 NPU 为远期」改为：**交付目标为 NPU TTS（自然、温柔）**；并增加「**TTS NPU 探索结论与落地方案**」：无现成 RKNN TTS，需自建 ONNX→RKNN 链路（模型选型 VITS/MeloTTS → rknn-toolkit2 → RKNN Runtime）；首版若 NPU 未就绪可暂保留 CPU TTS 过渡。

2. **AEC/BF/NS/AGC（§3 与 Milestone C2）**  
   - 增加「**确认步骤**」：依据麦克风芯片资料与 adb/logcat 确认主板是否已做 AEC/BF/NS/AGC；若已做，应用层启用系统/厂商能力；若未做，则在系统层或应用层（WebRTC APM）实现，并注明「未提供则需在系统层实现」及应用层可配合点（如提供 TTS 参考信号、会话 ID 等）。

以上可直接合并进 `RK3576-Android-TV-语音助手-v1-研发任务-给Claude.md` 的对应小节。
