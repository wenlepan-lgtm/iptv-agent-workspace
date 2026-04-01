# RK3576(Android14) NPU TTS 落地 + 音频前端能力核验（AEC/BF/NS/AGC）

**面向**：Cursor / Claude  
**工程**：iptv-edge-agent（RK3576 + Android 14）  
**目标**：① 探索并落地 RK3576 的 NPU TTS（可商用交付，可量产长期运行）；② 通过 ADB + 系统信息确认主板/USB 阵列麦克风是否已提供 AEC/BF/NS/AGC；若缺失，定义在系统里补齐的实现路径。

---

## 0. 目标与边界

### 目标

1. **探索并落地 RK3576 的 NPU TTS**（可商用交付，可量产长期运行）。
2. **通过 ADB + 系统信息确认**：主板/USB 阵列麦克风是否已经提供 AEC / BF / NS / AGC；若缺失，定义我们在系统里补齐的实现路径。

### 硬约束（不可违背）

- **TTS 必须 NPU 主推理**（不能靠 CPU 长时间高负载顶上去）。
- **音频前端必须支持**：
  - TTS 播放时仍可识别用户插话（barge-in）
  - 电视背景存在时尽可能抑制（不要求降电视音量）
  - **AGC**：你说主板集成——我们先“核验是否存在”；如存在则不叠加；如不存在再评估是否需要“轻量 limiter”，默认不做软件 AGC。

---

## 1. NPU TTS 可落地路线（RK3576）

### 1.1 首选路线：Piper(VITS) Streaming + RKNN（迁移 Paroli 思路到 RK3576）

**核心判断**：目前最工程化、可控、可商用的 NPU TTS 路线，是把 Piper 的 VITS 做 Streaming 拆分，把“最重的 decoder”图固定化后上 RKNN/NPU。这个路线在 RK3588 上已有成熟工程实践：

- **Paroli**：C++ 的 Piper Streaming 实现，带（可选）RK3588 NPU 加速。
- 其作者对“为什么 decoder 更适合上 NPU、如何拆分与流式化”写过完整工程记录。
- 该思路的关键点也被整理成 PDF（encoder 仍不易 NPU，decoder 占大头可 NPU）。

你的芯片是 **RK3576**，不是 RK3588，但 Rockchip 的 RKNN 生态（toolkit/runtime/C API）对 RK3576 是覆盖的（Model Zoo 明确写支持 RK3576）。

#### 1.1.1 交付形态（建议）

- **Android App 内置 TTS Engine（JNI）**：
  - 文本 → 规范化/分句 → phoneme/ID → encoder（轻） → decoder（重，RKNN）→ PCM → AudioTrack
  - decoder 使用 **RKNN Runtime C API**（librknnrt.so）直接推理；JNI 做胶水与缓冲。
  - 支持多语种：优先用 **piper-voices**（声库/语言多）。

#### 1.1.2 研发任务拆分（Cursor 可直接排期）

**Task A：Piper 模型选型与“可 RKNN 化”评估**
- 选 2–3 个目标 voice（中文/英文优先；你要多语种后续扩）。
- 确认模型输入输出张量形状是否能“静态化/固定 chunk”。（Paroli 路线就是把 decoder 做成更静态的图。）

**Task B：ONNX → RKNN 转换流水线**
- 使用 **RKNN-Toolkit2** 进行 onnx 转 rknn，并做：
  - 量化策略（INT8/FP16，先以稳定为主）
  - 固定输入 shape（避免动态 shape 直接把你搞死）
  - 基准集对比：ONNX 输出与 RKNN 输出误差阈值
  - 参考 Rockchip 官方工具链与模型示例仓库（Model Zoo）。

**Task C：Android JNI 推理封装**
- `RknnTtsDecoder`（C++）：初始化/推理/释放、线程安全、内存复用（避免频繁 malloc/free）。
- Kotlin 层：TTS 队列 + 流式播放 + 可打断（barge-in 时 TTS 音量降低/暂停策略）。

**Task D：性能与温控验收（交付 KPI）**
- **KPI（建议你写死）**：
  - 首包延迟：< 300ms（欢迎语这种短句最敏感）
  - 实时系数 RTF：< 0.5（越小越稳）
  - 长时间（30min 循环播报）温度不触发降频/卡顿
  - 峰值内存：< 600MB（按你盒子 8G 很宽，但别放纵）

