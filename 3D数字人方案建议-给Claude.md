# 3D 数字人方案建议（基于 Filament + GPU 渲染）

> 针对 RK3576 Android 机顶盒，GPU 渲染，不占用 CPU/NPU 资源  
> 当前项目已使用 **Google Filament** 作为 3D 渲染引擎 ✅

---

## 一、当前技术栈确认

### ✅ 已实现
- **渲染引擎**：Google Filament（GPU 渲染，性能优秀）
- **模型格式**：glTF 2.0 Binary (.glb)
- **口型驱动**：Morph Targets (BlendShapes)
- **动画系统**：骨骼动画 + Morph 动画
- **集成状态**：已集成到 `iptv-edge-agent` 项目中

### 📋 代码位置
- `FilamentRenderer.kt` - Filament 渲染器封装
- `DigitalHumanController.kt` - 数字人状态控制
- `MorphTargetAnimator.kt` - Morph 动画插值
- `DigitalHumanConfig.kt` - 配置与命名规范

---

## 二、推荐的 3D 数字人方案（按优先级）

### 🥇 方案 1：Open Source Avatars（VRM 转 glb）⭐ 最推荐

**为什么推荐**：
- ✅ **100% 免费商用**（CC0 授权）
- ✅ **100+ 现成模型**，风格多样
- ✅ **自带 Blendshapes**（表情/口型）
- ✅ **可直接转换**为 glb 格式

**获取方式**：
1. **官网浏览**：https://opensourceavatars.com
   - 网页上直接预览和下载
   - 无需看 JSON，操作简单

2. **直接下载链接**（推荐角色）：
   - **Robert** (070): https://arweave.net/gwG7w4bY-A5c3R6A6GOz3xBCgbPvkFQmqPIDtvnNsYI
   - **Olivia** (056): https://arweave.net/MgsNlTetzAoVEC6E-lswj65vp7StkOZXXd5OjjqzYZI

**转换步骤**：
```bash
# 1. 下载 .vrm 文件（浏览器或 curl）
curl -o avatar.vrm "https://arweave.net/gwG7w4bY-A5c3R6A6GOz3xBCgbPvkFQmqPIDtvnNsYI"

# 2. 在 Blender 中转换
#    - 安装 UniVRM 插件：https://github.com/vrm-c/UniVRM
#    - 导入 VRM → 导出 glTF 2.0 Binary (.glb)

# 3. 推送到设备
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb
```

**注意事项**：
- VRM 的 Blendshape 命名可能是 VRM 规范（如 `aa`、`ee`、`blink`）
- 需要重命名为项目规范（`jawOpen`、`exp_blink`、`vis_A` 等）
- 可在 Blender 中重命名 Shape Key 后导出

---

### 🥈 方案 2：MakeHuman + Blender（自产，最可控）⭐ 最稳

**为什么推荐**：
- ✅ **完全可控**，无授权问题
- ✅ **可精确控制** morph targets 命名
- ✅ **商用友好**（CC0 导出）

**工具下载**：
- **MakeHuman**：https://www.makehumancommunity.org/
- **Blender**：https://www.blender.org/

**制作流程**：
1. **MakeHuman 生成基础人形**
   - 调整体型/脸型/性别
   - 导出为 `.mhx2` 或 `.fbx`

2. **Blender 中添加 Shape Keys**
   ```
   必需：
   - jawOpen（下颌开合）
   - exp_blink（眨眼）或 eyeBlinkLeft/eyeBlinkRight
   
   建议：
   - exp_smile（微笑）
   - vis_A, vis_I, vis_U, vis_E, vis_O（元音口型）
   - vis_Sil（静音）
   ```

3. **导出为 glTF 2.0 Binary (.glb)**
   - File → Export → glTF 2.0
   - Format: glTF Binary (.glb)
   - 勾选：Include Shape Keys

**优点**：
- 完全符合项目命名规范
- 可制作半身像（只做上半身，节省资源）
- 可控制面数（推荐 2-5 万三角面）

---

### 🥉 方案 3：Sketchfab（免费 CC0 模型）

**平台**：https://sketchfab.com/

