# 优化批次 001：语音链路延迟 + 资源优化（中间记录 / 待复审）

> 日期：2026-04-20 ~ 2026-04-21
> 状态：**中间记录，Codex 第一轮审核已返工，待复审**
> 测试轮次：Test 004 / 005 / 006 / 008 / 009

---

## Codex 第一轮审核意见及返工情况

### 审核结论：未通过，要求返工

返工要求及处理情况：

| # | 审核意见 | 返工状态 | 说明 |
|---|---------|---------|------|
| 1 | 安全：API Key 明文不可入仓库 | ✅ 已修 | assets 改占位符，WebAnswerClient 支持外部配置优先，真实 key 仅在设备外部文件 |
| 2 | 批次颗粒度失控，需拆分 | ✅ 已修 | 本文档已拆为独立任务，分别记录验证数据 |
| 3 | welcome 打断方案验证不充分 | ✅ 已修（打断场景待实测） | 代码修复完成（SPEAKING 状态 mute bug + onTTSError welcome 标志清理），Test 009 验证基本链路，但 welcome 中唤醒词打断场景尚未设备实测 |
| 4 | 配置与代码不一致 | ✅ 已修 | config.properties VAD 注释已标注实际运行值 |
| 5 | 不应作为最终归档 | ✅ 已修 | 本文档状态改为"中间记录/待复审" |

---

## 任务 A：LLM 切换（智谱 GLM-4-Flash-250414）

### 独立状态：已完成 + 验证通过

#### 动机
- 之前用 `qwen-turbo`（付费），首 token 延迟 ~2-3s
- 智谱 GLM-4-Flash-250414：免费、128K 上下文、兼容 OpenAI 格式

#### 改动
**文件**: `app/src/main/assets/config.properties`
- API URL: `https://open.bigmodel.cn/api/paas/v4/chat/completions`
- Model: `glm-4-flash-250414`
- API Key: 已移除明文，改为从 `/sdcard/iptv-agent-config/config.properties` 读取

**文件**: `app/src/main/java/com/joctv/agent/web/WebAnswerClient.kt`
- init 块增加外部配置优先读取逻辑（与 IntentRouter 同模式）

#### 兼容性
- 智谱 API 完全兼容 OpenAI `/chat/completions` 格式
- `WebAnswerClient.kt` 请求/响应/流式解析零改动

#### 验证数据（Test 004）

| 查询 | 首 Token | 总延迟 | 回答 |
|------|---------|--------|------|
| "3加5等于多少" | 907ms | 919ms | "八" |
| "讲个笑话" | **393ms** | 1042ms | 57字 |
| "给我讲个故事" | **381ms** | 1598ms | 96字 |

Test 006 再次验证：首 token **660ms**。

#### 风险
- API Key 已外置到设备本地文件，不在仓库中
- 免费模型有 QPS 限制，酒店单房间场景足够
- 仍需轮换当前 key（因曾在仓库中暴露过）

---

## 任务 B：VAD 静音 + IDLE 空识别过滤

### 独立状态：已完成 + 验证通过

### B-1: VAD 静音等待 800→600ms

#### 发现的问题
`config.properties` 中 `asr.vad.silence.duration.ms=1000` **完全未被代码读取**。
`NpuAsrController.GateConfig` 使用硬编码 `DEFAULT_SILENCE_DURATION_MS = 800L`。
已修正 config.properties 注释标注实际运行值。

#### 改动
**文件**: `NpuAsrController.kt` 第 84 行
```kotlin
private const val DEFAULT_SILENCE_DURATION_MS = 600L  // 从 800 改为 600
```

此常量用于 CAPTURE 和 IDLE 两条路径，本次一起调整。

#### 验证
- Test 003（800ms）：FINALIZE_FIRE silence=813ms
- Test 005/006（600ms）：600~606ms ✅

#### 风险
- 用户说话中间停顿 >600ms 可能被提前截断。实测关灯/天气/讲笑话/讲故事均未出现。
- 可随时回调。

### B-2: IDLE 路径空 NPU 识别过滤

#### 动机
IDLE 状态下环境噪音频繁触发 VAD → NPU → 空结果。Test 004 中 30 分钟 **192 次空识别**，每次 ~1.2s，浪费 ~230s CPU。

#### 改动
**文件**: `NpuAsrController.kt`
- 新增常量 `IDLE_MIN_SPEECH_MS = 800L`
- IDLE VAD 语音结束检查增加最短持续时间门槛（<800ms 的短噪音不提交 NPU）

#### 验证

| 版本 | 空识别次数 | 过滤率 |
|------|-----------|--------|
| Test 004（无过滤） | **192** | - |
| Test 005（400ms） | 86 | 55% |
| Test 006（800ms） | **18** | **91%** |
| Test 009（800ms） | 少量 | 稳定 |

Test 009 中仍过滤了短噪音（743ms < 800ms），正常唤醒词+指令 >1s 不受影响。

#### 风险
- 用户只说唤醒词 "小智小智"（~0.8s）可能踩线。实测 "小智小智帮我关灯" 整句 ~1.5s，安全。

---

## 任务 C：启动优化（Welcome TTS 不阻塞 ASR）

