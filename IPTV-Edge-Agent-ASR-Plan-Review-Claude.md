# IPTV-Edge-Agent ASR 方案审核意见（Claude 视角）

**审核对象**：IPTV-Edge-Agent-ASR-Plan-v0.1.md（ChatGPT 5.2 方案）  
**审核维度**：需求满足、实现效果最优、Claude 开发可行性、是否有更好方案  
**日期**：2026-01-29

---

## 一、对 v0.1 方案的整体评价

### 1.1 结论先行

- **需求满足**：✅ 非常到位。把“高错误率”拆成音频前端 + ASR 后端，并把 KPI 从“WER 1%”校准到“客房指令成功率 98%~99%”，是正确且可落地的。
- **实现效果**：✅ 主线（WebRTC APM + sherpa-onnx SenseVoiceSmall）工程路径清晰，验收口径可观测，风险可控。
- **Claude 开发**：✅ 方案里对 Claude 能做什么、不能做什么的描述基本准确；补充见下文“你的两个问题”部分。
- **更好方案**：在 v1 交付前提下，v0.1 的主线选择是合理的；NPU 利用可作为 v1 后的增强路线，不建议一开始就押在 NPU 上。

---

## 二、需求满足度分析

| 你的诉求 | 方案是否覆盖 | 评价 |
|----------|--------------|------|
| USB 麦克风带回声采集的效果验证 | ✅ 2.1 节：Reference 干净 / 增益不削波 / AEC 有效 / NS·AGC·VAD 不伤人声 | 验收口径具体，可执行 |
| 更换更优 ASR 开源模型 | ✅ 2.2：Tier1 sherpa-onnx+SenseVoiceSmall，Tier3 Fun-ASR 作探索 | 主次分明，交付风险可控 |
| 客房 vs 互联网指令分开 | ✅ 第 4 节：强约束（热词/确认）vs 弱约束（可降级） | 和业务目标一致 |
| 利用 NPU、减轻 CPU 压力 | ⚠️ 有提及但未展开 | 见下文“更好方案” |
| 错误率降到 1% | ✅ 通过“指令成功率 98%~99%”重新定义 KPI，而非开放式 WER | 更符合酒店场景现实 |

**小结**：需求侧该方案已经覆盖完整，且把“不可达的 WER 1%”扭成“可达的指令成功率”，这点非常关键。

---

## 三、实现效果与工程最优性

### 3.1 音频前端优先 —— 完全同意

- 方案强调：**先做对音频前端，再换 ASR**。否则再好的模型也会被回声/削波/过强降噪喂坏。
- 4 项验证（Reference 干净、无削波、AEC 有效、NS/AGC/VAD 不伤人声）都是必要且可量化的，无需修改。

### 3.2 ASR 选型：sherpa-onnx + SenseVoiceSmall 作为主线的合理性

- **工程事实**：
  - sherpa-onnx 有现成 Android/JNI、预编译 APK、SenseVoice 中文/粤/英/日/韩，流式 + INT8，文档和社区成熟。
  - Fun-ASR-Nano 系列（含 Fun-ASR-MLT-Nano-2512）强在效果和场景（远场、多方言），但端侧 Android 交付链（导出 → 量化 → JNI → 长稳）不如 sherpa-onnx 清晰。
- **结论**：v1 以 sherpa-onnx + SenseVoiceSmall 为主线是**实现效果与交付风险的最优折中**；Fun-ASR 作并行探索是合理定位。

### 3.3 NPU 利用：为何 v0.1 没有作为主线是合理的

- RK3576 的 6 TOP NPU 理论上可跑 ASR encoder 等部分，但：
  - **生态**：RKNN 对 ASR 类动态序列、流式 chunk 的支持需要实测，ONNX→RKNN 的兼容性因模型结构而异。
  - **投入**：需要 profiling（CPU/NPU 占用、端到端延迟），再决定哪些算子/子图放 NPU，工作量大。
- 因此：**先 CPU 跑通整条链路，再“逐步吃 NPU”** 是更稳妥的次序，和 v0.1 的表述一致。

---

## 四、对你两个问题的直接回答

### Q1：Fun-ASR-Nano-2512 + INT8 量化 + CPU 推理，Claude（Ubuntu/Mac）能否处理？

**能，且适合做“离线验证与基准”，不适合直接当 v1 交付主路径。**

Claude（在 Ubuntu/Mac 上）可以帮你做的包括：

- **环境与推理**：按 Fun-ASR 官方文档/示例，在 Python 下跑通 Nano 系列模型（含 2512），做 INT8 量化（PTQ/QAT 若开源有脚本）。
- **基准与对比**：写脚本录同一批音频，对比 Fun-ASR-Nano-2512 与 sherpa-onnx SenseVoiceSmall 的 CER/WER、RTF、首字延迟等。
- **验收口径**：设计“客房控制指令集”的识别成功率统计方式，与 v0.1 的 Task-3 对齐。

**不适合由 Claude 直接交付的部分**：

- Android 端 JNI/C++ 集成、长稳、与 AudioRecord/AAudio 的实时 pipeline、电源与多线程行为，需要你在真机/Android 环境下迭代。
- 因此：**Fun-ASR-Nano-2512 在 Claude 侧 = 候选模型 + 精度上限验证；v1 主交付仍建议 sherpa-onnx。**