**搜索关键词**：
- `glb character blendshapes`
- `glTF human avatar CC0`
- `free character model`

**筛选条件**：
- License = CC0（免费商用）
- Format = glTF/glb
- Features = Downloadable

**注意事项**：
- ⚠️ 需确认模型是否包含 Blendshapes
- ⚠️ 部分模型可能只有骨骼动画，没有 Morph Targets
- ⚠️ 下载后需在 Blender 中检查并补充所需 Shape Keys

---

### 方案 4：glTF Sample Models（官方示例）

**仓库**：https://github.com/KhronosGroup/glTF-Sample-Models

**可用模型**：
- `RiggedSimple` - 简单人形，含骨骼
- `RiggedFigure` - 更完整的人形

**限制**：
- ⚠️ 官方示例可能不包含完整 Blendshapes
- ⚠️ 需要在 Blender 中补充 Morph Targets

---

## 三、模型资源预算（重要）

| 项目 | 上限 | 推荐 | 说明 |
|------|------|------|------|
| **三角面** | 25 万 | 2～5 万 | 超过上限可能影响性能 |
| **纹理数量** | 3 张 | 2～3 张 | Albedo、Normal、ORM |
| **单张纹理** | 2K | 1K～2K | 过大影响内存和加载速度 |
| **文件大小** | - | < 50MB | glb 文件总大小 |

---

## 四、Morph Targets 命名规范（必须遵守）

### 4.1 口型（Viseme）- 必选

| 名称 | 必选 | 说明 |
|------|------|------|
| `jawOpen` | ✅ | 下颌开合（RMS 口型也使用） |
| `vis_Sil` | 建议 | 静音/闭口 |
| `vis_A`, `vis_I`, `vis_U`, `vis_E`, `vis_O` | 建议 | 元音口型 |
| `vis_P`, `vis_B`, `vis_M`, `vis_F`, `vis_V`, `vis_S`, `vis_T` | 可选 | 辅音口型 |

**校验**：至少存在 `jawOpen`，否则会 fallback 到 RMS 驱动。

### 4.2 表情（Expression）- 必选

| 名称 | 必选 | 说明 |
|------|------|------|
| `exp_blink` 或 `eyeBlinkLeft`/`eyeBlinkRight` | ✅ 至少其一 | 眨眼 |
| `exp_smile` | 建议 | 微笑（说话时微抬） |
| `exp_browRaise` | 可选 | 眉心上扬 |
| `exp_browFrown` | 可选 | 皱眉 |

**校验**：至少存在一个可用于眨眼的 morph。

---

## 五、快速开始（推荐流程）

### 步骤 1：选择一个模型

**最快方式**（推荐）：
```bash
# 下载 Open Source Avatars 的 Robert 模型
curl -o robert.vrm "https://arweave.net/gwG7w4bY-A5c3R6A6GOz3xBCgbPvkFQmqPIDtvnNsYI"
```

**或访问官网选择**：
- https://opensourceavatars.com
- 浏览缩略图，选择喜欢的角色
- 点击下载 VRM 文件

### 步骤 2：转换为 glb

1. **安装 Blender**（如果还没有）
   ```bash
   # macOS
   brew install --cask blender
   
   # 或从官网下载：https://www.blender.org/download/
   ```

2. **安装 UniVRM 插件**
   - 下载：https://github.com/vrm-c/UniVRM/releases
   - Blender → Edit → Preferences → Add-ons → Install
   - 选择下载的 `.zip` 文件
   - 启用 "VRM Add-on for Blender"

3. **导入并转换**
   - File → Import → VRM (.vrm)
   - 选择下载的 `.vrm` 文件
   - 检查 Shape Keys（Object Data Properties → Shape Keys）
   - 重命名 Shape Keys 以符合项目规范（如果需要）
   - File → Export → glTF 2.0
   - Format: glTF Binary (.glb)
   - 保存为 `avatar.glb`

### 步骤 3：推送到设备

```bash
# 创建目录（如果不存在）
adb shell mkdir -p /sdcard/iptv-agent-models/digital-human

# 推送模型文件
adb push avatar.glb /sdcard/iptv-agent-models/digital-human/avatar.glb

# 验证文件
adb shell ls -lh /sdcard/iptv-agent-models/digital-human/
```

