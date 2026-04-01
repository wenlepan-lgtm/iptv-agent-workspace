# CoolBee 数字人集成 - 进度报告

**时间**: 2026-02-18 23:03
**状态**: ✅ 模型加载成功，渲染正常，60fps 稳定运行

---

## 一、问题诊断与解决

### 1.1 原始问题

原始 CoolBee glb 文件 (`Untitled.glb`) 在 Filament 中加载时崩溃：

```
Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0xcd
Cause: null pointer dereference
```

### 1.2 根本原因分析

原始 glb 文件包含以下不兼容特性：

| 特性 | 状态 | 影响 |
|------|------|------|
| **KHR_draco_mesh_compression** | ❌ 必须 | Draco 压缩，Filament 默认不支持 |
| **Sparse Accessors (24个)** | ❌ 可能 | 稀疏访问器，可能导致解析错误 |
| **KHR_materials_clearcoat** | ⚠️ 可选 | Filament 可能忽略 |
| **KHR_materials_specular** | ⚠️ 可选 | Filament 可能忽略 |
| **Skinning (39 joints)** | ⚠️ 可选 | 骨骼蒙皮 |
| **Morph Targets (12个)** | ✅ 需要 | 口型/表情驱动 |

### 1.3 解决方案

通过 Python 脚本重建 glb 文件：

1. **解码 Draco 压缩**: 使用 `gltf-transform copy` 解压
2. **提取基础网格**: 只保留 POSITION, NORMAL, TEXCOORD_0
3. **转换 Sparse 为 Dense**: 将稀疏 morph target 数据转换为密集格式
4. **移除不兼容扩展**: 去掉 skinning、materials、textures
5. **重建 glb**: 生成 Filament 兼容的文件

---

## 二、最终模型

### 2.1 文件信息

```
文件: avatar_coolbee_with_morphs.glb
大小: 303,932 bytes (~297 KB)
位置:
  - 本地: /Users/ala/工作项目/agent/avatar_coolbee_with_morphs.glb
  - Assets: iptv-edge-agent/app/src/main/assets/digitalhuman/avatar_coolbee.glb
  - 设备: /sdcard/iptv-agent-models/digital-human/avatar.glb
```

### 2.2 模型规格

| 属性 | 值 |
|------|-----|
| 顶点数 | 1,616 |
| 三角形数 | 2,740 |
| Morph Targets | 12 |
| 动画 | 0 |
| 材质 | 无 (使用默认光照) |

### 2.3 Morph Target 映射

| 原始名称 | 映射名称 | 用途 |
|----------|----------|------|
| `blendShape1.mouth_a` | `vis_A` | 口型 A |
| `blendShape1.mouth_e` | `vis_E` | 口型 E |
| `blendShape1.mouth_i` | `vis_I` | 口型 I |
| `blendShape1.mouth_o` | `vis_O` | 口型 O |
| `blendShape1.mouth_u` | `vis_U` | 口型 U |
| `blendShape1.blink` | `exp_blink` | 眨眼 |
| `blendShape1.Blink_L` | `eyeBlinkLeft` | 左眼眨眼 |
| `blendShape1.Blink_R` | `eyeBlinkRight` | 右眼眨眼 |
| `blendShape1.happy` | `exp_smile` | 微笑 |
| `blendShape1.angry` | - | 愤怒 (未映射) |
| `blendShape1.sorrow` | - | 悲伤 (未映射) |
| `blendShape1.joy` | - | 开心 (未映射) |

---

## 三、测试结果

### 3.1 加载测试

```
✅ Filament native libraries loaded
✅ FilamentRenderer initialized
✅ Surface available: 465x583
✅ ModelViewer created
✅ Model loaded: 12 morphs, 0 animations
✅ All morph targets mapped correctly
✅ Blink morph identified: blendShape1.blink
✅ Rendering started
```

### 3.2 渲染性能

```
Frame Rate: ~59 FPS
Stability: 稳定，无崩溃
```

### 3.3 Morph Target 映射日志

```
Mapped morph: blendShape1.mouth_a -> vis_A
Mapped morph: blendShape1.mouth_e -> vis_E
Mapped morph: blendShape1.mouth_i -> vis_I
Mapped morph: blendShape1.mouth_o -> vis_O
Mapped morph: blendShape1.mouth_u -> vis_U
Mapped morph: blendShape1.blink -> exp_blink
Mapped morph: blendShape1.happy -> exp_smile
Mapped morph: blendShape1.Blink_R -> eyeBlinkRight
Mapped morph: blendShape1.Blink_L -> eyeBlinkLeft
Blink morph: blendShape1.blink (mapped from available morphs)
```

---

## 四、待完成事项

### 4.1 视觉验证 (需要用户确认)

- [ ] 数字人是否在屏幕上可见
- [ ] 模型外观是否正常 (无贴图，使用默认材质)
- [ ] 眨眼动画是否工作
- [ ] 口型是否随 TTS 驱动

### 4.2 功能验证 (需要用户测试)

- [ ] ASR 触发时数字人飞入显示
- [ ] TTS 发声时口型驱动
- [ ] TTS 结束 30 秒后自动隐藏

### 4.3 可选优化

1. **添加贴图**: 从原始模型提取贴图并应用
2. **调整相机**: 可能需要调整相机位置以更好显示小蜜蜂
3. **添加动画**: 在 Blender 中添加翅膀动画

---

## 五、技术细节

### 5.1 转换脚本

转换过程使用 Python 脚本完成，主要步骤：

1. 读取原始 glb 的 JSON 和二进制数据
2. 解析 sparse accessor 数据结构
3. 将稀疏数据转换为密集数组
4. 重建符合 Filament 要求的 glb 结构
5. 保留所有 morph target 数据和名称

### 5.2 Filament 兼容性说明

Filament gltfio 的限制：
- ❌ 不支持 KHR_draco_mesh_compression
- ⚠️ Sparse accessors 可能有 bug
- ✅ 支持标准的 morph targets
- ✅ 支持基本的 PBR 材质

---

## 六、文件清单

| 文件 | 位置 | 用途 |
|------|------|------|
| `avatar_coolbee_with_morphs.glb` | 项目根目录 | 最终工作模型 |
| `avatar_coolbee.glb` | assets/digitalhuman/ | Assets 中的备份 |
| `Untitled.glb` | 项目根目录 | 原始 VRM 转换文件 (4.1 MB) |
| `avatar_coolbee_extracted.glb` | 项目根目录 | 最小化测试版本 (无 morph) |

---

**备注**: 模型目前没有贴图，显示为灰色/白色。如需贴图，需要从原始 glb 提取并重新应用。
