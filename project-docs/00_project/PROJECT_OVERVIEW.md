# JOCTV IPTV Edge Agent — 项目概览

> 本文档供代码审核工具（Codex 等）快速理解项目全貌。

---

## 一、项目定位

**酒店 IPTV 语音助手**，部署在 RK3576 Android TV 机顶盒上。

用户说"小智小智"唤醒，说出自然语言指令，系统路由到本地命令 / 实时数据 API / 远程 LLM，以 TTS 语音回复 + 数字人口型同步渲染。

---

## 二、技术栈

| 类别 | 技术 |
|------|------|
| 平台 | Android TV, RK3576 (ARM64), NPU |
| 语言 | Kotlin, minSdk 29, targetSdk 34 |
| ASR | sherpa-onnx + RKNN NPU Provider (SenseVoice) |
| TTS | sherpa-onnx (VITS MeloTTS/Piper Lessac) + MNN (Bert-VITS2, 优先) |
| 数字人 | Alibaba MNN-TaoAvatar SDK (3D, NNR+A2BS) |
| LLM | OpenAI 兼容 API (DeepSeek/Qwen), SSE 流式 |
| 音频 | AudioRecord, AudioTrack, AcousticEchoCanceler, NoiseSuppressor, AGC |
| HTTP | OkHttp 4.12 |
| 协程 | kotlinx-coroutines 1.7.3 |
| 构建 | Gradle, 本地 AAR (sherpa-onnx-1.12.24-rknn.aar) |
| 方向 | 横屏 (TV), Leanback Launcher |

---

## 三、源码结构

```
iptv-edge-agent/app/src/main/java/com/joctv/agent/
│
├── MainActivity.kt                    # 主 Activity，所有模块的编排中心 (~2170行)
├── SpeechRecognitionApplication.kt    # Application 子类（仅日志）
│
├── asr/                               # 语音识别模块
│   ├── IAsrController.kt             # ASR 控制器接口 + AsrListener 回调
│   ├── NpuAsrController.kt           # ASR 主控制器（AudioRecord → WakeGate → Gate → VAD → ASR Engine）
│   ├── NpuOfflineAsrEngine.kt        # SenseVoice RKNN NPU 推理引擎
│   ├── WakeGateController.kt         # 唤醒词门控（三态 FSM: IDLE→ARMED→CAPTURE）
│   ├── AsrStateMachine.kt            # ASR 语音状态机 (IDLE/IN_SPEECH/FINALIZING)
│   ├── VadDetector.kt                # 能量 VAD 检测器
│   ├── VAD.kt                        # VAD 工具类
│   ├── Hotwords.kt                   # 唤醒词定义
│   └── HotwordCorrector.kt           # 热词纠错
│
├── tts/                               # 语音合成模块
│   ├── TtsOrchestrator.kt            # TTS 编排器（流式分句、语言检测、双引擎调度）
│   ├── TtsEngineWrapper.kt           # 引擎选择包装（Bert-VITS2-MNN 优先，SherpaOnnx 兜底）
│   ├── ITtsEngine.kt                 # TTS 引擎接口
│   ├── MnnBertVits2Tts.kt            # Bert-VITS2 MNN 引擎
│   ├── SherpaOnnxTts.kt              # sherpa-onnx TTS 引擎（Matcha/Piper/Kokoro/VITS）
│   ├── EnglishTtsModelConfig.kt      # 英文 TTS 模型配置（Kokoro/Piper 多变体）
│   ├── EnglishTtsNormalizer.kt       # 英文文本预处理
│   ├── ChineseTtsNormalizer.kt       # 中文文本预处理
│   ├── SentenceSegmenter.kt          # 中英文分句器
│   ├── TTSManager.kt                 # TTS 生命周期管理
│   ├── TTSQueueManager.kt            # TTS 播放队列
│   └── UserLexiconManager.kt         # 用户多音字词典热更新
│
├── audio/                             # 音频处理模块
│   ├── SimpleGate.kt                 # 参考信号回声门控（能量+相关性阈值）
│   ├── RefSource.kt                  # 参考信号源抽象
│   ├── RefTap.kt                     # MediaPlayer Visualizer 采样适配
│   ├── RefCapture.kt                 # es8388 回环采集适配
│   ├── RefRingBuffer.kt              # 参考信号环形缓冲区
│   ├── VisualizerTap.kt              # 音频可视化
│   ├── AudioDuckingManager.kt        # 音频闪避（TTS 播放时降低媒体音量）
│   └── Es8388LoopbackController.kt   # es8388 硬件回环控制
│
├── digitalhuman/                      # 数字人模块
│   ├── DigitalHumanConfig.kt         # 数字人配置（路径、缩放参数）
│   ├── DigitalHumanState.kt          # 状态枚举
│   ├── VisemeWeights.kt              # 口型权重映射
│   ├── MorphTargetAnimator.kt        # 混合变形动画器
│   ├── DigitalHumanController.kt     # 2D 数字人控制器（Filament glb，旧版）
│   ├── DigitalHumanSurfaceView.kt    # Filament 渲染 SurfaceView
│   ├── FilamentRenderer.kt           # Filament 渲染器
│   └── taoavatar/
│       └── TaoAvatarController.kt    # 3D 数字人控制器（MNN TaoAvatar，当前主用）
│
├── conversation/                      # 对话管理
│   ├── ConversationStateManager.kt   # 对话状态 FSM（IDLE→LISTENING→READY→SPEAKING→COOLDOWN）
│   ├── ConversationState.kt          # 状态枚举
│   └── DialogStateStore.kt           # 对话历史存储
│
├── intent/                            # 意图路由
│   └── IntentRouter.kt               # JSON 规则引擎（实时查询 > 本地命令 > LLM）
│
├── engine/                            # 早期引擎抽象（部分未使用）
│   ├── AssistantEngine.kt            # 早期引擎编排
│   ├── IntentRouter.kt               # 早期意图路由（已被 intent/ 替代）
│   ├── Models.kt                     # 数据模型
│   └── providers/Providers.kt        # 提供者接口
│
├── realtime/                          # 实时数据
│   ├── RealtimeHandler.kt            # 实时查询调度
│   ├── WeatherProvider.kt            # 天气 API
│   ├── NewsProvider.kt               # 新闻 API
│   ├── ExchangeRateProvider.kt       # 汇率 API
│   ├── StockProvider.kt              # 股票 API
│   └── TimeProvider.kt               # 本地时间
│
├── web/                               # LLM 集成
│   └── WebAnswerClient.kt            # OpenAI 兼容 API 客户端（阻塞/SSE 流式）
│
└── utils/
    ├── WavReader.kt                   # WAV 文件读取
    └── MetricsCollector.kt           # 性能指标收集
```

