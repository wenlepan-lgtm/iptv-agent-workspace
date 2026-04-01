#!/system/bin/sh

# AEC/NS/AGC 录音对比测试脚本
#
# 用法:
#   ./record_aec_compare.sh            # 交互式
#   ./record_aec_compare.sh auto       # 自动模式（各录 10 秒）
#
# 说明:
#   脚本会在 /sdcard/ 下生成两个 WAV 文件：
#   - record_raw.wav         (无 AEC/NS/AGC)
#   - record_voice_comm.wav  (启用 AEC/NS/AGC)

set -e

OUTPUT_DIR="/sdcard"
RAW_FILE="$OUTPUT_DIR/record_raw.wav"
VOICE_FILE="$OUTPUT_DIR/record_voice_comm.wav"
DURATION=10  # 录音时长（秒）

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查设备连接
check_device() {
    log_info "检查设备连接..."
    if ! adb devices | grep -q "device$"; then
        log_error "未检测到 ADB 设备，请先连接设备"
        exit 1
    fi
    log_info "设备已连接 ✓"
}

# 使用 MediaRecorder 录音（需要通过 am 命令启动录音应用）
record_with_mediarecorder() {
    local output_file=$1
    local audio_source=$2
    local duration=$3

    log_info "开始录音: $output_file"
    log_info "音频源: $audio_source"
    log_info "时长: ${duration}秒"

    # 方法1: 使用 Intent 启动系统录音应用（需要用户手动操作）
    # adb shell am start -a android.provider.MediaStore.RECORD_SOUND

    # 方法2: 使用 shell 命令录制（需要 root 或 tinycap）
    # 由于 tinycap 不能直接指定 AudioSource，这里使用替代方案

    log_warn "需要安装测试 APK 或手动操作"
    log_info "请使用以下命令之一："
    echo ""
    echo "1. 如果已安装测试应用："
    echo "   adb shell am start -n com.example.audiorecordtest/.MainActivity -e mode $audio_source"
    echo ""
    echo "2. 使用系统录音应用（需手动选择音频源）："
    echo "   adb shell am start -a android.provider.MediaStore.RECORD_SOUND"
    echo ""
}

# 检查录音文件
check_recording() {
    local file=$1
    local expected_size=$((16000 * 2 * DURATION))  # 16kHz * 2 bytes * duration

    if adb shell "[ -f $file ]"; then
        size=$(adb shell "ls -l $file" | awk '{print $4}')
        log_info "录音文件已创建: $file (${size} bytes)"

        if [ "$size" -gt 100 ]; then
            log_info "录音成功 ✓"
            return 0
        else
            log_warn "录音文件可能不完整"
            return 1
        fi
    else
        log_error "录音文件未找到: $file"
        return 1
    fi
}

# 拉取录音文件
pull_files() {
    log_info "拉取录音文件到本地..."

    if adb shell "[ -f $RAW_FILE ]"; then
        adb pull "$RAW_FILE" .
        log_info "已拉取: $(basename $RAW_FILE)"
    fi

    if adb shell "[ -f $VOICE_FILE ]"; then
        adb pull "$VOICE_FILE" .
        log_info "已拉取: $(basename $VOICE_FILE)"
    fi
}

# 显示使用说明
show_usage() {
    cat << EOF
========================================
AEC/NS/AGC 录音对比测试
========================================

该脚本需要以下之一：

1. 已安装测试 APK（推荐）
   创建一个简单的 Android 应用，支持：
   - AudioSource.MIC (raw)
   - AudioSource.VOICE_COMMUNICATION (AEC+NS+AGC)

2. 使用系统录音应用
   手动操作录音，然后重命名文件

3. 使用 tinycap（需要 root）
   直接录制 PCM 并添加 WAV 头

请选择执行模式：
  1) 交互模式（逐步提示）
  2) 自动模式（各录 10 秒）
  3) 仅拉取已有文件
  4) 显示详细说明

EOF
}

