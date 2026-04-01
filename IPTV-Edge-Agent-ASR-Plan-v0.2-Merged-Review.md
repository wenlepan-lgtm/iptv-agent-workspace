# IPTV Edge Agent 语音链路优化/重构方案 v0.2（合并 Cursor/Claude 审核意见）
**日期**：2026-01-29  
**适用**：RK3576 Android TV（USB 4MIC + 2Reference 回采），客房控制 vs 互联网指令分流，2D/3D 数字人展示  
**v1 目标**：先交付稳定可用（“指令成功率”优先），再做性能迁移（NPU）

---

## 0) 结论（拍板版）
- **先 CPU 跑通**：WebRTC APM（AEC/NS/AGC/VAD）+ sherpa-onnx + SenseVoiceSmall INT8（流式）作为 v1 主线。
- **KPI 改口径**：不追“开放式 WER 1%”，改为 **客房控制域指令成功率 98%~99%**。
- **Fun-ASR-Nano-2512**：定位为“离线对比/精度上限探索”，不进入 v1 主交付链路（端侧 Android 交付链风险更高）。
- **NPU**：v1 不强绑定；v1.1 加 Task-5：profiling + 评估 encoder 下沉 NPU 的收益与路径。

---

## 1) 目标与指标（必须写死）
### 1.1 两类场景两套指标
**客房控制域（强约束）**
- 主指标：**Command Success Rate（指令成功率）**：目标 98%~99%
- 次指标：首字延迟、端到端延迟、p95/p99 抖动、误触发率

**互联网域（弱约束）**
- 主指标：可用性（问答能继续推进）
- 次指标：失败兜底（复述确认/改问法/联网/降级）

### 1.2 统一“验收样本集”
- 固定同一批录音（近讲/远讲/电视外放/噪声/多人干扰）
- 固定命名与标签：场景、距离、音量、是否外放、是否回声

---

## 2) v1 主线（交付优先）
### 2.1 音频前端：WebRTC APM（AEC/NS/AGC/VAD）
**必须先通过 4 项验收：**
1) Reference 通道干净（主要是系统播放声）
2) MIC 通道无明显削波(clipping)，增益可控
3) AEC 有效（回声残留显著降低）
4) NS/AGC/VAD 不“伤人声”（尾音/轻辅音不被抹掉）

> 注意：这一步不通过，换再强 ASR 都没意义。

### 2.2 ASR：sherpa-onnx + SenseVoiceSmall（INT8，流式）
**理由**
- Android/JNI/预编译示例/文档生态成熟
- 流式延迟更可控，工程交付链清晰

### 2.3 业务侧分流与兜底（强烈建议）
**客房控制域（强约束）**
- 热词/词表（设备、动作、房间实体）
- 低置信度触发二次确认（提升成功率）

**互联网域（弱约束）**
- 允许更强模型/联网/语义纠错
- 限频 + 降级策略（避免抢 IPTV CPU）

---

## 3) Fun-ASR 并行探索（非主线）
### 3.1 模型命名澄清（避免歧义）
- 开源常见命名为：**Fun-ASR-MLT-Nano-2512**（或同系列 2512 版本）
- 方案与脚本中必须写清具体的 ModelScope/HF 模型 ID

### 3.2 Claude/GLM4.7 可承担的工作（Ubuntu/Mac）
- 跑通 Python 推理 demo
- 做离线 AB：与 SenseVoiceSmall 对比 CER/WER、RTF、首字延迟
- 做 INT8 量化尝试（如有官方/社区脚本优先）

---

## 4) NPU 路线（v1.1 增强，不提前绑死）
### 4.1 原则
- NPU 不等于“更少调试”，而是更底层的调试。
- 必须建立在 v1 CPU 基线之上，才能确定性定位问题。

### 4.2 Task-5（v1.1）：profiling + encoder 下沉评估
**步骤**
1) Android profiling：CPU/NPU 占用、各模块耗时（AEC/ASR/TTS/渲染/解码）
2) 若 ASR 占比高：评估 **仅 encoder 下沉 NPU**（decoder 仍 CPU）
3) 以“端到端延迟 + 指令成功率”验收，避免“占用上去了但更慢/更不稳”

---

## 5) 最小可执行任务包（SOP）
### Task-1：Android 侧验证 USB 6 通道采集
- 确认通道数、采样率、通道顺序
- 输出：原始多通道 WAV（含 time-aligned reference）

**验收**
- Reference 通道可用且同步合理
- MIC 通道无严重削波/爆音

### Task-2：AEC/NS/AGC 前后对比（离线）
- raw / AEC / AEC+NS / AEC+NS+AGC 四版本输出
- 统一听感对比 + 波形/能量检查（是否削波、是否“糊掉”）

### Task-3：ASR AB（同一批样本）
- SenseVoiceSmall INT8（流式/离线）
- Fun-ASR-MLT-Nano-2512（离线，探索）
- 输出：CER/WER + **指令成功率**（按指令集统计）

### Task-4：CPU 预算表（必须做）
- IPTV 解码/点播/数字人渲染/AEC/ASR/TTS 的 CPU/内存占用
- 明确降级策略触发条件（如关数字人、降码率、停联网 ASR）

### Task-5（v1.1）：NPU profiling & encoder 下沉
- 见第 4 节

---

## 6) 需要补齐的关键事实（讨论/实施必填）
1) USB 麦克风型号 + 通道定义（4MIC+2Ref 的排列）
2) Android 采集 API（AudioRecord/AAudio/OpenSL ES）与采样率/帧长
3) 电视外放路径与音量范围（HDMI/喇叭），reference 是否稳定同步
4) 客房控制指令集规模（动作/实体/同义词）
5) 端到端延迟容忍度（<300ms / <600ms / <1s）

（完）
