# 麦克风与音频前端参数摘录（用于 Claude/Cursor 识别）

> 来源：你提供的两张截图（YD1076S 音频前端板参数 + 驻极体麦克风规格书页面）

---

## 1) YD1076S（音频前端/回声消除板）参数

| 字段 | 值 |
|---|---|
| 产品型号 | **YD1076S** |
| 输入电压 | **5V** |
| 工作电流 | **10–30 mA** |
| 工作温度 | **-20°C ~ 70°C** |
| 硬件保护 | **反接保护；USB 静电和浪涌保护** |
| 支持功能 | **回声消除（AEC）**；**噪声抑制（Noise Suppression）**；**人声增强（The sound of man is enhanced）**；**自动增益控制（AGC）** |
| 音频回采 | **MX1.25 4P**（回声消除回采接口） |
| 音频输入 | **MX1.25 2P**（驻极体咪头接口） |

---

## 2) 驻极体麦克风规格（Omnidirectional Electret Condenser Microphone）

**Name**: Omnidirectional Electret Condenser Microphone (Back Electret Type)  
**TYPE**: **OB6027L200-2A363-C1033**

### Electrical Specifications

> 0 dB = 1 V/Pa

| 条目 | 指标 |
|---|---|
| 3.1 Sensitivity Range | **-36 ± 3 dB** @1kHz；RL = **2.2 kΩ**；Vs = **2.0 V** |
| 3.2 Backward Impedance | **Max 2.2 kΩ** @1kHz（RL = 2.2 kΩ） |
| 3.3 Frequency | **20–20000 Hz** |
| 3.4 Current Consumption | **Max 500 µA** @RL = 2.2 kΩ；Vs = 2.0 V |
| 3.5 Operation Voltage Range | **1.0–10 V (DC)** |
| 3.6 Max. Sound Pressure Level | **> 110 dB SPL**（1kHz，THD < 3%） |
| 3.7 S/N Ratio | **> 60 dB**（1kHz，0 dB = 1 V/Pa，A-weighted） |
| 3.8 Sensitivity Reduction | **2.0V → 1.5V** 时灵敏度变化 **< 3 dB** |
| 3.9 Typical Frequency Response Curve | 测试距离 **L = 50 cm**（图中给出 Frequency Response 曲线） |

---

## 3) 用于你调试的工程提示（可选）

- **YD1076S 支持 AEC/NS/AGC**：先把 **输入增益/削波（clipping）** 与 **Reference 回采质量**确认好，再开 AEC/NS/AGC 做 AB 对比。
- **驻极体咪头**：供电范围 1–10V DC；Vs=2.0V、RL=2.2kΩ 是典型工作点。

