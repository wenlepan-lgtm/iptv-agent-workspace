#!/bin/bash
# RK3576 音频前端能力核验 ADB 命令清单
# 用途：核验主板/麦克风是否已提供 AEC/BF/NS/AGC
# 执行方式：adb connect <设备IP> 后，运行此脚本

echo "=========================================="
echo "RK3576 音频前端能力核验 - ADB 命令清单"
echo "=========================================="
echo ""

# 创建输出目录
OUTPUT_DIR="./adb-audio-verification-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUTPUT_DIR"
echo "输出目录: $OUTPUT_DIR"
echo ""

# Step 1: 确认输入设备与通道形态（BF 判断前置）
echo "【Step 1】确认输入设备与通道形态（BF 判断前置）"
echo "----------------------------------------"
echo "执行: cat /proc/asound/cards"
adb shell cat /proc/asound/cards > "$OUTPUT_DIR/01-asound-cards.txt"
cat "$OUTPUT_DIR/01-asound-cards.txt"
echo ""

echo "执行: cat /proc/asound/pcm"
adb shell cat /proc/asound/pcm > "$OUTPUT_DIR/02-asound-pcm.txt"
cat "$OUTPUT_DIR/02-asound-pcm.txt"
echo ""

echo "执行: dumpsys media.audio_policy"
adb shell dumpsys media.audio_policy > "$OUTPUT_DIR/03-audio-policy.txt"
echo "已保存到文件（内容较长）"
echo ""

echo "执行: dumpsys audio"
adb shell dumpsys audio > "$OUTPUT_DIR/04-audio-dumpsys.txt"
echo "已保存到文件（内容较长）"
echo ""

# Step 2: 检查系统是否提供 Android 平台级 AEC/NS/AGC effect
echo "【Step 2】检查系统是否提供 Android 平台级 AEC/NS/AGC effect"
echo "----------------------------------------"
echo "执行: dumpsys media.audio_flinger"
adb shell dumpsys media.audio_flinger > "$OUTPUT_DIR/05-audio-flinger.txt"
echo "已保存到文件（内容较长）"
echo ""

echo "执行: dumpsys media.audio_flinger --list-effects"
adb shell dumpsys media.audio_flinger --list-effects 2>/dev/null > "$OUTPUT_DIR/06-audio-effects-list.txt" || echo "命令不支持或返回空"
if [ -s "$OUTPUT_DIR/06-audio-effects-list.txt" ]; then
    cat "$OUTPUT_DIR/06-audio-effects-list.txt"
else
    echo "（无输出或命令不支持）"
fi
echo ""

echo "执行: dumpsys media.audio_flinger --effects"
adb shell dumpsys media.audio_flinger --effects 2>/dev/null > "$OUTPUT_DIR/07-audio-effects-active.txt" || echo "命令不支持或返回空"
if [ -s "$OUTPUT_DIR/07-audio-effects-active.txt" ]; then
    cat "$OUTPUT_DIR/07-audio-effects-active.txt"
else
    echo "（无输出或命令不支持）"
fi
echo ""

# Step 3: 应用层日志（查看 AsrController 的 AEC/NS/AGC 启用状态）
echo "【Step 3】应用层日志（查看 AsrController 的 AEC/NS/AGC 启用状态）"
echo "----------------------------------------"
echo "提示：请先启动应用并进入录音状态，然后按 Ctrl+C 停止"
echo "执行: logcat -s AsrController:D | grep -E 'AEC:|NS:|AGC:'"
echo ""
echo "正在捕获日志（10秒后自动停止，或按 Ctrl+C 提前停止）..."
timeout 10 adb logcat -c 2>/dev/null
timeout 10 adb logcat -s AsrController:D | grep -E "AEC:|NS:|AGC:" > "$OUTPUT_DIR/08-asr-controller-effects.log" || true
if [ -s "$OUTPUT_DIR/08-asr-controller-effects.log" ]; then
    cat "$OUTPUT_DIR/08-asr-controller-effects.log"
else
    echo "（无日志输出，请确保应用已启动并进入录音状态）"
fi
echo ""

# 汇总信息提取
echo "【汇总】关键信息提取"
echo "----------------------------------------"
echo ""

# 检查 USB 音频设备
echo ">>> USB 音频设备信息："
grep -i "usb\|card\|device" "$OUTPUT_DIR/01-asound-cards.txt" | head -5 || echo "未找到 USB 设备信息"
echo ""

# 检查通道数
echo ">>> 输入通道数（channelMasks）："
grep -i "channel\|channels\|mask" "$OUTPUT_DIR/03-audio-policy.txt" | head -10 || echo "未找到通道信息"
echo ""

# 检查 AEC/NS/AGC effect
echo ">>> AEC/NS/AGC Effect 存在性："
if grep -qi "AcousticEchoCanceler\|EchoCanceler\|AEC" "$OUTPUT_DIR/05-audio-flinger.txt" "$OUTPUT_DIR/06-audio-effects-list.txt" "$OUTPUT_DIR/07-audio-effects-active.txt" 2>/dev/null; then
    echo "✓ 发现 AEC 相关标识"
else
    echo "✗ 未发现 AEC 相关标识"
fi

if grep -qi "NoiseSuppressor\|Noise.*Suppress\|NS" "$OUTPUT_DIR/05-audio-flinger.txt" "$OUTPUT_DIR/06-audio-effects-list.txt" "$OUTPUT_DIR/07-audio-effects-active.txt" 2>/dev/null; then
    echo "✓ 发现 NS 相关标识"
else
    echo "✗ 未发现 NS 相关标识"
fi

if grep -qi "AutomaticGainControl\|Gain.*Control\|AGC" "$OUTPUT_DIR/05-audio-flinger.txt" "$OUTPUT_DIR/06-audio-effects-list.txt" "$OUTPUT_DIR/07-audio-effects-active.txt" 2>/dev/null; then
    echo "✓ 发现 AGC 相关标识"
else
    echo "✗ 未发现 AGC 相关标识"
fi
echo ""

# 检查应用层启用状态
echo ">>> 应用层启用状态（AsrController）："
if [ -s "$OUTPUT_DIR/08-asr-controller-effects.log" ]; then
    grep -E "AEC:|NS:|AGC:" "$OUTPUT_DIR/08-asr-controller-effects.log" | tail -10
else
    echo "（无日志，请确保应用已启动并进入录音状态后重新运行此脚本）"
fi
echo ""

echo "=========================================="
echo "所有输出已保存到: $OUTPUT_DIR/"
echo "=========================================="
echo ""
echo "下一步："
echo "1. 查看 $OUTPUT_DIR/ 目录下的文件"
echo "2. 执行 TTS 自回灌测试（见研发计划文档 Step 3 实验 A）"
echo "3. 执行 barge-in 插话测试（见研发计划文档 Step 3 实验 B）"
echo ""
