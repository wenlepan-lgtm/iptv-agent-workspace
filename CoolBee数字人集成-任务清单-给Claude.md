# CoolBee 数字人集成 - 任务清单（给 Claude）

> 已完成代码改动，需要 Claude 完成集成编译和测试

---

## 一、已完成的工作

### 1.1 模型准备（用户完成）

- ✅ 下载 CoolBee.vrm（Open Source Avatars）
- ✅ 在 Blender 中使用 UniVRM 插件导入 VRM
- ✅ 导出为 glTF Binary (.glb) 格式
- ✅ 文件：`/Users/ala/工作项目/agent/Untitled.glb`（约 4.1 MB）

**模型包含**：
- 12 个 Shape Keys（口型、眨眼、表情）
- 2 个 Mesh
- 43 个节点（骨骼）
- **注意**：当前模型**没有动画 clip**（翅膀不会自动动，但代码已支持，后续可在 Blender 中添加）

### 1.2 代码改动（已完成）

#### A. VRM Shape Keys 名称映射支持

**文件**：`iptv-edge-agent/app/src/main/java/com/joctv/agent/digitalhuman/DigitalHumanConfig.kt`

- ✅ 添加了 `VRM_MORPH_NAME_MAPPING` 映射表
- ✅ 支持识别 `blendShape1.mouth_u` → `vis_U` 等 VRM 常见命名
- ✅ 添加了 `findMappedMorphName()` 函数，自动映射 Shape Keys
- ✅ 支持 `Blink_L` / `Blink_R`（首字母大写）的映射

**效果**：**无需在 Blender 中重命名 Shape Keys**，代码会自动识别并映射。

#### B. 数字人显示/隐藏逻辑

**文件**：`iptv-edge-agent/app/src/main/java/com/joctv/agent/MainActivity.kt`

**新增功能**：
1. **默认隐藏**：应用启动时，数字人区域隐藏（`digitalHumanContainer.visibility = GONE`）
2. **ASR 时飞入**：当 ASR 识别到用户说话时，数字人区域从上方“飞入”显示（动画 400ms）
3. **TTS 发声**：数字人进入 SPEAK 状态，口型/表情随 TTS 驱动
4. **TTS 结束 30 秒后**：自动隐藏数字人区域，并退到后台（`moveTaskToBack(true)`）

**新增变量**：
```kotlin
private lateinit var digitalHumanContainer: View
private val digitalHumanExitDelayMs = 30_000L  // 30 秒
private val mainHandler = Handler(Looper.getMainLooper())
private var digitalHumanExitRunnable: Runnable? = null
```

**新增函数**：
- `showDigitalHumanWithFlyIn()` - 飞入显示动画
- `hideDigitalHuman()` - 隐藏数字人
- `cancelDigitalHumanExitToBack()` - 取消 30 秒定时

**调用位置**：
- `processFinalResult()` 开头：ASR 有结果时飞入
- `onWakeWordDetected()`：唤醒词时也飞入
- `onTTSStart()`：取消 30 秒定时（避免正在说话时被关闭）
- `onTTSDone()`：启动 30 秒定时，到时后隐藏并退到后台
- `onTTSError()`：取消定时

#### C. 翅膀动画配置（预留）

**文件**：`iptv-edge-agent/app/src/main/java/com/joctv/agent/digitalhuman/DigitalHumanConfig.kt`

- ✅ 添加了 `ANIM_WING` 常量（当前为空字符串）
- ✅ 在 `DigitalHumanController.enterIdle()` 中优先播放翅膀动画（如果存在）

**说明**：当前 CoolBee 模型没有翅膀动画，但代码已支持。若后续在 Blender 中添加翅膀骨骼动画，只需：
1. 导出 glb 时勾选 Animation
2. 在 `DigitalHumanConfig.ANIM_WING` 中填入动画名称（如 `"Wing"`）

---

## 二、需要 Claude 完成的任务

### 2.1 准备模型文件

**任务**：将 `Untitled.glb` 重命名为 `avatar.glb` 并推送到设备

```bash
# 1. 重命名文件
cd /Users/ala/工作项目/agent
cp Untitled.glb avatar.glb

# 2. 创建目录（如果不存在）
adb shell mkdir -p /sdcard/iptv-agent-models/digital-human

# 3. 推送模型文件
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 4. 验证文件
adb shell ls -lh /sdcard/iptv-agent-models/digital-human/
```

**预期结果**：
- 文件大小约 4.1 MB
- 文件名：`avatar.glb`

---

### 2.2 编译 APK

**任务**：编译包含数字人功能的 APK

```bash
cd /Users/ala/工作项目/agent/iptv-edge-agent

# 清理并编译
./gradlew clean
./gradlew assembleDebug

# 或使用 Android Studio 编译
```

**检查点**：
- ✅ 确认 `DigitalHumanConfig.kt` 中的映射表已更新
- ✅ 确认 `MainActivity.kt` 中的显示/隐藏逻辑已添加
- ✅ 确认没有编译错误

---

### 2.3 安装并测试

**任务**：安装 APK 并验证数字人功能

```bash
# 1. 安装 APK
adb install -r app/build/outputs/apk/debug/app-debug.apk

# 2. 启动应用
adb shell am force-stop com.joctv.agent
adb shell am start -n com.joctv.agent/.MainActivity

# 3. 查看日志（关注数字人相关）
adb logcat -s DigitalHumanController FilamentRenderer MainActivity | grep -E "(DigitalHuman|Morph|fly-in|Asset loaded)"
```

---

### 2.4 功能验证清单

**测试场景**：

