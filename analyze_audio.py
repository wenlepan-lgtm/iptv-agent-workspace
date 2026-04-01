#!/usr/bin/env python3
"""
音频对比分析工具
用于分析 AEC/NS/AGC 处理前后的音频差异

用法:
    python analyze_audio.py record_raw.wav record_voice_comm.wav
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
from scipy.io import wavfile
from scipy import signal

def read_audio(filename):
    """读取 WAV 文件"""
    try:
        rate, data = wavfile.read(filename)
        # 转换为 float 并归一化
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        return rate, data
    except Exception as e:
        print(f"错误: 无法读取文件 {filename}: {e}")
        return None, None


def calculate_metrics(data, rate):
    """计算音频指标"""
    # 能量
    energy = np.sum(data.astype(np.float32)**2) / len(data)

    # RMS (均方根)
    rms = np.sqrt(np.mean(data.astype(np.float32)**2))

    # 峰值
    peak = np.max(np.abs(data))

    # 动态范围 (dB)
    dynamic_range = 20 * np.log10(peak + 1e-10)

    # 零交叉率 (用于估计频率内容)
    zero_crossings = np.sum(np.abs(np.diff(np.sign(data)))) / (2 * len(data))

    # 信噪比估计 (简化版)
    # 找出最强的 10% 帧作为"信号"，其余作为"噪声"
    frame_size = int(rate * 0.05)  # 50ms 帧
    num_frames = len(data) // frame_size
    frame_powers = []
    for i in range(num_frames):
        frame = data[i*frame_size:(i+1)*frame_size]
        if len(frame) == frame_size:
            frame_powers.append(np.sum(frame**2))

    if frame_powers:
        frame_powers = np.array(frame_powers)
        # 排序，取前 10% 作为信号
        frame_powers.sort()
        signal_power = np.mean(frame_powers[-int(num_frames*0.1):])
        noise_power = np.mean(frame_powers[:int(num_frames*0.9)])
        snr_estimate = 10 * np.log10((signal_power + 1e-10) / (noise_power + 1e-10))
    else:
        snr_estimate = 0

    return {
        'energy': energy,
        'rms': rms,
        'peak': peak,
        'dynamic_range_db': dynamic_range,
        'zero_crossing_rate': zero_crossings,
        'snr_estimate_db': snr_estimate
    }


def plot_comparison(filename1, filename2, data1, data2, rate1, rate2):
    """绘制波形对比图"""
    fig, axes = plt.subplots(4, 1, figsize=(14, 10))

    # 时间轴
    time1 = np.arange(len(data1)) / rate1
    time2 = np.arange(len(data2)) / rate2

    # 1. 原始音频波形
    axes[0].plot(time1, data1, color='blue', alpha=0.7, linewidth=0.5)
    axes[0].set_title('Raw Audio (No AEC/NS/AGC)', fontsize=12, fontweight='bold')
    axes[0].set_ylabel('Amplitude')
    axes[0].grid(True, alpha=0.3)
    axes[0].set_ylim(-1, 1)

    # 2. 处理后音频波形
    axes[1].plot(time2, data2, color='green', alpha=0.7, linewidth=0.5)
    axes[1].set_title('Processed Audio (With AEC/NS/AGC)', fontsize=12, fontweight='bold')
    axes[1].set_ylabel('Amplitude')
    axes[1].grid(True, alpha=0.3)
    axes[1].set_ylim(-1, 1)

    # 3. 频谱对比
    # 计算功率谱密度
    f1, P1 = signal.welch(data1, rate1, nperseg=1024)
    f2, P2 = signal.welch(data2, rate2, nperseg=1024)

    axes[2].semilogy(f1, P1, color='blue', alpha=0.7, label='Raw', linewidth=1)
    axes[2].semilogy(f2, P2, color='green', alpha=0.7, label='Processed', linewidth=1)
    axes[2].set_title('Power Spectral Density Comparison', fontsize=12, fontweight='bold')
    axes[2].set_xlabel('Frequency (Hz)')
    axes[2].set_ylabel('PSD')
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)
    axes[2].set_xlim(0, 8000)  # 显示到 8kHz

    # 4. 短时能量对比
    frame_size = int(rate1 * 0.05)  # 50ms
    energy1 = []
    energy2 = []
    for i in range(0, len(data1) - frame_size, frame_size):
        energy1.append(np.sum(data1[i:i+frame_size]**2))
    for i in range(0, len(data2) - frame_size, frame_size):
        energy2.append(np.sum(data2[i:i+frame_size]**2))

    time_energy1 = np.arange(len(energy1)) * frame_size / rate1
    time_energy2 = np.arange(len(energy2)) * frame_size / rate2

    axes[3].plot(time_energy1, energy1, color='blue', alpha=0.7, label='Raw', linewidth=1)
    axes[3].plot(time_energy2, energy2, color='green', alpha=0.7, label='Processed', linewidth=1)
    axes[3].set_title('Short-time Energy Comparison', fontsize=12, fontweight='bold')
    axes[3].set_xlabel('Time (s)')
    axes[3].set_ylabel('Energy')
    axes[3].legend()
    axes[3].grid(True, alpha=0.3)

    plt.tight_layout()
    output_file = 'audio_comparison.png'
    plt.savefig(output_file, dpi=150)
    print(f"\n波形图已保存到: {output_file}")


def print_metrics_table(metrics1, metrics2):
    """打印指标对比表"""
    print("\n" + "="*80)
    print("音频指标对比表".center(80))
    print("="*80)
    print(f"{'指标':<25} {'Raw (无处理)':<20} {'Processed (有处理)':<20} {'变化':<15}")
    print("-"*80)

    def format_value(name, key, unit="", fmt=".6f"):
        v1 = metrics1[key]
        v2 = metrics2[key]
        diff = v2 - v1
        diff_pct = (diff / (v1 + 1e-10)) * 100 if v1 != 0 else 0

        diff_str = f"{diff:+.4f}"
        if abs(diff_pct) > 1:
            diff_str += f" ({diff_pct:+.1f}%)"

        print(f"{name:<25} {v1:<20.{fmt}f} {v2:<20.{fmt}f} {diff_str:<15} {unit}")

    format_value("能量 (Energy)", "energy")
    format_value("RMS", "rms")
    format_value("峰值 (Peak)", "peak")
    format_value("动态范围", "dynamic_range_db", "dB", ".2f")
    format_value("零交叉率", "zero_crossing_rate", "", ".6f")
    format_value("信噪比估计", "snr_estimate_db", "dB", ".2f")

    print("="*80)

    # 分析结论
    print("\n分析结论:")
    print("-"*80)

    if metrics2['energy'] < metrics1['energy'] * 0.9:
        print("✓ 能量下降: 可能是噪声/回声被抑制")
    elif metrics2['energy'] > metrics1['energy'] * 1.1:
        print("⚠ 能量上升: 可能是 AGC 增益生效")
    else:
        print("→ 能量基本持平")

    if metrics2['rms'] < metrics1['rms']:
        print("✓ RMS 下降: 整体音量降低（NS 可能在工作）")

    if abs(metrics2['dynamic_range_db']) < abs(metrics1['dynamic_range_db']):
        print("✓ 动态范围缩小: 可能是 AGC/压缩生效")

    if metrics2['snr_estimate_db'] > metrics1['snr_estimate_db']:
        snr_improvement = metrics2['snr_estimate_db'] - metrics1['snr_estimate_db']
        print(f"✓ 信噪比提升: {snr_improvement:.2f} dB")
    else:
        snr_diff = metrics2['snr_estimate_db'] - metrics1['snr_estimate_db']
        print(f"→ 信噪比变化: {snr_diff:+.2f} dB")

    print("="*80)


def main():
    if len(sys.argv) < 3:
        print("用法: python analyze_audio.py <raw_audio.wav> <processed_audio.wav>")
        print("\n示例:")
        print("  python analyze_audio.py record_raw.wav record_voice_comm.wav")
        sys.exit(1)

    filename1 = sys.argv[1]
    filename2 = sys.argv[2]

    print("="*80)
    print("音频对比分析工具".center(80))
    print("="*80)

    # 读取音频文件
    print(f"\n读取文件: {filename1}")
    rate1, data1 = read_audio(filename1)
    if rate1 is None:
        sys.exit(1)

    print(f"  采样率: {rate1} Hz")
    print(f"  时长: {len(data1) / rate1:.2f} 秒")
    print(f"  声道数: {1 if len(data1.shape) == 1 else data1.shape[1]}")

    print(f"\n读取文件: {filename2}")
    rate2, data2 = read_audio(filename2)
    if rate2 is None:
        sys.exit(1)

    print(f"  采样率: {rate2} Hz")
    print(f"  时长: {len(data2) / rate2:.2f} 秒")
    print(f"  声道数: {1 if len(data2.shape) == 1 else data2.shape[1]}")

    # 转换为单声道
    if len(data1.shape) > 1:
        data1 = np.mean(data1, axis=1)
    if len(data2.shape) > 1:
        data2 = np.mean(data2, axis=1)

    # 计算指标
    print("\n计算音频指标...")
    metrics1 = calculate_metrics(data1, rate1)
    metrics2 = calculate_metrics(data2, rate2)

    # 打印对比表
    print_metrics_table(metrics1, metrics2)

    # 绘制对比图
    print("\n生成波形对比图...")
    try:
        plot_comparison(filename1, filename2, data1, data2, rate1, rate2)
    except Exception as e:
        print(f"警告: 无法生成波形图: {e}")

    print("\n分析完成！")


if __name__ == "__main__":
    main()
