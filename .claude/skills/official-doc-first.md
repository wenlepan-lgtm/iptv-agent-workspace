# official-doc-first-skill

任何涉及框架/SDK/API 的改动，必须先查官方文档。

## 触发方式
```
/official-doc-first [技术关键词]
```

## 适用场景
- Android API 使用
- Kotlin/Java SDK
- sherpa-onnx ASR
- RKNN NPU
- WebRTC
- 音频 API
- 第三方库

## 执行流程

### Phase 1: 确定查询范围
1. 列出涉及的技术/框架
2. 确定需要查询的 API/功能
3. 确定版本信息

### Phase 2: 查询官方文档
1. 优先查询官方文档
2. 摘要关键约束
3. 记录版本兼容性
4. 注意废弃警告

### Phase 3: 对照现有代码
1. 检查现有实现是否符合官方建议
2. 识别潜在问题
3. 确认是否有更好的官方推荐方案

### Phase 4: 输出结论
1. 官方推荐做法
2. 现有代码对比
3. 建议修改方向

## 输出模板

```markdown
## 官方文档调研报告

### 调研范围
- 技术/框架: [名称]
- 版本: [版本号]
- 查询点: [具体 API/功能]

### 官方文档摘要

#### 关键约束
1. [约束1]
2. [约束2]

#### 推荐做法
1. [做法1]
2. [做法2]

#### 注意事项
1. [注意1]
2. [注意2]

### 现有代码对比
| 方面 | 官方推荐 | 现有实现 | 差异 |
|------|---------|---------|------|
| xxx | yyy | zzz | 是否符合 |

### 建议
[基于官方文档的建议]
```

## 常用官方文档链接

### Android
- Android 官方文档: https://developer.android.com
- Kotlin 文档: https://kotlinlang.org/docs/

### 音频
- Android Audio: https://developer.android.com/reference/android/media/AudioRecord
- WebRTC: https://webrtc.org/

### AI/NPU
- sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx
- RKNN: [瑞芯微官方文档]

### 数字人
- MNN: https://github.com/alibaba/MNN

## 强制约束
- ✅ 必须先查文档再动手
- ✅ 必须标注信息来源
- ✅ 必须注明版本兼容性
- ❌ 不允许基于记忆假设
