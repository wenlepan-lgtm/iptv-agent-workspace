#!/usr/bin/env python3
"""
sherpa-onnx + SenseVoiceSmall (INT8) 语音识别脚本

用法: python run_asr_sensevoice.py <模型目录> <wav文件1> [wav文件2]

示例:
  python run_asr_sensevoice.py ./sensevoice-model record_raw.wav
  python run_asr_sensevoice.py ./sensevoice-model record_raw.wav record_voice_comm.wav

模型下载:
  git clone https://www.modelscope.cn/poloniumrock/SenseVoiceSmallOnnx.git sensevoice-model
"""

import sys
import os
import time

try:
    import sherpa_onnx
except ImportError:
    print("错误: 未安装 sherpa-onnx")
    print("请运行: pip install sherpa-onnx")
    sys.exit(1)


def create_sensevoice_recognizer(model_dir):
    """创建 SenseVoice 识别器"""
    model_path = os.path.join(model_dir, "model.int8.onnx")
    tokens_path = os.path.join(model_dir, "tokens.txt")

    if not os.path.exists(model_path):
        print(f"错误: 模型文件不存在: {model_path}")
        sys.exit(1)
    if not os.path.exists(tokens_path):
        print(f"错误: tokens 文件不存在: {tokens_path}")
        sys.exit(1)

    # 检查文件大小
    model_size = os.path.getsize(model_path)
    if model_size < 100_000_000:  # 小于 100MB 可能是 LFS 指针
        print(f"警告: 模型文件大小 ({model_size} bytes) 偏小，可能不是实际模型")
        print(f"请确保已运行 git lfs pull 下载完整模型")

    print(f"使用本地模型: {model_dir}")
    print(f"  模型: {model_path} ({model_size / 1024 / 1024:.1f} MB)")
    print(f"  Tokens: {tokens_path}")

    # 使用官方推荐的 from_sense_voice 类方法创建识别器
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=model_path,
        tokens=tokens_path,
        use_itn=False,    # 不使用逆文本标准化
        debug=False,
    )
    return recognizer


def recognize_audio(recognizer, wav_file):
    """识别音频文件"""
    if not os.path.exists(wav_file):
        print(f"错误: 文件不存在: {wav_file}")
        return None

    try:
        import soundfile as sf
    except ImportError:
        print("错误: 未安装 soundfile")
        print("请运行: pip install soundfile")
        return None

    # 使用 soundfile 读取音频 (官方推荐)
    audio, sample_rate = sf.read(wav_file, dtype="float32", always_2d=True)
    audio = audio[:, 0]  # 只使用第一个声道

    duration = len(audio) / sample_rate

    print(f"\n识别文件: {wav_file}")
    print(f"  采样率: {sample_rate} Hz")
    print(f"  声道数: 1 (单声道)")
    print(f"  时长: {duration:.2f} 秒")

    # 创建音频流并识别
    stream = recognizer.create_stream()
    start_time = time.time()
    stream.accept_waveform(sample_rate, audio)
    recognizer.decode_stream(stream)

    # 获取结果
    elapsed = time.time() - start_time
    text = stream.result.text.strip()

    print(f"\n识别结果:")
    print(f"  文本: {text}")
    print(f"  耗时: {elapsed:.2f} 秒")
    print(f"  实时率: {duration / elapsed:.1f}x")

    return text


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("\n错误: 参数不足")
        print("\n使用方法:")
        print("  python run_asr_sensevoice.py <模型目录> <wav文件1> [wav文件2]")
        print("\n示例:")
        print("  python run_asr_sensevoice.py ./sensevoice-model record_raw.wav")
        print("  python run_asr_sensevoice.py ./sensevoice-model record_raw.wav record_voice_comm.wav")
        print("\n模型目录应包含:")
        print("  - model.int8.onnx (约 230MB)")
        print("  - tokens.txt")
        sys.exit(1)

    model_dir = sys.argv[1]
    wav_files = sys.argv[2:]

    print("=" * 60)
    print("sherpa-onnx + SenseVoiceSmall (INT8) 语音识别")
    print("=" * 60)

    # 创建识别器
    recognizer = create_sensevoice_recognizer(model_dir)

    # 识别每个音频文件
    results = {}
    for wav_file in wav_files:
        text = recognize_audio(recognizer, wav_file)
        if text:
            results[wav_file] = text
        print("-" * 60)

    # 打印对比总结
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("识别结果对比")
        print("=" * 60)
        for wav_file, text in results.items():
            print(f"\n{wav_file}:")
            print(f"  {text}")


if __name__ == "__main__":
    main()