### 步骤 4：运行应用

```bash
# 重启应用
adb shell am force-stop com.joctv.agent
adb shell am start -n com.joctv.agent/.MainActivity

# 查看日志确认加载
adb logcat -s FilamentRenderer DigitalHumanController
```

---

## 六、技术优势总结

### ✅ Filament 的优势

1. **GPU 渲染为主**
   - 使用 OpenGL ES 3.0 / Vulkan
   - CPU 只做控制逻辑（权重计算、状态机）
   - 不占用 NPU（NPU 留给 ASR）

2. **性能优秀**
   - Google 开源，专为移动设备优化
   - 支持 PBR（物理渲染）
   - 支持 Morph Targets（口型驱动）
   - 支持骨骼动画

3. **集成简单**
   - Android 原生支持（Kotlin/Java）
   - 已有现成的 `ModelViewer` 封装
   - 支持 glTF 2.0 标准格式

4. **资源可控**
   - 模型外置（`/sdcard/iptv-agent-models/digital-human/`）
   - 可随时替换，无需重新编译
   - 支持 fallback 到 assets

---

## 七、常见问题

### Q1: VRM 模型的 Blendshape 命名不匹配怎么办？

**A**: 在 Blender 中重命名 Shape Keys：
1. 选择模型对象
2. Object Data Properties → Shape Keys
3. 双击 Shape Key 名称进行重命名
4. 导出 glb 时命名会被保留

### Q2: 模型太大，加载慢怎么办？

**A**: 优化建议：
- 减少三角面数（2-5 万推荐）
- 压缩纹理（1K-2K 分辨率）
- 使用 glTF 压缩工具（如 `gltf-pipeline`）

### Q3: 模型没有 Blendshapes 怎么办？

**A**: 在 Blender 中添加：
1. 选择模型 → Object Data Properties
2. 点击 "Shape Keys" 旁边的 "+"
3. 添加 Base Shape Key
4. 添加各个 Morph Targets（jawOpen、exp_blink 等）
5. 编辑每个 Shape Key 的顶点位置
6. 导出时勾选 "Include Shape Keys"

### Q4: 如何测试模型是否符合规范？

**A**: 使用项目中的校验脚本（如果有），或：
1. 在 Blender 中检查 Shape Keys 名称
2. 确认至少包含 `jawOpen` 和 `exp_blink`
3. 推送到设备后查看日志确认加载成功

---

## 八、推荐资源链接汇总

| 资源 | 链接 | 格式 | Blendshapes | 授权 |
|------|------|------|-------------|------|
| **Open Source Avatars** | https://opensourceavatars.com | VRM | ✅ | CC0 |
| **MakeHuman** | https://www.makehumancommunity.org/ | 导出多种 | 需自加 | CC0 |
| **Sketchfab CC0** | https://sketchfab.com/3d-models?features=downloadable&q=glb+character | glb | 需确认 | CC0 |
| **glTF Samples** | https://github.com/KhronosGroup/glTF-Sample-Models | glb | 部分 | 开源 |
| **UniVRM (Blender)** | https://github.com/vrm-c/UniVRM | VRM→glb | ✅ | MIT |

---

## 九、总结

### ✅ 当前方案优势

1. **技术栈成熟**：Filament + glTF + Morph Targets
2. **性能优秀**：GPU 渲染，不占用 CPU/NPU
3. **资源丰富**：100+ 免费商用模型可选
4. **集成简单**：已有完整代码框架

### 🎯 推荐行动

1. **立即行动**：下载 Open Source Avatars 的模型，转换为 glb
2. **测试验证**：推送到设备，确认渲染和口型驱动正常
3. **优化迭代**：根据实际效果调整模型或添加更多 Morph Targets

### 📝 下一步

- 如果模型加载成功，可以开始优化口型驱动
- 如果模型不符合规范，在 Blender 中调整 Shape Keys
- 如果需要自定义模型，使用 MakeHuman + Blender 制作

---

*文档基于项目现有实现和调研结果整理，适用于 RK3576 Android 机顶盒环境。*
