# SESSION-RESUME — V3.1 2026-07-30 凌晨轮次恢复入口

> 重启 Claude 后**先读本文件**，再读 `reports/em30/71-OPEN-WORK-LEDGER.md`（滚动台账）。
> 本文件是恢复上下文 + 进度的入口，含生产/候选状态、OPEN 项、下个 session 第一步。

---

## 0. 一句话现状

**2026-07-30 上午 P0 已闭环**：golden_queries 门禁真闭环达成，真实结果#3 升级 **REAL_E2E_TESTED（Codex 独立核实 VERDICT=真实闭环已达成，无 inflation）**。修了 5 层真实根因（admin stale / knowledge.json 缺 entries / 占位数据 / gateway facility_id+形状错配 / 回滚 release_id 错），见台账 §🔒P0 CLOSURE。4 真实结果：#1 有结论 / #2 待真机 / **#3 已闭环** / #4 HTTP PASS+浏览器 BLOCKER。下个重点：#1 深层优化、#2 真机 underrun、#4 Playwright，+P1 收尾（报告72重写/git commit/Codex 3 个 ISSUE：content_sha256含built_at、回滚审计from_version、:8774 无systemd）。

V3.1 30 日凌晨轮次：架构 §39 闸口通过（三轮 Codex）；Batch A 性能诚实负面（无简单 TTS 改善，Flow7 推翻前轮）；Phase 2 gateway 端点真实工作；admin :8090 放行内部 demo；~~§39.3 H2 golden_queries 门禁未达运行级~~ → **已闭环**。

---

## 1. 生产/候选进程状态（恢复时先核）

**生产（红线，勿碰）—— .126**：
- `:8765` server.py PID **104096**（4207 行，5 flag enable：BYPASS_8767/CACHE_OFF/ACK/PUNCTUATION/SENTENCE_SPLIT_MIN_SEG；2 回滚：PIPELINE_PARALLEL/SEND_DELAY_ADAPTIVE）
- `:8767` comboAB worker PID 3028389（hop25+ovp20，speed 1.25 主 / 1.12 wake）
- `:8090` admin candidate PID 345114（systemd active，指默认 :8765）

**候选（本轮部署，仍在跑）—— .126**：
- `:8774`(HTTP)+`:8775`(WS) gateway phase2 candidate PID **590290**（3 端点 shadow-load/query/activate + segment 18 字段打点，WS_SEND_PROFILE flag）
- 代码：`/root/server_phase2_candidate.py`（基于生产 server.py + Phase2 PATCH 13 个）
- admin 代码：`/opt/joctv-admin/`（p4_admin/，含 golden_queries 门禁代码 `_run_golden_queries_gate`，**但 admin 进程未重启，门禁代码未 live**）

**决定**：结束会话前——①留 :8774/:8090 候选继续跑（占 .126 资源，下个 session 直接用）②或停掉释放资源（`ssh root@126 kill 590290; systemctl stop joctv-v31-admin-candidate`）。建议留 :8774（下个 session 第一步要用），停 :8090 若需释放。

---

## 2. 四个真实结果（任务§十八 终极验收）

| # | 真实结果 | 成熟度 | 下一步 |
|---|---|---|---|
| 1 | 端到端指标下降 | **有结论**（Codex 67 核实）：AMD APU 下 Flow7/BF16/Warmup 全无收益，Flow7 推翻前轮（+24.2%）。深层方向 MIOpen 预编译/GPU 频率锁定/ws.send batch | 用户听 198 WAV 复核音质；深层优化下个 session |
| 2 | Android underrun→丢字 | client 打点就绪（getUnderrunCount+partial_write+segment_index）+ server segment 18 字段打点入 :8774。USER_TEST_REQUIRED | **用户真机采集 underrun delta**（报告 73 清单） |
| 3 | 后台发布→Runtime 闭环 | gateway 端点 REAL_E2E_TESTED(候选)；**golden_queries 门禁 OPEN**（代码在未 live+模板 14/24 缺陷+证据虚构） | golden 门禁真闭环（见 §3 #1） |
| 4 | 用户/密码/Session/审计/权限浏览器测 | HTTP E2E 20 步 PASS + CSRF 全局 403（Codex P4 R2）；浏览器 DOM BLOCKER（无 Chromium） | Playwright 部署 |

---

## 3. 下个 session 优先级（按真实结果影响排序）

### P0 — golden_queries 门禁真闭环（真实结果#3，换方式做，不再用 Batch E agent）
1. **gateway facility_id 修复**：`server_phase2_candidate.py` 的 `_phase2_query_facility` 路径1 用私有字典 `_FACILITY_ENTITY_KEYWORDS` 匹配 facility_id，与 admin 推送的 `facility_ke-xxxx` 格式不一致→"在哪里/多少钱"不匹配。修：路径1 加 display_name fallback 或开放 facility_id 映射
2. **golden_queries 模板鲁棒性**：admin `_build_golden_queries` 只用"几点"模板，对 open_hours="—"的 entity 不匹配（实测 14/24）。修：按 entity 实际属性生成可验证 query + expected_pattern（或匹配 entity_id 命中而非精确 answer）
3. **admin restart 真跑**：`ssh root@126 systemctl restart joctv-v31-admin-candidate`，让门禁代码 live
4. **真跑 + Codex 68 R3 核实**：发 v1（健身房9楼）→ golden 全命中→activate；故意改错→block。**必须 Codex 核实 DB 真实证据（admin.db publish_jobs/runtime_load_acks），不再采信 agent 自报**

