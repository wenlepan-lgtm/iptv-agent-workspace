# 75 - MT9679 Android 麦克探测 (报告75, L2_RUNTIME + L3_APP_ACCESSIBLE)

> Commit 3 / §三。自建 AECProbe APK (com.aecprobe, Java+手动 aapt2/d8 构建, 不走 Gradle)
> 在 .113 (MT9679) 实跑, 复核 Kimi 结论 + 回答"新麦是哪个 / 有无 AEC"。
> AudioSource 可切 (COMM=VOICE_COMMUNICATION / REC=VOICE_RECOGNITION / UNP / MIC),
> 录时自带 1kHz 提示音当回声源 (经内置扬声器 AUDIO_DEVICE_OUT_SPEAKER 播放)。

## 1. 决定性发现: JOCTV 实际录的是 USB SYP-48M 摄像头麦, 不是内置麦
probe `getRoutedDevice()` + `getActiveMicrophones()` (REC/COMM/UNP 三组一致):
```
routed_device = 11 (AUDIO_DEVICE_IN_USB_DEVICE)  product = "USB-Audio - SYP-48M"
active_mics = 1; mic id=10 addr=card=4;device=0 location=3
```
- **这就是用户"换的新麦克风"**: USB 摄像头 SYP-48M。Android 自动把已连接的 USB 音频设备选为默认输入 → 内置麦被旁路。
- 即会议屏内置麦阵列(vocsndcard)当前**未被使用**。

## 2. 回答"是否有 AEC": 当前 USB 麦路径 **无可用 AEC**
| 层 | 结果 |
|---|---|
| HAL AEC (vocsndcard/voc_hw_aec) | **不适用** —— 走 USB HAL(card4), 不走 vocsndcard; VOICE_COMMUNICATION(source 7) 也仍路由到 USB SYP-48M, 不会切到内置麦触发 HAL AEC |
| 软件 AEC (AcousticEchoCanceler) | `isAvailable()=true` 但 **create 后 setEnabled(true) 不生效, getEnabled()=false** —— USB 麦 session 挂不上软件 AEC |
| NS / AGC | `isAvailable()=false` (该路径不可用) |
- 结论: **新麦(USB SYP-48M)当前没有可用的 AEC**, 也无法通过软件 AEC 补上。

## 3. USB 麦灵敏度差 (L3 旁证)
内置扬声器播 1kHz 提示音, USB 麦录到:
```
rms ≈ 20-31 / 32768  (≈ -60 ~ -64 dB),  peak ≈ 200-475
1kHz 能量仅略高于噪声底, 三组无明显差异(无 AEC 故 REC≈COMM)
```
- USB 摄像头麦对内置扬声器回声**几乎拾不到** (摄像头麦朝向用户/远离喇叭 + 灵敏度低)。
- 推论: 对 KWS("小智小智")/ASR 拾音也不利 (待用户人声实测确认, 本测无人声)。

## 4. 证伪 Kimi 静态推断的部分前提
- Kimi: "硬件 AEC 需 VOICE_COMMUNICATION 才启用" —— **前提是真用内置麦(vocsndcard)**。
- 实测: 即使用 VOICE_COMMUNICATION, 系统仍路由到 USB SYP-48M, **永远不进 vocsndcard 的 HAL AEC 通路**。
- 故 "VOICE_COMMUNICATION 触发硬件 AEC" 在当前 USB 麦下**不成立**。要测 HAL AEC 必须强制切回内置麦。

## 5. 成熟度
- L2_RUNTIME: USB 输入路径可创建 AudioRecord + 取 PCM (frames 全非零, restarts=0, 无死流) → **CONFIRMED**(USB 麦能录)。
- L3_APP_ACCESSIBLE: App 能录, 但软件 AEC 挂不上 → **PARTIAL**(可录, 无 AEC)。
- L4_ACOUSTICALLY_VALIDATED: **OPEN** (echo 太弱 + 无人声, 待强回声/人声实测)。
- AEC: **NOT_AVAILABLE (当前 USB 路径)**。

## 6. 内置麦在 USB 连接时不可达 (BUILTIN 模式实测)
probe 加 `setPreferredDevice(TYPE_BUILTIN_MIC)` 强制内置麦:
```
builtin_mic_not_found; available inputs=3   ← getDevices(GET_DEVICES_INPUTS) 返回 3 个, 无 BUILTIN_MIC
routed_device 仍 = USB SYP-48M
```
- audio_policy 静态列了 "Built-In Mic" (Port ID 2), 但 **USB 连接时框架不把它枚举给 app 的 getDevices** → `setPreferredDevice` 找不到目标, 无法强制切回内置麦。
- **结论**: USB SYP-48M 一旦连接, 就接管为唯一可达输入; 内置麦(及其 HAL AEC 通路)对普通 app 不可达。要用内置麦 + HAL AEC, **必须物理拔掉 USB 摄像头**, 让系统回落内置麦。

## 7. 最终结论 (回答用户"新麦是否有 AEC")
**当前 USB SYP-48M 麦 = 无可用 AEC, 且无法在 USB 连接下补救:**
- HAL AEC 不覆盖 USB 路径 (走 vocsndcard 才有);
- 软件 AEC 挂不上 USB session;
- 内置麦(HAL AEC 所在)在 USB 连接时不可达。
- 另: USB 摄像头麦灵敏度差 (-58 ~ -66 dB), 对 KWS/ASR 拾音不利。

**唯一拿到 AEC 的路径**: 拔掉 USB 摄像头 → 系统回落内置麦 → VOICE_COMMUNICATION 触发 HAL AEC (待拔掉后实测确认)。或保留 USB 麦但接受无 AEC + 低灵敏度。

## 8. 待补 (需用户操作)
1. **拔 USB 摄像头后重测**: 确认内置麦路由 + VOICE_COMMUNICATION 下 HAL AEC 残留回声效果 + 灵敏度。(用户物理拔, 我重跑 probe)
2. **强回声源**: 1kHz 经内置喇叭到麦太弱; 用视频/外放做强回声量化 AEC 残留。
3. **人声 KWS/ASR**: 用户现场喊"小智小智"+ asr_test_300, 确认拾音+识别达标。

## 附录: probe 三组 diag 摘要
| mode | source | routed | AEC_enabled | rms_db |
|---|---|---|---|---|
| REC | 6 VOICE_RECOGNITION | USB SYP-48M | false | -63.9 |
| COMM(swaec=0) | 7 VOICE_COMMUNICATION | USB SYP-48M | false | -66.2 |
| COMM(swaec=1) | 7 VOICE_COMMUNICATION | USB SYP-48M | **false(setEnabled 失败)** | -63.3 |
