import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;

/**
 * USB 麦克风录音测试程序（支持 AEC/NS/AGC 对比）
 *
 * 编译: javac -cp $ANDROID_HOME/platforms/android-*/android.jar AudioRecordTest.java
 *
 * 用法:
 *   AudioRecordTest              → 默认 MIC，输出 usb_mic_test.wav
 *   AudioRecordTest raw          → MIC（无 AEC/NS/AGC），输出 usb_mic_test_raw.wav
 *   AudioRecordTest voice_comm   → VOICE_COMMUNICATION（启用 AEC/NS/AGC），输出 usb_mic_test_voice_comm.wav
 *
 * 对比测试: 同一场景各录一段 raw 与 voice_comm，拉回 PC 听感/波形对比。
 */

public class AudioRecordTest {
    private static final String TAG = "AudioRecordTest";
    private static final int SAMPLE_RATE = 16000;
    private static final int CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO;
    private static final int AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT;
    private static final int BUFFER_SIZE_FACTOR = 4;
    private static final int RECORD_DURATION_MS = 10000; // 10 秒，便于对比

    public static void main(String[] args) {
        String mode = "raw";
        if (args != null && args.length > 0) {
            String arg = args[0].toLowerCase();
            if ("voice_comm".equals(arg) || "voice_communication".equals(arg)) {
                mode = "voice_comm";
            } else if ("raw".equals(arg) || "mic".equals(arg)) {
                mode = "raw";
            }
        }

        Log.i(TAG, "========================================");
        Log.i(TAG, "USB 麦克风录音测试 [mode=" + mode + "]");
        Log.i(TAG, "========================================");

        printAudioDeviceInfo();

        int usbDeviceId = findUsbMicrophone();
        if (usbDeviceId == -1) {
            Log.e(TAG, "未找到 USB 麦克风！");
            return;
        }
        Log.i(TAG, "找到 USB 麦克风，Device ID: " + usbDeviceId);

        int audioSource = "voice_comm".equals(mode)
                ? MediaRecorder.AudioSource.VOICE_COMMUNICATION
                : MediaRecorder.AudioSource.MIC;
        String outFileName = "voice_comm".equals(mode)
                ? "usb_mic_test_voice_comm.wav"
                : "usb_mic_test_raw.wav";

        testRecording(usbDeviceId, audioSource, outFileName);
    }

    /**
     * 打印音频设备信息
     */
    private static void printAudioDeviceInfo() {
        Log.i(TAG, "========================================");
        Log.i(TAG, "音频设备信息");
        Log.i(TAG, "========================================");

        AudioManager audioManager = (AudioManager) android.app.ActivityThread.currentApplication()
                .getSystemService(android.content.Context.AUDIO_SERVICE);

        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M) {
            android.media.AudioDeviceCallback callback = new android.media.AudioDeviceCallback() {
                @Override
                public void onAudioDevicesAdded(android.media.AudioDeviceInfo[] addedDevices) {
                    for (android.media.AudioDeviceInfo device : addedDevices) {
                        logAudioDeviceInfo(device);
                    }
                }
            };
            // Note: 在实际环境中需要注册回调
        }

