# AEC/NS/AGC 测试方案（cctv1.ts 背景 + 环境噪音）

**目标**：在「背景视频声（cctv1.ts）+ 环境噪音」下说话，验证启用 AEC/NS/AGC 后 **ASR 近乎 100% 正确率**（或指令成功率达标）。

**背景音源**：机顶盒 `/sdcard/cctv1.ts`（可循环播放模拟电视声）。

---

## 1. 测试流程总览

```
1) 在机顶盒上开始播放 /sdcard/cctv1.ts（系统播放声 = 回声/背景）
2) 录一段「raw」（MIC，无 AEC/NS/AGC）→ 保存
3) 保持或重新播放 cctv1.ts，录一段「voice_comm」（AEC+NS+AGC）→ 保存
4) 拉取两段 WAV 到 PC，做波形/指标对比（analyze_audio.py）
5) 用同一套 ASR 对两段做识别，对比正确率 → 验收「近乎 100%」
```

---

## 2. 播放 cctv1.ts（背景视频声）

在机顶盒上让系统播放 cctv1.ts，使扬声器/HDMI 有声音输出（供 AEC 消除）。

**方式 A：adb 调起系统播放器**

```bash
# 用系统默认视频应用打开（若支持）
adb shell am start -a android.intent.action.VIEW -d file:///sdcard/cctv1.ts -t video/*

# 或指定包名（根据你机顶盒实际视频应用修改）
# adb shell am start -n <包名>/<Activity> -d file:///sdcard/cctv1.ts
```

**方式 B：机顶盒上手动打开「文件管理」→ 打开 cctv1.ts 播放**

**方式 C：用 ffplay / MediaPlayer 等可循环的（若已安装）**

播放后保持音量固定（建议 50%–70%），便于两次录音条件一致。

---

## 3. 录音（raw vs voice_comm）

同一环境：**cctv1.ts 在播 + 环境噪音 + 你说话**。先录 raw，再录 voice_comm，两段都说**同一批测试句**（如客房指令），便于后面对比 ASR 正确率。

### 3.1 方案 A：测试 APK（推荐）

1. 构建并安装项目里的 **AecTestApp**：
   ```bash
   cd AecTestApp && ./gradlew assembleDebug && adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```
2. 在机顶盒上**先开始播放 cctv1.ts**（见第 2 节），音量固定。
3. 打开「AEC 测试」应用，点击 **「录 Raw (MIC)」**：录约 15 秒，期间说 3–5 句固定话（如「打开灯光」「关闭空调」）。
4. 再点击 **「录 Voice Comm (AEC+NS+AGC)」**：再录 15 秒，**重复说同样几句**。
5. 文件保存在应用专属目录，应用内会显示完整路径。拉取示例：
   ```bash
   adb pull /sdcard/Android/data/com.example.aectest/files/Music/record_raw.wav .
   adb pull /sdcard/Android/data/com.example.aectest/files/Music/record_voice_comm.wav .
   ```

### 3.2 方案 B：AudioRecordTest.java（需编译 + app_process）

```bash
# 编译、打包、推送（在项目目录执行）
javac -cp $ANDROID_HOME/platforms/android-34/android.jar AudioRecordTest.java
$ANDROID_HOME/build-tools/34.0.0/d8 --output . AudioRecordTest.class  # 或 dx --dex
adb push AudioRecordTest.dex /data/local/tmp/

# 1) 先开始播放 cctv1.ts，然后录 raw
adb shell "CLASSPATH=/data/local/tmp/AudioRecordTest.dex app_process /system/bin AudioRecordTest raw"

# 2) 再录 voice_comm（可继续播放 cctv1.ts）
adb shell "CLASSPATH=/data/local/tmp/AudioRecordTest.dex app_process /system/bin AudioRecordTest voice_comm"
```

输出为 `/sdcard/usb_mic_test_raw.wav`、`/sdcard/usb_mic_test_voice_comm.wav`（若 Java 已改为写 WAV 并带正确文件名）。

---

## 4. 拉取文件并分析

```bash
# 拉取（若用 APK，文件在应用专属目录）
adb pull /sdcard/Android/data/com.example.aectest/files/Music/record_raw.wav .
adb pull /sdcard/Android/data/com.example.aectest/files/Music/record_voice_comm.wav .

# 或若用 Java 输出到 /sdcard/
adb pull /sdcard/usb_mic_test_raw.wav .
adb pull /sdcard/usb_mic_test_voice_comm.wav .

# 波形/指标对比
pip install numpy matplotlib scipy
python analyze_audio.py record_raw.wav record_voice_comm.wav
# 或
python analyze_audio.py usb_mic_test_raw.wav usb_mic_test_voice_comm.wav
```

查看输出的**指标对比表**和 **audio_comparison.png**，确认 processed 是否：回声/噪声降低、人声清晰、无严重削波。

---

## 5. 正确率验收（近乎 100%）

用**同一套 ASR**（如 sherpa-onnx SenseVoiceSmall 或你当前使用的引擎）对两段 WAV 做识别：

- 对 **record_raw.wav** 识别 → 得到文本 A  
- 对 **record_voice_comm.wav** 识别 → 得到文本 B  

**验收标准**：

- **语音识别正确率**：对「voice_comm」这段，识别结果与您说的测试句一致（字/词正确率或 CER 接近 0%，即「近乎 100% 正确」）。
- **对比**：通常 B（voice_comm）应明显好于 A（raw）；若 B 仍错误多，需继续排查 AEC/NS/AGC 是否生效或参数。

若你已有 sherpa-onnx 命令行或脚本，可写成：

```bash
# 示例（具体命令以你本地 ASR 为准）
./run_asr.sh record_voice_comm.wav   # 输出应为你说的话
```

用输出与「预期句子」逐句对比，统计正确句数 / 总句数 = 指令成功率，目标 **98%–99%**（v0.2）。

---

## 6. 建议测试句（客房指令）

便于复现和统计，可固定 5–10 句，例如：

- 打开灯光 / 关闭灯光  
- 打开空调 / 关闭空调  
- 调高音量 / 调低音量  
- 打开电视 / 关闭电视  
- 请打扫房间  

每段录音里按顺序说**相同句子**，后面 ASR 结果与预期逐句对比即可算正确率。

---

## 7. 小结

| 步骤 | 动作 |
|------|------|
| 1 | 机顶盒播放 `/sdcard/cctv1.ts`，音量固定 |
| 2 | 用 APK 或 Java 录「raw」+「voice_comm」各一段，说相同测试句 |
| 3 | `adb pull` 两段 WAV，`python analyze_audio.py` 对比 |
| 4 | 用 ASR 对两段识别，对 voice_comm 段验收「近乎 100% 正确率」 |

完成上述步骤即可在「背景视频 + 环境噪音」下验证 AEC/NS/AGC 效果，并用量化正确率验收。