### 1.2 备选路线（不推荐做主线，但可做风险兜底）

只列“可商用/可控”的方向，不碰一堆 AGPL 的现成 rknn 项目（那是合规雷）。

- **“TTS 只做酒店话术”专用小模型**：
  训练/蒸馏一个小 voice（固定说话人）+ 限域文本（酒店场景），更容易压到 RKNN 友好的图结构与性能。
- **系统 TTS 引擎替换**：
  sherpa-onnx 有 TTS engine APK 体系，但它主要是 ONNXRuntime 路线，更多是“模型管理/引擎形态”参考，你的核心仍是 NPU 化。

---

## 2. 核验主板/麦克风是否已提供 AEC / BF / NS / AGC（ADB 取证方案）

你现在的 USB 阵列麦克风来自 **Haokai 系列**。其同系列（V3.1 4阵列）官方页面明确写了 Echo cancellation / Noise suppression / Automatic gain 等功能。

**但：营销文案≠你这台主板实际链路已启用，必须用 ADB 把证据链跑出来。**

### 2.1 你要的最终结论格式（必须输出）

对每一项给一个判定：

- **AEC**：已存在且生效 / 存在但未启用 / 不存在
- **NS**：已存在且生效 / 存在但未启用 / 不存在
- **BF**：已存在（硬件阵列输出已波束）/ 不存在（拿到的是 raw 多通道）/ 不确定
- **AGC**：已存在且生效 / 存在但未启用 / 不存在（默认不补）

并把证据贴出来（dumpsys/日志/实验结果）。

### 2.2 ADB 核验步骤（按顺序执行，别跳）

#### Step 1：确认输入设备与通道形态（BF 判断前置）

```bash
adb shell cat /proc/asound/cards
adb shell cat /proc/asound/pcm
adb shell dumpsys media.audio_policy
adb shell dumpsys audio
```

**看点**：
- USB 设备名/声卡号是否稳定
- 输入 profile 是否标了 channelMasks（1ch/2ch/4ch/6ch…）
- **如果系统只暴露 1ch 给 AudioRecord**：很可能 BF/混音在硬件/驱动里已经做掉了
- **如果暴露 4ch/6ch**：我们才有空间做软件 BF/AEC（也意味着你的前端研发量会上升）

#### Step 2：检查系统是否提供 Android 平台级 AEC/NS/AGC effect

```bash
adb shell dumpsys media.audio_flinger
adb shell dumpsys media.audio_flinger --list-effects 2>/dev/null
adb shell dumpsys media.audio_flinger --effects 2>/dev/null
```

**看点**：
- 是否存在类似：
  - `AcousticEchoCanceler`
  - `NoiseSuppressor`
  - `AutomaticGainControl`
- 是否挂载在你的 RecordThread 上（“存在但未启用”的常见原因：音频 session 没 attach effect）

**说明**：Android 有这些标准 effect 的 API，但是否真正可用取决于厂商实现。我们要的不是“类名存在”，而是它确实 attach 并起作用。

#### Step 3：通过“可复现实验”判断 AEC 是否真的生效（最硬证据）

**实验 A（TTS 自回灌测试）**：
1. 让 TTS 播放一段固定文本（音量正常）
2. 人不说话，录麦 10 秒
3. 把录到的 PCM 喂给 ASR（你现在 SenseVoice/Paraformer NPU）

- **如果 ASR 能稳定识别出 TTS 文本**：AEC 基本没生效（或参考信号没接入）
- **如果 ASR 输出接近空/很弱**：AEC 有效（至少对 TTS 自身回声有效）

**实验 B（barge-in 插话测试）**：
1. 播放 TTS "您好我是智能服务员…"
2. 播放过程中用户插话"我要退房"

- **观察**：ASR 是否能输出"我要退房"且不是被 TTS 文本淹没
- **这是你交付体验的关键 KPI**

**注意**：电视节目音频没有 reference 时，AEC 不可能"完全消掉电视声"；这时 NS + 阵列方向性 + 业务策略才是主力。

#### Step 4：确认 NS（噪声抑制）是否存在且有效

