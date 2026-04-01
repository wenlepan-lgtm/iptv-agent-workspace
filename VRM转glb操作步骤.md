# VRM 转 glb 详细操作步骤

> 针对 CoolBee.vrm（Open Source Avatars 卡通风格模型）

---

## 一、准备工作

### 1.1 安装 Blender（如果还没有）

**macOS**：
```bash
brew install --cask blender
```

**或手动下载**：
- 官网：https://www.blender.org/download/
- 下载最新稳定版（推荐 3.6+ 或 4.0+）

### 1.2 确认文件位置

确保你的 VRM 文件在某个目录下，例如：
```
/Users/ala/工作项目/agent/CoolBee.vrm
```

如果文件名没有 `.vrm` 扩展名，建议重命名：
```bash
# 如果下载的文件名不是 CoolBee.vrm，重命名一下
mv 下载的文件名 CoolBee.vrm
```

---

## 二、安装 UniVRM 插件

### 步骤 1：下载 UniVRM 插件

**方式 A：从 GitHub 下载（推荐）**
1. 访问：https://github.com/vrm-c/UniVRM/releases
2. 下载最新版本的 `.zip` 文件（例如：`UniVRM-0.xxx.x.zip`）

**方式 B：从 Blender 内安装**
- Blender → Edit → Preferences → Add-ons → Install
- 搜索 "VRM" 或 "UniVRM"
- 如果找不到，用方式 A

### 步骤 2：在 Blender 中安装插件

1. **打开 Blender**
2. **进入插件设置**：
   - `Edit` → `Preferences`（或 `Blender` → `Preferences`）
   - 左侧选择 `Add-ons`
3. **安装插件**：
   - 点击右上角 `Install...` 按钮
   - 选择下载的 `UniVRM-xxx.zip` 文件
   - 点击 `Install Add-on`
4. **启用插件**：
   - 在搜索框输入 "VRM"
   - 找到 "VRM Add-on for Blender"
   - 勾选左侧的复选框启用
   - 点击 `Save Preferences`

---

## 三、导入 VRM 文件

### 步骤 1：导入模型

1. **在 Blender 中**：
   - `File` → `Import` → `VRM (.vrm)`
   - 如果看不到 "VRM (.vrm)" 选项，说明插件未正确安装/启用

2. **选择文件**：
   - 浏览到你的 VRM 文件位置
   - 选择文件（例如：`CoolBee.vrm`）
   - 点击 `Import VRM`

3. **等待导入**：
   - Blender 会解析 VRM 文件
   - 可能需要几秒到几十秒，取决于文件大小
   - 导入完成后，模型会出现在 3D 视图中

### 步骤 2：检查导入结果

1. **查看模型**：
   - 按 `Numpad 7`（顶视图）或 `Numpad 1`（前视图）
   - 按 `Numpad 0`（相机视图）
   - 鼠标中键拖动旋转视角

2. **选择模型对象**：
   - 在 3D 视图中点击模型
   - 或在右侧 `Outliner`（大纲视图）中选择对象

---

## 四、检查并重命名 Shape Keys（重要）

### 步骤 1：打开 Shape Keys 面板

1. **选择模型对象**（在 3D 视图中点击）
2. **切换到 Object Data Properties**：
   - 点击右侧属性面板的绿色图标（Object Data Properties）
   - 或按 `Tab` 进入编辑模式，再按 `Tab` 退出
3. **找到 Shape Keys 部分**：
   - 向下滚动找到 `Shape Keys` 面板
   - 如果看到很多 Shape Key，说明导入成功

### 步骤 2：查看现有 Shape Keys

VRM 模型通常包含以下 Shape Keys（VRM/ARKit 规范）：
- `aa`, `ee`, `ih`, `oh`, `ou`（口型）
- `blink`, `blink_L`, `blink_R`（眨眼）
- `happy`, `sad`, `surprised`（表情）
- 等等...

### 步骤 3：重命名为项目规范

**需要映射的 Shape Keys**：

| VRM 原始名称 | 项目规范名称 | 说明 |
|-------------|-------------|------|
| `aa` | `vis_A` | 元音 A |
| `ee` | `vis_E` | 元音 E |
| `ih` | `vis_I` | 元音 I |
| `oh` | `vis_O` | 元音 O |
| `ou` | `vis_U` | 元音 U |
| `blink` 或 `blink_L`/`blink_R` | `exp_blink` | 眨眼 |
| `happy` 或 `smile` | `exp_smile` | 微笑 |
| 需要添加 | `jawOpen` | 下颌开合（如果没有） |

