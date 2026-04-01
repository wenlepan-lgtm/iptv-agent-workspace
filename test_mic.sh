#!/system/bin/sh

# 麦克风测试脚本
# 测试 USB 麦克风 (Card 2) 的录音功能

CARD=2
DEVICE=0
RATE=16000
CHANNELS=1
FORMAT=S16_LE
DURATION=5
OUTPUT="/data/local/tmp/test_mic.raw"

echo "========================================="
echo "USB 麦克风测试 - YD1076S"
echo "========================================="
echo ""

# 检查设备节点
echo "1. 检查设备节点..."
if [ -e "/dev/snd/pcmC${CARD}D${DEVICE}c" ]; then
    echo "   ✓ 设备节点存在: /dev/snd/pcmC${CARD}D${DEVICE}c"
else
    echo "   ✗ 设备节点不存在!"
    exit 1
fi

# 检查权限
echo ""
echo "2. 检查设备权限..."
LS_OUTPUT=$(ls -l /dev/snd/pcmC${CARD}D${DEVICE}c)
echo "   $LS_OUTPUT"
if echo "$LS_OUTPUT" | grep -q "crw-rw----"; then
    echo "   ✓ 权限正常 (crw-rw----)"
else
    echo "   ! 权置可能异常"
fi

# 显示当前音频输入设备
echo ""
echo "3. 当前 Android 音频输入设备:"
dumpsys media.audio_flinger | grep -A 5 "USB audio" || echo "   USB audio 暂无活动流"

# 录音测试
echo ""
echo "4. 录音测试 (5秒)..."
echo "   开始录音... (请说话)"

# 使用 tinycap (如果存在) 或直接读取设备
if command -v tinycap >/dev/null 2>&1; then
    echo "   使用 tinycap 录音..."
    tinycap "$OUTPUT" -C "$CHANNELS" -r "$RATE" -b 16 -D "$CARD" -d "$DEVICE" &
    TINYPID=$!
    sleep "$DURATION"
    kill -TERM "$TINYPID" 2>/dev/null
    wait "$TINYPID" 2>/dev/null
else
    echo "   tinycap 不可用，跳过录音测试"
    echo "   (需要 MediaRecorder API 或 tinycap 工具)"
fi

# 检查录音结果
if [ -f "$OUTPUT" ]; then
    SIZE=$(ls -l "$OUTPUT" | awk '{print $5}')
    EXPECTED_SIZE=$((RATE * CHANNELS * 2 * DURATION))  # 16-bit = 2 bytes

    echo ""
    echo "5. 录音结果:"
    echo "   文件: $OUTPUT"
    echo "   大小: $SIZE bytes"
    echo "   期望: ~$EXPECTED_SIZE bytes"

    if [ "$SIZE" -gt 100 ]; then
        echo "   ✓ 录音成功!"
        echo ""
        echo "   拉取文件到本地分析:"
        echo "   adb pull $OUTPUT"
    else
        echo "   ✗ 录音文件太小，可能失败"
    fi
else
    echo ""
    echo "   未生成录音文件"
fi

# 音频参数总结
echo ""
echo "========================================="
echo "音频参数总结"
echo "========================================="
echo "Card:        $CARD (YD1076S)"
echo "Device:      $DEVICE"
echo "Sample Rate: $RATE Hz"
echo "Channels:    $CHANNELs (MONO)"
echo "Format:      $FORMAT"
echo "========================================="
