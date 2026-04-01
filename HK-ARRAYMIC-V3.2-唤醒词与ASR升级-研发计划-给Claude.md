## 0. 背景与目标

- **设备**：RK3576 Android 14 机顶盒  
- **麦克**：HK-ARRAYMIC-V3.2（USB 2ch@48k）  
- **当前 ASR 引擎**：Vosk（`vosk-model-small-cn-0.22`，本地离线识别）  
- **当前状态**：  
  - 音频链路：2ch@48k → `StereoToMono(avg)` → 48k→16k → VAD（阈值+hangover）→ Vosk。  
  - 代码中已做：VAD 拖尾（连续 N 帧低于阈值才 VOICE_END）、静音时长可配、TTS 后丢弃窗口（post_unmute_discard）、GC 优化（固定数组 + 复用 ByteBuffer）、录音线程优先级提升、120ms 录音缓冲。  
  - 《静夜思》测试：后两句「举头望明月 / 低头思故乡」较稳定；前两句「床前明月光 / 疑是地上霜」长期偏成「歌房钱光 疑是地上霜」「朕叶子 床前明月光」「行情呢着…」等，句首噪声明显。  

**目标**：先通过「唤醒词 + 命令窗口 + 前滚缓存」把**交互时序和句首稳定性打穿**，再在此基础上线 sherpa-onnx ASR，并为后续 AEC/降噪/波束预留空间。  

---

## 1. 总体路线图（Phase 规划）

- **Phase-A：唤醒词 + 命令窗口 + 前滚缓存**  
  - 常驻轻量唤醒词 `"小聚小聚"`。  
  - 唤醒通过 → 播放固定提示音（beep WAV）→ 打开「命令识别窗口」（如 4 秒）。  
  - 命令窗口开启时，将前 200–400ms 的音频一并送入 ASR（前滚缓存），解决开头半个音节被吃掉的问题。  
  - 本阶段保持现有 Vosk 或并行试用 sherpa-onnx，但**不做 AEC/降噪/波束**。

- **Phase-B：更换 / 接入 sherpa-onnx ASR**  
  - 在保持 Phase-A 会话时序不变的前提下，把命令识别从 Vosk 切换或切到 sherpa-onnx 中文端侧模型。  
  - 利用已有的 `asr_S1 / asr_S2 / asr_S3`（wav+txt）做**离线回放 A/B**：  
    - Vosk vs sherpa，在相同 WAV 输入下的识别稳定性与句首鲁棒性。

- **Phase-C：AEC / 波束 / 降噪 / AGC**（后续）  
  - 仅当 Phase-A/B 在「干净环境近/中/远」下指标达标后，才允许启动。

---

## 2. Phase-A：唤醒词 + 命令窗口（Claude 执行）

### 2.1 会话状态机设计

在现有 ASR 状态机（LISTENING / FINAL / COOLDOWN）之上，再加一层**会话状态机**：

- `IDLE`  
  - 常驻轻量唤醒监听（只负责 listening `"小聚小聚"`）。  
  - 不产生业务指令，只可能进入 `WAKE_CONFIRMED`。

- `WAKE_CONFIRMED`  
  - 唤醒词检测通过。  
  - 立刻播放 **固定 beep 提示音**（短 200–300ms WAV，避免用动态 TTS 作为提示音）。  
  - 切换到 `COMMAND_LISTENING`。

- `COMMAND_LISTENING`（命令窗口）  
  - 窗口时长：配置项 `command.window.ms`（建议默认 4000ms，可配 3000–6000）。  
  - 仅在该窗口内启用「命令 ASR」并产生业务意图；窗口结束后回到 `IDLE`。  
  - 在窗口内沿用现有 ASR/VAD 状态机，只是多了时间/会话边界。

> 要求：会话状态机放在 Kotlin 层（例如新建 `ConversationStateManager`），对底层音频/ASR 引擎透明。

### 2.2 唤醒词引擎接口（WakeWordEngine）

定义统一接口，便于后续切换实现：

- 接口草案（示意）：  
  - `fun start()` / `fun stop()`  
  - `fun onPcm16kFrame(frame: ShortArray)` —— 持续送入 16k/mono 帧。  
  - 回调：`fun onWakeWordDetected()` —— 唤醒通过。

初始实现可以先用 **现有 ASR 做粗唤醒**（工程向）：

- 开一条「轻量识别流水线」只盯 `"小聚小聚"`：  
  - 当文本里**高置信度包含**这句时，触发 `onWakeWordDetected()`。  
- 后续再考虑接入 sherpa-onnx 自带 KWS 或其他更专业的 KWS 模型替换。

### 2.3 前滚缓存（Pre-roll Buffer）

**目的**：命令窗口一开，就把刚起音的前 200–400ms 一起送给命令 ASR，提升句首鲁棒性。

实现细节（给 Claude）：

- 在 16k/mono 阶段维护一个环形缓冲区：  
  - `RingBuffer<Short>`，容量约 0.5 秒（例如 8000 samples）。  
  - 所有实时音频在下采样到 16k 单声道后，**先写入 ring buffer**。  

- 会话逻辑：  
  - 在 `IDLE`/唤醒候选阶段：  
    - 持续往 ring buffer 写 16k mono，但不送入命令识别引擎。  
  - 一旦进入 `COMMAND_LISTENING`：  
    - 先从 ring buffer 中**回取 `pre_roll.ms` 对应的样本**（如 300ms → 4800 个样本），  
    - 把这段 + 之后的实时帧按顺序送入命令 ASR 引擎。  

- 配置：  
  - 新增 `command.pre_roll.ms`，建议默认 `300`（可选范围 200–400ms）。

### 2.4 提示音策略（Beep vs TTS）