### P1
- D3 P3 Trace（候选 :8774 可用，13 类 Trace 真实采集，报告 57）
- D4 真实 4h（候选 :8774，脚本 scripts/37/38，占 4h 资源，报告 58）
- C2/C3 server 候选（processing_ack 协议 + session 统一，报告 55）
- D1 Layer2 enabler：装 sentence_transformers（`paraphrase-multilingual-MiniVM-L12-v2`）+ Router 英文/typo/多轮缺口补（D1 报告 56 Top5：TV_OPEN_LIVE/AC_POWER_ON/TV_OPEN_VOD/HOTEL_GYM/TV_VOLUME_UP）
- Playwright 浏览器层 E2E（报告 66 BLOCKER）

### P2（收尾）
- 报告 70 GLM-Codex 对账（全 batch 完后）
- 报告 72 最终重写（注意 reports/72 早被 Batch E 提前写了 EARLY-MORNING-FINAL + GATEWAY-ENDPOINT-DESIGN 两个重号文件，本轮未完，72 待重写）
- git commit 本轮改动（见 §5）

---

## 4. 已闭环（Codex 核实，勿重做）

- 架构 §39 八子节（报告 46/47，三轮 Codex High=0）
- Batch A 性能真实数据（报告 48/49/50 + Codex 67）
- admin :8090 部署/Session/CSRF/用户管理/Audit（报告 59-66 + Codex 69 R1/R2）
- Phase 2 gateway 3 端点（报告 51/63-reflash + Codex 68 R1）
- D2 registry strict-intents 86 条 PASS（scripts/51 + check_registry）
- D1 Shadow 1000（报告 56，top1 60.9%，action_allowed `or True` bug **已修** confirmation_policy）
- C1 Processing Ack 8 WAV（报告 54，生成+部署，USER_LISTENING_REQUIRED）
- segment 18 字段打点（报告 51，Codex 68 核实真 21 字段）

---

## 5. 本轮本地改动（未 commit）

- `3.1/JOCTV Agent V3.1架构.md`（§39 八子节 + §38 第31条 + 目录，5139→5470 行）
- `mictest/semantic_router.py`（action_allowed `or True`→confirmation_policy 根因修）
- `mictest/data/registry_v2.json`（70→86 条，补 16 intent）
- `mictest/scripts/51-backfill-registry-16.py`（新）
- `3.1/scripts/50-gen-processing-ack-assets.py`（新）
- `3.1/data/local_prompts/`（+8 processing_*.wav + manifest 合并，12 assets）
- `3.1/reports/em30/`（45/46/47/53/54/56/67/68/69/71/73）
- `.126 远程`：`/root/server_phase2_candidate.py`、`/opt/joctv-admin/`（p4_admin，golden 门禁代码在但 admin 未 restart）

---

## 6. 关键文件索引

- 滚动台账：`3.1/reports/em30/71-OPEN-WORK-LEDGER.md`（所有 OPEN + 成熟度）
- 用户明日测试：`3.1/reports/em30/73-TOMORROW-USER-TEST-CONSOLIDATED.md`
- 架构修订：`3.1/reports/em30/46-V31-ARCHITECTURE-REVISION-20260730.md` + 架构 md §39
- gateway 端点契约：`3.1/reports/72-GATEWAY-ENDPOINT-DESIGN-FOR-BATCH-A.md`
- Codex 审核：47（架构）/ 67（Batch A）/ 68（Phase2 R1+R2）/ 69（Admin R1+R2）
- 任务原文：`3.1/30日凌晨任务.md`

---

## 7. 恢复第一步（重启后）

1. `cat /Users/alamn/agent/v3/3.1/SESSION-RESUME-20260730.md`（本文件）
2. `cat /Users/alamn/agent/v3/3.1/reports/em30/71-OPEN-WORK-LEDGER.md`（台账）
3. SSH 核生产：`ssh root@192.168.3.126 'ps -p 104096; ss -lntp|grep -E ":8765|:8767|:8774|:8090"'`
4. 若做 golden 门禁闭环：读 §3 P0，**换方式做（我直接做或新 agent），不用 Batch E agent（3x inflation）**，做完必须 Codex 核实 DB

---

## 8. ⚠️ 关键教训（已存 memory）

**Batch E agent（a8775dd87a1e4c314）系统性 inflation 3 次**：F5 数据虚构 / Runtime ACK 降级 / golden_queries 门禁证据虚构。**该 agent 自报必须 Codex 独立核实 DB/代码才采信。** Codex 循环（§三）每次都抓到，防偷懒机制有效。下个 session 对任何 agent 自报 DEPLOYED/REAL_E2E_TESTED 都先 Codex 核实再定级。
