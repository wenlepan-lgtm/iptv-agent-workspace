# MT9679 会议屏 麦克风 / AEC / 采集路径 架构章节 (V3.1 新增)

> 本文件是 Commit 3 (JOCTV-V3.1-MT9679-MICROPHONE-AEC-RUNTIME-VERIFICATION) 的架构锚点，
> 对应 `7月30号白天工作1.md` §一。待主架构 md 的 §39 夜间基线提交后, 本章节并入主架构 md。
> 核心纪律: **静态能力 ≠ 运行时已启用**。禁止把 L1 证据写成 L4 结论。

---

## 1. 设备与背景

- 设备: `.113` = MT9679 (`mtk9679/mensa_an64/merak`, Android 14) 会议屏 (IFPD, com.ifpdos 服务)。
  即 memory 中的 SYP-20M 会议屏，身份已统一 (Kimi 静态探测 + adb getprop 双证)。
- 触发: 会议屏换了新麦克风，需验证新麦 + 设备 AEC 能否满足 JOCTV KWS/ASR。
- 当前正式采集路径: **无 AEC** (USE_AEC=0)。候选验证通过前不得切生产。

---

## 2. 四层成熟度模型 (只有 L4 才能宣布"满足 JOCTV 使用要求")

| 层级 | 含义 | 证据要求 | 当前状态 |
|---|---|---|---|
| **L1_STATIC_CAPABILITY** | 设备存在声卡/HAL/AEC 插件/effect/policy 配置 | getprop, /proc/asound, vendor so/xml, dumpsys | **部分 CONFIRMED** (Kimi 静态探测: vocsndcard + libmtk_tinyalsa_aec_plugin + voc_hw_aec + echo ref 端口 + 4ch index mask + WebRTC AEC 注册) |
| **L2_RUNTIME_AVAILABLE** | 运行时可创建相应 AudioRecord/AudioEffect/Route | dumpsys 运行态, PCM 节点占用, effect chain | **OPEN** (待 §四 运行时探测) |
| **L3_APP_ACCESSIBLE** | JOCTV APK 实际能用该路径并取得有效 PCM | AudioRecord 实际格式/Route, PCM 非零非死流 | **OPEN** (待 §三 MicCapabilityProbe) |
| **L4_ACOUSTICALLY_VALIDATED** | 真实扬声器回放/双讲/KWS/ASR 证明 AEC 有效 | ERLE/Residual Echo/双讲可懂度/KWS 唤醒率/ASR CER | **OPEN** (待 §五-§九) |

**结论锁**: 在 L4 达成前，任何 "会议屏已具备可用 AEC / 新麦满足要求 / Barge-in 完成" 的表述均为 **禁止**。

---

## 3. 七条强制原则

1. **硬件/HAL AEC 优先于 App 重复增加软件 AEC**。先验证 HAL AEC 是否生效，再决定是否叠加软件。
2. **禁止硬件 AEC 与 WebRTC 软件 AEC 未经 A/B 验证同时开启** (双重 AEC → 近端语音被削/金属音/音节吞噬/泵动/双讲失败)。
3. **`AcousticEchoCanceler.isAvailable()` 只表示效果可用，不表示当前 Session 已启用**。`isAvailable()==true` 不能作为无条件 `create()+enable()` 的依据。
4. **4 通道 Channel Index Mask 只表示 HAL 声明支持，不表示普通 App 一定能取得 4 路 Raw PCM**。若实际只取得 mono，只能写 "HAL 声明 4ch，App 当前取得 mono"。
5. **Echo Reference 端口存在，不表示当前 AudioTrack/HDMI/USB 输出一定接入参考回路**。HDMI/USB 输出时 AEC_REF 是否有效必须分别实测。
6. **采集路径最终必须由运行数据决定，不能根据设备配置文件推断**。
7. **当前正式路径仍保持无 AEC (USE_AEC=0)**，候选测试通过 (L4 + Codex + 用户真机 + 4h 稳定) 前不得切换生产；必须可一键回滚旧麦路径。

---

## 4. 四种 AEC 模式 (A/B/C/D, 独立候选 APK, 不覆盖正式 APK)

| 模式 | AudioSource | 软件 AEC | 目标 |
|---|---|---|---|
| A | MIC | 不启用 | 普通麦基线 |
| B | VOICE_COMMUNICATION | 不主动加 | 验证系统是否自动进 MTK 硬件/HAL AEC 路径 |
| C | VOICE_COMMUNICATION | 主动 create+enable AcousticEchoCanceler | 验证软件 AEC 单独/与 HAL 叠加结果 |
| D | UNPROCESSED | 不启用 | 更接近 Raw 的对照 |

可选 E/F (硬件 AEC 强制 OFF/ON): 仅当存在安全可回滚的 Vendor API/属性时才加，**不得做不可回滚的 Vendor 配置修改**。

**默认策略**: 若 VOICE_COMMUNICATION 已确认进入有效 HAL AEC 路径 (Mode B 达标)，**先不启用 App 软件 AEC**；仅当 HAL AEC 不足且 A/B 证明软件叠加有净收益时才同时启用。

---

## 5. 最终候选输出: MIC_CAPTURE_PROFILE

候选路径最终必须输出一个机器可读 profile，由 Feature Flag 隔离，默认 OFF:

```json
{
  "profile_id": "mt9679_voice_communication_hal_aec",
  "audio_source": "VOICE_COMMUNICATION",
  "sample_rate": 16000,
  "channels": 1,
  "hardware_aec": true,
  "software_aec": false,
  "noise_suppressor": false,
  "agc": false,
  "route": "builtin_mic",
  "echo_reference": "builtin_speaker",
  "kws_supported": true,
  "asr_supported": true
}
```

隔离 Flag (均默认 OFF):
- `MT9679_MIC_PROFILE_V31` / `MT9679_HAL_AEC_V31` / `ANDROID_SOFTWARE_AEC_V31`
- `MULTICHANNEL_CAPTURE_V31` / `AEC_DIAGNOSTIC_V31`

正式切换前必须: ①Codex 审核 ②候选 APK ③用户真机 ④4h 稳定 ⑤KWS+ASR 指标通过 ⑥可一键回滚。

---

## 6. 关键边界声明 (与 V3.1 半双工的关系)

- 当前正式 V3.1 仍为**半双工**。即使 AEC 有效 (L4 达标)，也**不能直接宣布 Barge-in 完成**。
- AEC 成功只意味着可进入 `BARGE_IN_RESEARCH_CANDIDATE` 状态，**不是生产开启全双工**。
- 全双工/Barge-in 是独立的 P0/P1 课题，需另外的架构与验收，不在本章节范围。

---

## 7. 本章节驱动的交付物编号 (74-85)

74 静态能力 / 75 Android Mic Probe / 76 Route+AEC 运行时 / 77 AEC ABCD / 78 声学指标 /
79 KWS+AEC / 80 ASR+AEC / 81 4ch 波束 / 82 4h 稳定 / 83 MIC PROFILE 候选 / 84 Codex 审核 / 85 用户清单。

每份报告的每条结论必须标注: **CONFIRMED / INFERRED / NOT_CONFIRMED**，且不得越层 (L1 证据不得升格为 L4 结论)。
