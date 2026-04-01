# AEC / NS / AGC 启用与效果测试步骤

**目的**：启用 AEC、NS、AGC 后做对比测试，验收 v0.2 Task-2（raw / AEC / AEC+NS / AEC+NS+AGC 前后对比）。

---

## 1. 是否应该启用再测？—— 是

- 当前状态：硬件已确认（YD1076S 单声道），但 **AEC/NS/AGC 未启用**，效果未知。
- 下一步：**先启用，再在同一场景下录「无处理」vs「有处理」两段，对比听感与波形**，才能判断是否满足 ASR 前端要求。

---

## 2. 两种启用方式（建议先做 2.1）

### 2.1 应用层启用（推荐，无需改系统）

在录音时使用 **`AudioSource.VOICE_COMMUNICATION`**，系统会为该流挂上 AEC/NS/AGC（若策略和效果库已加载）。

- **优点**：不用改 `/vendor`，不用 root/关 dm-verity。
- **注意**：若系统里 `audio_effects.xml` 未配置 `voice_communication` 的 aec/ns/agc，则可能仍无效果，此时再考虑 2.2。

**用法**：用本项目里的 **`AudioRecordTest.java`**（或下面的脚本）录两段：

- 一段：`AudioSource.MIC`（或 `DEFAULT`）→ 视为 **raw**
- 一段：`AudioSource.VOICE_COMMUNICATION` → 视为 **AEC+NS+AGC**

同一场景（同一距离、同一环境、可同时放一点电视声），然后对比两段 WAV。

### 2.2 系统层启用（需要可写 /vendor 或重打包系统）

使用项目中的 **`audio_effects_enabled.xml`**，让系统加载 AEC/NS/AGC 并作用到 USB 输入或 `voice_communication` 流。

- 将 `audio_effects_enabled.xml` 推到 `/vendor/etc/audio_effects.xml`（需先 `mount -o remount,rw /vendor`，且设备已关 dm-verity 或已 root）。
- 重启音频服务或整机：`killall audioserver` 或 `reboot`。
- 再用 2.1 的方式录音，此时 `VOICE_COMMUNICATION` 应会真正挂上 AEC/NS/AGC。

---

## 3. 建议的测试流程（Task-2 验收）

1. **场景**：机顶盒接 YD1076S，同一位置；可选：电视/播放器放固定音乐或白噪声。
2. **录 raw**：用 `AudioSource.MIC` 录约 10–20 秒，保存为 `record_raw.wav`。
3. **录 processed**：用 `AudioSource.VOICE_COMMUNICATION` 录同样时长、同样环境，保存为 `record_voice_comm.wav`。
4. **对比**：
   - **听感**：人声是否更干净、回声/电视声是否变小、是否发糊/断字。
   - **波形**：看是否有明显削波、processed 是否整体更“稳”（AGC）。
5. **验收**（对齐 v0.2）：  
   - 回声场景下，processed 听感上回声明显降低；  
   - 人声清晰度不下降（尾音/轻辅音不被抹掉）。

若 2.1 下两段几乎无差别，再考虑 2.2 推配置后重测。

---

## 4. 录音脚本/代码

- 项目内 **`record_aec_compare.sh`**：在机顶盒上分别用 MIC 与 VOICE_COMMUNICATION 各录一段，并生成带 WAV 头的文件（见下方脚本说明）。
- **`AudioRecordTest.java`** 已支持 `raw` / `voice_comm` 参数，输出 `*_raw.wav` 与 `*_voice_comm.wav`，便于对比。

执行方式见脚本内注释或本文档第 5 节。

---

## 5. 快速命令小结

```bash
# 若用 Java 测试（需先编译并推到设备）
# 录 raw（无 AEC/NS/AGC）
adb shell "cd /data/local/tmp && app_process -Djava.class.path=... -- record raw"

# 录 processed（启用 AEC/NS/AGC）
adb shell "cd /data/local/tmp && app_process -Djava.class.path=... -- record voice_comm"

# 拉回对比
adb pull /sdcard/usb_mic_test_raw.wav .
adb pull /sdcard/usb_mic_test_voice_comm.wav .
```

若使用 **`record_aec_compare.sh`**，在机顶盒或通过 adb shell 执行一次即可得到两段 WAV（需设备上有 toybox/sox 或项目提供的可执行录音方式）。

---

**结论**：应该先启用 AEC/NS/AGC 再测效果；优先用应用层 `VOICE_COMMUNICATION` 做对比，满足 Task-2 验收后再决定是否改系统配置。