        Log.i(TAG, "录音采样率: " + SAMPLE_RATE + " Hz");
        Log.i(TAG, "声道: MONO");
        Log.i(TAG, "格式: PCM 16-bit");
    }

    /**
     * 记录音频设备信息
     */
    private static void logAudioDeviceInfo(android.media.AudioDeviceInfo device) {
        Log.i(TAG, "----------------------------------------");
        Log.i(TAG, "设备 ID: " + device.getId());
        Log.i(TAG, "设备名称: " + device.getProductName());
        Log.i(TAG, "设备类型: " + getDeviceTypeName(device.getType()));

        int[] sampleRates = device.getSampleRates();
        if (sampleRates != null && sampleRates.length > 0) {
            StringBuilder sb = new StringBuilder("采样率: ");
            for (int rate : sampleRates) {
                sb.append(rate).append(" ");
            }
            Log.i(TAG, sb.toString());
        }

        android.media.AudioFormat[] channelCounts = device.getChannelMasks();
        if (channelCounts != null && channelCounts.length > 0) {
            Log.i(TAG, "声道配置: " + channelCounts.length + " 种");
        }
    }

    /**
     * 获取设备类型名称
     */
    private static String getDeviceTypeName(int type) {
        switch (type) {
            case android.media.AudioDeviceInfo.TYPE_BUILTIN_MIC:
                return "内置麦克风";
            case android.media.AudioDeviceInfo.TYPE_USB_DEVICE:
                return "USB 设备";
            case android.media.AudioDeviceInfo.TYPE_USB_HEADSET:
                return "USB 耳机";
            default:
                return "未知类型 (" + type + ")";
        }
    }

    /**
     * 查找 USB 麦克风设备
     */
    private static int findUsbMicrophone() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M) {
            android.media.AudioManager audioManager = (android.media.AudioManager)
                    android.app.ActivityThread.currentApplication()
                            .getSystemService(android.content.Context.AUDIO_SERVICE);

            android.media.AudioDeviceInfo[] devices = audioManager.getDevices(android.media.AudioManager.GET_DEVICES_INPUTS);

            Log.i(TAG, "----------------------------------------");
            Log.i(TAG, "输入设备列表:");
            for (android.media.AudioDeviceInfo device : devices) {
                logAudioDeviceInfo(device);

                if (device.getType() == android.media.AudioDeviceInfo.TYPE_USB_DEVICE ||
                    device.getType() == android.media.AudioDeviceInfo.TYPE_USB_HEADSET) {
                    return device.getId();
                }
            }
        }
        return -1;
    }

    /**
     * 测试录音功能
     * @param deviceId USB 设备 ID
     * @param audioSource MIC 或 VOICE_COMMUNICATION
     * @param outFileName 输出 WAV 文件名（含 .wav）
     */
    private static void testRecording(int deviceId, int audioSource, String outFileName) {
        Log.i(TAG, "========================================");
        Log.i(TAG, "开始录音 (" + RECORD_DURATION_MS + "ms, source=" + (audioSource == MediaRecorder.AudioSource.VOICE_COMMUNICATION ? "VOICE_COMMUNICATION" : "MIC") + ")");
        Log.i(TAG, "========================================");

        int minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT);
        int bufferSize = minBufferSize * BUFFER_SIZE_FACTOR;

        Log.i(TAG, "最小缓冲区大小: " + minBufferSize + " bytes");
        Log.i(TAG, "使用缓冲区大小: " + bufferSize + " bytes");

        AudioRecord audioRecord = null;
        FileOutputStream outputStream = null;

        try {
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M) {
                AudioRecord.Builder builder = new AudioRecord.Builder()
                        .setAudioSource(audioSource)
                        .setAudioFormat(new android.media.AudioFormat.Builder()
                                .setEncoding(AUDIO_FORMAT)
                                .setSampleRate(SAMPLE_RATE)
                                .setChannelMask(CHANNEL_CONFIG)
                                .build())
                        .setBufferSizeInBytes(bufferSize);
                if (getAudioDeviceInfo(deviceId) != null) {
                    builder.setPreferredDevice(getAudioDeviceInfo(deviceId));
                }
                audioRecord = builder.build();
            } else {
                audioRecord = new AudioRecord(
                        audioSource,
                        SAMPLE_RATE,
                        CHANNEL_CONFIG,
                        AUDIO_FORMAT,
                        bufferSize
                );
            }

            if (audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord 初始化失败！");
                return;
            }

            Log.i(TAG, "AudioRecord 初始化成功 ✓");

            // 开始录音
            audioRecord.startRecording();
            Log.i(TAG, "开始录音... (请说话)");

            String outputPath = Environment.getExternalStorageDirectory().getAbsolutePath()
                    + "/" + outFileName;
            outputStream = new FileOutputStream(outputPath);

            short[] buffer = new short[bufferSize / 2];
            long startTime = System.currentTimeMillis();
            long totalSamples = 0;
            int maxAmplitude = 0;

            // 先不写 WAV 头，等知道 totalSamples 后再写（先写占位头，最后回填）
            outputStream.write(new byte[44]);  // WAV header placeholder

            while (System.currentTimeMillis() - startTime < RECORD_DURATION_MS) {
                int read = audioRecord.read(buffer, 0, buffer.length);
                if (read > 0) {
                    byte[] byteBuffer = new byte[read * 2];
                    for (int i = 0; i < read; i++) {
                        byteBuffer[i * 2] = (byte) (buffer[i] & 0xFF);
                        byteBuffer[i * 2 + 1] = (byte) ((buffer[i] >> 8) & 0xFF);

                        int amplitude = Math.abs(buffer[i]);
                        if (amplitude > maxAmplitude) {
                            maxAmplitude = amplitude;
                        }
                    }
                    outputStream.write(byteBuffer);
                    totalSamples += read;

                    // 打印进度
                    if (totalSamples % (SAMPLE_RATE) == 0) {
                        Log.d(TAG, "已录制: " + (totalSamples / SAMPLE_RATE) + "s, 最大振幅: " + maxAmplitude);
                    }
                }
            }

            audioRecord.stop();
            outputStream.close();

            // 回写 WAV 头（44 字节）
            writeWavHeader(outputPath, (int) totalSamples, SAMPLE_RATE, 1);

            long duration = System.currentTimeMillis() - startTime;
            long expectedSamples = (SAMPLE_RATE * RECORD_DURATION_MS / 1000);

            Log.i(TAG, "========================================");
            Log.i(TAG, "录音完成！");
            Log.i(TAG, "========================================");
            Log.i(TAG, "文件: " + outputPath);
            Log.i(TAG, "时长: " + duration + " ms");
            Log.i(TAG, "采样数: " + totalSamples + " / " + expectedSamples);
            Log.i(TAG, "最大振幅: " + maxAmplitude);

            if (totalSamples >= expectedSamples * 0.9) {
                Log.i(TAG, "录音成功 ✓");
            } else {
                Log.w(TAG, "录音数据可能不完整");
            }

            if (maxAmplitude < 100) {
                Log.w(TAG, "警告: 最大振幅太小，可能没有声音输入");
            } else if (maxAmplitude > 30000) {
                Log.w(TAG, "警告: 最大振幅过大，可能存在削波失真");
            } else {
                Log.i(TAG, "音量正常 ✓");
            }

            Log.i(TAG, "========================================");

        } catch (Exception e) {
            Log.e(TAG, "录音测试失败: " + e.getMessage());
            e.printStackTrace();
        } finally {
            if (audioRecord != null) {
                audioRecord.release();
            }
            if (outputStream != null) {
                try {
                    outputStream.close();
                } catch (IOException e) {
                    e.printStackTrace();
                }
            }
        }
    }

    /** 写入 44 字节 WAV 头，便于 PC 播放与 analyze_audio.py 分析 */
    private static void writeWavHeader(String filePath, int numSamples, int sampleRate, int numChannels) {
        int byteRate = sampleRate * numChannels * 2;
        int dataSize = numSamples * numChannels * 2;
        int totalSize = dataSize + 36;
        try (RandomAccessFile raf = new RandomAccessFile(new File(filePath), "rw")) {
            raf.seek(0);
            raf.write("RIFF".getBytes());
            raf.write(intToLittleEndian(totalSize));
            raf.write("WAVE".getBytes());
            raf.write("fmt ".getBytes());
            raf.write(intToLittleEndian(16));           // fmt chunk size
            raf.write(shortToLittleEndian((short) 1));  // PCM
            raf.write(shortToLittleEndian((short) numChannels));
            raf.write(intToLittleEndian(sampleRate));
            raf.write(intToLittleEndian(byteRate));
            raf.write(shortToLittleEndian((short) (numChannels * 2)));  // block align
            raf.write(shortToLittleEndian((short) 16)); // bits per sample
            raf.write("data".getBytes());
            raf.write(intToLittleEndian(dataSize));
        } catch (IOException e) {
            Log.e(TAG, "writeWavHeader failed: " + e.getMessage());
        }
    }

    private static byte[] intToLittleEndian(int v) {
        return new byte[] { (byte)(v & 0xff), (byte)((v >> 8) & 0xff), (byte)((v >> 16) & 0xff), (byte)((v >> 24) & 0xff) };
    }

    private static byte[] shortToLittleEndian(short v) {
        return new byte[] { (byte)(v & 0xff), (byte)((v >> 8) & 0xff) };
    }

    /**
     * 根据 ID 获取 AudioDeviceInfo
     */
    private static android.media.AudioDeviceInfo getAudioDeviceInfo(int deviceId) {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M) {
            android.media.AudioManager audioManager = (android.media.AudioManager)
                    android.app.ActivityThread.currentApplication()
                            .getSystemService(android.content.Context.AUDIO_SERVICE);

            android.media.AudioDeviceInfo[] devices = audioManager.getDevices(android.media.AudioManager.GET_DEVICES_INPUTS);
            for (android.media.AudioDeviceInfo device : devices) {
                if (device.getId() == deviceId) {
                    return device;
                }
            }
        }
        return null;
    }
}