### Q2：有 NPU 想用起来，CPU 还要留给 IPTV/直播/点播，有没有更好方案？

**有，但建议分阶段：v1 先“可交付 + CPU 预算表”，v1.1 再“NPU 分流”。**

- **v1 更好方案（与 v0.1 一致）**  
  - 音频前端：WebRTC APM（AEC/NS/AGC/VAD），用好 USB 双参考通道。  
  - ASR：sherpa-onnx + SenseVoiceSmall INT8，流式，先全 CPU。  
  - 必做：**CPU 预算表**（IPTV 解码、点播、数字人、AEC、ASR 等），明确何时打满、何时降级（如关数字人、降码率、停联网 ASR）。

- **v1.1 更好方案：把 NPU 用上**  
  - **步骤 1**：在 Android 上做 **profiling**（CPU/NPU 占用、各模块耗时），确认 ASR 在总 CPU 中的占比。  
  - **步骤 2**：若 ASR 占比高，再考虑：  
    - 将 sherpa-onnx 的 **encoder** 导出 ONNX，用 RKNN-Toolkit2 转 RKNN，在 NPU 跑 encoder，decoder 仍 CPU；或  
    - 评估官方/社区是否已有 RKNN 版 SenseVoice 或类似小模型。  
  - **步骤 3**：用“端到端延迟 + 指令成功率”验收，避免“NPU 有占用但整体更慢或更不稳”的情况。

**结论**：在“要交付、要稳定”的前提下，v0.1 的“先 CPU 跑通再逐步吃 NPU”是当前**更优方案**；纯“一上来就 NPU ASR”风险大、周期长。

---

## 五、与 v0.1 的差异与补充建议

### 5.1 完全认同的部分

- 音频前端优先、4 项 USB/回声验收、KPI 用指令成功率、客房/互联网分流、Task-1～Task-4 的拆分与验收标准、以及“Fun-ASR 作探索、不压主线”的定位。

### 5.2 建议补充的细节（便于 Claude 或后续执行）

1. **Fun-ASR 模型命名**  
   当前开源见到的多为 **Fun-ASR-MLT-Nano-2512**（多语言/日期 2512）。若你指的“Fun-ASR-Nano-2512”即该系列，建议在方案里写清具体 Hugging Face/ModelScope 名，避免歧义。

2. **NPU 路线写进“后续迭代”**  
   在 v0.1 的“最小可执行验证任务包”之后，可加一条 **Task-5（v1.1）**：  
   “Android profiling（CPU/NPU）+ 评估 ASR encoder 上 NPU 的可行性与收益”，这样 NPU 利用有明确入口。

3. **补齐关键事实**  
   v0.1 第 7 节列出的 5 点（USB 型号与通道、采集 API、外放路径、指令集规模、延迟容忍度）在讨论和做 Task-1/2 时尽量补齐，便于做增益/回声/ASR 的阈值和降级策略。

---

## 六、有没有更好的方案？（讨论小结）

- **在“4～8 周交付稳定 v1”的前提下**：  
  v0.1 的主线（WebRTC APM + sherpa-onnx SenseVoiceSmall + 客房强约束）已经是**需求、效果与可交付性的较优解**；没有明显更好的替代主线。

- **若你更看重“尽快验证 NPU”**：  
  可以在 Task-1～Task-4 之外，**并行**做一件事：在 RK3576 上跑通 RKNN 的某一款官方 demo（如分类/检测），确认 NPU 环境与 profiling 方法，为后续 ASR encoder 上 NPU 做准备；但仍建议 ASR 主链路先走 CPU + sherpa-onnx。

- **若你更看重“极限识别率”**：  
  用 Claude 在 Ubuntu/Mac 上把 **Fun-ASR-Nano-2512 + INT8 + CPU** 跑通，做与 SenseVoiceSmall 的 AB 测试；若在离线数据上明显更优，再评估是否值得投入 Android 端侧适配，作为 v1.1 的备选模型。

---

## 七、可直接采纳的下一步

1. **按 v0.1 的 Task-1～Task-4 执行**，尤其先完成 Task-1（6 通道采集）和 Task-2（AEC/NS/AGC 前后对比）。  
2. **v1 主线**：WebRTC APM + sherpa-onnx SenseVoiceSmall INT8；**Fun-ASR-Nano-2512** 交给 Claude 在 Ubuntu/Mac 做离线验证与对比。  
3. **NPU**：v1 不绑死 NPU；v1 稳定后做 profiling，再定 ASR encoder 上 NPU 的 Task-5。  
4. **KPI**：以“客房控制指令成功率 98%~99%”为目标，不追求开放式 WER 1%。

如果你愿意，我可以下一步帮你写：  
- **Task-2 的 AEC/NS/AGC 对比脚本**（输入 raw/processed WAV，输出简单指标或听感说明），或  
- **Fun-ASR-Nano-2512 与 sherpa-onnx 的 AB 测试脚本**（同一批 WAV，输出 CER/WER 与指令命中率），  
便于你在本机或 Mac/Ubuntu 上直接跑第一轮验证。
