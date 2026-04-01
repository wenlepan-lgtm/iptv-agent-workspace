# JOCTV Agent 酒店 IPTV 本地语音智能体技术白皮书
## v2.0（NPU ASR/TTS + 3D数字人版本）

## 1. 项目定位

**JOCTV Agent 是部署在酒店 IPTV 电视终端侧的本地语音智能体（Edge Agent）**，运行于客房终端本地，提供语音识别、意图理解、服务路由、语音反馈与数字人交互能力。  
在可联网条件下，系统可接入外部大模型与实时数据 API；在网络受限场景下，核心语音与本地服务能力仍可持续运行。

JOCTV Agent 的核心定位是：  
**面向酒店场景的可控、可运营、可集成的端侧 AI 服务入口**。

---

### 1.1 典型使用场景

- **客房控制**：灯光、空调、窗帘、电视音量/频道等联动 RCU 或终端能力。  
- **酒店服务**：前台、客房服务、机器人呼叫、客服转接。  
- **本地知识查询**：早餐时间、设施说明、Wi-Fi、酒店介绍、周边推荐。  
- **互联网信息查询**：天气、时间、新闻、股票等实时信息（通过 API 网关）。  
- **合规拒答**：敏感、违规、越权内容统一拦截与合规回复。

---

### 1.2 解决的核心问题

| 维度 | 传统云语音方案 | JOCTV Agent v2.0 |
|------|----------------|------------------|
| 隐私合规 | 原始语音上传云端 | **语音本地处理、最小化外发** |
| 稳定性 | 网络决定可用性 | **离线核心能力可持续** |
| 延迟体验 | 云链路抖动明显 | **端侧低时延响应** |
| 系统集成 | 外挂式接入 | **原生 IPTV + RCU 深度集成** |
| 交互形态 | 语音播报为主 | **语音 + 3D数字人联动** |

---

## 2. 设计原则

### 2.1 端侧优先（Edge-First）

- 语音采集、ASR、TTS、数字人驱动优先在终端侧完成。  
- 网络用于增强能力：实时信息 API、云端大模型补充、运营配置同步。  
- 网络异常不影响基础客控和本地服务能力。

### 2.2 隐私安全与责任边界清晰

- 原始语音默认不出房间。  
- 对外仅传递必要文本与结构化请求。  
- 统一路由与审计，保障可追踪、可回溯、可合规。

### 2.3 Agent 化架构

- 采用常驻 Agent 进程，具备状态管理、能力路由、策略执行和多模块协同。  
- 非“单点语音 SDK 拼装”，而是可持续演进的平台能力。

---

## 3. 系统总体架构

### 3.1 逻辑架构（Agent 内部）

![未命名绘图.drawio](/Users/ala/工作项目/未命名绘图.drawio.svg)

**Intent Router 是整个系统的核心控制中枢**。

---

1. **Audio In**：麦克风采集 + 前处理  
2. **ASR 层**：端侧 NPU加速识别  
3. **Intent Router**：本地指令/本地知识/实时查询/通用问答分类  
4. **Executor 层**：RCU 控制、IPTV 控制、酒店服务接口、实时 API 网关  
5. **LLM Orchestrator**：仅在需要时做语言组织与上下文补充  
6. **TTS 层**：端侧语音合成（含中英文链路）  
7. **Digital Human 层**：3D数字人渲染、口型同步、状态动画

#### Audio In 已实现能力（v2.0）

- **唤醒词链路**：支持问候语，以及唤醒词触发（如”小智小智”），与对话状态机联动。
- **背景声音消除：系统声音背景抑制**，支持 TV 播放背景音场景下的语音门控与抑制策略（Gate 门控）。
- **回声消除（AEC）**：降低扬声器回放对麦克风采集的回灌影响（Android AcousticEchoCanceler）。
- **人声增强（Speech Enhancement）**：提升人声可懂度，降低非人声干扰。
- **波束成形拾音（Beam-forming）**：面向客房远场语音优化拾音方向性。
- **噪声抑制（Noise Suppression）**：抑制空调、环境底噪等稳定噪声（Android NoiseSuppressor）。
- **自动增益控制（AGC）**：动态调节输入电平，保持识别稳定（Android AutomaticGainControl）。

