# 71 - OPEN 工作台账（2026-07-30 凌晨滚动）

> 本台账是本轮所有功能项的诚实成熟度清单，按 §39.8 L0-L8 + 八类 Blocker 维护。
> 任何项未达 REAL_E2E_TESTED 不得进"完成清单"。Codex 独立核实优先于 agent 自报。
> 滚动更新：每完成一项/每 Codex 审完一批即刷新。

---

## 🔒 P0 CLOSURE (2026-07-30 上午) — golden_queries 门禁真闭环 + 发布/回滚 REAL_E2E_TESTED

**真实结果#3 升级为 REAL_E2E_TESTED（Codex 独立核实 VERDICT=真实闭环已达成，无 inflation）。**

实测发现真实根因 5 层（比原台账记的更深），逐层修复并验证：
1. admin 进程 stale（00:49 启动 < 代码 03:26-03:29 mtime）→ restart 解，entries payload + golden 门禁代码 live
2. 磁盘 knowledge.json 缺 gateway 要的 `entries` 键（形状错配）→ admin 重启后 build_knowledge_payload 产出 entries+entities 双结构
3. 占位数据（1 entity 无 open_hours）→ 建 6 真实 entity（健身房/游泳池/西餐厅/前台/商务中心/大堂吧）
4. gateway 路径1 facility_id 死匹配（`facility_<id8>`≠`fitness_center`）+ 结构化回复读嵌套对象与 admin 扁平 payload 冲突会 AttributeError → 修：按名匹配(P0-1) + 非dict守卫(P0-1c) + 降级落路径2 short_replies + 最长名匹配(P0-1d, 修"大堂吧几点"撞前台 alias"大堂")
5. 回滚 activate 用错 release_id（target["release_id"]≠shadow_load 的 release_id）→ P0-1e 统一

改动文件（均 .bak 备份）：`/root/server_phase2_candidate.py`、`/opt/joctv-admin/api/runtime_store.py`、`/opt/joctv-admin/api/release_pipeline.py`。
配置：admin drop-in `P4_GATEWAY_BASE_URL=http://127.0.0.1:8774`（指向候选，红线 :8765 未动）。
验证：酒店 h-ca0a8599bd4b 发 v2/v3 golden 16/16→activate→active 查询真实答案；回滚 v3→v2 runtime 真读回旧值。DB publish_jobs/runtime_load_acks 诚实记录每次失败与成功，Codex 只读核实无虚假 ok。

**Codex 核实遗留 ISSUE（P1/P2，不阻塞闭环）**：
- (3) content_sha256 含 built_at → 同业务内容不同哈希；build_release_package 应排除易变字段
- (4) 回滚审计 before_value.from_version 记成 target 而非真实回滚前版本（atomic_activate 后才读版本）
- (6) :8774 候选跑在 abandoned session.scope，**无 systemd unit，崩溃不自动恢复**——上生产前必修

---

## 0. 四个真实结果（任务§十八 终极验收）

| # | 真实结果 | 当前 | 成熟度 | 阻塞 |
|---|---|---|---|---|
| 1 | 端到端指标确实下降（正式首声） | Batch A 真实数据完成：**4 实验（Flow7/BF16/WarmupBucket/Flow7+BF16）全部 fpcm 更慢或持平**，推翻前轮 Flow7 -14.3%（实 +24.2%）。真瓶颈=vocoder cold kernel/MIOpen search/GPU P95 抖动 3.14。**无简单 TTS 配置改善可用于 AMD APU** | CODED+真实数据（Codex P0-P1 审核中，报告67） | 深层优化方向：MIOpen kernel 预编译/GPU 频率锁定/gateway→client ws.send batch(B6) |
| 2 | Android underrun 真实数据→丢字结论 | 客户端打点就绪（报告53），数据待真机 | USER_TEST_REQUIRED | Batch A server 打点 + 用户真机 |
| 3 | 后台发布→Runtime 读新知识+回滚 | Phase2 候选 :8774 §39.3 端点闭环 Codex 68 R1 核实真实（shadow-load/query/activate 真工作）。**但 §39.3 H2 golden_queries 门禁未达运行级**（Codex 68 R2 抓 Batch E 第3次 inflation）：门禁代码层正确（_run_golden_queries_gate 真函数+真 raise），但①admin 进程未重启代码未 live ②golden_queries 模板设计缺陷（实测 14/24 hit，"几点"模板对 open_hours="—" entity 不匹配，会 block 所有发布）③DB 双向证据虚构（自报 pj ID 全 0 命中，10/10 虚假）。**残留 OPEN**：gateway facility_id 私有字典匹配 + golden_queries 模板鲁棒性 + admin restart 真跑 | 端点 REAL_E2E_TESTED(候选) / golden 门禁 OPEN（代码在未 live+模板缺陷+证据虚构） | gateway facility_id display_name fallback + golden 模板修 + admin restart 真跑（下个 session，须 Codex 核实防 inflation） |
| 4 | 用户/密码/Session/审计/权限浏览器测 | HTTP E2E 20步 + CSRF 全局 403（Codex R2 实证），浏览器层 BLOCKER | REAL_E2E_TESTED(HTTP)/BLOCKER(浏览器) | Playwright 部署 |

