你的判断正确。**现在不是P0–P4验收阶段，而是P0音频链路验收失败。**

之前报告本来就把“真机underrun、长句丢字、Playwright”等列为待办，却又继续推进后面的“完成报告”，这就是验收顺序失控。

## 当前应立即重新定级

| 项目                | 当前真实状态                     |
| ------------------- | -------------------------------- |
| 启动欢迎词本地播放  | **失败：播放中途停止**           |
| KWS唤醒寒暄本地播放 | **未证明稳定完成**               |
| 长句按标点分段      | **没有按架构正确落地**           |
| 分段边传边播        | **没有证明正常**                 |
| 终端流控            | **没有证明生效**                 |
| AudioTrack完整播放  | **失败或未闭环**                 |
| 长句无丢字          | **失败**                         |
| P0验收              | **不通过**                       |
| P1验收              | **不通过**                       |
| P2–P4               | 可以保留代码，但暂停宣布验收完成 |

现在不要让GLM继续扩后台、继续写P2/P3/P4报告。**先把音频核心链路做对。**

---

# 你架构里的三个硬要求

## 1. 欢迎词必须完全本地化

启动欢迎词必须：

```text
APK启动
→ 本地读取固定PCM/WAV
→ 本地AudioTrack完整播放
→ 播放完成后才进入后续状态
```

不得依赖：

- Gateway；
- WebSocket；
- GPU TTS；
- 网络；
- Server Session；
- 运行时TTS流。

欢迎词播放到一半消失，说明至少存在以下一种问题：

1. `AudioTrack`被提前`stop/release/flush`；
2. 进入`LISTENING`时统一清理播放资源；
3. 建立WebSocket或收到`session_active`后重置播放器；
4. Main TTS的`tts_start`抢占了本地AudioTrack；
5. 麦克风启动或Audio Route切换导致本地输出中止；
6. 本地WAV长度、PCM解析或播放完成计时错误；
7. `postDelayed(duration)`计算错误；
8. `resetPlaybackClientState()`误释放`localPromptTrack`；
9. `onPause/onStop/onAudioFocusChange`触发清理；
10. 本地Prompt和Main TTS的状态机没有真正隔离。

这不是音色或TTS模型问题，是**客户端生命周期与播放状态机问题**。

---

## 2. 唤醒寒暄也必须完全本地化

流程应当是：

```text
KWS_ACCEPTED
→ 创建Session/session_epoch
→ 本地播放对应语言Wake Prompt
→ 同时建立Gateway会话
→ 本地Prompt播放完成
→ 开始LISTENING或处理Wake+Command
```

中文唤醒播放中文本地资源，英文唤醒播放英文本地资源。

Gateway只需要知道：

```json
{
  "type": "local_prompt_complete",
  "prompt_id": "wake_ack_zh",
  "session_id": "...",
  "session_epoch": 12,
  "played_frames": 28438,
  "expected_frames": 28438,
  "result": "complete"
}
```

不能再由服务器实时TTS生成寒暄。

---

## 3. 长句必须按标点形成Segment，再流式传输

按你的产品规则：

```text
一个标点结束一个Segment
```

例如：

```text
健身房位于酒店三楼，
营业时间是早上六点到晚上十一点。
如需私人教练，
请提前联系健身中心预约。
```

形成：

```text
Segment 0：健身房位于酒店三楼，
Segment 1：营业时间是早上六点到晚上十一点。
Segment 2：如需私人教练，
Segment 3：请提前联系健身中心预约。
```

仅需排除不是句读符号的情况：

- 小数点；
- URL中的点；
- 英文缩写中的点；
- 日期和版本号内部符号。

不能再按20字、25字、Token数量或音频长度硬切。

---

# 正确的音频模型

```text
一个reply_text
=
一个tts_id
=
一个AudioTrack
=
多个标点Segment
=
每个Segment多个PCM Frame
```

关键点：

