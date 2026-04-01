# 数字人白块与 GPU 显示 - 审核与修改任务（给 Claude）

> 现象：数字人区域只显示白色几何图形；GPU 显示 90000000（需改为可读并支持百分比）

---

## 一、问题与原因分析

### 1.1 数字人区域只显示“白色几何图形”

**根本原因（已确认）**：

- 当前使用的 **CoolBee glb 里带了 Blender 默认的 Cube**：
  - 场景根节点为 `[0, 42]`，其中 **node[0] 名为 "Cube"，使用 mesh[0]（名为 "Cube"）**，材质为无贴图的灰白色 `baseColorFactor [0.8,0.8,0.8,1]`。
  - 真正的小蜜蜂是 **mesh[1]（248_low1.baked）**，挂在 node[40] 下（属于 node 42 子树）。
- 加载时把 **所有 entity 都加入了场景**，所以会先看到这个白色立方体；小蜜蜂可能被挡在后面或同屏显示但被白块盖住。

**已做代码修改**：

- **FilamentRenderer.kt**：当 `asset.entities.size > 1` 时，**只把从第 2 个起的 entity 加入场景**（跳过第一个，即 Blender 默认 Cube），这样画面里只会显示小蜜蜂。
- 同时保留之前的：`transformToUnitCube` + `setupCamera`，保证缩放和相机正确。

**其他可能原因**（若仍异常再查）：

| 原因 | 说明 | 处理方式 |
|------|------|----------|
| 设备上未放置 CoolBee | 若用的是 fallback 或错误文件，可能仍是白块。 | 确认 `adb shell ls /sdcard/iptv-agent-models/digital-human/avatar.glb` 存在且约 4MB。 |
| 材质/光照 | 若 mesh[1] 使用 KHR_materials_clearcoat/specular 等，Filament 支持度可能有限。 | 若小蜜蜂仍发白，可在 Blender 中改为标准 PBR 再导出。 |

**已做代码修改**（可直接用）：

- 在 `FilamentRenderer.kt` 中已恢复：
  - `viewer.transformToUnitCube(Float3(0.0f, 0.0f, 0.0f))`
  - `setupCamera(viewer)`（不再用 `setupCameraForDebug`）
- 这样 CoolBee 会按边界框缩放到单位立方体内并居中，相机从正面偏上观看，应能完整看到模型而不是一块白面。

**Claude 需要做的**：

1. 确认设备上存在且使用的是 CoolBee 的 `avatar.glb`（见上文 adb 命令）。
2. 若仍为白块：抓日志确认加载的是 sdcard 还是 assets：
   ```bash
   adb logcat -s FilamentRenderer | grep -E "(Loading model|from sdcard|from assets|Model loaded)"
   ```
3. 若日志是 “from sdcard” 且仍白：再排查材质（见 ③）；若是 “from assets” 或 “模型文件不存在”，则先保证 sdcard 上有 `avatar.glb`。

---

### 1.2 GPU 显示 “90000000” 的含义与显示方式

**含义**：

- `/sys/class/devfreq/.../cur_freq` 读到的单位是 **Hz**。
- “90000000” = 90_000_000 Hz = **90 MHz**；若用户看到的是 “900000000” 则为 **900 MHz**。
- 直接显示原始数字不利于阅读，且无法表达“负载”。

**期望**：

- 频率以 **MHz**（或必要时 kHz）显示，例如：`GPU: 900MHz`。
- 若系统提供 GPU 负载（如 `/sys/class/devfreq/.../load`），则显示为 **百分比**，例如：`GPU: 900MHz 45%`。

**已做代码修改**（可直接用）：

- **MetricsCollector.kt**
  - `readGpuInfo()` 中：
    - 将 `cur_freq` 从 Hz 转为可读字符串：≥1_000_000 时用 MHz，否则用 kHz/Hz。
    - 读取 `load`（若有）：若在 0–100 视为百分比；若在 0–255 则按比例换算为 0–100% 显示。
  - 返回 `Pair<String?, String?>`：`freqStr`（如 `"900MHz"`）、`loadStr`（如 `"45%"`）。
