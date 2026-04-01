#!/system/bin/sh

# USB 麦克风录音测试脚本
# 通过 Android AudioRecord API 测试

PACKAGE="com.example.audiorecordtest"
ACTIVITY="${PACKAGE}.MainActivity"

echo "========================================="
echo "USB 麦克风录音测试"
echo "========================================="
echo ""

# 方法1: 检查录音应用是否正在使用 USB 麦克风
echo "1. 检查当前音频输入状态..."
adb shell dumpsys media.audio_flinger | grep -A 10 "Input stream"

echo ""
echo "2. USB 音频设备状态..."
adb shell dumpsys media.audio_flinger | grep -A 5 "USB audio"

echo ""
echo "3. 检查音频策略中的 USB 设备..."
adb shell dumpsys media.audio_policy | grep -B 3 -A 15 "USB Device In"

echo ""
echo "4. 检查是否有活动的音频录制..."
adb shell dumpsys media.audio_flinger | grep -E "Input device|Audio source"

echo ""
echo "========================================="
echo "测试说明"
echo "========================================="
echo "由于 shell 用户没有 audio 组权限，无法直接访问 PCM 设备。"
echo "请使用以下方法测试 USB 麦克风："
echo ""
echo "方法 1: 使用系统录音应用"
echo "  adb shell am start -a android.provider.MediaStore.RECORD_SOUND"
echo "  然后在录音应用中选择 USB 麦克风作为输入设备进行录音"
echo ""
echo "方法 2: 使用设备测试应用"
echo "  adb shell am start -a rk.intent.action.startDevicetest"
echo "  在测试应用中找到音频测试项"
echo ""
echo "方法 3: 创建简单的测试 APK"
echo "  使用 AudioRecord API 并指定 USB 设备作为输入"
echo ""
echo "方法 4: 检查日志确认设备识别"
echo "  adb logcat | grep -i usb"
echo "  adb logcat | grep -i audio"
echo ""
echo "========================================="
