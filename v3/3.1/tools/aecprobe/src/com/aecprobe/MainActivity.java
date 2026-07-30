package com.aecprobe;

import android.app.Activity;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.media.audiofx.AutomaticGainControl;
import android.media.audiofx.NoiseSuppressor;
import android.os.Bundle;
import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;

/**
 * AECProbe - 录音路径 A/B 探测 (Commit3 §三/报告75)
 * 按 intent extra "mode" 选 AudioSource:
 *   COMM -> VOICE_COMMUNICATION (7)  触发 HAL AEC (+ 可选挂软件 AEC)
 *   REC  -> VOICE_RECOGNITION (6)    基线, 无 AEC (与 com.joctv.mictest 一致)
 *   UNP  -> UNPROCESSED (9)
 *   MIC  -> MIC (1)
 * 录音时用 AudioTrack 播 1kHz 正弦当回声源. 存 pcm + 诊断到 getExternalFilesDir.
 * 诊断: 实际格式/route/effect 可用+启用/RMS/零帧/重启. 无 UI, 录完 finish().
 */
public class MainActivity extends Activity {
    static final String TAG = "AECProbe";
    static final int SR = 16000;

    @Override protected void onCreate(Bundle s) {
        super.onCreate(s);
        final String mode = getIntent().getStringExtra("mode");
        final int secs = parseInt(getIntent().getStringExtra("secs"), 8);
        final boolean playTone = !"0".equals(getIntent().getStringExtra("tone"));
        final boolean swAec = "1".equals(getIntent().getStringExtra("swaec")); // COMM 时是否额外挂软件 AEC

        final int source;
        switch (mode == null ? "COMM" : mode.toUpperCase(Locale.US)) {
            case "REC":  source = MediaRecorder.AudioSource.VOICE_RECOGNITION; break;
            case "UNP":  source = MediaRecorder.AudioSource.UNPROCESSED; break;
            case "MIC":  source = MediaRecorder.AudioSource.MIC; break;
            default:     source = MediaRecorder.AudioSource.VOICE_COMMUNICATION; break; // COMM
        }

        new Thread(() -> runProbe(mode == null ? "COMM" : mode.toUpperCase(Locale.US), source, secs, playTone, swAec)).start();
    }

    static int parseInt(String s, int d) { try { return Integer.parseInt(s); } catch (Exception e) { return d; } }

