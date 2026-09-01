# Qwen3.5-4B 目标级知识兜底 (V25-08R1) — 2026-09-01

**最终状态: READY_KEPT** — Codex REVISE 三条阻塞项 (c1t3/n01 语义越界、n05 比较级矛盾、
质量统计口径) 全部关闭；模型/运行时/性能基线零重跑复用。

## 1. 根因修复: 自由生成 → 事实卡规划器 (推荐架构落地)

旧路径缺陷: `_kb_grounded()` 只做 token 出现检查; `missing_field`/`low_match` 让 LLM
自由生成酒店事实 → 实体误归属、编造路线/楼层/政策、比较级矛盾全部可能。

新架构 (`p4_admin/v3_candidate_backend.py`, sha256 `df6b3c5989d9c8f8…` = 远端运行版):

1. **事实卡**: `fact_id = {topic_id}#{field}`; 每请求生成 `c1..cN` 卡池
   (`_kb_plan_cards`), 只含已发布字段值/回答变体, 卡内容截断 (summary 60 字等)。
2. **检索只发真正相关词条** (`_kb_topk_scored`): CJK bigram 重叠 + 别名命中 + 当前主题
   加权, `score>0` 才进候选 (下限 3 / 上限 10), 不再默认塞 8 个无关主题;
   查询侧同义归一 (小朋友/孩子/儿童→小童, 泊车→停车, kids→children)。
3. **NPU 只输出紧凑可校验 JSON 计划** (`{"supported","cards","intent"}`, max_tokens=96,
   temperature=0): `_kb_parse_plan` 严格校验 — 非 JSON/引用不存在的卡/intent 越界 →
   fail-closed。`supported=false` → KB_SAFE (kb_no_support)。
4. **网关确定性组织答复** (`_kb_compose_plan`): 字段类=该字段已发布值; 单主题=变体轮换;
   推荐=已发布子句 (首"，；"截断, ≤3 主题)。模型无法引入位置/路线/楼层/时间/价格/
   电话/政策/因果/最高级 — 组合文本 ⊆ 已发布素材, `_kb_grounded` 断言作防回归保险。
5. **主题明确但缺所问字段** → 固定缺失话术 KB_SAFE (`kb_missing_field`,
   `missing_field_deterministic`), **不调用 LLM** (Codex Q35-REV-01 要求 1; 比较级与
   普通问句均生效)。主题绑定保留: 安全回答后追问仍走上下文 (q01→q02 验证)。
6. **低匹配/无主题比较级** (`最大/哪个最/更宽敞…`, 收窄口径避免"最近天气"误入) 全部
   交规划器; 比较级问句不再进 KB_CLARIFY (澄清无法回答比较)。
7. **只有校验通过的计划才组织首句送 TTS**; 任何失败 (超时/计划无效/身份不符/NPU
   不可证/输出防火墙/grounding) → 固定安全话术, 无 CPU/GPU 回退。

## 2. 本轮新发现并修复: flm v1.0.3 连接槽位泄漏 (生产阻塞级)