**Gate 门控实现（v2.0 已工程化）：**

- 支持双参考信号来源：`RefCapture`（es8388 loopback 48kHz）和 `RefTap`（播放器 PCM tap 16kHz）
- 基于 SimpleGate 相关性检测，计算麦克风与参考信号的相关系数
- 与 VAD 协同：RMS 高时跳过 Gate 检查（保证说话时不误杀），RMS 低时启用 Gate 过滤电视声
- 支持 Shadow Mode（仅记录日志）和正常模式（真正拦截）

> 注：不同机型麦克风阵列、Codec、驱动能力不同，AEC/Beam-forming/AGC 实际效果随硬件能力和调参策略变化。

### 3.2 关键控制中枢

**Intent Router + Policy Guardrail + Realtime Gateway** 是 v2.0 的核心中枢：

- 实时问题工具优先（先查后答）
- 本地指令直达（低时延）
- 大模型负责表达，不负责编造事实

#### Intent Router 实现细节（v2.0）

**路由类型（RouteType）：**
- `LOCAL_COMMAND`：本地指令（TV控制、RCU控制、酒店服务、机器人服务）
- `REALTIME_QUERY`：实时查询（天气、新闻、时间、汇率、股票）
- `LLM`：通用问答（回退到云端大模型）

**配置化路由规则（intents_local.json）：**
- 支持 50+ 意图规则，覆盖 TV、HOTEL、RCU、ROBOT、POI 五大业务域
- 支持 `keywords_any`（任意匹配）+ `keywords_none`（排除关键词）组合
- 支持优先级排序（priority 字段）
- 支持槽位提取（如温度数值：`TEMP_NUMBER` 正则提取）
- 支持敏感词拦截（SENSITIVE 域，优先级最高 200）

**实时查询 Provider（v2.0 已实现）：**
| Provider | 数据源 | 特点 |
|----------|--------|------|
| TimeProvider | 本地系统时间 | 无网络依赖，支持中英文格式 |
| WeatherProvider | wttr.in API | 免费、无需 API Key、支持城市映射 |
| NewsProvider | 预留接口 | 可对接酒店新闻源 |
| ExchangeRateProvider | 预留接口 | 可对接汇率 API |
| StockProvider | 预留接口 | 可对接股票 API |

**"先查后答"策略：**
1. 检测到实时查询意图（天气/新闻/汇率等）
2. 先调用对应 Provider 获取真实数据
3. 若数据获取成功，LLM 仅负责润色转述
4. 若数据获取失败，返回固定错误模板（禁止编造）

### 3.3 当前生产模型与规模（v2.0）

| 模块 | 当前主用模型 | 大小（实测） | 说明 |
|------|--------------|--------------|------|
| **ASR（NPU）** | `SenseVoice-RKNN` | **~200M** | RKNN NPU 离线识别，支持中英日韩粤，带 ITN |
| **TTS（中文主链路）** | `matcha-icefall-zh-en` (SherpaOnnxTts) | **~1.3G** | 中文英文音色可切换，支持流式合成 |
| **TTS（英文链路）** | `kokoro-en-v0_19` / `piper-en-us-lessac` | **~150M** | 英文音色可切换，支持 Kokoro/Piper 双模型 |
| **3D 数字人** | TaoAvatar SDK (NNR + A2BS) | **~550M** | 淘宝 NNR 渲染 + A2BS 音频驱动口型 |
| **Gate 门控** | SimpleGate (相关性检测) | **<1M** | 参考信号比对，过滤电视背景声误唤醒 |

### 3.4 外部依赖与 SDK 集成

| 依赖项 | 用途 | 来源 |
|--------|------|------|
| **SherpaOnnx** | ASR/TTS 推理框架 | k2-fsa/sherpa-onnx |
| **MNN-TaoAvatar** | 3D 数字人渲染 | MetaAvatar SDK |
| **NNR Runtime** | 神经网络渲染 | 淘宝 NNR |
| **A2BS Service** | 音频驱动口型 | 淘宝 A2BS |
| **OkHttp** | HTTP 客户端 | Square |
| **Kotlinx Coroutines** | 协程调度 | JetBrains |