- Segment是语义与流控单位；
- Frame是传输单位；
- 一个回答只创建一个Main AudioTrack；
- Segment之间不能`stop/flush/release`；
- Segment 0播放时，Segment 1已经在生成或接收；
- 客户端按缓冲水位反馈Credit；
- 服务端按Credit继续发；
- 不能等Segment 0完全播放结束才开始合成Segment 1；
- 不能把所有Segment全部合成完才开始播放。

---

# 终端流控协议必须真正存在

建议最小协议：

## 服务端开始回答

```json
{
  "type": "tts_start",
  "tts_id": "tts_xxx",
  "session_epoch": 12,
  "segment_count": 4,
  "sample_rate": 22050,
  "frame_ms": 20
}
```

## Segment开始

```json
{
  "type": "segment_start",
  "tts_id": "tts_xxx",
  "segment_index": 0,
  "text": "健身房位于酒店三楼，",
  "text_sha256": "...",
  "expected_pcm_bytes": 123456
}
```

## PCM Frame

至少带：

```text
tts_id
session_epoch
segment_index
frame_index
global_frame_index
pts_samples
payload_length
first_frame
last_frame
```

## 客户端流控反馈

```json
{
  "type": "audio_flow_control",
  "tts_id": "tts_xxx",
  "session_epoch": 12,
  "queue_ms": 840,
  "queue_bytes": 37044,
  "credit_ms": 1200,
  "underrun_count": 0,
  "playback_head_frames": 44100
}
```

服务端规则：

```text
queue_ms低于低水位
→ 加快或继续发送

queue_ms达到高水位
→ 暂停读取或发送

队列恢复
→ 继续发送
```

建议初始水位：

```text
启动水位：600–1000ms
低水位：400ms
目标水位：1000–1500ms
高水位：2500ms
最大水位：5000ms
```

流控不是简单加`sleep(10ms)`。那不是流控，只是盲目节流。

---

# 今晚19点GLM只能做什么

你不在电脑前，今晚不能做主观听感验收，但仍然可以完成大量工程工作：

1. 修复本地欢迎词提前停止；
2. 修复本地Wake Prompt完整播放；
3. 重写标点Segmenter；
4. 重写Segment传输协议；
5. 实现客户端Credit流控；
6. 实现Segment预取；
7. 增加AudioTrack真实指标；
8. 运行自动化、Instrumentation、协议故障注入；
9. 构建候选APK；
10. Codex审核真实代码与日志；
11. 明天只留试听和真机最终确认。

今晚禁止：

- 覆盖正式APK；
- 切正式音频参数；
- 宣布丢字解决；
- 宣布P0/P1完成；
- 继续扩P2–P4；
- 只生成报告不修代码。

---

# 19点直接给GLM的提示词

