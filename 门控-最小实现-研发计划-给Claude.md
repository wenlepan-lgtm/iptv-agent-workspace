# 门控最小实现：HK 净化麦 + es8388 Loopback(Ref) → Gate → ASR

**目标**：HK 输出已板内消除，系统再用 es8388 回采做一次门控，显著降低「播放器/电视声音偶发被 ASR 识别」的概率。  
**原则**：最短有效实现。不碰 KWS/TTS 状态机，不做指标系统，不改 ASR 内核。

---

## 0. 目标与非目标

| 必须达成 | 不做 |
|----------|------|
| 双路采集：HK mic + es8388 Loopback R（ref） | 不重构状态机 |
| 每帧 E_ref / E_mic / corr，一个 if 决策 | 不做 M5 指标/AB 实验框架 |
| 满足条件时本帧不喂 ASR（或喂静音） | 不做「二次自适应 AEC」 |
| Gate 可开关、阈值可配置 | 不碰 KWS/TTS 逻辑 |
| 启动时固化 es8388 Loopback（tinymix） | |

---

## 1. 现状与约束

- **HK-ARRAYMIC-V3.2**：USB 2ch，板内已做 AEC/NS/AGC，系统拿不到内部 ref。当前 ASR 用的就是 HK 这路。
- **es8388（card0）**：已有 Loopback 控件，已验证 `a_play.wav` 有播放、`a_sil.wav` 无。R 通道 = 播放器参考（L:MIC R:LP）。
- **误识别**：偶发，主要来自 TV 残留、VAD 过敏感、或 double-talk。本方案优先砍「无人说话时误识别」。

---

## 2. 数据流（逻辑）

```
HK Mic (现有采集)     → MicCapture ──┐
                                      ├→ Gate(E_ref, E_mic, corr) → 通过/不通过 → ASR
es8388 Loopback 2ch   → RefCapture(R) ─┘
```

- **Mic**：沿用现有 HK 采集（AudioRecord 或现有链路），不改。
- **Ref**：新增一路，仅采集 es8388 loopback，**只用 R 通道**（L:MIC R:LP 中的 LP）。

---

## 3. 任务拆解（可直接执行）

### Task 1：启动时固化 es8388 Loopback

- **动作**：应用启动时（或录音/ASR 启动前）执行一次（需 root/su）：
  ```bash
  tinymix 22 Enable   # SAI1 SDI0 Loopback Switch
  tinymix 14 Enable   # SAI1 SDI0 Loopback I2S LR Switch
  ```
- **实现**：`Runtime.getRuntime().exec("su -c 'tinymix 22 Enable && tinymix 14 Enable'")` 或 JNI 里 `system()`，失败只打日志不阻塞。
- **验收**：重启后打开应用，`adb shell tinymix 22` / `tinymix 14` 显示 Enable。

---

### Task 2：RefCapture（es8388 loopback 稳定采集）

- **目标**：持续读取 es8388 的 capture PCM，输出 48k/16bit/2ch，只把 **R 通道** 作为 ref。
- **建议**：Native 层用 **tinyalsa** 打开 `pcmC0D0c`（card0 device0 capture），单独线程 + ring buffer，按 **20ms 一帧**（960 samples/channel @48k）向外提供 `getRefFrame()`。
- **格式**：S16_LE stereo，在 C++ 里抽 R：`ref[i] = in[2*i+1]`。
- **验收**：Ref 线程能稳定取帧，无 busy/卡死；可选：写一段 ref 到 `/sdcard/ref_test.raw` 用 Audacity 确认有播放时才有波形。

---

### Task 3：帧对齐与 Gate 输入

- **统一**：Mic 与 Ref 都按 **20ms 一帧**（48k 下 960 samples/channel）。若当前 HK 是 16k，先在 Gate 前 resample 到 48k，或 Gate 统一在 16k 下做（则 ref 也 resample 到 16k），二选一即可。
- **对齐**：处理线程每 20ms 从 Mic 取一帧、从 Ref 取一帧（取不到就等）。先不做时间平移；若后续发现 corr 很飘再考虑 ±5ms 平移取最大 corr。
- **Mono**：Mic 若为 2ch 则 `(L+R)/2`；Ref 只用 R，无需再平均。

---

### Task 4：SimpleGate 实现