- **推荐**：使用固定 beep 提示音（短 WAV 文件，SoundPool 或 `MediaPlayer` 播放），不要用运行时动态 TTS 合成。  
- 若产品上必须用 TTS 声音作为提示音：  
  - 请将该提示语（如“我在”）**预先离线合成为固定 WAV 文件**，运行时仅做简单播放，不要每次现合成，避免尾音不确定和额外负载。

### 2.5 Phase-A 验收标准

场景：安静环境，HK-ARRAYMIC-V3.2 2ch@48k，现有 2ch→avg→16k 链路不变。

- **唤醒词 `"小聚小聚"`**：  
  - 距离：0.5m / 1m / 3–4m，正前与轻微左/右各测 50 次；  
  - 成功率 ≥ **99%**；  
  - 长时间静置（1 小时）误唤醒 ~ 0（≤ 1 次）。

- **命令窗口（示例：酒店 10 条常用指令）**：  
  - 唤醒后播放 beep，再在窗口内说命令：  
    - 近/中/远各 10 次，命令意图识别正确率 ≥ **98–100%**；  
  - 句首不再出现「朕叶子/行情呢着/歌房钱」这种明显无关前缀（允许一个轻微语气词“嗯”类）。

---

## 3. Phase-B：接入并评估 sherpa-onnx ASR

### 3.1 sherpa-onnx ASR 接入（保持链路形状）

要求保持**输入形状不变**：

- 仍然使用：2ch@48k → `StereoToMono(avg)` → 48k→16k → VAD/状态机 → ASR。  
- 新增 `SherpaAsrEngine`，与现有 Vosk 引擎实现相同的接口：  
  - 输入：16k / mono / 16bit PCM（与当前 Vosk 引擎相同）。  
  - 输出：partial / final 文本，通过统一的 `AsrListener` 回调。  
- 支持在配置里切换引擎：  
  - `asr.engine = vosk | sherpa`。  
  - 初期允许同时保留 Vosk 作为 fallback。

### 3.2 利用 asr_S1 / asr_S2 / asr_S3 做离线回放 A/B

已有资产：`asr_S1.wav / asr_S2.wav / asr_S3.wav` 及对应 txt，离线评测表现“非常正常”。

实现一个「回放模式」，用于精确评估**模型 vs 实时链路**：

- 新增 Debug/测试入口（Activity 或隐藏命令）：
  - 读取 `/sdcard/asr_S1.wav`（及 S2/S3）；  
  - 对每个 WAV：  
    1. 按现有 2ch→avg→16k 逻辑处理（如果 WAV 已是 16k mono 则直接送）；  
    2. 分别喂给：  
       - Vosk 引擎（现有），  
       - sherpa-onnx 引擎（新加），  
    3. 记录：final 文本、耗时、RMS 分布（可简化）、关键日志。

- 对比维度：
  - `Vosk_offline` vs `Vosk_online（APK 实时）`：  
    - 若离线也长期把“床前”听成“歌房钱/朕叶子”，说明更多是模型能力问题。  
  - `sherpa_offline` vs `sherpa_online`：  
    - 用以判断 sherpa 端侧的句首鲁棒性和远场表现。  

裁决策略（写死在文档中，后续按此决策）：

- 若 `Vosk_offline` 也表现糟糕（静夜思句首长期错）：  
  - **结论：Vosk 中文模型能力不足，Phase-B 优先用 sherpa-onnx 替换为主引擎**。  
- 若 `Vosk_offline` 好但 `Vosk_online` 差：  
  - **结论：问题主要在实时链路（线程/overflow/pre-roll），继续从工程侧优化**。  
- 若 `sherpa_offline` + `sherpa_online` 显著优于 Vosk：  
  - 完成 Phase-B 后可考虑：  
    - 将 `asr.engine` 默认改为 `sherpa`，  
    - Vosk 仅作备份或下线。

### 3.3 Phase-B 验收标准

在 Phase-A 已通过的前提下：

- **固定命令集**（例如 20 条酒店常用指令）：  
  - 近/中/远各 10 次：业务意图识别正确率 ≥ **98–100%**。  

- **《静夜思》基线**：  
  - 关键字：床前、明月光、疑是、地上霜、举头、望明月、低头、思故乡 **不漏不错**；  
  - 允许非常短的语气词前缀，但**不接受长串无关前缀**（如“朕叶子/行情呢着/歌房钱”）。  

---

## 4. 为什么 asr_S1/S2/S3 很正常，而 APK 识别很低？

统一解释（给调研/讨论用）：

- **离线 asr_S1/S2/S3**：  
  - 整段 WAV 一次性送入，不经过 VAD/窗口切分，也没有实时线程调度和 overflow；  
  - 完全不会丢首帧，模型看到的是完整的「床前明月光…」。

- **APK 实时流式**：  
  - 必须处理：  
    - AudioRecord 线程调度、GC、`RecordThread: buffer overflow`；  
    - TTS / mute / unmute 的过渡阶段；  
    - VAD 阈值与 hangover 的时序；  
  - 任何在「开头 200–400ms」的抖动（早说、晚说、被 TTS 尾音/环境噪声牵扯）都会让模型乱猜前缀（“朕叶子”、“歌房钱”、“行情呢着”），  
    即使后半句逐步收敛到「明月光/地上霜」，**final 里仍残留错误前缀**。

因此：硬件本身（HK-ARRAYMIC-V3.2）并不是主矛盾，真正影响识别质量的是**“模型能力 + 实时链路时序 + 缺乏唤醒/命令窗口 + 无前滚缓存”**的组合。  
本研发计划通过 Phase-A/B 先把时序与交互模式打稳，再评估/切换 sherpa-onnx，以此为 AEC/降噪阶段做好地基。

