# Gate 门控实现总结

## 实现概述

实现了基于 es8388 loopback 参考信号的门控系统，用于防止播放器/电视声音被 ASR 误识别。

## 架构

```
HK-ARRAYMIC (2ch@48k)    ES8388 Loopback (Ref)
        │                            │
        │                            │
        ▼                            ▼
   StereoToMono                 RefCapture
        │                            │
        └────────┬─────────────────────┘
                 │
                 ▼
             SimpleGate
           (能量/相关性判定)
                 │
                 ▼
        ┌────────┴────────┐
        │                 │
    shouldPass=true   shouldPass=false
        │                 │
        ▼                 ▼
     喂给 ASR         阻断（不喂）
```

## 实现的文件

### 1. Es8388LoopbackController.kt
位置: `app/src/main/java/com/joctv/agent/audio/Es8388LoopbackController.kt`

功能：
- 应用启动时自动执行 tinymix 命令启用 es8388 loopback
- `tinymix 22 Enable` - SAI1 SDI0 Loopback Switch
- `tinymix 14 Enable` - SAI1 SDI0 Loopback I2S LR Switch
- 提供 verifyLoopback() 验证功能

### 2. RefCapture.kt
位置: `app/src/main/java/com/joctv/agent/audio/RefCapture.kt`

功能：
- 使用 AudioRecord 采集 es8388 capture 设备
- 格式: 48kHz, 16bit, 2ch (只取 R 通道作为 ref)
- 20ms 一帧 (960 samples/channel)
- Ring buffer 实现稳定采集
- 提供 `getRefFrame()` 接口获取参考帧

### 3. SimpleGate.kt
位置: `app/src/main/java/com/joctv/agent/audio/SimpleGate.kt`

功能：
- 计算 ref 能量 (dB)
- 计算归一化相关系数 (corr)
- 滞回状态机:
  - 连续 on_frames 帧满足条件 → 进入 blocked 状态
  - 连续 off_frames 帧不满足 → 解除 blocked 状态
- 默认参数:
  - refOnDb = -45
  - corrOn = 0.35
  - onFrames = 3 (60ms)
  - offFrames = 5 (100ms)

### 4. AsrController.kt 修改
位置: `app/src/main/java/com/joctv/agent/asr/AsrController.kt`

修改内容：
- 添加 Gate 相关配置参数
- 添加 RefCapture 和 SimpleGate 变量
- startContinuousAsr(): 初始化并启动 RefCapture
- stopContinuousAsr(): 停止 RefCapture 并打印统计
- 2ch@48k 处理流程: 在数据喂给 ASR 前应用 Gate

### 5. SpeechRecognitionApplication.kt 修改
位置: `app/src/main/java/com/joctv/agent/SpeechRecognitionApplication.kt`

修改内容：
- onCreate() 中调用 Es8388LoopbackController.enableLoopback()

### 6. config.properties.template 更新
添加的配置项：
```properties
# Gate 开关
asr.gate.enabled=false

# Gate 参数
asr.gate.ref_on_db=-45
asr.gate.corr_on=0.35
asr.gate.on_frames=3
asr.gate.off_frames=5
```

## 使用方法

### 启用 Gate

1. 编辑 `app/src/main/assets/config.properties` 或创建配置文件:
```properties
asr.gate.enabled=true
asr.gate.ref_on_db=-45
asr.gate.corr_on=0.35
```

2. 重新编译安装应用

3. 应用启动后会自动:
   - 执行 tinymix 命令启用 es8388 loopback
   - 启动 RefCapture 采集参考信号
   - 在 ASR 处理流程中应用 Gate

### 调试日志

```bash
adb logcat | grep -E "ES8388_Loopback|RefCapture|SimpleGate|Gate:"
```

关键日志：
- `ES8388 loopback enabled` - loopback 启用成功
- `RefCapture started` - 参考信号采集启动
- `Gate BLOCKED` - 进入阻断状态
- `Gate UNBLOCKED` - 解除阻断状态
- `Gate stats: total=X, blocked=Y (Z%)` - 阻断统计

## 参数调优

| 参数 | 默认值 | 说明 | 调优建议 |
|------|---------|------|-----------|
| gate.enabled | false | 是否启用 Gate | 调试时先设为 true |
| ref_on_db | -45 | ref 能量阈值 | 漏阻断则降低，误阻断则提高 |
| corr_on | 0.35 | 相关性阈值 | 漏阻断则降低，误阻断则提高 |
| on_frames | 3 | 进入阻断速度 | 漏阻断则减少 |
| off_frames | 5 | 解除阻断速度 | 解除太快则增加 |

## 验收标准

- [x] Task 1: 启动时固化 es8388 Loopback
- [x] Task 2: RefCapture 采集模块
- [x] Task 3: SimpleGate 门控实现
- [x] Task 4: 帧对齐与 ASR 集成
- [x] 配置文件更新

## 下一步

1. 编译并部署到设备测试
2. 播放视频时验证 Gate 是否正确阻断
3. 说话时验证 Gate 是否正确通过
4. 根据实际情况调优参数
