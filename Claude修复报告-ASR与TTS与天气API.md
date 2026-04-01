# Claude 修复报告：ASR 准确率、TTS 死循环、上海天气请稍后重试

> 本报告供 Claude 按项修改代码，不在此处直接改代码。

---

## 问题一：TTS 播放时 ASR 仍在采集，导致死循环

### 现象
TTS 发声时 ASR 仍在采集，把 TTS 声音识别成用户指令 → 再次触发回复 → 再 TTS → 形成死循环。

### 根因
- MainActivity 已在 **onTTSStart()** 调用 `asrController?.setMuted(true)`，在 **onTTSDone()** 里延迟后调用 `setMuted(false)`，逻辑正确。
- **单声道**路径下：录音线程里 `if (asrMuted) { continue }` 在循环前部，会跳过整帧，不送 Vosk，行为正确。
- **多声道**路径下：在 `AsrController.kt` 的 `while (isRunning)` 循环里，多通道分支在「累积满 BUFFER_SIZE → 暖机判断 → processAudioData」这一段**没有判断 asrMuted**，所以 TTS 播放期间仍会把多通道融合后的音频送给 Vosk，触发识别，导致死循环。

### 代码位置（iptv-edge-agent）
- 文件：`iptv-edge-agent/app/src/main/java/com/joctv/agent/asr/AsrController.kt`
- 大致行号：约 286–328（多通道分支内，`if (accumulatedMonoData.size >= BUFFER_SIZE)` 整段）

### 修改建议
在多通道分支内，在「暖机检查」之前或与暖机并列，先判断 **asrMuted**：

- 若 `asrMuted == true`：**不要调用** `processAudioData`，并**清空** `accumulatedMonoData`（避免解静音后把 TTS 尾音当语音送进 Vosk）。
- 仅当 `!asrMuted` 且非暖机且 `accumulatedMonoData.size >= BUFFER_SIZE` 时，才调用 `processAudioData(...)`。

逻辑顺序建议：`if (asrMuted) { accumulatedMonoData.clear() } else if (isInWarmup) { ... } else if (accumulatedMonoData.size >= BUFFER_SIZE) { processAudioData(...) }`。

---

## 问题二：上海天气等回答“请稍后重试”，已配置阿里 API 和模型

### 现象
用户已填写阿里 API Key 和模型，但问「上海天气」仍得到「查询超时，请稍后重试」或「查询服务暂时不可用，请稍后重试」。

### 根因
- **配置 key 不一致**：  
  - 模板里写的是 **`web.api.url`**（例如阿里 dashscope 的 URL）。  
  - 代码里读取的是 **`web.api.base.url`**，若不存在则用默认值 `"https://api.example.com"`。  
- 用户按模板只填了 `web.api.url`、`web.api.key`、`web.api.model` 时，**baseUrl 实际用的是 api.example.com**，请求会失败（超时或不可用），从而返回“请稍后重试”。

### 代码位置
- 文件：`iptv-edge-agent/app/src/main/java/com/joctv/agent/web/WebAnswerClient.kt`  
  - init 里：`baseUrl = properties.getProperty("web.api.base.url", "https://api.example.com")`
- 模板：`iptv-edge-agent/app/src/main/assets/config.properties.template`  
  - 内容为：`web.api.url=https://dashscope.aliyuncs.com/...`（无 `web.api.base.url`）

3576v2 若使用同一套 Web 客户端和模板，需做相同兼容（见下）。

### 修改建议
1. **WebAnswerClient**（iptv-edge-agent 与 3576v2 若存在同样逻辑则一起改）：  
   - 读取 baseUrl 时兼容两种 key：  
     - 先读 `web.api.base.url`；  
     - 若为空或不存在，再读 `web.api.url`；  
     - 若仍为空再用默认 `"https://api.example.com"`（或改为阿里默认 URL，与产品约定一致）。
2. **config.properties.template**：  
   - 在注释或说明中写清：`web.api.base.url` 与 `web.api.url` 二选一即可（或保留 `web.api.url` 并说明代码已兼容该 key），避免用户只填 template 里的 key 导致请求发错地址。

---

## 问题三：ASR 准确率仍然很低

### 现象
识别错误率偏高，体验不佳。

### 已确认的代码与结论（不在此改代码，仅结论与建议）
- **多通道**：iptv-edge-agent 使用 3 通道 Channel Picker（选能量最大一路）融合成单声道送 Vosk；3576v2 主路径为单声道 AsrController，未用多通道。
- **TTS 期间送 ASR**：见问题一，多通道路径在静音时仍送 Vosk，会加重误触发和“听起来像识别错”的观感；修好问题一有助于减少误触发。
- **VAD/静音参数**：如 `MIN_VAD_RMS`、`SILENCE_DURATION_MS`、`COOLDOWN_MS` 等目前多为常量，不同设备/环境下一刀切容易导致截断或误检，影响准确率观感。

### 修改建议（供 Claude 选做）
1. **先落实问题一**：TTS 期间多通道不送 ASR，减少死循环和误触发。  
2. **可配置化**：将 ASR 相关常量（如 `MIN_VAD_RMS`、`SILENCE_DURATION_MS`、`COOLDOWN_MS`、暖机时间等）改为从配置文件或 SharedPreferences 读取，便于按设备/场景调参。  
3. **日志**：在多通道路径打各通道 RMS 与当前选中通道，便于排查通道顺序和 VAD 阈值是否合理。  
4. **3576v2**：若需提升远场/噪声场景准确率，可考虑让主 ASR 走多通道融合（或接入现有 EnhancedAmb37 + 融合逻辑），再送 Vosk。

---

## 汇总：请 Claude 实施的修改清单

| 序号 | 问题 | 修改内容 | 涉及文件（示例） |
|------|------|----------|------------------|
| 1 | TTS 时 ASR 死循环 | 多通道分支内：在送 Vosk 前判断 asrMuted；若 true 则清空 accumulatedMonoData 且不调用 processAudioData | `iptv-edge-agent/.../AsrController.kt` |
| 2 | 上海天气返回请稍后重试 | WebAnswerClient 的 baseUrl 兼容 web.api.url（若无 web.api.base.url 则用 web.api.url）；模板/注释说明两个 key 的兼容关系 | `iptv-edge-agent/.../WebAnswerClient.kt`，`.../config.properties.template`；3576v2 同逻辑则同步改 |
| 3 | ASR 准确率低 | 先完成 1；可选：VAD/静音/暖机等参数可配置、多通道日志、3576v2 多通道接入 | 见上文「问题三」 |

以上为报告全文，请 Claude 按报告修改代码，不在此处直接改。