**模型资源路径（设备端）：**
```
/sdcard/iptv-agent-models/
├── asr/                    # ASR 模型（SenseVoice-RKNN）
├── tts/
│   ├── matcha-zh-en/       # 中文 TTS（Matcha）
│   ├── kokoro-en/          # 英文 TTS（Kokoro）
│   └── piper-en-us-lessac/ # 英文 TTS（Piper）
└── taoavatar/              # 3D 数字人模型
    ├── nnr/                # NNR 渲染模型
    └── a2bs/               # A2BS 口型驱动模型
```

---

## 4. 硬件与系统环境

### 4.1 推荐平台

- **SoC**：Rockchip RK3576  
- **CPU**：4x Cortex-A72 + 4x Cortex-A53  
- **GPU**：Mali-G52 MC3  
- **NPU**：6 TOPS  
- **内存**：8GB LPDDR4X
- **存储**：32GB eMMC 起  
- **系统**：Android TV 14

### 4.2 NPU 定位（v2.0）

- **ASR**：NPU 路径已工程化落地（端侧实时识别主通道）。  
- **TTS**：完成 NPU 化工程接入与方案验证，按模型/平台启用；CPU 路径持续作为稳定兜底。  
- **策略**：NPU 优先 + CPU 兜底 + 运行时能力探测。

### 4.3 NPU / CPU / GPU 资源分配策略

为保证机顶盒长期稳定运行，v2.0 采用“模块分工 + 运行时限流”策略：

- **NPU**：优先承载 ASR 主推理链路；TTS 按模型与平台能力启用 NPU 方案。  
- **CPU**：负责 Agent 路由、协议栈、TTS 兜底推理、文本处理、业务编排与系统监控。  
- **GPU**：承载 3D 数字人渲染、动画与 UI 合成。  
- **内存策略**：模型按需加载、缓存上限控制、异常回收与降级策略。  
- **稳定性目标**：在连续运行场景下维持 CPU 与内存在可控区间，避免因单模块峰值导致系统卡顿。

#### 资源控制实践

- ASR 与 TTS 分线程/分优先级调度，避免互相抢占。  
- 关键链路埋点（识别、合成、播放、渲染）用于动态调参。  
- 负载高时可触发降级（如切换轻量模型、降低并发、关闭非关键特效）。

---

## 5. 软件技术栈

### 5.1 基础框架

- Android TV Framework
- 常驻 Service + 前台交互 UI
- 协程并发调度 + 状态机驱动
- 结构化日志与性能埋点

### 5.2 AI 能力栈

- **ASR**：NPU 离线识别（SenseVoice-RKNN），支持流式 VAD 分段 + Partial 结果
- **TTS**：SherpaOnnxTts 双引擎（中文 Matcha + 英文 Piper/Kokoro），支持流式合成
- **LLM**：云端大模型（Qwen/DeepSeek 兼容 API），支持流式 delta 输出
- **Realtime API**：天气、新闻、时间、汇率、股票等统一网关接入

### 5.3 TTS 编排器（TtsOrchestrator）

**核心能力：**
- 双引擎管理：中文 TTS（Matcha）+ 英文 TTS（Piper/Kokoro）
- 流式切句：SentenceSegmenter 支持中英文句界检测
- 文本规范化：ChineseTtsNormalizer（数字转中文读法）+ EnglishTtsNormalizer
- 队列管理： ConcurrentLinkedQueue 保证播报顺序
- 去重机制：避免流式 delta 重复入队
- 延迟埋点：t_output_first → t_first_sentence → t_tts_enqueue → t_audio_play

**流式 TTS 优化（P0）：**
- LLM 流式输出 → 切句器实时切分 → TTS 立即合成 → 首句播报延迟最小化
- 支持 PCM 回调：TTS 合成的 PCM 数据实时喂给数字人驱动口型

### 5.4 数字人渲染栈

**TaoAvatar SDK 集成（v2.0）：**
- **NNR 渲染**：NnrAvatarRender，支持上半身/全身模式、透明背景
- **A2BS 口型驱动**：AudioToBlendShapeData，16kHz PCM 输入 → BlendShape 输出
- **AudioBlendShapePlayer**：队列化播放，保证音画同步