### 独立状态：代码已完成，打断场景待设备实测

#### 动机
旧逻辑：welcome TTS 播放 ~10s 期间 ASR 被 muted，用户必须等播完。

#### 改动
**文件**: `MainActivity.kt`
- `tryPlayWelcomeMessage()`：不再调用 `asrController?.setMuted(true)`，改为设置 `conversationStateManager?.isWelcomePlaying = true`
- 唤醒词检测块：检测到唤醒词时如果 `isWelcomeTtsPlaying`，调用 `ttsOrchestrator?.stop()` 打断 welcome

**文件**: `ConversationStateManager.kt`
- 新增 `isWelcomePlaying` 属性
- `handleStateTransition` 的 `SPEAKING` 分支：当 `isWelcomePlaying == true` 时不 mute ASR

#### Codex 审核发现的 bug 及修复
第一版改动中，`tryPlayWelcomeMessage` 不再 mute ASR，但 `ConversationStateManager.handleStateTransition(SPEAKING)` 会无条件 `setAsrMuted(true)`，覆盖了 P1 的意图。

**修复**：在 `ConversationStateManager` 中增加 `isWelcomePlaying` 标记，SPEAKING 状态下判断是否为 welcome 播放，是则跳过 mute。

#### 验证数据（Test 009）

| 检查项 | 结果 |
|--------|------|
| Welcome SPEAKING 状态下 ASR 是否被 mute | ✅ 不再 mute |
| Welcome 播放期间 IDLE_VAD 是否检测到语音 | ✅ `RMS=703` at 14:06:18 |
| Welcome 自然结束后状态机 | ✅ SPEAKING→IDLE→LISTENING 正常 |
| 后续问答状态机 | ✅ 唤醒→关灯→TTS→LISTENING→IDLE 全链路正常 |
| Wake word during welcome 打断 | ⚠️ 未覆盖（用户未在 welcome 中说唤醒词） |

#### 遗留项
- **未测试 welcome 播放中说唤醒词打断的核心场景**。log 确认 ASR 在 welcome 期间 active 且检测到语音，但用户未说唤醒词。
- Codex 要求的验证点：在 welcome 播放中说唤醒词 → welcome 被打断 → 状态机稳定 → 后续问答正常。这需要下一次专门测试。

---

## 任务 D：P2 内存排查

### 独立状态：已完成（无代码改动）

#### 结论
- 内存 2.4→2.9GB / 10min，来自模型加载（TTS Matcha ~44s + NPU SenseVoice）
- 运行期增长速率低，属于正常 GC 波动，**非泄漏**
- NPU 推理 FloatArray 临时对象、TTS samples 临时对象均短期 GC 可回收
- `tvLlmLog.append` 每次交互几行，增长很慢

---

## 综合验证数据

### Test 009（最新，含所有返工修复）

| 指标 | Test 003（基线） | Test 009（当前） | 改善 |
|------|-----------------|----------------|------|
| VAD 静音等待 | 813ms | 602ms | **-211ms** |
| Welcome 期间 ASR | muted | **active** | 可提前唤醒 |
| 空识别/30min | ~192 | ~18 | **-91%** |
| LLM 首 Token | ~2-3s (qwen-turbo) | **660ms** (GLM-4-Flash) | **快 3-4x** |
| LLM 费用 | 付费 | **免费** | 0 成本 |
| API Key 安全 | 明文在 assets | **外置到设备** | 仓库无明文 |

### 状态机验证（Test 009）
```
IDLE → SPEAKING (welcome, ASR unmuted) → IDLE (welcome_done)
→ LISTENING (wakeword) → SPEAKING (tts) → LISTENING (tts_done)
→ IDLE (follow_up_timeout)
```
全部正确，无异常状态。

---

## 变更文件清单

| 文件 | 变更类型 | 变更内容 | 任务 |
|------|---------|---------|------|
| `app/src/main/assets/config.properties` | 修改 | API 切智谱 + Key 改占位符 + VAD 注释修正 | A, B |
| `app/src/main/java/com/joctv/agent/web/WebAnswerClient.kt` | 修改 | 外部配置优先读取 | A |
| `app/src/main/java/com/joctv/agent/asr/NpuAsrController.kt` | 修改 | VAD 600ms + IDLE 800ms 门槛 | B |
| `app/src/main/java/com/joctv/agent/MainActivity.kt` | 修改 | Welcome 不 mute + 唤醒打断 + isWelcomePlaying 标记 | C |
| `app/src/main/java/com/joctv/agent/conversation/ConversationStateManager.kt` | 修改 | isWelcomePlaying 属性 + SPEAKING 跳过 mute | C |

---

## 待 Codex 复审确认项

1. **API Key 安全**：assets 中已无明文，WebAnswerClient 优先读外部文件
2. **批次拆分**：本文档已按任务 A/B/C/D 独立记录
3. **Welcome 打断**：ASR 不再被 mute 已验证，但 "播放中说唤醒词打断" 场景待下次补测
4. **配置一致性**：config.properties 已标注实际运行值
5. **Key 轮换**：当前 key 曾暴露过，建议轮换