- **MainActivity.kt**
  - 系统监控里 GPU 显示改为：`GPU: ${snap.gpuFreq ?: "N/A"} ${snap.gpuLoad ?: ""}`，即 “频率 + 空格 + 负载百分比”（无负载时不显示百分比）。

**Claude 需要做的**：

- 确认上述两处已合入并编译通过。
- 在真机/模拟器上确认界面显示为 “GPU: xxxMHz” 或 “GPU: xxxMHz xx%”，而不再是裸的 “90000000”。

---

### 1.3 CPU 29–33%

- CPU 使用率 29–33% 在跑 TTS/ASR + 数字人渲染时属正常范围。
- 当前显示已是百分比（`snap.cpuPercent`），无需改逻辑；若需优化负载，可后续再做（降帧率、简化场景等）。

---

## 二、小蜜蜂（CoolBee）模型审核结论

- **格式**：glTF 2.0 Binary (.glb)，约 4.1 MB。
- **内容**：2 个 Mesh，1 个带 12 个 Shape Keys（口型、眨眼、表情）；0 个动画 clip；43 个节点。
- **命名**：`blendShape1.mouth_*`、`blink`、`Blink_L/R`、`happy` 等，与当前 `DigitalHumanConfig` 中的 VRM 映射一致，无需在 Blender 里改名。
- **结论**：模型本身可用于当前数字人逻辑；白块问题优先按 **① 缩放+相机** 和 **② 设备路径** 排查，再考虑 **③ 材质**。

---

## 三、已修改文件与修改点摘要

| 文件 | 修改内容 |
|------|----------|
| **FilamentRenderer.kt** | ① 恢复 `transformToUnitCube` + `setupCamera`。② **跳过第一个 entity 再加入场景**（当 entity 数 >1 时），避免 Blender 默认 Cube 显示为白色几何。 |
| **MetricsCollector.kt** | `readGpuInfo()`：cur_freq 转 MHz/kHz 字符串；load 解析为百分比字符串（0–100 或 0–255→100）。 |
| **MainActivity.kt** | GPU 显示改为 `GPU: ${snap.gpuFreq} ${snap.gpuLoad}`，支持“频率 + 百分比”。 |

---

## 四、Claude 执行清单

1. **确认并合入上述三处代码**（若仓库中尚未包含）。
2. **设备上确认 CoolBee 模型**：
   ```bash
   adb shell ls -lh /sdcard/iptv-agent-models/digital-human/avatar.glb
   ```
   若无或不是约 4MB，则推送：
   ```bash
   adb push /path/to/avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb
   ```
3. **编译并安装**：
   ```bash
   cd iptv-edge-agent && ./gradlew assembleDebug
   adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```
4. **验证**：
   - 数字人区域应看到完整小蜜蜂（或至少完整模型轮廓），而不是一块白几何。
   - 状态栏/监控处应显示 “GPU: xxxMHz” 或 “GPU: xxxMHz xx%”，不再出现 “90000000”。
5. **若仍白块**：按第一节抓 FilamentRenderer 日志，确认是 sdcard 还是 assets、是否有 “Model loaded”，并反馈结果以便继续排查材质/光照。

---

## 五、若白块仍存在的后续方向（材质/光照）

- 在 Blender 中检查 CoolBee 导出 glb 的材质：尽量用标准 **glTF 2.0 PBR**（Metallic-Roughness），避免 MToon/Unlit 等扩展。
- 或在 Filament 端为 gltfio 加载的材质做后处理（若引擎支持），或增强 IBL/方向光强度（当前已有方向光 + IBL 强度 30000，可再微调）。

完成上述步骤后，数字人应能正常显示，GPU 显示为人可读的 “xxxMHz” 及可选 “xx%”。
