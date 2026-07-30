# 74 - MT9679 会议屏 静态音频能力证据包 (L1_STATIC_CAPABILITY)

> Commit 3 / §二。独立 adb 只读采集 (2026-07-30)，复核 Kimi 静态探测结论 (反 inflation)。
> 每条结论标注 CONFIRMED / INFERRED / NOT_CONFIRMED。**L1 证据不得升格为 L2-L4 结论**。
> 设备: .113 = mtk9679/mensa_an64/merak, Android 14 (IFPD 会议屏)。

---

## 1. 设备身份 (CONFIRMED)
- `ro.product.device=mensa_an64`, `ro.board.platform=merak`, `ro.build.version.release=14`
- IFPD 服务活跃: `com.ifpdos.mcu.IMcuDataHelper`, `com.ifpdos.udi.service.{device,mcu,network,pc.audio,pc.system,pc.network}` (service list 实查)

## 2. 声卡 (/proc/asound/cards — CONFIRMED)
```
0 [vocsndcard    ]: vocsndcard - vocsndcard          ← MTK 专用语音声卡 (HAL AEC 通路)
1 [Loopback      ]: Loopback
2 [mtkaloopsndcard]: mtkaloopsndcard
3 [mtktvsndcard  ]: MTK-TV-SOUND-ASOC-AUDIO-DRV      ← 主 TV 声卡
4 [SYP48M        ]: USB-Audio - SYP-48M              ← ⚠️ USB 摄像头麦, 在线作 USB-Audio 采集
```
- /proc/asound/pcm: `00-00 voc-platform : capture 1` (vocsndcard 有 capture PCM); `04-00 USB Audio : capture 1` (USB 麦 capture PCM)。
- **CONFIRMED**: vocsndcard 存在并有 capture PCM。**CONFIRMED**: SYP-48M USB 麦作为独立 USB-Audio 采集设备在线。
- **INFERRED (不得写 CONFIRMED)**: vocsndcard 对应物理麦阵列 (4ch index mask 声明见 §5，但物理麦数量未拆机确认)。

## 3. AEC / 语音处理 HAL 库 (CONFIRMED)
`/vendor/lib64/` 实查:
- `libmtk_tinyalsa_aec_plugin.so` (57848B) ← tinyalsa 级 AEC 插件
- `libmtk_tinyalsa_mic_plugin.so` / `_format_converter_plugin.so` / `_link_plugin.so`
- `libtinyalsa-master.so`
- 主 HAL `audio.primary.merak.so` 含 `voc_hw_aec` 类 (strings 实查):
  - `open AEC_MIC plugin c%d d%d` / `open AEC_REF plugin c%d d%d` (+ fail 变体) ← **双输入 (麦+参考) AEC 通路**
  - lifecycle 符号: `voc_hw_aec::{init_plugin,close_plugin,get_property,read,start,stop,aec_task}` (完整)
  - 控制旋钮: `aecEn`, `agcSetAecRefAmp`, `vendor.audio.aec.CmftNsG` (舒适噪声增益)
- **CONFIRMED**: HAL 具备完整 voc_hw_aec 实现 + AEC_MIC/AEC_REF 双输入 + 运行时开关。
- **NOT_CONFIRMED**: 当前 AudioRecord 是否真的打开了 vocsndcard/AEC_MIC/AEC_REF (这是 L2, 待报告75/76)。

## 4. Echo Reference 端点 (primary_audio_policy_configuration.xml — CONFIRMED)
```xml
<mixPort name="echo reference" role="sink" maxOpenCount="2" maxActiveCount="2">
  <profile format="AUDIO_FORMAT_PCM_16_BIT" .../>   <!-- 16kHz/48kHz 立体声 -->
<devicePort tagName="Echo Reference In" type="AUDIO_DEVICE_IN_ECHO_REFERENCE" role="source">
<route type="mix" sink="echo reference" sources="Echo Reference In"/>
```
- **CONFIRMED**: echo reference 端点已配置。
- **NOT_CONFIRMED**: 当前扬声器/HDMI/USB 输出是否真接入该参考回路 (L2/L4, 需运行时双讲实测)。