```text
任务名称：

JOCTV-V3.1-P0-AUDIO-EMERGENCY-RECOVERY-20260730


当前用户真机验收结果：

1. 启动欢迎词播放到一半完全没声音。
2. 长句仍然存在丢字和断续。
3. 欢迎词没有稳定实现完整本地播放。
4. KWS唤醒后的寒暄没有稳定实现完整本地播放。
5. 长句没有按照“每个有效标点一个Segment”正确实现。
6. 标点Segment的边传边播和终端流控没有验收证据。
7. 因此P0和P1验收失败。
8. P2、P3、P4暂停扩展和完成声明。

本轮只解决音频主链路。

禁止继续开发后台、Semantic Router、P3或P4新功能。
禁止生成“全部完成”报告。
禁止未经用户试听覆盖正式APK。


==================================================
一、先纠正状态和架构
==================================================

更新《JOCTV Agent V3.1架构.md》。

明确写入：

P0_AUDIO_ACCEPTANCE = FAILED
P1_LOCAL_PROMPT_ACCEPTANCE = FAILED

原因：

- Welcome Prompt early stop。
- Wake Prompt本地链路未验证。
- Long TTS dropout仍存在。
- Punctuation Segment Protocol未正确落地。
- Client Flow Control未证明生效。

架构硬约束：

1. 启动欢迎词必须是APK本地固定资源。
2. 唤醒寒暄必须是APK本地固定资源。
3. 本地Prompt不依赖Gateway、WebSocket或实时TTS。
4. 一个reply_text只有一个tts_id。
5. 一个tts_id只有一个Main AudioTrack。
6. 每个有效标点结束一个Semantic Segment。
7. 每个Segment包含多个20ms PCM Frame。
8. Segment之间禁止stop、flush和release Main AudioTrack。
9. Client通过队列水位和Credit控制Server发送。
10. Segment N播放时必须预取Segment N+1。
11. tts_end只代表Server发送完成，不代表播放完成。
12. playback_complete只能在AudioTrack真实播放完后发送。

标点规则：

中文：
，。！？；：

英文：
, . ! ? ; :

排除：

小数点
URL
英文缩写
日期
版本号
IP地址
数字内部符号

禁止按字符数、Token数或固定长度硬切。


==================================================
二、Welcome Prompt提前中止根因调查
==================================================

对MainActivity及所有音频类全量搜索：

stop(
flush(
release(
pause(
resetPlaybackClientState
stopPlayer
cancelLocalPrompt
onPause
onStop
onDestroy
onAudioFocusChange
session_active
state_change
LISTENING
WebSocket reconnect
mic start
AudioRecord start

建立所有localPromptTrack释放点清单：

调用位置
触发条件
reason
调用栈
是否可能在播放完成前调用

为每次Local Prompt记录：

prompt_id
expected_pcm_bytes
expected_frames
written_bytes
written_frames
playback_head_position
marker_position
start_time
stop_time
complete_time
stop_reason
release_reason
audio_route
audio_focus_state
session_epoch
arbiter_state

硬验收：

expected_bytes == AudioTrack.write实际返回累计
expected_frames == 最终playback_head_position允许的误差范围
播放完成前stop次数=0
播放完成前release次数=0
播放完成前flush次数=0
early_stop_count=0

local_prompt_complete只能在：

playback_head_position >= expected_frames - tolerance

之后发送。

禁止只依赖postDelayed(duration)判断播放完成。
postDelayed只能作为超时保护，不能作为权威完成条件。


==================================================
三、本地Welcome Prompt独立链路
==================================================

启动欢迎词不能走：

Gateway
server TTS
tts_start
Main AudioTrack
reply_text
WebSocket

建立独立：

StartupPromptController
LocalPromptPlayer
LocalPromptPlaybackState

流程：

APP_READY
→ load local welcome PCM
→ validate SHA
→ create local AudioTrack
→ partial-write循环写满
→ play
→ monitor playback head
→ complete
→ release
→ 后续初始化

即使网络断开、Gateway未启动、TTS Worker未启动，也必须完整播放欢迎词。

建立自动测试：

1. 无网络；
2. Gateway关闭；
3. WebSocket连接中；
4. WebSocket重连；
5. 麦克风同时初始化；
6. Session创建；
7. Activity短暂失焦；
8. 音频Route变化；
9. 连续启动100次；
10. 启动后立即收到server消息。

100次播放：

early_stop=0
written mismatch=0
unexpected release=0


==================================================
四、本地Wake Prompt独立链路
==================================================

流程：

KWS_ACCEPTED
→ Server权威session_id/session_epoch创建或绑定
→ 立即本地播放对应语言Wake Prompt
→ 同时建立Gateway会话
→ Prompt完整播放
→ local_prompt_complete
→ LISTENING或Wake+Command后续处理

中文唤醒：
中文本地Prompt。

英文唤醒：
英文本地Prompt。

禁止服务器实时TTS生成寒暄。

测试：

中文100次
英文100次
Gateway断开
Gateway慢
重复唤醒
旧epoch
Prompt播放期间Main TTS到达
Prompt播放期间Session状态变化

不得发生Prompt中途停止。


==================================================
五、标点Segmenter重新实现
==================================================

输入：

reply_text

输出：

segments[]

每个有效标点结束一个Segment，标点保留在Segment文本中。

例如：

“健身房位于三楼，营业时间是早上六点到晚上十一点。如需教练，请提前预约。”

必须输出：

0 “健身房位于三楼，”
1 “营业时间是早上六点到晚上十一点。”
2 “如需教练，”
3 “请提前预约。”

测试至少1000条：

中文
英文
中英混合
小数
金额
时间
日期
URL
IP地址
酒店名称
房间号
版本号
引号
括号
省略号

验收：

normalize(join(segments)) == normalize(reply_text)
字符丢失=0
字符重复=0
标点丢失=0
固定字符数切分=0
数字误切=0
URL误切=0


==================================================
六、Audio Segment Protocol真实落地
==================================================

一个reply：

一个tts_id
一个Main AudioTrack
多个segment
多个frame

新增或核实消息：

tts_start
segment_start
audio_frame
segment_end
tts_end
audio_flow_control
playback_complete
playback_interrupted
tts_error

每个Frame必须带：

protocol_version
session_id
session_epoch
tts_id
segment_index
frame_index
global_frame_index
pts_samples
payload_length
flags

Client验证：

frame连续
segment连续
pts连续
payload长度
stale epoch
stale tts_id

任何缺口：

当前tts_id进入ERROR
清理队列
停止播放
不发送虚假playback_complete
记录完整错误


==================================================
七、真正的客户端流控
==================================================

删除用固定sleep冒充流控的设计。

客户端周期性发送：

queue_ms
queue_bytes
credit_ms
playback_head_frames
underrun_count
written_bytes
received_bytes

服务端按Credit控制发送。

建议初始参数：

START_THRESHOLD_MS=800
LOW_WATERMARK_MS=400
TARGET_BUFFER_MS=1200
HIGH_WATERMARK_MS=2500
MAX_BUFFER_MS=5000

Server发送原则：

credit_ms > 0
→ 继续发送

queue_ms >= HIGH_WATERMARK
→ 暂停读取或发送

queue_ms <= LOW_WATERMARK
→ 优先供给

不得无限缓存。
不得盲目每Frame sleep。


==================================================
八、Segment并行预取
==================================================

Segment N开始传输或播放时：

异步启动Segment N+1合成。

Segment N+1首批PCM达到READY后等待客户端Credit。

不得：

等Segment N全部播放完才开始Segment N+1合成。

不得：

多个Segment争抢同一个阻塞WebSocket recv。

可选方案：

每个Segment独立Worker连接
或
Worker单连接支持segment_id多路复用

必须保证：

不乱序
不重叠播放
一个tts_id
一个Main AudioTrack
stale取消
可回滚

自动测试目标：

segment gap P50 < 200ms
segment gap P95 < 400ms


==================================================
九、AudioTrack真实完整性
==================================================

对Main TTS记录：

expected_bytes
received_bytes
written_bytes（AudioTrack.write真实返回累计）
played_frames
partial_write_count
write_zero_count
write_error_count
underrun_before
underrun_after
underrun_delta
queue_min_ms
queue_max_ms
AudioTrack_create_count
stop_count
flush_count
release_count

硬验收：

一个tts_id AudioTrack_create_count=1
Segment间stop=0
Segment间flush=0
Segment间release=0
received_bytes=written_bytes
frame sequence gap=0
segment sequence gap=0

不能用输入buffer长度伪造written_bytes。


==================================================
十、自动化故障注入
==================================================

无需用户听感即可完成：

RTF：

0.6
0.8
1.0
1.2
1.5

网络和服务：

Frame延迟
Frame乱序
Frame缺失
Segment延迟
Worker慢
Gateway暂停
Client Credit=0
WebSocket重连
旧epoch
重复tts_end
tts_end提前
Main TTS在Local Prompt期间到达

每种至少100轮。

验收：

崩溃=0
错误播放完成=0
跨Session污染=0
Local Prompt提前停止=0
Main AudioTrack重复创建=0
协议未检测缺口=0


==================================================
十一、今晚候选与生产边界
==================================================

今晚只能：

修改候选代码
构建候选APK
运行单元测试
运行Instrumentation测试
运行协议模拟
运行独立Gateway/TTS端口
生成日志和报告
Codex审核

今晚禁止：

覆盖正式会议屏APK
切正式Gateway
切正式TTS Worker
宣称丢字解决
宣称Welcome Prompt解决
宣称P0/P1完成

所有听感项目标记：

USER_TEST_REQUIRED


==================================================
十二、Codex审核
==================================================

Codex必须独立读取：

V3.1架构
Android真实代码
Gateway真实代码
diff
测试代码
测试输出
AudioTrack日志
协议日志

Codex禁止只看GLM报告。

Codex必须确认：

1. Welcome Prompt完全本地，不依赖server。
2. Wake Prompt完全本地，不依赖server TTS。
3. Welcome Prompt播放完成前没有stop/release。
4. Wake Prompt播放完成前没有stop/release。
5. 每个有效标点一个Segment。
6. 没有固定字符数硬切。
7. 一个reply一个tts_id。
8. 一个tts_id一个Main AudioTrack。
9. Segment之间没有stop/flush/release。
10. Client Credit真实控制Server发送。
11. 固定sleep没有冒充流控。
12. Segment N播放时预取N+1。
13. written_bytes是真实AudioTrack.write返回值。
14. playback_complete在真实播放完后发送。
15. 故障注入能检测Frame/Segment缺失。
16. 候选APK未覆盖正式APK。
17. 未经用户测试没有宣布完成。

Codex输出：

CODE_BLOCKER
INTEGRATION_BLOCKER
REAL_DEVICE_BLOCKER
USER_TEST_REQUIRED
PRODUCTION_BLOCKER

不能只写Blocker=0。


==================================================
十三、停止条件
==================================================

今晚只有以下内容可以等待用户：

1. 真机扬声器听感；
2. 欢迎词实际听感；
3. Wake Prompt实际听感；
4. 长句实际丢字；
5. 会议屏真实AudioTrack表现。

其他代码、协议、测试和审核不允许做到一半停止。

不得因为：

已经构建APK
代码已写
测试脚本已准备
需要明天试听

就提前生成最终完成报告。


==================================================
十四、交付物
==================================================

输出：

P0-AUDIO-FAILURE-STATUS.md
WELCOME-LOCAL-PLAYBACK-ROOTCAUSE.md
WELCOME-LOCAL-PLAYBACK-FIX.md
WAKE-PROMPT-LOCAL-PLAYBACK-FIX.md
PUNCTUATION-SEGMENTER-1000-CASE.md
AUDIO-SEGMENT-PROTOCOL-IMPLEMENTATION.md
CLIENT-CREDIT-FLOW-CONTROL.md
SEGMENT-PREFETCH-IMPLEMENTATION.md
AUDIOTRACK-INTEGRITY-REPORT.md
AUDIO-FAULT-INJECTION-REPORT.md
CODEX-P0-AUDIO-REVIEW.md
TOMORROW-P0-AUDIO-USER-TEST.md

最终状态只能写：

CANDIDATE_READY_FOR_USER_TEST

不能写：

P0完成
P1完成
音频问题解决
丢字解决
欢迎词修复完成
```

---

# 明天你回来只测六件事

1. 断网启动APK，欢迎词是否完整；
2. 连续重启10次，欢迎词是否每次完整；
3. 中文唤醒10次，本地寒暄是否完整；
4. 英文唤醒10次，本地寒暄是否完整；
5. 外滩长回答连续10次，是否丢字、断续；
6. 查看日志中：
   - `underrun_delta`
   - `queue_min_ms`
   - `received_bytes`
   - `written_bytes`
   - `played_frames`
   - `segment_gap_ms`

只要这六项没过，P0/P1就继续判定失败。不要再被“代码已写、APK已构建、Codex Blocker=0”带偏。