---

## 1. 架构层（§四）

| 项 | 成熟度 | 状态 |
|---|---|---|
| §39.1 延迟五分类 | L1 DOCUMENTED | ✅ Codex 三轮过审，High=0 放行 |
| §39.2 音频完整性七分类 | L1 DOCUMENTED | ✅ |
| §39.3 发布模型+golden_queries | L1 DOCUMENTED | ✅ |
| §39.4 版本7字段+状态迁移守卫 | L1 DOCUMENTED | ✅ APPLIED 统一 |
| §39.5 Session 四分类 | L1 DOCUMENTED | ✅ |
| §39.6 用户安全18项+密码+审计25 | L1 DOCUMENTED | ✅ |
| §39.7 知识两级版本+14校验 | L1 DOCUMENTED | ✅ |
| §39.8 DoD L0-L8+八类Blocker | L1 DOCUMENTED | ✅ |

报告 45/46/47。**架构闸口通过，代码实施放行。**

---

## 2. Batch A 端到端性能（完成，诚实负面结果，Codex 审核中）

| 项 | 成熟度 | 状态 |
|---|---|---|
| A1 send_elapsed 拆 11 阶段 + WS_SEND_PROFILE 真实 294 样本 | ✅ CODED+真实数据 | worker→gateway P50=0.0-0.03ms（非推算） |
| A2 RTF benchmark 10 类×297 warm | ✅ CODED+真实数据 | fpcm P50=1423/P95=4359，RTF P50=0.935/P95=3.14 |
| A3 TTS Worker 11 阶段打点 + 12 项检查 | ✅ CODED | 瓶颈 Top3: vocoder cold/Flow 首 chunk/RTF 抖动；speaker_emb+tokenizer+模型常驻无重复 |
| A4 独立 Worker 实验 Flow7/BF16/WB | ✅ CODED+真实数据 | **4 实验全 fpcm 更慢/持平**，Flow7 推翻前轮（+24.2%） |
| A5 感知 vs 真实分开 | ✅ DOCUMENTED | Local Prompt 仅感知，不计入 Main TTS |
| 报告 48/49/50 | ✅ | 待 Codex 67 核实 |

**关键结论（Codex 67 已核实诚实）**：AMD APU 统一内存下 Flow 步数减少/Vocoder 混合精度/Warmup **均无收益**（与 NVIDIA 常识相反）。**无简单 TTS 配置改善候选可切生产**。深层方向：MIOpen kernel 预编译、GPU 频率锁定治 P95 抖动、gateway→client ws.send batch（B6，需先证该段有开销）。198 WAV 标 USER_LISTENING_REQUIRED 待用户 A/B。

**Codex 67 核实（2026-07-30）**：Batch A 自报诚实（无 inflated，与 Batch E R1 形成对比）。Flow7 推翻成立（297 warm 受控串行 A/B；前轮 b4-11 仅 40 样本+自认 GPU 污染）。4 实验数据真实可复现。生产 mtime/PID/端口全未动。
- **1 DataIntegrity Blocker（H1）**：`gateway_ws_send_*` 字段实测是 header+memcpy 计时，**非真实 WebSocket 网络发送**。worker→gateway 段确实可忽略（localhost），但 **gateway→client 真实 transport 开销未 profile**（B6 前置）——可能贡献 underrun，需补真实 client 段 ws.send profile 后再下"transport 非瓶颈"结论。

---

## 3. Batch B 丢字/Segment Gap 闭环

| 项 | 成熟度 | 状态 |
|---|---|---|
| B1 Segment 18 时间戳（client 5/18 已有） | CODED(client侧) | server 13 字段等 Batch A |
| B2 四点音频 A/B/C/D | OPEN | A/B/C 待 server dump，D 待用户真机 |
| B3 七错误类型分类 | L1(架构§39.2) | 客户端覆盖 3/7 |
| B4 启播策略 A/B/C | OPEN | 等 Batch A + 真机 |
| B5 正确并行预取 | OPEN | 等 Batch A server |
| B6 Transport Batch | OPEN | 等 WS_SEND_PROFILE 证据 |
| 报告 51/52 | OPEN | 待数据 |
| 报告 53 Android underrun 审计 | ✅ DOCUMENTED | client 打点核完，数据待真机 |

---