    void runProbe(String tag, int source, int secs, boolean playTone, boolean swAec) {
        File dir = getExternalFilesDir(null);
        File pcmFile = new File(dir, "rec_" + tag + ".pcm");
        File diagFile = new File(dir, "diag_" + tag + ".txt");
        StringBuilder diag = new StringBuilder();

        int ch = AudioFormat.CHANNEL_IN_MONO;
        int fmt = AudioFormat.ENCODING_PCM_16BIT;
        int minBuf = AudioRecord.getMinBufferSize(SR, ch, fmt);
        int bufSize = Math.max(minBuf * 2, SR * 2); // 至少 ~1s
        diag.append("mode=").append(tag).append("\n")
            .append("requested_source=").append(source).append("\n")
            .append("sample_rate=").append(SR).append("\n")
            .append("min_buf=").append(minBuf).append(" buf=").append(bufSize).append("\n");

        AudioRecord ar = null;
        AcousticEchoCanceler aec = null;
        NoiseSuppressor ns = null;
        AutomaticGainControl agc = null;
        AudioTrack tone = null;
        try {
            ar = new AudioRecord(source, SR, ch, fmt, bufSize);
            diag.append("record_state=").append(stateName(ar.getState())).append("\n");
            if (ar.getState() != AudioRecord.STATE_INITIALIZED) {
                diag.append("ERROR=record_not_initialized\n");
                write(diagFile, diag.toString());
                Log.e(TAG, diag.toString());
                finish();
                return;
            }
            int sess = ar.getAudioSessionId();
            diag.append("session=").append(sess).append("\n");
            diag.append("AEC_available=").append(AcousticEchoCanceler.isAvailable()).append("\n");
            diag.append("NS_available=").append(NoiseSuppressor.isAvailable()).append("\n");
            diag.append("AGC_available=").append(AutomaticGainControl.isAvailable()).append("\n");

            // 路由 + 麦克风信息 (API 23/28)
            try {
                android.media.AudioDeviceInfo rt = ar.getRoutedDevice();
                if (rt != null) diag.append("routed_device=").append(rt.getType())
                    .append(" product=").append(rt.getProductName() == null ? "" : rt.getProductName()).append("\n");
                else diag.append("routed_device=null\n");
            } catch (Throwable t) { diag.append("routed_device_err=").append(t).append("\n"); }
            try {
                java.util.Collection<android.media.MicrophoneInfo> mics = ar.getActiveMicrophones();
                diag.append("active_mics=").append(mics == null ? 0 : mics.size()).append("\n");
                if (mics != null) for (android.media.MicrophoneInfo m : mics)
                    diag.append("  mic id=").append(m.getId()).append(" addr=").append(m.getAddress())
                       .append(" loc=").append(m.getLocation()).append("\n");
            } catch (Throwable t) { diag.append("active_mics_err=").append(t).append("\n"); }

            // 软件效果: COMM 默认靠 HAL; swAec=1 时额外挂软件 AEC/NS/AGC
            boolean aecEnabled = false, nsEnabled = false, agcEnabled = false;
            if (AcousticEchoCanceler.isAvailable() && (swAec || "COMM".equals(tag))) {
                try {
                    aec = AcousticEchoCanceler.create(sess);
                    if (aec != null) { aec.setEnabled(swAec); aecEnabled = aec.getEnabled(); }
                } catch (Throwable t) { diag.append("aec_create_err=").append(t).append("\n"); }
            }
            if (NoiseSuppressor.isAvailable() && swAec) {
                try { ns = NoiseSuppressor.create(sess); if (ns != null) { ns.setEnabled(true); nsEnabled = ns.getEnabled(); } } catch (Throwable t) {}
            }
            if (AutomaticGainControl.isAvailable() && swAec) {
                try { agc = AutomaticGainControl.create(sess); if (agc != null) { agc.setEnabled(true); agcEnabled = agc.getEnabled(); } } catch (Throwable t) {}
            }
            diag.append("AEC_enabled=").append(aecEnabled).append(" (swAec flag=").append(swAec).append(")\n");
            diag.append("NS_enabled=").append(nsEnabled).append("\n");
            diag.append("AGC_enabled=").append(agcEnabled).append("\n");

            // 播 1kHz 正弦当回声源
            if (playTone) {
                try {
                    int tsr = 48000;
                    int toneSecs = secs;
                    int n = tsr * toneSecs;
                    short[] wave = new short[n];
                    double amp = Short.MAX_VALUE * 0.35;
                    for (int i = 0; i < n; i++) wave[i] = (short)(amp * Math.sin(2 * Math.PI * 1000.0 * i / tsr));
                    tone = new AudioTrack(AudioManager.STREAM_MUSIC, tsr, AudioFormat.CHANNEL_OUT_MONO,
                            AudioFormat.ENCODING_PCM_16BIT, n * 2, AudioTrack.MODE_STATIC);
                    tone.write(wave, 0, n);
                    tone.setLoopPoints(0, n, toneSecs); // 循环整个时长
                    tone.play();
                    diag.append("tone=1000Hz played\n");
                } catch (Throwable t) { diag.append("tone_err=").append(t).append("\n"); }
            }

            // 录音循环
            ar.startRecording();
            FileOutputStream fos = new FileOutputStream(pcmFile);
            short[] b = new short[bufSize / 2];
            long total = 0, zeroFrames = 0, restarts = 0, sumSq = 0, peak = 0;
            long deadline = System.currentTimeMillis() + secs * 1000L;
            while (System.currentTimeMillis() < deadline) {
                int r = ar.read(b, 0, b.length);
                if (r > 0) {
                    fos.write(shortsToBytes(b, r));
                    boolean allZero = true;
                    for (int i = 0; i < r; i++) {
                        long v = b[i];
                        long v2 = v * v;
                        sumSq += v2;
                        long av = Math.abs(v);
                        if (av > peak) peak = av;
                        if (v != 0) allZero = false;
                    }
                    if (allZero) zeroFrames++;
                    total += r;
                } else if (r == AudioRecord.ERROR_INVALID_OPERATION || r == AudioRecord.ERROR_BAD_VALUE) {
                    restarts++; diag.append("read_err=").append(r).append(" @total=").append(total).append("\n");
                    try { Thread.sleep(50); } catch (Exception e) {}
                } else if (r == 0) { /* skip */ }
            }
            fos.flush(); fos.close();
            ar.stop();

            double rms = total > 0 ? Math.sqrt((double) sumSq / total) : 0;
            double rmsDb = 20 * Math.log10((rms + 1) / 32768.0);
            diag.append("frames=").append(total).append("\n");
            diag.append("zero_frames=").append(zeroFrames).append("\n");
            diag.append("restarts=").append(restarts).append("\n");
            diag.append("rms=").append(String.format(Locale.US, "%.1f", rms)).append("\n");
            diag.append("rms_db=").append(String.format(Locale.US, "%.2f", rmsDb)).append("\n");
            diag.append("peak=").append(peak).append("\n");
            diag.append("pcm_file=").append(pcmFile.getAbsolutePath()).append("\n");
            diag.append("pcm_bytes=").append(total * 2).append("\n");
            diag.append("RESULT=ok\n");
        } catch (Throwable t) {
            diag.append("EXCEPTION=").append(t).append("\n");
            Log.e(TAG, "probe failed", t);
        } finally {
            if (aec != null) try { aec.release(); } catch (Exception e) {}
            if (ns != null) try { ns.release(); } catch (Exception e) {}
            if (agc != null) try { agc.release(); } catch (Exception e) {}
            if (ar != null) try { ar.release(); } catch (Exception e) {}
            if (tone != null) try { tone.stop(); tone.release(); } catch (Exception e) {}
            write(diagFile, diag.toString());
            Log.i(TAG, "=== AECProbe " + tag + " done ===\n" + diag);
            finish();
        }
    }

    static byte[] shortsToBytes(short[] s, int n) {
        byte[] out = new byte[n * 2];
        for (int i = 0; i < n; i++) { out[i*2] = (byte)(s[i] & 0xff); out[i*2+1] = (byte)((s[i] >> 8) & 0xff); }
        return out;
    }

    static String stateName(int s) {
        switch (s) {
            case AudioRecord.STATE_INITIALIZED: return "INITIALIZED";
            case AudioRecord.STATE_UNINITIALIZED: return "UNINITIALIZED";
            default: return String.valueOf(s);
        }
    }

    void write(File f, String s) {
        try (Writer w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8)) { w.write(s); }
        catch (Exception e) { Log.e(TAG, "write diag fail", e); }
    }
}