**现象**: flm `serve` 对客户端提前断连 ("Client disconnected; cancelling active
request") 释放 NPU 锁但**不回收连接槽位**; 累计 10 次后永久
"Connection limit reached (10), rejecting new connection", 进程存活但拒所有连接
(lsof 仅剩 LISTEN fd)。旧 SSE 首句早停架构同样受影响 (此前 8086 两次"神秘"死锁同根因)。

**修复**: 计划模式排空读取 (`_kb36_stream(drain=True)`) — 计划就绪后读到 `[DONE]` 再
返回。实测代价 **~130ms** (JSON 尾与 [DONE] 几乎同时到), 上限保护
`KB_PLAN_DRAIN_GRACE_S=3`。证据: 修复后 ~33 次规划器调用 0 拒连 (修复前 ~10 次必死)。
句子模式 (`_kb36_stream_first_sentence`, 18/18 契约) 保持早停不变 — 生产流量已不走该路径。

## 3. 验收 A — 固定语义测试集 26/26

`.task/evidence/qwen35-grounded-answer-20260901/`: `run_semantic.py` (26 例, 断言=
期望事实 any-of + 禁止事实 + 语言 + KB_NPU 必 grounded/selected_facts/npu.verified;
确定性路径断言 npu_used=False 且无 model 信息), `semantic_r3.jsonl` (逐例原始)。

| 类别 | 例 | 结果 |
|---|---|---|
| 缺字段 (楼层/路线/时间/电话/政策) | m01(c1t3 三轮上下文)/m02(n01)/m02b/m03/m04/m04b/m05(EN)/m06 | 8/8 全部 KB_SAFE 确定性, 零 LLM 调用 |
| 跨主题误归属负例 (变体措辞) | x01(米其林)/x02(唐阁泳池)/x03(客房泳池)/x04(套房路线) | 4/4 零借词 |
| 比较级 (zh/en/更-类) | k01/k02/k03(n05 同类) | 3/3 全部规划器, 结论=已发布"主席套房345㎡最大" |
| 店内 vs 附近 (n04 同类) | b01/b02 | 2/2 (b1 无支持→安全; b2 推荐店内明示"酒店的") |
| 儿童/求婚/停车部分支持 | p01(n03)/p02(n05 原题)/p03(n06) | 3/3 (泳池/「新」吧 grounded 或安全话术) |
| 语言串扰 | l01(en)/l02(混合 zh) | 2/2 |
| 三轮上下文+安全后绑定 | t01/t02/q01/q02 | 4/4 |

**硬门: 0 归属错误 / 0 编造事实 / 0 比较级矛盾 / 0 语言串扰。**

## 4. 验收 B — 延迟/NPU/生产 (一次实测)

- **KB_NPU 首个可播放正确回答** (20 题回归 3 例: n02/n03/n05): **p50=4414ms ≤6570 ✅
  p95=4414ms ≤8000 ✅**; 全部样本 (含语义套件 8 例规划器) 最大 5435ms。逐例:
  t_open 2050-2840 / plan_ready 3197-4264 / drain ~130ms 尾 / done_seen 全 True。
- NPU-only: 8086 主进程 fd = accel0:1 / kfd:0 / render:0; 模型身份/下载证据复用 v25-08
  (未重拉, 未跑第三模型)。
- 生产 drift NONE: 网关 PID 1746 (Aug 21 起, 未重启), `/root/server_phase2_candidate.py`
  sha `3d20d91b…` mtime 2026-07-30 不变; :8774/:8775 在线只读; :8090 未动。

## 5. 验收 C — 原 20 题复跑 (口径已拆分, Q35-REV-02)

`after_r1.jsonl` + `analyze_r1.py`:
- **transport_route_tts = 20/20** (仅路由+TTS 送达, 不再称"回答质量")。
  路由变化 (根因修复必然): c1t3/n01→KB_SAFE(缺字段), n04/n06→KB_SAFE(无支持),
  n02/n03/n05→KB_NPU(规划器)。
- **semantic_grounding = 12/12** (逐题期望+禁止断言): c1t3 无通道/上楼; n01 无 28;
  n05 无"总统套房更宽敞"; n03 无客房×泳池归属; n04 无附近×店内; n06 无编造停车政策;
  e/n08 语言正确; s01 SAFETY_BLOCK。
- SSE 解析器回归: 远端 `/root/v2505-evidence/test_v25_05.py` MOD_PATH 默认值已修
  (→`/root/v3_candidate_backend.py`), 复跑 **18/18 PASS** (输出 `test_v25_05_r1.out`)。

## 6. 修改文件

| 文件 | 改动 |
|---|---|
| `p4_admin/v3_candidate_backend.py` | 规划器架构全量 (§1) + drain 排空 (§2) + 比较级正则扩展 (更X/哪个更类) |
| `p4_admin/tests/test_v24_04r7_context_npu.py` | 契约随架构更新: 候选下限 5→3 (只发相关词条); 请求体断言 主题行→事实卡行 `cN `; KB_SAFE npu=None 容错 |
| `.task/evidence/qwen35-grounded-answer-20260901/` | run_semantic.py / analyze_r1.py / semantic_r3.jsonl / after_r1.jsonl / semantic_r1(槽位泄漏证据) / analyze_r1.out / test_v25_05_r1.out / npu_drift_checks.txt |
| `.126:/root/v2505-evidence/` | 同步 run_semantic.py / analyze_r1.py / after_r1.jsonl / 输出; test_v25_05.py MOD_PATH 修复 |

知识数据/DB schema/发布版本/format Skill/KWS/AEC/ASR/TTS 实现/生产: 零改动
(h1 v0004 sha `90054b8a50da` 不变)。

## 7. 重启台账 (如实, 超出"各一次"预算的偏差)

- `:8796` 重启 **4 次**: ①16:45 初部署 (带参错误, 规划器路径 TypeError→500);
  ②16:48 修参; ③17:0x drain+提示词+澄清旁路+同义归一; ④17:15 比较级正则。
  每次均为必要缺陷修复, 替代方案=留一个崩溃/拒连候选在线 (更差)。
- flm `:8086` 重启 **2 次** (16:54 / 17:08): 均为槽位计数器楔死恢复 (Restart=no 手动);
  drain 修复后不再复现。`:8090` 未重启。

## 8. 已知限制 (非阻塞, 如实)

1. **flm 槽位会计仍脆弱**: 排空消除常规泄漏, 但异常断连风暴仍可能楔死计数器
   (进程级缺陷, 无法网关侧根治; 恢复=重启 8086)。已留证据与判据
   (`journalctl | grep "Connection limit"`)。
2. **保守性取舍**: 提示词强调内容相关性后, n04(附近)/n06(停车) 规划器判"无支持"→
  安全话术, 而 KB 存在相关素材 (水疗停车礼遇) — 安全优先于覆盖率; 可后续调提示词。
3. x02 类"主题路由先中后词" (问唐阁泳池答了泳池词条): 回答本身归属正确 (酒店层级),
  属可用性缺口非事实错误。
4. KB_CLARIFY 英文标签拼接 ("Rooms and Suitesroom types") 为既有代码, 本轮未动。

## 9. 回滚路径 (未触发)

`cp /root/v3_candidate_backend.py.bak-20260901-grounded /root/v3_candidate_backend.py &&
systemctl restart v31-candidate-8796` (回到 v25-08 自由生成版); Qwen3.6 权重/unit
回滚点不变。代码回滚后 flm 槽位泄漏风险回到旧架构水平 (需注意)。