---

## 四、核心数据流

```
用户语音
  │
  ▼
AudioRecord (16kHz mono, VOICE_RECOGNITION)
  │
  ▼
WakeGateController (IDLE态: 轻量VAD; 检测到唤醒词: ARMED→CAPTURE)
  │
  ▼
SimpleGate (参考信号回声门控, 可选)
  │
  ▼
VadDetector (能量VAD, 800ms静音判结束)
  │
  ▼
NpuOfflineAsrEngine (SenseVoice RKNN, NPU推理)
  │
  ▼
MainActivity.onAsrResult(text, isFinal)
  │
  ├── 检测唤醒词 ──→ ConversationStateManager: IDLE→LISTENING
  │                   WakeGateController: IDLE→CAPTURE
  │
  └── 最终识别文本 ──→ IntentRouter.route(text)
                        │
                        ├── LOCAL_COMMAND ──→ 直接执行 + 固定回复
                        ├── REALTIME_QUERY ──→ RealtimeHandler → 外部API
                        └── LLM ──→ WebAnswerClient.getAnswerStreaming()
                                      │
                                      ▼ SSE delta text
                                   TtsOrchestrator.onDeltaText()
                                      │
                                      ├── SentenceSegmenter 分句
                                      ├── 语言检测 (中文/英文)
                                      └── TtsEngineWrapper → PCM合成
                                            │
                                            ├── AudioTrack 播放
                                            └── TaoAvatarController (口型同步)
                                                  │
                                                  A2BSService: 音频→BlendShape
                                                  NnrAvatarRender: 3D渲染
```

---

## 五、对话状态机

```
IDLE ──[唤醒词]──→ LISTENING ──[ASR最终结果]──→ READY ──[收到回复]──→ SPEAKING
 ▲                                                    │                    │
 │                  COOLDOWN ←──[TTS完成]──────────────┘                    │
 │                      │                                                   │
 └──[8秒超时]───────────┘                                                   │
                                                                            │
                      READY ←──[用户8秒内继续说]─────────────────────────────┘
                       │               (无需唤醒词)
                       └──[ASR结果]──→ SPEAKING (循环)
```

- **IDLE**: 等待唤醒词，ASR 只做轻量 VAD
- **LISTENING**: 唤醒后，ASR 完整识别
- **READY**: ASR 有结果，等待回复或用户继续说
- **SPEAKING**: TTS 播放中，ASR 静音
- **COOLDOWN**: TTS 结束，8 秒窗口允许连续对话（不需唤醒词）

---

## 六、线程模型