**状态管理：**
- IDLE：空闲状态，播放 idle 动画 + 随机眨眼
- SPEAKING：说话状态，A2BS 驱动口型 + 微笑表情

**配置能力（DigitalHumanConfig）：**
- 眨眼间隔：BLINK_MIN_INTERVAL_MS / BLINK_MAX_INTERVAL_MS
- RMS 平滑：RMS_SMOOTHING / RMS_SCALE / RMS_MIN_THRESHOLD
- Morph 映射：支持不同模型的标准名称映射

---

## 6. v2.0 已实现能力（相对 v1.0）

### 6.0 代码模块结构（实际实现）

```
com.joctv.agent/
├── MainActivity.kt                    # 主界面、权限、UI 交互
├── asr/
│   ├── NpuAsrController.kt           # ASR 控制器（VAD + Gate + NPU）
│   ├── NpuOfflineAsrEngine.kt        # NPU 离线识别引擎
│   ├── AsrStateMachine.kt            # ASR 状态机（IDLE/LISTEN/THINK/SPEAK）
│   ├── HotwordCorrector.kt           # 热词纠正
│   └── IAsrController.kt             # ASR 接口定义
├── tts/
│   ├── TtsOrchestrator.kt            # TTS 编排器（双引擎 + 流式）
│   ├── SherpaOnnxTts.kt              # SherpaOnnx TTS 封装
│   ├── SentenceSegmenter.kt          # 流式切句器
│   ├── ChineseTtsNormalizer.kt       # 中文文本规范化
│   ├── EnglishTtsNormalizer.kt       # 英文文本规范化
│   └── UserLexiconManager.kt         # 用户词典管理
├── intent/
│   └── IntentRouter.kt               # 意图路由器（JSON 配置驱动）
├── realtime/
│   ├── RealtimeHandler.kt            # 实时查询处理器
│   ├── TimeProvider.kt               # 时间 Provider
│   ├── WeatherProvider.kt            # 天气 Provider（wttr.in）
│   ├── NewsProvider.kt               # 新闻 Provider
│   ├── ExchangeRateProvider.kt       # 汇率 Provider
│   └── StockProvider.kt              # 股票 Provider
├── digitalhuman/
│   ├── DigitalHumanController.kt     # 通用数字人控制器（Filament）
│   ├── TaoAvatarController.kt        # TaoAvatar 控制器（NNR + A2BS）
│   └── DigitalHumanConfig.kt         # 数字人配置
├── audio/
│   ├── RefCapture.kt                 # 参考信号采集（loopback）
│   ├── RefTap.kt                     # 参考信号 tap
│   ├── SimpleGate.kt                 # Gate 门控算法
│   └── AudioDuckingManager.kt        # 音频闪避管理
├── web/
│   └── WebAnswerClient.kt            # LLM API 客户端（流式）
└── conversation/
    └── ConversationStateManager.kt   # 对话状态管理
```

### 6.1 语音能力升级

- **NPU ASR 主链路上线**：SenseVoice-RKNN 模型，支持中英日韩粤五语言
- **VAD 分段**：基于 RMS 的语音活动检测，支持静音超时自动结束
- **Partial 识别**：说话过程中实时返回部分识别结果
- **Gate 门控**：SimpleGate 相关性检测，过滤电视背景声误触发
- **音频效果**：AEC/NS/AGC 系统级音频处理

**ASR 控制器架构（NpuAsrController）：**
```
AudioRecord(16kHz mono) → Ring Buffer → Gate门控 → VAD分段 → NpuOfflineAsrEngine → 回调
                                    ↑
                              RefSource (参考信号)
```

- 中文/英文 TTS 双链路工程化，支持模型切换与词典增强。
- 流式句段调度、首播 warmup、播放状态埋点持续完善。

### 6.2 3D 数字人能力上线

- 从 2D 形态升级为 3D 数字人交互（TaoAvatar SDK）
- 支持口型同步（A2BS 音频驱动）、播报状态联动、视觉在场感增强
- 与 TTS 播放链路打通，支持实时 PCM 音频驱动

