# V3.1 执行报告 — 2026-07-30（白天轮次）

> 本报告覆盖 2026-07-30 白天会话的全部 V3.1 执行结果。诚实区分：已完成并验证 / 暂停 / 待办。
> 提交分布在两个分支：`feature/joctv-v3.1-nightly-p0-20260728`（V3.1 主线 P0）+ `feature/mt9679-mic-aec-20260730`（麦克风/AEC）。

---

## 0. 一句话总览

P0（后台发布→Runtime 闭环 / golden_queries 门禁）**真实闭环，Codex 独立核实通过**；Commit 1/2 代码已提交并实测；**麦克风/AEC 调查暂停，移交 Kimi**（按用户指示）。

---

## 1. ✅ P0 Golden Queries 门禁 + 发布/回滚闭环（主线，已完成并验证）

### 真实根因（5 层，全部 SSH 实读代码 + 实跑 :8774 + 查 admin.db 确认，非推理）
1. admin 进程 stale（启动 00:49 < 代码 mtime 03:26-03:29）→ entries payload + golden 门禁代码未 live。
2. 磁盘 knowledge.json 缺 gateway 要的 `entries` 键（只有 entities）→ shadow-load 失败 "no entries key"。
3. 占位数据（1 entity 健身房 location-only，无 open_hours）→ 0 golden query。
4. gateway 路径1 facility_id 死匹配（admin 推 `facility_<id8>` ≠ `fitness_center`）+ 结构化回复读嵌套对象与 admin 扁平 payload 冲突会 AttributeError。
5. 回滚 activate 用错 release_id（与 shadow_load 不一致）。

### 修复（最小根因，全部 .bak 备份）
- `server_phase2_candidate.py`：P0-1 按名匹配 + P0-1c 非dict守卫+降级落路径2 + P0-1d 最长名匹配（修"大堂吧"撞前台 alias"大堂"）+ 补回误删的 `fac_attr=None`。
- `runtime_store.py`：P0-3 golden 模板按 entity 实际属性(location/hours/price)生成。
- `release_pipeline.py`：P0-1e 回滚 activate 用 shadow_load 同一 release_id。
- systemd drop-in：admin 指向候选 :8774（红线 :8765 全程未动）。

### 验证（实测 + Codex 独立核实）
- 发布 v2/v3 golden **16/16 全命中** → activate → runtime active 查询返回真实答案。
- 回滚 v3→v2 → runtime 真读回旧值。
- DB publish_jobs/runtime_load_acks 诚实记录每次失败(14/16 golden / activate ack=failed)与成功。
- **Codex 只读独立核实：VERDICT=真实闭环已达成，无状态 inflation**（C1-C6 全 CONFIRMED）。

**真实结果#3 升级 REAL_E2E_TESTED（Codex-confirmed）。** 生产红线 :8765/:8767/:8090 全程未动。

---

## 2. ✅ Commit 2 — 3 个 Codex ISSUE 全修（已实测）

| ISSUE | 修复 | 实测 |
|---|---|---|
| content_sha256 含 built_at | build_release_package 用 stable 子集(排除 built_at, sort_keys) | v6/v7 同内容 content_sha 完全一致，file_sha==manifest_sha |
| 回滚审计 from_version | `_pre_rollback_version` 在 atomic_activate 前捕获 | 审计 from_version=7（回滚前），非 target 6 |
| :8774 无 systemd | 部署 joctv-v31-gateway-phase2-candidate.service（补 SENTENCE_SPLIT_MIN_LEN=18） | cgroup /system.slice/...，enabled+active，取代 abandoned scope |

**新发现 P1（不阻塞，记台账）**：gateway 候选 active 知识纯内存，systemd 重启后归 0，需重发 publish 恢复；生产前需加启动时按 hotel_runtime_state 重载。

---

## 3. ⏸️ 麦克风 / AEC 调查（部分完成，**暂停，移交 Kimi**）

> 按用户指示，此项**已暂停，深查交 Kimi**。以下为已确认结论 + 开放项，如实记录。

### 已确认（实测）
- 设备 = .113 MT9679 会议屏。**当前麦 = USB 摄像头+麦（同一块主板，直焊/直插线，拔不掉）**；主板只有 1 USB 口 + 1 SPK-in 口；**系统设置里只有这 1 个麦**（内置麦阵列不可选）。
- **AEC 对真实 JOCTV TTS 生效**：Kimi 自己喇叭-vs-外部声源对照（~24dB）+ 我用真实 JOCTV TTS 实测（自 TTS 录到 **-66dB**，压到噪声底）→ **自 TTS 屏蔽 / TTS 时打断 成立**。
- **USB 麦拾人声没问题**：无回声时人声 -16~-20dB（清楚）。
- **勘误**：我先前"USB 麦无 AEC / 灵敏度差"是误判（把被 AEC 压掉的提示音当成麦不灵），已纠正入报告75。

### 开放（移交 Kimi）
- **AEC 疑似 usage/path 相关**：TTS(语音通路) 被压 -66dB；AudioTrack 1kHz(媒体通路) 录到 22dB（没怎么压）→ 推测 merak AEC 主要压语音通路，媒体(视频/音乐)可能不压。需 usage 控制实验定性（AECProbe usageTest 模式已改好，未跑完即移交）。
- **视频播放时双讲/打断 未定论**：视频(音量26)播放时录到 -53dB（人声疑似被一起压），单次+音量低，疑 AEC 过消近端，待复核。
- **内置麦能否软件强制启用**：我 setPreferredDevice 失败（USB 连接时内置不在 getDevices）；Kimi 的 com.test.aec 按钮5标"强制内置麦"，可能有系统权限路子——交 Kimi 查。

### Commit 3 已提交产出
- 架构章节（L1-L4 成熟度+7原则）、报告74（静态能力 L1）、报告75（麦探测+AEC结论+勘误）、AECProbe 工具源码。
- 设备上留 com.aecprobe / com.test.aec 测试 APK。

---

## 4. 设备维护
- .113 /data 从 100%(12MB) 清到 13%(10.8GB)，释放 ~10.7GB；采集 9.9GB 备份到 macmini `/Users/alamn/DeviceBackups/mt9679_113_20260730/`（50 pcm + 1.mp4 + kws_model，校验 50==50）。设备保留 kws_model + 真 1.mp4。

---

## 5. 完成情况（诚实）

| 项 | 状态 |
|---|---|
| P0 golden 门禁 + 发布/回滚闭环 | **已完成并验证**（Codex-confirmed） |
| 3 个 Codex ISSUE 修复 | **已完成并验证** |
| 麦克风/AEC 深查 | **暂停，移交 Kimi**（AEC 对 TTS 已证；视频双讲 + 内置麦启用 待续） |
| git 提交 | 7 个提交（P0 分支 2 + 麦克分支 5），均含 Co-Authored-By |
| 生产红线 | :8765/:8767/:8090 全程未动 |

**未做/待办**：
- 麦克风/AEC 视频双讲定性 + 内置麦启用路子（Kimi）。
- P1：gateway active 状态持久化（重启恢复）。
- 真实结果#1 深层优化 / #2 真机 underrun / #4 Playwright。
- 本轮 git push（提交均在本地，未 push）。

## 6. 提交清单
- P0 分支：`b386048` P0 Golden Query 闭环 / `dce8374` 3 Codex ISSUE。
- 麦克分支：`b84454a` 架构章节 / `c6d1519` 报告74 静态 / `fd735dd` 报告75+probe / `7430761` 内置麦不可达结论 / `6775291` AEC 生效勘误。