## 5. 软件 AEC (audio_effects.xml — CONFIRMED 可用, NOT_CONFIRMED 已启用)
- `/vendor/etc/audio_effects.xml` 注册 `libaudiopreprocessing.so` (AOSP WebRTC 预处理), 含 AEC/AGC/NS effect UUID:
  - `aec` uuid `bb392ec0-8d4d-11e0-a896-0002a5d5c51b`
  - `ns` / `agc` 标准 UUID
- **CONFIRMED**: `AcousticEchoCanceler.isAvailable()` 预期为 true (库已注册)。
- **NOT_CONFIRMED**: 当前 Session 是否 enable (架构 §3.3: isAvailable≠enabled)。未在 `<preprocess>` 自动绑定 → 需 App 主动 create (待报告75 MicCapabilityProbe 实测 effect enable 状态)。
- 4 通道: policy 含 `AUDIO_CHANNEL_INDEX_MASK_4` 声明 (Kimi 探测), **INFERRED** (声明≠App 能取得 4 路 Raw, 待报告81)。

## 6. ⚠️ 关键风险: USB 麦路径与 HAL AEC 的关系 (INFERRED → 必须 L2/L4 实测)
- SYP-48M USB 摄像头作为 USB-Audio 采集设备在线 (`04-00 USB Audio : capture 1`)。
- **INFERRED**: HAL AEC (vocsndcard 通路) **大概率不覆盖 USB-Audio 采集路径** (USB 麦走 USB HAL, 非 vocsndcard)。
- **影响**: 若"新麦克风"= SYP-48M USB 麦 (或新插的 USB 麦), 则 voc_hw_aec 与其无关, AEC 只能靠软件 AEC 或 USB 麦自带 DSP。**这是决定整个 AEC 验证方向的前提, 必须先在 L2 确认 AudioRecord 实际 routed 到哪张声卡。**
- 待报告75 MicCapabilityProbe 的 `getRoutedDevice()` / `getActiveMicrophones()` 实测坐实。

---

## 7. L1 结论汇总
| 子能力 | L1 状态 |
|---|---|
| vocsndcard 语音声卡 + capture PCM | **CONFIRMED** |
| HAL voc_hw_aec + AEC_MIC/AEC_REF 双输入 + 控制旋钮 | **CONFIRMED** |
| Echo Reference 端点配置 | **CONFIRMED** |
| 软件 WebRTC AEC 库注册 (可用) | **CONFIRMED** |
| IFPD 会议屏 + com.ifpdos 服务 | **CONFIRMED** |
| SYP-48M USB 麦在线作 USB-Audio 采集 | **CONFIRMED** (新增, Kimi 摘要低估) |
| 4 通道物理麦阵列 / App 能取 4ch Raw | **INFERRED** (声明级, 待 L2/L3) |
| HAL AEC 当前已启用 / 已接 echo ref / 适 KWS-ASR | **NOT_CONFIRMED** (L2-L4) |
| USB 麦路径是否被 HAL AEC 覆盖 | **NOT_CONFIRMED** (高优, 决定验证方向) |

**允许的 L1 结论**: "设备具备 HAL 级 AEC 能力的完整静态基础 (vocsndcard + voc_hw_aec + echo ref 端点 + 软件 AEC 库), 且有 USB 麦在线"。
**禁止的 L1 结论**: "AEC 已生效 / App 已用硬件 AEC / 新麦满足要求 / Barge-in 完成"。这些需 L2-L4。

## 8. 下一报告优先级
- **报告75 (MicCapabilityProbe)**: 决定性——AudioRecord 用 VOICE_COMMUNICATION/MIC/UNPROCESSED 时 `getRoutedDevice()` 到底落 vocsndcard 还是 USB SYP-48M。这一条直接决定后面所有 AEC 测试的方向。
- 报告76 (Route+AEC 运行时): AEC_MIC/AEC_REF 是否真开, echo ref 是否接当前输出。