# 显示详细说明
show_detailed_instructions() {
    cat << 'EOF'
========================================
详细测试说明
========================================

方法 1: 使用 AudioRecordTest.java
----------------------------------
编译并运行 Java 程序：

  # 编译
  javac -cp $ANDROID_HOME/platforms/android-*/android.jar AudioRecordTest.java

  # 打包
  $ANDROID_HOME/build-tools/*/dx --dex --output=AudioRecordTest.dex *.class

  # 推送
  adb push AudioRecordTest.dex /data/local/tmp/

  # 运行 - 录制 raw
  adb shell "CLASSPATH=/data/local/tmp/AudioRecordTest.dex app_process /system/bin AudioRecordTest raw"

  # 运行 - 录制 voice_comm
  adb shell "CLASSPATH=/data/local/tmp/AudioRecordTest.dex app_process /system/bin AudioRecordTest voice_comm"

  # 拉取文件
  adb pull /sdcard/usb_mic_test_raw.wav
  adb pull /sdcard/usb_mic_test_voice_comm.wav


方法 2: 使用 tinycap（需要 root）
----------------------------------
  # 录制 raw PCM
  adb shell "su 0 tinycap /data/local/tmp/test.raw -C 1 -r 16000 -b 16"

  # 转换为 WAV（在 PC 上）
  # 使用 sox 或 ffmpeg 添加 WAV 头

  # 使用 ffmpeg
  ffmpeg -f s16le -ar 16000 -ac 1 -i test.raw -ar 16000 -ac 1 test.wav


方法 3: 创建测试 APK
------------------------------------
创建一个简单的 Android 应用：

1. 在 Android Studio 中新建项目
2. 添加录音功能
3. 支持选择 AudioSource
4. 生成 APK 并安装


对比分析
--------
使用 Python 分析录音差异：

  python analyze_audio.py record_raw.wav record_voice_comm.wav


测试场景
--------
建议在以下场景下对比测试：

1. AEC 测试：播放音乐时说话
   - 预期：voice_comm 中音乐声被消除

2. NS 测试：嘈杂环境（如开窗、开空调）
   - 预期：voice_comm 中背景噪声降低

3. AGC 测试：不同距离说话（近/远）
   - 预期：voice_comm 中音量更稳定

========================================
EOF
}

# 主函数
main() {
    check_device

    local mode=${1:-"interactive"}

    case "$mode" in
        "auto")
            log_info "自动录音模式"
            log_warn "注意：需要安装测试 APK 或手动操作录音应用"
            ;;
        "pull")
            pull_files
            exit 0
            ;;
        "help"|"-h"|"--help")
            show_detailed_instructions
            exit 0
            ;;
        *)
            show_usage
            read -p "请选择 (1-4): " choice
            case $choice in
                1) mode="interactive" ;;
                2) mode="auto" ;;
                3) pull_files; exit 0 ;;
                4) show_detailed_instructions; exit 0 ;;
                *) log_error "无效选择"; exit 1 ;;
            esac
            ;;
    esac

    if [ "$mode" = "auto" ]; then
        log_info "========================================="
        log_info "步骤 1: 录制原始音频（无 AEC/NS/AGC）"
        log_info "========================================="
        log_info "请确保环境安静，不要播放音乐"
        read -p "按回车继续..."
        record_with_mediarecord "$RAW_FILE" "raw" "$DURATION"

        echo ""
        log_info "========================================="
        log_info "步骤 2: 录制处理音频（启用 AEC/NS/AGC）"
        log_info "========================================="
        log_info "请播放背景音乐或电视"
        read -p "按回车继续..."
        record_with_mediarecord "$VOICE_FILE" "voice_comm" "$DURATION"

        echo ""
        log_info "========================================="
        log_info "步骤 3: 拉取文件"
        log_info "========================================="
        pull_files

        echo ""
        log_info "========================================="
        log_info "测试完成！"
        log_info "========================================="
        log_info "使用以下命令分析音频："
        echo "  python analyze_audio.py $RAW_FILE $VOICE_FILE"
    fi
}

main "$@"