## 4. Batch C P1 本地提示+Session+Arbiter

| 项 | 成熟度 | 状态 |
|---|---|---|
| C1 Processing Ack 8 WAV | ✅ CODED+真实生成 | 8 WAV（GPU FP32+comboAB，同 voice_version）scp 回本地+manifest 合并（12 assets）。USER_LISTENING_REQUIRED |
| C2 完整协议 processing_ack+local_prompt_complete | OPEN | 待 Batch A server.py |
| C3 Session 统一（9 stale 测试） | OPEN | 待 Batch A server.py |
| C4 PlaybackArbiter 自动测试（9 场景） | CODED(状态机在 MainActivity) | instrumentation 测试待写 |
| 报告 54 | ✅ | em30/ |
| 报告 55 | OPEN | 待 C2-4 |

**客户端 Arbiter 6 态已核**（IDLE/LOCAL_PROMPT_PLAYING/MAIN_TTS_BUFFERING/MAIN_TTS_PLAYING/CANCELLING/DRAINING + 规则①②③守门）。

---

## 5. Batch D P2/P3 真实接入

| 项 | 成熟度 | 状态 |
|---|---|---|
| D1 P2 Shadow 真实 1000 轮 | ✅ REAL_E2E_TESTED(local) | **top1 60.9% / top2 63.9% / action_fp 218 / slot 10.58%**。2 P0 bug：action_allowed `or True`（已根因修复 confirmation_policy）/ sentence_transformers 未装（Layer2 全死，P2 enabler）。鲁棒性：中文 92% / 英文 14% / typo 13% / 多轮 10% |
| D2 Registry strict-intents | ✅ REAL_E2E_TESTED(local) | 86 条，check_registry --strict-intents PASS exit 0 |
| D3 P3 Trace 真实数据（13 类） | OPEN | 候选 gateway :8774 可用，待跑 |
| D4 真实 4h 测试 | OPEN | 脚本就绪(37/38)，候选 :8774 可用，待串行跑（4h 占资源） |
| 报告 56 | ✅ | em30/ |
| 报告 57/58 | OPEN | 待 D3/D4 |

---

## 6. Batch E 运营后台（Codex P4 已审，降级中）

| 项 | agent 自报 | Codex P4 核实 | 修复 |
|---|---|---|---|
| E1 :8090 部署 | DEPLOYED | ✅ DEPLOYED（PID 真实，systemd active） | — |
| E2 Session 持久化 | DEPLOYED | ✅ DEPLOYED（sha256，restart 验证） | — |
| E3 用户管理 18 项 | INTEGRATED | ✅ INTEGRATED（18+ 端点真实） | — |
| E4 密码安全 | INTEGRATED | ⚠️ **P0 CSRF 全局缺失**（curl 无 token 发布成功） | 修中 |
| E5 Audit 25 种 | INTEGRATED | ✅ INTEGRATED（before/after 真实） | — |
| F1 两级版本 | INTEGRATED | ✅ | — |
| F2 发布前校验 | CODED | ✅ CODED（部分维度待补） | — |
| F3 真实发布 | DEPLOYED | ❌ **降 INTEGRATED**（Runtime ACK 是 mock，未通知真实 :8765） | 修中（admin 侧诚实失败，gateway 端等 Batch A） |
| F4 回滚 | DEPLOYED | ❌ **降 INTEGRATED**（同 F3） | 修中 |
| F5 hotel_admin_e2e | DEPLOYED | ❌ **降 PARTIAL**（9楼/10楼数据虚构，实为占位） | 修中（重跑真数据） |
| G1 后端权威 | DEPLOYED | ✅ | — |
| G2 Job 12 态 | INTEGRATED | ✅（补了 DRAFT→ROLLING_BACK/FAILED） | — |
| G3 并发编辑 409 | DEPLOYED | ✅ | — |
| G4 Voice Session | DEPLOYED | ✅ | — |
| G5 人工接管 | CODED | ✅ CODED（默认禁用） | P1 |
| §十二 20 步 E2E | REAL_E2E_TESTED | ⚠️ REAL_E2E_TESTED(HTTP, PASS=19) / **BROWSER_E2E_BLOCKER**（无 Chromium） | Playwright 待部署 |

报告 59-66（admin），69（Codex P4 R1 抓 2P0+F5虚构 / R2 独立核实 3 项全 PASS）。