证据链两条：
- audio_flinger effect 确实 attach 了 NoiseSuppressor
- **实验**：录一段"空调/电视噪声 + 无人说话"的底噪，计算 RMS/频谱（你们内部脚本即可），开关 NS 前后有显著差异

#### Step 5：确认 BF（波束形成）是否已在硬件做掉

这个最容易被误判，所以要用"现象+通道+方向性"三证据：
- **通道证据**：系统暴露 1ch vs 4ch/6ch
- **方向性证据**：人从左/右/远近说话，能量与识别率变化是否明显（硬件 BF 常表现为"正前方更清晰"）
- **若你能拿到 raw 多通道**：再谈软件 BF（WebRTC/自研 MVDR）

#### Step 6：确认 AGC（自动增益）是否存在

- 同样从 effect 列表与 attach 关系查
- 再做"远近距离录音 RMS 分布"统计
- **默认策略**：不叠加软件 AGC（双 AGC 会把音频抽吸得像鬼片旁白）

### 2.3 麦克风回采接口接线方式（已按厂家指导完成）

**接线方式**（已按厂家指导接好）：
- **回采接口的两个正极**：分别并联到喇叭的 **LP**（左正）跟 **RP**（右正）
- **回采接口的两个负极**：分别并联到喇叭的 **LN**（左负）跟 **RN**（右负）

**目的**：这样可以屏蔽喇叭的声音被麦克风录入（硬件级 AEC 参考信号接入）。

**后续测试**：与 ChatGPT 一起测试 AEC/BF/NS/AGC，结果后续同步。

---

## 3. 如果发现主板/麦克风没有做好：系统内补齐的实现路线（只列你关心的 AEC/BF/NS）

你最终想要的体验其实是："TTS 播放时也能识别用户插话"。

这件事的最短路径是：**TTS reference AEC + NS**。BF 属于"锦上添花/阵列能力不足时才补"。

### 3.1 AEC（优先级最高）

- **方案**：WebRTC APM（AEC3）或同等级 AEC
- **关键**：把 TTS 的 PCM 作为 far-end reference 同步喂进去（你完全拿得到）
- **输出**：AEC 后的 near-end → VAD → ASR(NPU)

### 3.2 NS（第二优先级）

- **方案**：WebRTC NS（或更强的 DNN-NS，但要评估 NPU/CPU 负载）
- **目标**：抑制稳态噪声（空调、风噪），对电视节目只求"减轻"，别承诺"消除"。

### 3.3 BF（第三优先级，条件触发）

**触发条件**（满足其一才启动 BF 项目）：
- 系统暴露 raw 多通道，并且当前识别在"电视背景/远场"下仍达不到交付线
- 或你们阵列实际没有做 BF，且客诉"必须更远也能喊得动"

---

## 4. 输出物（Cursor 研发计划需要的交付件）

1. **《NPU TTS 技术选型报告》**
   - Piper Streaming + RKNN（主线）
   - 转换链路、风险点、KPI
   - 参考 Paroli/拆分思路证据

2. **《音频前端能力核验报告》**
   - ADB dumpsys 原文 + 判定
   - TTS 自回灌实验数据 + 判定
   - BF/通道形态结论
   - 麦克风宣称能力参考（同系列明确写了 AEC/NS/AGC）

3. **《实施计划（分 Task 可验收）》**
   - A/B/C/D（见 1.1.2）
   - 每个 Task 的 Done 标准（可测/可截图/可复现）

---

## 5. 你现在可以立刻让 Cursor 开工的"第一条命令清单"

（先把事实查清，不要在黑箱里猜）

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

**跑完把输出贴我，我就能把 AEC/NS/AGC 是否存在先给你"像审计报告一样"定性定量落锤；然后我们再把 TTS reference AEC 的接线方式写成你们工程里的接口契约（谁提供 reference PCM、采样率对齐、buffer 对齐、延迟补偿）。**

---

## 6. 总结

你吐槽那句"逼得上 NPU 才 99% 成功率"没毛病：在端侧语音这条赛道，NPU 才是交付级生产力，CPU 只是原型工具。现在我们就按"交付思维"干：

**先核验硬件能力 → 再补齐系统前端 → 最后把 NPU TTS 打进产线。**
