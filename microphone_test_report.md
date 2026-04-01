# USB 麦克风 (YD1076S) 测试报告

**测试日期**: 2026-02-04
**设备**: RK3576 机顶盒 (Android 14)
**麦克风**: YD1076S (Yundea 1076S USB Audio)

---

## 1. 硬件识别 ✓

USB 麦克风已被 Android 系统正确识别：

```bash
/proc/asound/cards:
  2 [Y1076S]: USB-Audio - Yundea 1076S
              Yundea Technology Yundea 1076S at usb-xhci-hcd.0.auto-1.3
```

**设备参数**:
- Card: 2, Device: 0
- 采样率: 16000 Hz
- 声道: MONO (单声道)
- 格式: S16_LE (16-bit PCM)
- 端点: 0x83 (ASYNC IN)

---

## 2. 软件配置 ✓

### AudioPolicy 配置
USB 麦克风已正确配置在音频策略中：

```
Port ID: 9; "USB Device In"
  Type: AUDIO_DEVICE_IN_USB_DEVICE
  Card: 2, Device: 0
  Sampling rates: 16000
  Channel masks: MONO
  Format: PCM 16-bit
```

### 录音线程状态
AudioFlinger 已成功创建录音线程：

```
Input thread: AudioIn_26
  Sample rate: 16000 Hz
  Channels: 1 (MONO)
  Format: PCM 16-bit
  Frames read: 200783 (有数据流入)
```

---

## 3. 功能验证

### 3.1 基础录音 ✓
- **状态**: 已验证
- **说明**: AudioFlinger 正在从 USB 麦克风读取音频数据
- **录音线程**: AudioIn_26 (tid 5648)
- **已读取帧数**: 200,783 帧

### 3.2 回声消除 (AEC) ⚠️
- **硬件支持**: YD1076S 硬件支持 AEC
- **软件状态**: 未启用
- **原因**: `/vendor/etc/audio_effects.xml` 中 AEC 配置被注释
- **解决方案**:
  1. 需要修改 `audio_effects.xml` 启用预处理效果
  2. 添加 `libaudiopreprocessing.so` 库引用
  3. 配置 AEC 效果用于 USB 输入设备

### 3.3 噪声抑制 (NS) ⚠️
- **硬件支持**: YD1076S 硬件支持 NS
- **软件状态**: 未启用
- **原因**: 同 AEC，需要修改配置文件

### 3.4 自动增益控制 (AGC) ⚠️
- **硬件支持**: YD1076S 硬件支持 AGC
- **软件状态**: 未启用
- **原因**: 同 AEC，需要修改配置文件

---

## 4. 预处理库状态

**库文件存在** ✓
```
/vendor/lib/soundfx/libaudiopreprocessing.so
  Size: 711,068 bytes
```

**可用效果**:
- AEC (Acoustic Echo Cancellation)
- NS (Noise Suppression)
- AGC (Automatic Gain Control)

---

## 5. 启用 AEC/NS/AGC 的方法

### 方法 1: 修改系统配置（需要禁用 dm-verity）

由于 `/vendor` 分区是只读的，需要：

1. **禁用 dm-verity** (需要刷机或修改 boot 参数)
2. **修改配置文件**:
   ```bash
   mount -o remount,rw /vendor
   cp /vendor/etc/audio_effects.xml /vendor/etc/audio_effects.xml.bak
   # 推送新配置文件
   adb push audio_effects_enabled.xml /vendor/etc/audio_effects.xml
   ```
3. **重启音频服务**:
   ```bash
   killall audioserver
   # 或重启设备
   reboot
   ```

### 方法 2: 应用层配置（推荐）

在应用代码中使用 `AudioRecord` 时配置预处理效果：

```java
// 创建 AudioRecord 时指定音频源
int audioSource = MediaRecorder.AudioSource.VOICE_COMMUNICATION;  // 启用 AEC/NS/AGC

AudioRecord audioRecord = new AudioRecord.Builder()
    .setAudioSource(audioSource)  // VOICE_COMMUNICATION 会启用预处理
    .setAudioFormat(audioFormat)
    .setBufferSizeInBytes(bufferSize)
    .build();
```

**音频源类型**:
- `VOICE_COMMUNICATION`: 启用 AEC + NS + AGC
- `VOICE_RECOGNITION`: 启用 NS + AGC
- `MIC`: 无预处理
- `CAMCORDER`: 仅 AGC

### 方法 3: 使用签名工具重新构建系统

使用项目提供的 **3576android14签名文件** 重新构建包含修改后配置的系统镜像。

---

## 6. 测试建议

### 6.1 验证麦克风硬件连接

确保 YD1076S 的回采接口已正确连接：
- **MX1.25 4P 接口**: 连接到机顶盒的音频输出（用于回声消除参考）
- **MX1.25 2P 接口**: 连接驻极体麦克风

### 6.2 测试 AEC 功能

1. 播放一段音乐/视频
2. 同时说话录音
3. 检查录音中是否还有播放的音乐声（应该被消除）

### 6.3 测试 NS 功能

1. 在有背景噪音的环境中录音
2. 检查录音的噪音水平

### 6.4 测试 AGC 功能

1. 在不同距离说话录音
2. 检查录音音量是否保持一致

---

## 7. 下一步行动

### 立即可行（不需要修改系统）
1. ✓ **验证硬件识别** - 已完成
2. ✓ **确认基础录音功能** - 已完成
3. **编写测试应用** - 使用 AudioRecord API 测试 AEC/NS/AGC

### 需要系统修改
1. **修改 audio_effects.xml** - 启用预处理效果
2. **验证 YD1076S 回采连接** - 确保 AEC 参考信号正确
3. **性能测试** - 验证 6 TOPS NPU 是否可以加速音频处理

### ASR/TTS 优化准备
1. ✅ USB 麦克风硬件已识别
2. ⚠️ AEC/NS/AGC 需要软件启用
3. **下一步**: 测试阿里 ASR/TTS 模型在 RK3576 上的性能

---

## 8. 附录

### A. 已创建的文件

1. `audio_effects_enabled.xml` - 启用 AEC/NS/AGC 的配置文件
2. `test_mic.sh` - 麦克风测试脚本
3. `AudioRecordTest.java` - AudioRecord 测试程序

### B. 参考命令

```bash
# 检查音频设备
adb shell cat /proc/asound/cards

# 检查 USB 麦克风流参数
adb shell cat /proc/asound/card2/stream0

# 查看 AudioFlinger 状态
adb shell dumpsys media.audio_flinger

# 查看 AudioPolicy 配置
adb shell dumpsys media.audio_policy

# 查看音频日志
adb logcat | grep -i audio
```

---

**总结**:
- ✅ USB 麦克风硬件已正确识别并工作
- ✅ **ADB 验证（2026-02-04）**：YD1076S 仅暴露 **单声道**（card2 仅 stream0，Channels: 1），无独立 Reference 通道到 Android；Task-1 按「单路 + 板内或应用层 AEC」执行。详见 `microphone_test_report_REVIEW.md`。
- ⚠️ AEC/NS/AGC 需要通过应用层配置或系统修改来启用
- 📋 建议优先测试 ASR/TTS 模型，同时准备启用音频预处理功能
