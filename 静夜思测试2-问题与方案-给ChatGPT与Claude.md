# 静夜思测试2 — 问题分析与解决方案

> 基于日志 `静夜思_测试2.txt` 与代码 `AsrController.kt`。供与 **ChatGPT** 讨论，并交给 **Claude** 实现。

---

## 一、当前效果小结

- **关键字**：床前、明月光、疑是、地上霜、举头、望明月、低头、思故乡 — **全部正确** ✅  
- **仍有两类问题**：  
  1. **5 次 buffer overflow** — 需进一步增大 buffer 或改为多线程。  
  2. **句首噪音「叶子」** — 前半首测试出现「叶子 床前明月光…」，疑似 unmute 后过渡期误识别。

---

## 二、问题 1：Buffer overflow（5 次）

### 2.1 日志中的 5 次 overflow 时间点

| 序号 | 时间戳 | 上下文 |
|------|--------|--------|
| 1 | 14:39:19.412 | 第一次说话段末尾，紧接着 VOICE_END |
| 2 | 14:39:24.831 | 第二次说话（建议/建业/这意思）段中 |
| 3 | 14:39:30.398 | **TTS 结束、ASR unmute 后约 0.1s** |
| 4 | 14:39:39.018 | 下一段说话开始后不久（随后出现 partial「叶子」） |
| 5 | 14:39:44.287 | 句中「地上 霜 举头」段中 |

### 2.2 与代码的对应关系

- **当前配置**：`BUFFER_2CH_48K_MS = 80` → 2ch@48k 的 `AudioRecord` buffer 约 **80ms**（15360 bytes）。  
- **原因**：录音线程在同一线程内顺序执行：`read()` → downmix → resample → 每 100ms 调用 `acceptWaveForm()`。若某次 Vosk 或 GC 稍慢，未及时 `read()`，系统 RecordThread 即报 buffer overflow。  
- **规律**：overflow 易出现在 **VOICE_START 后、句首** 或 **unmute 后**（系统/GC 有短暂延迟）。

### 2.3 方案选项（可多选）

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **A. 继续增大 buffer** | 将 `BUFFER_2CH_48K_MS` 从 80 提到 **120ms 或 160ms** | 实现简单，不改架构 | 仍可能在高负载/GC 时偶发 overflow |
| **B. 录音与识别解耦** | 录音线程只负责 read → downmix → resample → 放入队列；**单独线程**从队列取 16k mono 送 Vosk | 读数据与 Vosk 互不阻塞，overflow 概率最低 | 需改架构、队列与线程 |

**建议**：先做 **A（120ms 或 160ms）**，若日志仍频繁 overflow 再考虑 **B**。

---

## 三、问题 2：句首噪音「叶子」

### 3.1 日志时间线（前半首测试）

- **14:39:38.597** — `VOICE_START`（用户开始说话或环境被判为有声音）  
- **14:39:38.743** — `ASR unmuted after TTS`（TTS 结束后 500ms 解除静音）  
- **14:39:39.018** — **buffer overflow**（句首段）  
- **14:39:39.688** — **ASR_PARTIAL_EMIT text=叶子**（首个 partial）  
- 之后 partial 演进为「叶子 床前」「叶子 床前明月光」…

### 3.2 根因分析

- **Warmup 只对「录音启动」有效**：当前 warmup 使用 `elapsedTime = currentTime - stereoInputStartTime`，即从**录音线程启动**开始计时。Unmute 时已过去很久，**不存在「unmute 后的 warmup」**。  
- Unmute 后**立刻**把麦克风数据送 Vosk，此时可能包含：  
  - TTS 尾音/回声残留、  
  - 设备/环境噪声、  
  - 或 overflow 导致的畸变/混叠。  
- 模型将这段**过渡期音频**识别成「叶子」，并与此后真实「床前明月光」拼接，形成句首噪音。

### 3.3 方案：Unmute 后丢弃窗口（post-unmute discard）

- **思路**：在 **unmute 后的前 X ms 内**（如 400–600ms），**不把音频送 Vosk**（或送但不参与 partial/final 的拼接），避免过渡期误识别。  
- **实现要点**：  
  - 在 `setMuted(false)` 时记录 `lastUnmuteTime = System.currentTimeMillis()`。  
  - 在 2ch/3ch 分支中，在调用 `processAudioData` 之前判断：若 `currentTime - lastUnmuteTime < postUnmuteDiscardMs`，则**清空累积数据并跳过本次 processAudioData**（不送 Vosk）。  
  - 新增配置项：`asr.post_unmute_discard.ms`，默认 **500**，模板中注明「用于减少 TTS 后句首误识别（如「叶子」）」。

---

## 四、给 ChatGPT 的讨论点

1. **Buffer 大小**：80ms → 120ms 与 160ms 在机顶盒上的内存与延迟取舍？是否优先 120ms，仍 overflow 再试 160ms？  
2. **多线程方案**：若采用「录音线程 + 识别线程」，队列长度建议（如 200–300ms 的 16k 数据）？是否需要背压（队列满时丢帧策略）？  
3. **Post-unmute 丢弃**：500ms 是否合适？是否需可配置两档（短指令 300ms / 长句 600ms）？  
4. **「叶子」的其他可能**：除 unmute 过渡期外，是否考虑句首**低置信度过滤**（若 Vosk 支持）或**首词白名单**（如仅允许与当前场景相关的词）？  

---

## 五、给 Claude 的修改清单

| 序号 | 项 | 文件/位置 | 说明 |
|------|----|------------|------|
| 1 | 增大 2ch@48k buffer | `AsrController.kt` | 将 `BUFFER_2CH_48K_MS` 从 80 改为 **120**（若仍 overflow 可改为 160） |
| 2 | 新增 post-unmute 丢弃 | `AsrController.kt` | 增加 `lastUnmuteTime`，在 `setMuted(false)` 时赋值；2ch/3ch 分支中若 `currentTime - lastUnmuteTime < postUnmuteDiscardMs` 则清空累积数据并跳过 `processAudioData` |
| 3 | 配置项 | `AsrConfig` + `config.properties.template` | 新增 `asr.post_unmute_discard.ms`，默认 **500**，模板注释说明用途 |
| 4 | （可选）多线程 | 仅当 A 仍不足时 | 录音线程只写队列，单独线程读队列送 Vosk |

---

## 六、验收标准

- **Buffer overflow**：同场景下 5 次 → 0 次（或仅极偶发 1 次）。  
- **句首噪音**：前半首测试不再出现「叶子」等明显句首误识别；关键字仍全部正确。

完成 1–3 后建议再抓一轮静夜思日志，与 ChatGPT 对齐是否需进一步调参或上多线程。