| 场景 | 预期行为 | 验证方法 |
|------|---------|---------|
| **1. 应用启动** | 数字人区域**隐藏** | 界面看不到数字人区域 |
| **2. 用户说话（ASR）** | 数字人区域**从上方飞入**显示 | 观察动画效果（约 400ms） |
| **3. TTS 发声** | 数字人**口型/表情**随 TTS 动 | 观察嘴巴开合、眨眼 |
| **4. TTS 结束** | 数字人进入 IDLE（待机） | 观察待机动画 |
| **5. TTS 结束 30 秒后** | 数字人**隐藏**，应用**退到后台** | 观察界面消失，应用在后台运行 |
| **6. 再次唤醒** | 数字人再次**飞入**显示 | 重复场景 2-5 |

**日志检查**：
```bash
# 检查 Shape Keys 映射
adb logcat -s DigitalHumanController | grep "Mapped morph"

# 检查飞入动画
adb logcat -s MainActivity | grep "fly-in"

# 检查 30 秒定时
adb logcat -s MainActivity | grep "Digital human exit"
```

---

### 2.5 问题排查

**如果数字人不显示**：

1. **检查模型文件**：
   ```bash
   adb shell ls -lh /sdcard/iptv-agent-models/digital-human/avatar.glb
   ```

2. **检查日志**：
   ```bash
   adb logcat -s FilamentRenderer | grep -E "(Model loaded|Asset loaded|Failed)"
   ```

3. **检查 Shape Keys 映射**：
   ```bash
   adb logcat -s DigitalHumanController | grep -E "(Mapped morph|Blink morph)"
   ```

**如果飞入动画不工作**：

1. **检查容器引用**：
   - 确认 `digitalHumanContainer` 已正确初始化
   - 确认 `R.id.digitalHumanContainer` 在 layout 中存在

2. **检查 ASR 触发**：
   ```bash
   adb logcat -s MainActivity | grep "processFinalResult"
   ```

**如果 30 秒后不退到后台**：

1. **检查定时器**：
   ```bash
   adb logcat -s MainActivity | grep -E "(Digital human exit|cancelDigitalHuman)"
   ```

2. **确认 Handler**：
   - 确认 `mainHandler` 已初始化
   - 确认 `digitalHumanExitRunnable` 不为 null

---

## 三、代码改动文件清单

| 文件 | 改动内容 |
|------|---------|
| `DigitalHumanConfig.kt` | ✅ 添加 VRM 名称映射表<br>✅ 添加 `findMappedMorphName()`<br>✅ 添加 `ANIM_WING` 配置 |
| `DigitalHumanController.kt` | ✅ 使用映射表查找 Shape Keys<br>✅ `enterIdle()` 优先播放翅膀动画 |
| `MainActivity.kt` | ✅ 添加 `digitalHumanContainer` 引用<br>✅ 添加显示/隐藏逻辑<br>✅ 添加 30 秒定时退到后台<br>✅ ASR/TTS 回调中调用显示/隐藏 |

---

## 四、模型信息

**当前模型**：CoolBee（卡通风格小蜜蜂）

**文件位置**：
- 本地：`/Users/ala/工作项目/agent/Untitled.glb`
- 设备：`/sdcard/iptv-agent-models/digital-human/avatar.glb`

**模型规格**：
- 格式：glTF 2.0 Binary (.glb)
- 大小：约 4.1 MB
- Shape Keys：12 个（口型、眨眼、表情）
- 动画：0 个（当前无骨骼动画，翅膀不动）

**Shape Keys 列表**：
```
blendShape1.mouth_a  → vis_A
blendShape1.mouth_e  → vis_E
blendShape1.mouth_i  → vis_I
blendShape1.mouth_o  → vis_O
blendShape1.mouth_u  → vis_U
blendShape1.blink    → exp_blink
blendShape1.Blink_L  → eyeBlinkLeft
blendShape1.Blink_R  → eyeBlinkRight
blendShape1.happy    → exp_smile
blendShape1.angry    → (未映射)
blendShape1.sorrow   → (未映射)
blendShape1.joy      → (未映射)
```

---

## 五、后续优化（可选）

### 5.1 添加翅膀动画

**步骤**：
1. 在 Blender 中给 CoolBee 的翅膀骨骼做循环动画（如 `"Wing"`）
2. 导出 glb 时勾选 Animation
3. 在 `DigitalHumanConfig.ANIM_WING = "Wing"` 中填入动画名
4. 重新推送 glb 到设备

### 5.2 优化飞入动画

**可选改进**：
- 调整飞入速度（当前 400ms）
- 添加旋转效果
- 添加缩放效果

### 5.3 优化 30 秒定时

**可选改进**：
- 可配置延迟时间（当前硬编码 30 秒）
- 添加“取消退出”按钮
- 添加“立即退出”按钮

---

## 六、快速命令总结

```bash
# 1. 准备模型
cd /Users/ala/工作项目/agent
cp Untitled.glb avatar.glb
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 2. 编译（如果需要）
cd iptv-edge-agent
./gradlew assembleDebug

# 3. 安装
adb install -r app/build/outputs/apk/debug/app-debug.apk

# 4. 启动并查看日志
adb shell am force-stop com.joctv.agent
adb shell am start -n com.joctv.agent/.MainActivity
adb logcat -s DigitalHumanController FilamentRenderer MainActivity | grep -E "(DigitalHuman|fly-in|Mapped)"
```

---

**文档创建时间**：2026-01-29  
**代码状态**：✅ 已完成，待集成测试  
**模型状态**：✅ 已准备，待推送