**重命名步骤**：
1. 在 `Shape Keys` 面板中，找到要重命名的 Shape Key
2. **双击** Shape Key 名称（或右键 → Rename）
3. 输入新的名称（例如：`aa` → `vis_A`）
4. 按 `Enter` 确认

**如果没有 `jawOpen`，需要添加**：
1. 在 `Shape Keys` 面板中，点击 `+` 按钮
2. 选择 `From Mix`（基于当前混合状态）
3. 重命名为 `jawOpen`
4. 进入编辑模式（`Tab`），手动调整下颌顶点位置（向下移动）
5. 退出编辑模式（`Tab`）

---

## 五、导出为 glb

### 步骤 1：导出设置

1. **选择模型对象**（确保选中了包含 Shape Keys 的对象）
2. **导出**：
   - `File` → `Export` → `glTF 2.0 (.glb/.gltf)`
3. **导出选项**：
   - **Format**: 选择 `glTF Binary (.glb)` ✅
   - **Include**: 
     - ✅ `Selected Objects`（如果只想导出当前模型）
     - ✅ `Mesh`（网格）
     - ✅ `Armature`（骨骼，如果有）
     - ✅ **`Shape Keys`**（重要！必须勾选）
   - **Transform**: 
     - ✅ `+Y Up`（默认）
   - **Geometry**:
     - ✅ `Apply Modifiers`
     - ✅ `UVs`
     - ✅ `Normals`
     - ✅ `Vertex Colors`（如果有）
   - **Animation**: 
     - ✅ `Bake Animation`（如果有动画）
4. **保存文件**：
   - 文件名：`avatar.glb`（或 `robert.glb`）
   - 保存位置：选择一个方便的位置（例如：`/Users/ala/工作项目/agent/`）
   - 点击 `Export glTF 2.0`

### 步骤 2：验证导出

导出完成后，检查文件：
```bash
# 查看文件大小（应该在几 MB 到几十 MB）
ls -lh avatar.glb

# 可以用 glTF Viewer 在线预览（可选）
# https://gltf-viewer.donmccurdy.com/
```

---

## 六、推送到设备

### 步骤 1：创建目录

```bash
adb shell mkdir -p /sdcard/iptv-agent-models/digital-human
```

### 步骤 2：推送文件

```bash
# 推送 glb 文件
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 验证
adb shell ls -lh /sdcard/iptv-agent-models/digital-human/
```

### 步骤 3：重启应用测试

```bash
# 重启应用
adb shell am force-stop com.joctv.agent
adb shell am start -n com.joctv.agent/.MainActivity

# 查看日志
adb logcat -s FilamentRenderer DigitalHumanController | grep -E "(Model loaded|Morph|Asset loaded)"
```

---

## 七、常见问题

### Q1: Blender 中看不到 "Import VRM" 选项？

**A**: 
- 确认 UniVRM 插件已安装并启用
- 重启 Blender
- 检查插件版本是否与 Blender 版本兼容

### Q2: 导入后模型是黑色的？

**A**: 
- 这是正常的，VRM 材质可能需要调整
- 在 `Material Properties` 中检查材质设置
- 导出 glb 时 Filament 会自动处理 PBR 材质

### Q3: Shape Keys 名称不对怎么办？

**A**: 
- 在 Blender 中重命名（双击名称）
- 或修改项目代码中的映射表（不推荐）

### Q4: 导出后文件太大？

**A**: 
- 检查纹理大小（Material Properties → Image Texture）
- 可以压缩纹理或降低分辨率
- 使用 `gltf-pipeline` 工具压缩：
  ```bash
  npm install -g gltf-pipeline
  gltf-pipeline -i avatar.glb -o avatar_compressed.glb --draco.compressionLevel 10
  ```

### Q5: 模型导入后位置不对？

**A**: 
- 在 Blender 中选中模型
- 按 `Alt + G` 重置位置
- 按 `Alt + R` 重置旋转
- 按 `Alt + S` 重置缩放

---

## 八、快速命令总结

```bash
# 1. 确认文件名为 CoolBee.vrm（如果下载的文件名不同，先重命名）
# mv 下载的文件名 CoolBee.vrm

# 2. 在 Blender 中：
#    - 安装 UniVRM 插件（如果还没装）
#    - File → Import → VRM (.vrm) → 选择 CoolBee.vrm
#    - 检查并重命名 Shape Keys（见下方映射表）
#    - File → Export → glTF 2.0 → 保存为 avatar.glb

# 3. 推送到设备
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 4. 重启应用
adb shell am force-stop com.joctv.agent && adb shell am start -n com.joctv.agent/.MainActivity
```

---

*按照以上步骤操作，应该可以成功导入并转换 VRM 文件。如果遇到问题，请查看日志或检查 Blender 控制台输出。*