**TaoAvatar 控制器能力：**

- `AvatarTextureView`：Surface 渲染视图，支持透明背景叠加
- `A2BSService`：音频 → BlendShape 转换，16kHz PCM 输入
- `NnrAvatarRender`：NNR 模型渲染，支持上半身/全身切换
- `AudioBlendShapePlayer`：队列化 BlendShape 播放，保证音画同步

**状态流转：**
- NONE → IDLE：模型加载完成，播放 idle 动画 + 随机眨眼
- IDLE → SPEAKING：TTS 开始播放，A2BS 驱动口型 + 微笑表情
- SPEAKING → IDLE：TTS 播放结束，恢复 idle 状态

### 6.3 Router 能力增强

- 本地指令直达（客控、终端控制）
- 本地知识问答（酒店配置化）
- 实时查询路由（天气/新闻/股票等）
- 大模型回答受策略约束，降低实时信息幻觉风险

**LLM 集成（WebAnswerClient）：**
- 支持 Qwen/DeepSeek 等 OpenAI 兼容 API
- 支持同步和流式两种模式
- 流式模式（Streaming）：SSE 协议，实时回调 delta 文本
- 系统提示词内置"禁编规则"：禁止编造实时信息、禁止臆测具体数值
- 网络错误处理：超时、DNS 失败、服务不可用的友好提示

### 6.4 工程可运营能力

- 词典热更新与多音字纠正机制（UserLexiconManager）
- 关键时延链路埋点（delta、enqueue、synth、audio_play）
- 日志可追踪，便于灰度和线上问题定位

**已实现的可配置能力（v2.0）：**

| 配置项 | 配置文件/类 | 说明 |
|--------|-------------|------|
| 意图路由规则 | `intents_local.json` | 50+ 意图，支持关键词、优先级、槽位提取 |
| 敏感词拦截 | `intents_local.json` (SENSITIVE 域) | 政治敏感、色情、暴力等关键词 |
| 酒店知识库 | `intents_local.json` (HOTEL 域) | WiFi、早餐、设施、服务 |
| 周边景点 | `intents_local.json` (POI 域) | 外滩、迪士尼、南京路等 |
| 数字人配置 | `DigitalHumanConfig` | 眨眼间隔、RMS 参数、Morph 映射 |
| 英文 TTS 模型 | `EnglishTtsModelConfig` | Kokoro/Piper 切换、模型路径 |
| API 配置 | `config.properties` | LLM API 地址、Key、模型名 |

---

## 7. 市场与方案对比（v2.0）

### 7.1 方案对比

| 维度 | 智能音箱方案 | 纯云语音方案 | JOCTV Agent v2.0 |
|------|--------------|--------------|------------------|
| 部署位置 | 云端/外设 | 云端 | **电视端本地 Agent** |
| 隐私合规 | 弱 | 中 | **强（本地优先）** |
| 断网可用 | 低 | 低 | **高（核心能力离线）** |
| 酒店可运营 | 弱 | 中 | **强（可编辑可配置）** |
| IPTV 集成 | 低 | 外挂式 | **原生集成** |
| RCU 联动 | 弱 | 需二次开发 | **标准化路由直连** |
| 交互形态 | 语音播报 | 语音播报 | **语音 + 3D数字人** |

### 7.2 对酒店管理方的价值

- **可运营**：意图路由、话术、知识、拒答策略可按集团/门店编辑。  
- **可控**：实时信息走 API 网关，敏感问题可统一策略拦截。  
- **可降本**：端侧优先降低云调用成本与网络依赖。  
- **可审计**：路由日志、执行结果、失败原因可追踪。

### 7.3 对住客体验的价值

- **更快**：客控与本地服务问题低时延响应。  
- **更稳**：弱网/断网下仍能完成核心服务。  
- **更自然**：3D数字人 + 语音播报提升在场感。  
- **更可信**：实时问题“先查后答”，降低胡编乱答。

### 7.4 Agent 语意路由“可编辑、可运营”

v2.0 将语意路由从“写死逻辑”升级为“可运营策略”：

