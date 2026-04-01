# CosyVoice 3 预研计划

**目标**：验证 CosyVoice 3 是否能解决当前 TTS 英文发音不准问题，并评估 RK3576 部署可行性

**日期**：2026-03-02
**状态**：预研阶段

---

## 1. CosyVoice 3 核心特性

### 1.1 为什么选择 CosyVoice 3

| 特性 | 说明 | 对我们的价值 |
|------|------|-------------|
| **Pronunciation Inpainting** | 支持中文拼音 + 英文 CMU phonemes (ARPAbet) 音素标注 | **解决英文发音不准的核心痛点** |
| **Bi-Streaming** | 双向流式，延迟低至 150ms | 满足实时交互需求 |
| **中英混合** | 9种语言 + 18种中文方言，跨语言零样本克隆 | IPTV 客房控制场景 |
| **模型大小** | 0.5B 参数 (~500M) | 边缘设备可接受 |

### 1.2 ARPAbet 音素标签示例

```
# 精确控制 "minute" 的发音
# 读作 /ˈmɪnɪt/ (分钟) 而不是 /maɪˈnjuːt/ (微小的)
请播放[M][AY0][N][UW1][T]的音乐

# 其他示例
[R][EH1][K][ER0][D] → record
[T][EH1][K][N][IH0][K][AH0][L] → technical
```

### 1.3 性能指标（官方）

| 指标 | Fun-CosyVoice3-0.5B |
|------|---------------------|
| 中文 CER | 1.21% |
| 英文 WER | 2.24% |
| 说话人相似度 (中文) | 78.0% |
| 说话人相似度 (英文) | 71.8% |
| 流式延迟 | ~150ms |

---

## 2. 预研阶段规划

### Phase 1: 本地验证（Ubuntu/Mac）

**目标**：验证英文发音质量和 ARPAbet 功能

**步骤**：
```bash
# 1. 克隆仓库
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice

# 2. 创建环境
conda create -n cosyvoice -y python=3.10
conda activate cosyvoice
pip install -r requirements.txt

# 3. 下载模型 (ModelScope 国内更快)
pip install modelscope
python -c "
from modelscope import snapshot_download
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='pretrained_models/Fun-CosyVoice3-0.5B')
"

# 4. 运行测试
python example.py
```

**验收标准**：
- [ ] 基础中英文合成正常
- [ ] ARPAbet 音素标注生效，英文发音可精确控制
- [ ] 中英混合文本输出自然
- [ ] 记录首次推理延迟和 RTF

### Phase 2: 性能评估

**目标**：评估是否适合 RK3576

**测试项**：
1. **模型大小**：确认最终模型文件体积
2. **CPU 推理速度**：在 ARM 环境测试 RTF
3. **内存占用**：峰值内存
4. **量化测试**：INT8 量化后的质量和速度

**验收标准**：
- [ ] RTF < 1.0（实时以下）
- [ ] 内存峰值 < 1GB
- [ ] 量化后发音质量可接受

### Phase 3: ONNX 转换

**目标**：验证 ONNX 导出可行性

```python
# 参考 CosyVoice ONNX 导出流程
# 1. 导出 LLM encoder
# 2. 导出 Flow decoder
# 3. 导出 HiFi-GAN vocoder
```

**验收标准**：
- [ ] 成功导出 ONNX 模型
- [ ] ONNX 推理结果与 PyTorch 一致
- [ ] ONNX 推理速度提升或持平

### Phase 4: RK3576 部署验证

**目标**：在 RK3576 实机验证

**步骤**：
1. 交叉编译 ONNX Runtime (ARM64)
2. 移植模型到 RK3576
3. 测试实际延迟和 CPU 占用

**验收标准**：
- [ ] 端到端延迟 < 500ms
- [ ] CPU 占用 < 50%
- [ ] 连续运行稳定

---

## 3. 技术要点

### 3.1 ARPAbet 音素表（常用）

| ARPAbet | IPA | 示例 |
|---------|-----|------|
| AA | ɑ | b**o**t |
| AE | æ | c**a**t |
| AH | ʌ | c**u**t |
| AO | ɔ | l**aw** |
| AW | aʊ | h**ow** |
| AY | aɪ | b**uy** |
| EH | ɛ | b**e**d |
| ER | ɝ | b**ir**d |
| EY | eɪ | s**ay** |
| IH | ɪ | b**i**t |
| IY | i | b**ea**t |
| OW | oʊ | g**o** |
| OY | ɔɪ | t**oy** |
| UH | ʊ | b**oo**k |
| UW | u | t**oo** |

重音标记：0=无重音, 1=主重音, 2=次重音
示例：[M][AY1][N][UW0][T] = /maɪˈnut/ (minute 微小的)

### 3.2 模型架构

```
CosyVoice 3 架构:
├── LLM (文本 → Speech Tokens)
│   └── 0.5B 参数
├── Flow Matching (Speech Tokens → Mel Spectrogram)
│   └── DiT 架构
└── HiFi-GAN (Mel Spectrogram → Audio)
    └── 声码器
```

### 3.3 部署选项对比

| 方案 | 延迟 | 复杂度 | RK3576 可行性 |
|------|------|--------|--------------|
| PyTorch 原生 | 高 | 低 | ❌ 依赖重 |
| ONNX Runtime | 中 | 中 | ✅ 推荐 |
| TensorRT-LLM | 低 | 高 | ❌ 不支持 ARM |
| vLLM | 低 | 高 | ❌ 不支持 ARM |

---

## 4. 风险与备选

### 4.1 已知风险

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 模型太大无法实时 | 中 | 高 | 量化、裁剪、或降级到 CosyVoice-300M |
| ONNX 转换失败 | 低 | 高 | 保留 PyTorch 推理作为备选 |
| ARPAbet 效果不达预期 | 低 | 中 | 准备测试用例验证 |

### 4.2 备选方案

如果 CosyVoice 3 预研失败：
1. 继续优化现有 sherpa-onnx + vits-melo-tts-zh_en
2. 尝试 Edge-TTS（云端，高质量）
3. 等待更轻量的 TTS 模型

---

## 5. 时间线

| 阶段 | 预计时间 | 里程碑 |
|------|---------|--------|
| Phase 1: 本地验证 | 1-2 天 | 确认 ARPAbet 有效 |
| Phase 2: 性能评估 | 2-3 天 | 得出 RTF/内存数据 |
| Phase 3: ONNX 转换 | 3-5 天 | 成功导出 ONNX |
| Phase 4: RK3576 部署 | 5-7 天 | 实机验证 |

---

## 6. 参考资源

- **GitHub**: https://github.com/FunAudioLLM/CosyVoice
- **ModelScope**: https://www.modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512
- **HuggingFace**: https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512
- **Paper**: https://arxiv.org/pdf/2505.17589
- **Demo**: https://funaudiollm.github.io/cosyvoice3/

---

## 7. 下一步行动

1. [ ] 在开发机/Mac 上执行 Phase 1 本地验证
2. [ ] 准备英文发音测试用例（酒店/客房控制相关词汇）
3. [ ] 记录测试结果，更新本文档