- **输入**：每帧 `mic_float[]`、`ref_float[]`，同长度（如 960），范围建议 [-1,1]（S16→float 除以 32768）。
- **计算**：
  - `E_ref = RMS(ref)`，`E_mic = RMS(mic)`
  - `E_ref_db = 20*log10(E_ref/32768 + 1e-9)`（若 float 已归一则 `20*log10(rms+1e-9)`）
  - `corr = dot(mic,ref) / (||mic||*||ref|| + eps)`
- **判定**：
  - `want_block = (E_ref_db > ref_on_db) && (corr > corr_on)`
  - **滞回**：连续 `on_frames` 帧 want_block → 进入 block；连续 `off_frames` 帧 !want_block → 解除 block。
- **默认参数**（可放配置文件/常量）：
  - `ref_on_db = -45`
  - `corr_on = 0.35`
  - `on_frames = 3`（60ms 进入 block）
  - `off_frames = 5`（100ms 解除 block）
- **输出**：`shouldPass()` → true 则本帧**喂 ASR**；false 则**不喂**（推荐直接 drop）或喂静音帧（若 ASR 对时间连续性敏感再改）。

参考 C++ 结构（可放 JNI 或 Kotlin 侧重算）：

```cpp
// 伪代码
bool shouldPass(mic_float[], ref_float[], n) {
  float e_ref = rms(ref, n);
  float e_mic = rms(mic, n);
  float db_ref = 20*log10(e_ref + 1e-9f);
  float c = dot(mic,ref,n) / (norm(mic,n)*norm(ref,n) + 1e-12f);

  bool want = (db_ref > ref_on_db) && (c > corr_on);
  if (want) { on_cnt++; off_cnt=0; } else { off_cnt++; on_cnt=0; }
  if (!blocked && on_cnt >= on_frames)  blocked = true;
  if ( blocked && off_cnt >= off_frames) blocked = false;
  return !blocked;
}
```

---

### Task 5：集成到 ASR 喂流前（最小改动）

- **位置**：在现有「把 PCM 喂给 ASR」的调用前（如 `AsrController` 或 NpuOfflineAsrEngine 的 feed 入口）。
- **逻辑**：
  - 每 20ms 拿到一帧 HK mic PCM，同时拿到同帧 ref（RefCapture 输出）。
  - 转成 float mono（mic 若 2ch 则 (L+R)/2，ref 取 R）。
  - 若 `gate.shouldPass(mic_float, ref_float, frameLen)` 为 true → 照常 `asr.feed(micFrame)`；否则 → 不喂（或喂静音）。
- **开关**：通过配置/常量控制「Gate 是否启用」，便于回滚和对比。

---

### Task 6：配置与回滚

- **Gate 开关**：如 `BuildConfig` 或 `config.properties` 中 `gate.ref.enabled=true/false`，false 时 Ref 不采、Gate 不判，直接全部喂 ASR（与现网一致）。
- **阈值**：`ref_on_db`、`corr_on`、`on_frames`、`off_frames` 可写死在代码或从本地配置读取，便于现场微调。

---

## 4. 验收标准（最小）

- 播放视频、无人说话时：Gate 能 block，误识别明显减少（主观或少量测试即可）。
- 有人说话时：正常识别不受明显影响（可接受极少量漏识别，先不追求数据指标）。
- 关闭 Gate 后行为与当前一致（回滚无副作用）。

---

## 5. 执行顺序建议

1. Task 1（tinymix 固化）  
2. Task 2（RefCapture）  
3. Task 4（SimpleGate 单测/桌面验证）  
4. Task 3（对齐） + Task 5（集成）  
5. Task 6（配置/开关）

---

## 6. 给 Claude 的一句话指令

> 实现「HK 净化麦 + es8388 Loopback(Ref) → Gate → ASR」最小门控：  
> 1）启动时执行 tinymix 22 Enable、tinymix 14 Enable；  
> 2）新增 RefCapture（JNI/tinyalsa）采集 card0 capture，48k/16bit/2ch，只取 R 通道；  
> 3）Gate 每 20ms 一帧，算 E_ref_db、corr，滞回 on_frames=3、off_frames=5，默认 ref_on_db=-45、corr_on=0.35；  
> 4）在现有 feed ASR 前调用 Gate，shouldPass 为 false 时本帧不喂 ASR；  
> 5）Gate 可配置开关，不改 KWS/TTS，不做指标。  
> 详见《门控-最小实现-研发计划-给Claude.md》。