- 路由类型可配置：`local_command` / `local_knowledge` / `realtime_query` / `general_chat`  
- 关键词与规则可配置：按酒店业务动态调整  
- 服务编排可配置：IPTV 指令、RCU 指令、酒店服务接口  
- 策略可配置：拒答范围、敏感词、降级话术、优先级  
- 支持灰度：按集团、门店、楼层、房型做差异化策略

---

## 8. 与 IPTV/RCU 集成方案（v2.0）

### 8.1 IPTV 深度集成

- 电视端常驻 Agent 与播放器/系统服务联动  
- 支持频道、音量、播放控制、页面导航等语音控制  
- 与酒店内容运营系统对接，实现“语音即入口”

### 8.2 RCU 深度集成

- 标准化指令映射（灯光、空调、窗帘、场景模式）  
- 支持设备状态查询与回执  
- 失败重试与兜底播报机制，保证客控可靠性

### 8.3 统一服务总线

- 将 IPTV/RCU/酒店服务系统纳入统一 Intent 执行总线  
- 保持协议解耦，便于不同酒店品牌和设备厂商适配

### 8.4 酒店云端与 LLM 协同

- 对接酒店云平台，承载配置下发、日志汇聚、策略更新。  
- 接入云端 LLM（如 Qwen）用于开放问答与话术组织。  
- 实时问题采用“**API 先查后答**”，LLM 仅做转述与总结。  
- 网络异常时自动降级到本地能力，保证基础服务不中断。

### 8.5 PMS / 机器人 / 酒店服务系统联动

- **PMS 联动**：住客身份、订单信息、房态与服务记录对接（按权限最小化访问）。  
- **机器人联动**：客需配送、引导服务、任务状态回传。  
- **酒店服务联动**：前台、客房服务、工程、保洁等系统工单触发与回执。  
- **统一回执机制**：所有外部系统调用可追踪、可重试、可降级播报。

### 8.6 面向酒店运营的集成价值

- 从“语音入口”升级为“服务编排中枢”。  
- 统一接口管理，降低多系统对接成本。  
- 支持集团级标准化 + 门店差异化运营。

---

## 9. 安全与合规策略

- 本地优先处理语音与敏感交互  
- 对外请求最小化、去标识化  
- 实时信息必须“先查后答”，无数据不编造  
- 拒答与越权拦截策略可配置、可审计

---

## 10. 未来演进路线（v2.x -> v3.0）

### 10.1 语音与模型优化

- **英文 TTS NPU 深化优化**：模型适配、算子优化、端侧低时延策略  
- 中文/英文发音准确率继续提升（词典 + 上下文规则 + 领域语料）  
- 端侧模型量化与多模型并发调度优化

### 10.2 数字人优化

- 3D 口型与表情细化（情绪、节奏、停顿自然度）  
- 长句播报连贯性优化（预合成与无缝接播）  
- 多形象与酒店品牌化定制能力

### 10.3 实时信息 API 接入

- 天气、新闻、股票、汇率等实时数据源统一接入  
- API 网关、缓存、降级策略与来源可信控制  
- LLM 仅做转述与总结，避免实时事实幻觉

### 10.4 平台化与运营化

- 集团级配置中心（多门店差异化）  
- 指标看板（识别率、时延、拒答率、服务转化）  
- A/B 实验与灰度发布体系

---

## 11. 版本记录

- **v1.0.0（2025-12-25）**：端侧语音能力初版、2D 数字人、基础路由
- **v2.0.0（2026-01）**：NPU ASR/TTS 工程化升级、3D 数字人（TaoAvatar）、实时 API 路由体系
- **v2.0.x（2026-02）**：
  - Gate 门控与 VAD 协同（P1-fix：关闭 Shadow Mode，真正拦截电视声）
  - TTS 流式优化（P0：删除全量/增量兼容逻辑，流式切句去重）
  - 英文 TTS 支持 Kokoro/Piper 双模型切换
  - LLM 流式输出 + TTS 流式合成首句延迟优化
  - IntentRouter 支持 REALTIME_QUERY 类型

---

## 12. 联系方式

如需进一步了解 JOCTV Agent 项目或探讨合作，请联系：  
**Ala Pan**  
Email: **Ala.pan@joctv.cn**