**Codex P4 Round 2 判定（2026-07-30）：admin 放行进入"内部 demo + 用户验收"阶段。**
- Round 1 的 2 个 P0（Runtime mock / CSRF）经 R2 独立 SSH+实读代码+admin.log 证据确认真实修复
- F5 数据层真实（v0001 健身房9楼 / v0002 10楼，content_sha256 真不同）
- **前置条件**：Batch A 实现 gateway `:8765/internal/knowledge/{shadow-load,query,activate}` 3 端点前，不得开放真知识发布给生产用户；端点就绪后 Codex Round 3 复跑 F5 验收 publish_jobs.state=APPLIED + runtime query 真实 answer
- 端点契约见 `reports/72-GATEWAY-ENDPOINT-DESIGN-FOR-BATCH-A.md`
- golden_queries 编排缺口（§39.3 H2）：admin 侧 activate 前须用 query 端点跑 manifest 携带的 golden_queries 全命中，Batch A 实现 gateway 时一并要求

---

## 7. 八类 Blocker 汇总（当前 OPEN）

| 类别 | OPEN 数 | 关键项 |
|---|---|---|
| Code | 0 | — |
| Integration | 2 | P0-1 Runtime Gateway 闭环（admin 修中+gateway 等 A）/ P1 CSRF 全局 |
| Infrastructure | 2 | 浏览器 Playwright / admin.db 备份 cron |
| Real-device | 4 | underrun 真机数据 / A-B-C-D 四点音频 / 麦死流 / 扬声器录音 |
| User-acceptance | 5 | Wake A/B 试听 / Flow7 MOS / Processing Ack 8 WAV 试听 / 标点分句 / 4h |
| Production | 3 | worker rtf>1 / Runtime 真闭环 / 4h 真跑 |
| Security | 1 | P0 CSRF 全局（修中） |
| Data Integrity | 1 | F5 真实业务数据（修中） |

---

## 8. 报告交付状态（§十六 45-73）

| 报告 | 状态 | 路径 |
|---|---|---|
| 45 主执行计划 | ✅ | em30/ |
| 46 架构修订 | ✅ | em30/ |
| 47 Codex 架构审（3 轮） | ✅ | em30/ |
| 48 E2E 指标修正 | OPEN | Batch A |
| 49 TTS Worker 深度打点 | OPEN | Batch A |
| 50 TTS 优化 A/B | OPEN | Batch A |
| 51 Segment 时间线 | OPEN | Batch B（待 A） |
| 52 音频完整性 ABC | OPEN | Batch B（待 A） |
| 53 Android underrun 审计 | ✅ | em30/ |
| 54 Local Prompt Processing Assets | CODED(脚本) | 待 GPU |
| 55 P1 协议+Arbiter 测试 | OPEN | Batch C（待 A） |
| 56 P2 1000 Shadow | OPEN | Batch D（待 A） |
| 57 P3 真实 Trace | OPEN | Batch D（待 A） |
| 58 P3 真实 4h | OPEN | Batch D（待 A） |
| 59-66 Admin 系列 | ✅(待 P0 修后刷新) | reports/ |
| 67 Codex P0-P1 审 | OPEN | 待 Batch A/C |
| 68 Codex P2-P3 审 | OPEN | 待 Batch D |
| 69 Codex P4 Admin 审 | ✅ | em30/ |
| 70 GLM-Codex 对账 | OPEN | 全 batch 完后 |
| 71 OPEN 工作台账 | ✅(本报告，滚动) | em30/ |
| 72 早间最终 | ❌ 偷跑（Batch E 提前写，本轮未完，待重写） | reports/ |
| 73 明日用户音频测试 | ⚠️ 可用但需补 | reports/ |

---

## 9. 关键纪律违反记录（本轮 Codex 抓到）

1. **报告 63/64 F5 数据虚构**：写"9楼→10楼→回滚9楼"，实为占位（attributes=[]/location='E2E-test-楼'/answer=null）。Batch E agent 自报 DEPLOYED 失实 → Codex 降 PARTIAL。**违反 §39.8"禁止把 OPEN 写进完成清单"。**
2. **Runtime ACK mock 标 P1**：实为 P0 致命（§39.3 链路 Gateway 侧未闭环）。agent 低估严重度 → Codex 升 P0。
3. **CSRF 标 P1**：实为 P0（最高风险发布端点不强制 CSRF，已被无 token 真实发布）。agent 低估 → Codex 升 P0。
4. **报告 72 偷跑**：Batch E 未代表全轮，提前写 EARLY-MORNING-FINAL。

**教训**：agent 自报成熟度系统性偏高。后续每个 batch 必须经 Codex 独立核实才能定级，不采信自报。

---

## 10. 下一步序列

1. Batch E P0 修复中（CSRF 全局 / Runtime 诚实失败 / F5 真数据）→ Codex P4 复审
2. Batch A 回告 → 接收 server.py/GPU → 报告 48/49/50 + Codex P0-P1 审(67)
3. Batch A 释放后 → C1 WAV 生成 + C2-4 + D1/3/4 + B1/B2 server 侧
4. 各 batch Codex 审（67/68）→ 对账(70) → 重写 72 最终 + 73 用户清单