| 组件 | 线程/调度器 | 说明 |
|------|------------|------|
| AudioRecord 读取 | 专用 Thread | 持续读取音频帧 |
| NPU ASR 推理 | Dispatchers.IO + Mutex | 同一时刻只允许一个推理 |
| ASR 结果回调 | Dispatchers.Main | 回调到 MainActivity |
| TTS 合成 | Dispatchers.IO | 句子队列处理 |
| LLM SSE 流式 | 专用 Thread | 同步 OkHttp 调用 |
| 数字人初始化 | Dispatchers.IO | A2BS + NNR 加载 |
| 数字人渲染 | Filament 渲染线程 | SurfaceView 内部 |
| ConversationFSM 定时器 | Handler(MainLooper) | 主线程延时 |
| WakeGate 定时器 | 专用 Thread | sleep 循环 |

---

## 七、外部服务依赖

| 服务 | 协议 | 用途 | 配置位置 |
|------|------|------|---------|
| SenseVoice RKNN | 本地 NPU (sherpa-onnx) | ASR 语音识别 | `/sdcard/sherpa-onnx-rk3576-20-seconds-sense-voice-zh-en-ja-ko-yue-2024-07-17/` |
| Bert-VITS2-MNN | 本地 MNN (TtsService) | 中文 TTS（优先引擎） | `/sdcard/iptv-agent-models/tts/bert-vits2-MNN/` |
| VITS MeloTTS zh_en | 本地 ONNX (sherpa-onnx) | 中文 TTS（SherpaOnnx 主引擎） | `/sdcard/iptv-agent-models/tts/vits-melo-tts-zh_en/` |
| Piper Lessac | 本地 ONNX (sherpa-onnx) | 英文 TTS（当前默认） | `/sdcard/iptv-agent-models/tts/piper-en-us-lessac/` |
| MNN-TaoAvatar | 本地 MNN (NNR+A2BS) | 3D 数字人渲染+口型同步 | `/sdcard/iptv-agent-models/taoavatar/` |
| DeepSeek/Qwen LLM | HTTP SSE | 通用问答 | `config.properties` (base_url/api_key/model) |
| 天气/新闻/汇率/股票 | HTTP | 实时数据 | 各 Provider 内 |

---

## 八、配置文件

| 文件 | 用途 |
|------|------|
| `assets/config.properties` | LLM API 配置、门控参数、降音参数 |
| `assets/intents_local.json` | 意图路由规则、槽位提取、业务域定义 |
| `/sdcard/iptv-agent-models/tts/user_lexicon.txt` | TTS 多音字词典（热更新） |

---

## 九、关键设计决策

1. **Activity 为中心**：无 DI 框架，`MainActivity` 手动编排所有模块。各模块通过接口回调通信。
2. **回调驱动**：无 EventBus/LiveData，模块间通过接口回调链传递数据。
3. **NPU 串行化**：ASR NPU 推理用 Mutex 保证同一时刻只有一个任务（防止 NPU 冲突）。
4. **TTS 双引擎**：TtsEngineWrapper 优先 Bert-VITS2-MNN，不存在则用 SherpaOnnxTts。TtsOrchestrator 管理中文（VITS MeloTTS）+ 英文（Piper Lessac）双路并行。
5. **唤醒门控**：三态 FSM 控制音频流，IDLE 态只做轻量 VAD 不走 NPU，省电。
6. **回声门控**：基于参考信号能量 + 麦克风-参考相关性，防止 TTS/媒体播放被 ASR 识别。
7. **连续对话**：TTS 结束后 8 秒窗口，用户无需唤醒词可继续对话。
8. **流式 LLM**：SSE 增量文本 → 实时分句 → TTS 并行合成，降低首句延迟。

---

## 十、已知架构问题（优化方向）

1. **MainActivity 过大** (~2170行)：ASR/TTS/数字人/路由/UI 全部集中，职责不清。
2. **engine/ 包冗余**：早期抽象，运行时未使用，可清理。
3. **DigitalHumanController 冗余**：已被 TaoAvatarController 替代，代码仍在。
4. **硬编码路径**：所有 sdcard 模型路径为硬编码常量，无配置文件机制。
5. **无 DI**：模块间手动连接，测试困难，替换实现需改多处。
6. **回调嵌套**：深层回调链，异常传播和生命周期管理复杂。
7. **config.properties 含 API Key**：密钥管理不安全（应在 buildConfigField 或本地加密存储）。

---

## 十一、构建与运行

```bash
# 构建
cd iptv-edge-agent
./gradlew assembleDebug

# 安装
adb install app/build/outputs/apk/debug/app-debug.apk

# 推送模型资源（见 机顶盒模型资源推送清单.md）
./deploy_models.sh
```

---

*最后更新：2026-04-16*
