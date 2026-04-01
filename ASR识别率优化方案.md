# ASR 识别准确率优化方案

针对当前现象（如 final=上海 里 / 才 天气 / 就 环境，应为「上海天气」等）给出的可落地方案，供 Claude 按项实现或调参。

---

## 一、现象与原因简要

| 现象 | 可能原因 |
|------|----------|
| 「上海 里」代替「上海天气」 | 首字或首词被截断/错识；Vosk 同音或近音（里/气） |
| 「才 天气」代替「上海天气」 | 前半段丢失或错成「才」；通道切换或 VAD 截断句首 |
| 「就 环境」等 | 同上，句首或关键词识别错误 |

共性：句首/关键词易错，与 **VAD 截断、通道选择、模型限制、缺少后处理** 有关。

---

## 二、方案概览

1. **先调参**：融合模式、VAD、暖机（不改代码，只改 config）。
2. **后处理纠错**：在 iptv-edge-agent 对 ASR final 结果做热词/同音纠错再路由。
3. **可选**：Vosk 语法/热词、Intent 容错。

---

## 三、方案 1：配置调参（优先尝试）

当前 `config.properties` 已支持下列项，建议**按顺序**试，每次只改一两项便于对比。

### 3.1 使用加权融合，减少通道切换导致的断字

```properties
# 从 channel_picker 改为 weighted_fusion，避免「只选一路」在句首切换时丢字
asr.multichannel.fusion.mode=weighted_fusion
```

- 行为：3 路（FC/FL/FR）按当前帧能量加权混合，不再每帧只选一路，句首更稳定。
- 若设备上 `weighted_fusion` 反而更糊，可再改回 `channel_picker` 并只调 VAD/暖机。

### 3.2 降低 VAD 阈值，减少句首被截断

```properties
# 当前 120.0，可先试 80～100，避免把轻声/远场句首判成静音
asr.vad.threshold=80.0
```

- 若 80 导致环境噪声频繁触发，可试 100.0 或 90.0，在「少截断」和「少误触发」之间折中。

### 3.3 适当拉长暖机，避免首字被清掉

```properties
# 当前 300，可试 400～500，保证开讲时已过暖机
asr.multichannel.warmup.ms=500
```

- 建议与 VAD 一起试：先 500ms 暖机 + 80.0 VAD，看「上海天气」等句首是否改善。

### 3.4 建议的完整配置示例（第一轮）

```properties
# 多通道
asr.multichannel.fusion.mode=weighted_fusion
asr.multichannel.warmup.ms=500

# VAD
asr.vad.threshold=80.0
```

若仍出现「上海 里」「才 天气」，再叠加**方案 2 后处理纠错**。

---

## 四、方案 2：ASR 后处理纠错（推荐实现）

在 **iptv-edge-agent** 里，对 ASR 的 **final** 结果在做意图路由之前做一次纠错，可明显弥补「上海 里」「才 天气」这类错误。

### 4.1 现状

- `MainActivity.onAsrResult` 仅做 `text.replace(" ", "")`，未做同音/热词纠错。
- `HotwordCorrector`（iptv-edge-agent）目前只有 JOCTV、Wi-Fi 等少量映射，**没有**「天汽→天气」「上海里→上海天气」等。

### 4.2 实现要点（给 Claude）

1. **扩展纠错表**（与 3576v2 的 `HotwordCorrector` 对齐思路）  
   在 **iptv-edge-agent** 的 `HotwordCorrector.kt` 中增加常见 ASR 误识与天气/查询相关纠错，例如：

   - 同音/近音：`天汽`→`天气`，`天起`→`天气`，`上还`→`上海`，`里` 在「上海里」语境→见下条。
   - 整句/片段替换（可选）：如 `上海里`→`上海天气`，`才天气`→`上海天气`（或统一成 `查询天气`，看产品偏好）。  
   若不想误伤其它「里」字，可只做「上海里」→「上海天气」整词替换。

2. **在 MainActivity 中接入纠错**  
   - 在 `onAsrResult` 中，对 **isFinal** 的文本：先 `replace(" ", "")` 得到 `cleanedText`，再调用 `HotwordCorrector.correct(cleanedText)`（或扩展后的纠错接口）得到 `correctedText`。  
   - 用 `correctedText` 做 UI 展示和 **processFinalResult(correctedText)**，这样意图路由（如「天气」走 WEB）会基于纠错后的文本。

3. **3576v2 已有纠错**  
   - 3576v2 的 `HotwordCorrector` 已包含「天汽」「天起」→「天气」等，若 iptv-edge-agent 与 3576v2 会同步维护，可把两边的纠错表对齐，并在 iptv-edge-agent 同样在 final 结果上应用一次纠错再路由。

### 4.3 纠错表示例（可写入 HotwordCorrector）

```kotlin
// 同音/近音
"天汽" to "天气"
"天起" to "天气"
"上还" to "上海"
// 常见 ASR 断句/错字（针对「上海天气」类）
"上海里" to "上海天气"
"才天气" to "上海天气"   // 或 "查询天气"，按产品定
```

这样即使 ASR 出「上海 里」或「才 天气」，去掉空格后仍可被纠成「上海天气」或「查询天气」，从而正确走天气 WEB 查询。

---

## 五、方案 3：可选增强

### 5.1 Vosk 语法/热词（限制在固定句式时用）

- 当前 `AsrController` 使用 `Recognizer(model, SAMPLE_RATE.toFloat())`，未传 grammar，为**大词表连续识别**。
- 若产品接受「仅支持若干固定句」时，可改为带 grammar 的 Recognizer，例如把「上海天气」「北京天气」「查询天气」等放入 grammar，提高这几句的识别率；代价是其它自由说法无法识别。
- 实现方式可参考同工程下 `AsrEngine.kt` 的 `createCommandRecognizer()`（传 grammar JSON 数组给 Recognizer 构造）。

### 5.2 意图路由容错（天气类）

- 在 `IntentRouter` 中，若文本包含「上海」「北京」等城市名且包含「里」「气」「查」等与「天气」易混的字，可**额外**视为天气意图（例如再判断是否包含「里」且长度较短，则当作「天气」查询）。  
- 此为兜底逻辑，优先仍建议用**方案 2 纠错**把「上海里」直接改成「上海天气」，再由现有 `contains("天气")` 路由到 WEB。

---

## 六、实施顺序建议

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 仅改配置 | `weighted_fusion` + `asr.vad.threshold=80.0` + `warmup.ms=500`，现场试「上海天气」等 |
| 2 | 加后处理纠错 | 扩展 HotwordCorrector，在 MainActivity 对 final 结果 correct 后再 processFinalResult |
| 3 | 仍不足时 | 再试 VAD 90/100、暖机 400，或按产品需求加 Vosk grammar / 意图容错 |

---

## 七、配置与代码位置速查

- **配置**：`iptv-edge-agent/app/src/main/assets/config.properties`（及 template）。  
- **融合模式**：`AsrController.kt` 内 `MultiChannelAudioProcessor`，`fusionMode` 已支持 `channel_picker` / `weighted_fusion`。  
- **VAD/暖机**：`AsrController.AsrConfig` 读取 `asr.vad.threshold`、`asr.multichannel.warmup.ms`。  
- **纠错**：`HotwordCorrector.kt` 的 `correct(text)`；调用点在 `MainActivity.onAsrResult`（需在 final 分支对 cleanedText 做 correct 再传入 processFinalResult）。

按上述顺序先调参、再加纠错，即可在不大改架构的前提下明显改善「上海天气」等识别与路由效果。
