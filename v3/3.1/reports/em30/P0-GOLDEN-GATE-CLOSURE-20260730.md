# P0 Golden Queries 门禁 + 发布/回滚闭环 — 真实闭环交付 (2026-07-30)

> 真实结果#3（后台发布→Runtime 读新知识+回滚）升级为 **REAL_E2E_TESTED**。
> Codex 独立核实：**VERDICT = 真实闭环已达成，无状态 inflation**。
> 本报告是 Commit 1 的可恢复节点记录。

---

## 1. 闭环达成证据（Codex 只读核实，C1-C6 全 CONFIRMED）

| 项 | 证据 |
|---|---|
| 发布 | 酒店h-ca0a8599bd4b 发 v2/v3，golden 16/16 全命中 → activate → active_runtime_version=2/3 |
| runtime 读新知识 | gateway :8774 namespace=active 查询返真实答案（大堂吧几点/健身房在9楼/西餐厅168元），match_type=knowledge_v2 |
| 回滚 | 回滚 v3→v2，runtime 真读回旧值（大堂吧 11:00-22:00 → 10:00-23:00），current→v2 |
| DB 诚实 | publish_jobs 5 行（v1 FAILED 14/16 / v2 APPLIED / v3 APPLIED / 首次回滚 FAILED / 修复回滚 ROLLED_BACK），runtime_load_acks 每次失败/成功均有记录，无虚假 ok |
| 门禁强制 | 14/16 那次 publish 的 activate 从未发生（DB+gateway日志双证）|

---

## 2. 真实根因（5 层，实测确认；比上轮 memory 记的更深）

| # | 根因 | 证据 | 修复 |
|---|---|---|---|
| R1 | admin 进程 stale（PID 345114 启动 00:49 < 代码 mtime 03:26-03:29） | ps lstart vs stat mtime | restart admin → entries payload + golden 门禁代码 live |
| R2 | 磁盘 knowledge.json 缺 gateway 要的 `entries` 键（形状错配：只有 entities） | 实跑 :8774 shadow-load 失败 "no entries key" | admin 重启后 build_knowledge_payload 产出 entries+entities 双结构 |
| R3 | 占位数据（1 entity 健身房 location-only，无 open_hours）→ 0 golden query | cat knowledge.json + DB | 建 6 真实 entity（健身房/游泳池/西餐厅/前台/商务中心/大堂吧） |
| R4 | gateway 路径1 facility_id 死匹配（admin 推 `facility_<id8>` ≠ `fitness_center`）+ 结构化回复读嵌套对象，admin 扁平 payload 会 AttributeError | 读 `_phase2_query_facility` + `_format_facility_attribute_reply` | P0-1 按名匹配 + P0-1c 非dict守卫+降级落路径2(short_replies) + P0-1d 最长名匹配(修"大堂吧"撞前台 alias"大堂") |
| R5 | 回滚 activate 用错 release_id（target["release_id"] ≠ shadow_load 的 release_id） | DB runtime_load_acks id13 "release_id 未 shadow-load" | P0-1e 统一用 shadow_load 的 release_id |

---

## 3. 代码改动（Codex 验证过的最终产物，已入库）

| 文件 | 改动 | 标记 |
|---|---|---|
| `server_phase2_candidate.py` | P0-1 按名匹配(fac_ent_kw) + 补回误删的 fac_attr=None + P0-1c 非dict守卫(_as_dict)+路径1降级落路径2(_path1_kb_missing) + P0-1d 最长名匹配(_best_entry) | grep "P0-1/1c/1d" = 6 |
| `p4_admin/api/runtime_store.py` | P0-3 `_build_golden_queries` 按 entity 实际属性(location/open_hours/price)生成可验证 query，expected_pattern=属性值 | grep "P0-3" = 1 |
| `p4_admin/api/release_pipeline.py` | P0-1e 回滚 activate 用 shadow_load 同一个 release_id | grep "P0-1e" = 1 |
| `systemd/.../gateway-candidate.conf` | admin drop-in `P4_GATEWAY_BASE_URL=http://127.0.0.1:8774`（指向候选，红线 :8765 未动） | — |

部署侧：三个 .py 在 .126 均有 `.bak-preP0-*` 备份；gateway 候选原样重启（cwd=interaction_core + 同 env + setsid）。

---

## 4. 验证矩阵（gateway :8774，P0-1c/1d 实测）

```
健身房几点      → 健身房营业时间是06:00-22:00。   (path-2)
健身房在哪里    → 健身房在9楼。                  (path-1→降级→path-2 short_replies) ← P0-1c
健身房多少钱    → 健身房对住客免费。              (path-1→降级→path-2) ← P0-1c
大堂吧几点      → 大堂吧营业时间是10:00-23:00。   (最长名匹配, 不再撞前台) ← P0-1d
游泳池在哪里    → knowledge_missing fallback (无该 entity 时的真"无资料")
随便问不存在的  → null / no_match
```
无崩溃（P0-1c 守卫防了 AttributeError；补回 fac_attr=None 防了 UnboundLocalError）。

---

## 5. Codex 核实遗留 ISSUE（→ Commit 2，不阻塞闭环）

1. **content_sha256 含 built_at**：同业务内容(v1/v2)不同哈希；`build_release_package` 应排除易变字段再算内容指纹。
2. **回滚审计 before_value.from_version 记错**：记成 target 而非真实回滚前版本（`atomic_activate` 切完后才读 `_current_active_version`）。
3. **:8774 候选无 systemd unit**：跑在 abandoned session.scope，崩溃/重启不自动恢复。仓库已有 `joctv-v31-gateway-phase2-candidate.service`，需部署。

---

## 6. 完成情况（诚实）

- 已完成并验证：5 根因修复 + 发布/回滚全链路，Codex 只读核实 C1-C6 CONFIRMED。
- 未做：生产红线 :8765/:8767/:8090 全程未动；测试数据为验证用酒店（P0-GATE-VERIFY），非生产业务数据。
- 部署配置：admin 当前指向候选 :8774（drop-in），适合 phase2 验证期；上生产前需决定是否切回 :8765（待 phase2 端点上生产）。
