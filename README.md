# JOCTV Agent

JOCTV Agent 是一个面向 Android TV / IPTV 大屏场景的本地语音智能体项目，运行在 RK3576 等边缘硬件平台上，目标是实现稳定、低延迟、可落地的端侧语音交互体验。

项目聚焦"端侧可用"而不是纯 Demo，核心链路包括：

- 唤醒词检测（关键词匹配 + VAD 门控）
- 离线 ASR（sherpa-onnx / SenseVoice，NPU 加速）
- 流式 LLM 响应（兼容 OpenAI 格式，支持多语言检测）
- 本地 TTS 播报（Matcha / Piper 双引擎，中英文切换）
- 数字人联动展示（MNN / TaoAvatar，口型同步）
- 电视业务控制与场景指令执行（灯光、服务呼叫等本地意图路由）

## 适用场景

- 酒店 IPTV 智能语音服务
- Android TV 大屏语音助手
- 边缘侧本地语音交互终端
- 需要离线能力、低延迟和设备侧可控性的语音系统

## 项目特点

- 面向 RK3576 Android TV 平台优化
- 本地音频链路：VAD → ASR → LLM → TTS → 数字人，全链路可追踪
- 流式语音回复与数字人口型联动
- 业务指令路由：灯光、天气、服务呼叫等场景
- 状态机驱动的对话管理（IDLE → LISTENING → SPEAKING → READY → IDLE）
- 强调真实设备调试、启动链路可靠性与异常路径兜底

## 当前技术栈

| 模块 | 技术方案 |
|------|---------|
| 平台 | Android TV / RK3576 |
| 语言 | Kotlin / Java |
| ASR | sherpa-onnx (SenseVoice)，NPU 加速 |
| TTS | Matcha (中文) + Piper (英文)，CPU 推理 |
| 数字人 | MNN / TaoAvatar |
| LLM | 兼容 OpenAI 格式的流式接口 |
| 音频前端 | 平台 AEC/NS/AGC（验证中）+ VAD + Ducking |

## 当前进展

项目已完成基础语音链路集成，并持续针对以下问题做工程化优化：

- 启动阶段 readiness 门禁（ASR/TTS 就绪状态协同）
- TTS / ASR 状态机一致性与错误恢复
- welcome 播报稳定性与唤醒词打断支持
- 音频前端回声消除与误识别抑制
- 连续对话窗口与流式响应时序协同

## 设计目标

这个项目不是通用聊天机器人外壳，而是一个面向真实终端场景的"可部署语音系统"：

- 启动链路可控
- 状态切换可解释
- 设备日志可回溯
- 异常路径可兜底
- 交互体验可持续优化

## 仓库结构

```
iptv-edge-agent/          # Android 主工程
  app/src/main/java/      # Kotlin 源码
    com/joctv/agent/
      MainActivity.kt             # 主 Activity，集成入口
      conversation/               # 对话状态机
      asr/                        # ASR 控制器与 VAD
      tts/                        # TTS 编排器与引擎
      web/                        # LLM / Web 交互
      intent/                     # 本地意图路由
      digitalhuman/               # 数字人控制
project-docs/              # 工程文档与任务记录
```
