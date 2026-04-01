# CoolBee.vrm 转 glb 快速指南

> 针对 CoolBee 卡通风格模型，快速转换并推送到设备

---

## 一、前提条件

✅ 已下载 `CoolBee.vrm` 文件  
✅ 已安装 Blender（如果没有：`brew install --cask blender`）

---

## 二、快速操作步骤

### 步骤 1：安装 UniVRM 插件（如果还没装）

1. **下载插件**：
   - 访问：https://github.com/vrm-c/UniVRM/releases
   - 下载最新版本的 `.zip` 文件

2. **在 Blender 中安装**：
   - 打开 Blender
   - `Edit` → `Preferences` → `Add-ons`
   - 点击 `Install...` → 选择下载的 `.zip` 文件
   - 安装后，搜索 "VRM" 并启用插件

### 步骤 2：导入 CoolBee.vrm

1. **导入模型**：
   - `File` → `Import` → `VRM (.vrm)`
   - 选择 `CoolBee.vrm` 文件
   - 点击 `Import VRM`

2. **等待导入完成**（可能需要几秒到几十秒）

### 步骤 3：检查并重命名 Shape Keys（重要！）

1. **打开 Shape Keys 面板**：
   - 选择模型对象（在 3D 视图中点击）
   - 右侧属性面板 → 绿色图标（Object Data Properties）
   - 向下滚动找到 `Shape Keys` 面板

2. **查看现有 Shape Keys**：
   - CoolBee 可能包含：`aa`, `ee`, `ih`, `oh`, `ou`, `blink`, `happy` 等

3. **重命名映射**（双击名称修改）：

   | VRM 原始名称 | 项目规范名称 | 说明 |
   |-------------|-------------|------|
   | `aa` | `vis_A` | 元音 A |
   | `ee` | `vis_E` | 元音 E |
   | `ih` | `vis_I` | 元音 I |
   | `oh` | `vis_O` | 元音 O |
   | `ou` | `vis_U` | 元音 U |
   | `blink` 或 `blink_L`/`blink_R` | `exp_blink` | 眨眼 ✅ 必选 |
   | `happy` 或 `smile` | `exp_smile` | 微笑 |
   | **如果没有，需要添加** | `jawOpen` | 下颌开合 ✅ 必选 |

4. **如果没有 `jawOpen`，添加一个**：
   - 在 `Shape Keys` 面板中，点击 `+` 按钮
   - 选择 `From Mix`
   - 重命名为 `jawOpen`
   - 进入编辑模式（`Tab`），选中下颌顶点，向下移动
   - 退出编辑模式（`Tab`）

### 步骤 4：导出为 glb

1. **导出设置**：
   - `File` → `Export` → `glTF 2.0 (.glb/.gltf)`
   - **Format**: `glTF Binary (.glb)` ✅
   - **Include**: 
     - ✅ `Selected Objects`
     - ✅ `Mesh`
     - ✅ `Armature`（如果有骨骼）
     - ✅ **`Shape Keys`**（重要！必须勾选）
   - **Geometry**: 全部勾选
   - **Animation**: ✅ `Bake Animation`（如果有）

2. **保存文件**：
   - 文件名：`avatar.glb`
   - 保存位置：`/Users/ala/工作项目/agent/`
   - 点击 `Export glTF 2.0`

### 步骤 5：推送到设备

```bash
# 进入项目目录
cd /Users/ala/工作项目/agent

# 创建目录（如果不存在）
adb shell mkdir -p /sdcard/iptv-agent-models/digital-human

# 推送 glb 文件
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 验证文件
adb shell ls -lh /sdcard/iptv-agent-models/digital-human/
```

### 步骤 6：重启应用测试

```bash
# 重启应用
adb shell am force-stop com.joctv.agent
adb shell am start -n com.joctv.agent/.MainActivity

# 查看日志确认加载
adb logcat -s FilamentRenderer DigitalHumanController | grep -E "(Model loaded|Morph|Asset loaded|CoolBee)"
```

---

## 三、Shape Keys 检查清单

转换前确认以下 Shape Keys 存在（或已重命名）：

- ✅ `jawOpen` - 下颌开合（必选）
- ✅ `exp_blink` - 眨眼（必选，至少一个）
- ✅ `vis_A`, `vis_E`, `vis_I`, `vis_O`, `vis_U` - 元音口型（建议）
- ✅ `exp_smile` - 微笑（建议）

---

## 四、常见问题

### Q: 导入后模型是黑色的？
**A**: 正常，VRM 材质在 Blender 中可能显示异常，导出 glb 后 Filament 会自动处理 PBR 材质。

### Q: 找不到某些 Shape Keys？
**A**: 
- 有些 VRM 模型可能没有完整的 Shape Keys
- 可以在 Blender 中手动添加缺失的 Shape Keys
- 至少要有 `jawOpen` 和 `exp_blink`，否则口型和眨眼功能无法工作

### Q: 导出后文件太大？
**A**: 
- 检查纹理大小（Material Properties → Image Texture）
- 可以压缩纹理或降低分辨率
- 使用 `gltf-pipeline` 工具压缩（可选）

---

## 五、快速命令总结

```bash
# 1. 在 Blender 中完成导入、重命名、导出后

# 2. 推送到设备
cd /Users/ala/工作项目/agent
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 3. 重启应用
adb shell am force-stop com.joctv.agent && adb shell am start -n com.joctv.agent/.MainActivity

# 4. 查看日志
adb logcat -s FilamentRenderer DigitalHumanController
```

---

*按照以上步骤操作，CoolBee 模型应该可以成功转换并在设备上运行。如果遇到问题，检查 Blender 控制台输出或查看详细操作步骤文档。*
