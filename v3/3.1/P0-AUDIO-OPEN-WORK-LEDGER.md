# P0-AUDIO-OPEN-WORK-LEDGER (2026-07-30)

| # | 项 | 状态 | 备注 |
|---|---|---|---|
| §一 | 架构定级 P0/P1 FAILED + 硬约束 + 标点规则 | ✅ 已提交 a4eb6e0 | |
| 根因 | Codex 审计: RTF>1+串行分段+固定节流→underrun; 20处偏离 | ✅ 已出(CODE-DEVIATION-20) | RTF样本1个, 需补P95 |
| 实验 | split逗号切7段 | ❌ 失败已回滚 | 串行实现错, 非架构错 |
| #12 | P0 止损: 长句整条预合成后播(候选, P0_SAFE_BUFFER_MODE_V31) | ⏳ 待做 | 临时安全模式, 不替代Segment |
| #13 | §二/§三/§四: 欢迎词早停根因 + 本地欢迎词 + 本地Wake寒暄 | ⏳ 待做 | 客户端 v3/mictest, 需本地PCM资源 |
| #14 | §五: 标点Segmenter(每有效标点一段, 1000例) | ⏳ 待做 | server候选+客户端 |
| #15 | §六/七/八: 段协议V2 + Client Credit流控 + Segment预取 | ⏳ 待做 | 最终架构, 与#12安全模式并存 |
| #16 | §九/十: AudioTrack完整性指标 + 故障注入 | ⏳ 待做 | 自动化测试 |
| Codex审 | §十二: 审真实代码/日志/diff/测试 | ⏳ 每阶段审 | 不只看报告 |
| 听感 | 明天真机: 断网欢迎词/重启10次/中英唤醒各10/外滩10次/underrun日志 | USER_TEST_REQUIRED | 6项过才摘 FAILED |

## 真实结果(V3.1 任务§十八)当前
| # | 真实结果 | 状态 |
|---|---|---|
| 1 | 端到端指标(首声/RTF) | 有数据(RTF≈1.29), 首声~1.3-1.7s; **长句丢字未解决** |
| 2 | underrun→丢字 | **FAILED**(14 underrun, 根因RTF>1) |
| 3 | 后台发布→Runtime闭环 | REAL_E2E_TESTED(Codex-confirmed, 早session) |
| 4 | 浏览器E2E | HTTP PASS, 浏览器BLOCKER(Playwright) |
| 麦克风/AEC | (移交Kimi) | AEC对TTS生效(-66dB), 视频双讲未定论 |

## 成熟度
- 代码(Code类): split早停/串行/无流控 → **OPEN**
- 丢字: **FAILED**
- 欢迎词/Wake本地化: **FAILED**(当前走server TTS)
- Segment协议V2/Credit流控: **未实现**
- 生产红线: :8765/:8767/:8090 全程未动(仅本session为分段实验重启过:8765两次, 已回滚稳定)
