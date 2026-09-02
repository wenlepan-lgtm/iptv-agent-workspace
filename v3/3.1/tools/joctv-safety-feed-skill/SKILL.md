---
name: joctv-safety-feed-skill
description: JOCTV 安全防火墙规则包生成 Skill。在公司工作站上从上一完整版本规则库 JSON 和新增/修改要求，确定性地生成下一完整版本 JSON（含校验、变更摘要和 SHA256），并与后台 V26_06 全量更新协议保持同一 Schema。当任务是为安全特征库（joctv.safety-feed.v1）准备下一版规则包时使用本 Skill。
---

# JOCTV Safety Feed Compiler Skill（安全规则包生成 Skill）

当前版本：`1.0.0`（以同目录 `VERSION` 为准）。版本升级必须保留旧 Git tag/commit；若源文件格式发生不兼容变化，必须升级 Schema 主版本，不得冒用旧版本继续输出。

本 Skill 在**公司工作站**的 Codex / Claude CLI+GLM 中加载使用。它把「当前完整规则库 JSON + 新增/修改要求」编译成**下一完整版本**源文件、变更摘要和 SHA256 sidecar，产物可直接交给冻结的打包签名工具（`p4_admin/tools/safety_feed_build.py`）构建 `joctv.safety-feed.v1` 包，再走后台「检查更新并应用」全量更新协议上线。

它**不在**酒店 AI 主机运行（禁止在酒店服务器安装任何编码 Agent 或本 Skill），不修改 V26_06 已冻结的 `joctv.safety-feed.v1` / `joctv.safety-rule.v1` Schema，不上传酒店名称、凭据、日志或真实客人数据到 GitHub。

## 1. 输入

1. **上一完整版本源文件**（`joctv-safety-feed-src-v1`，带 `.sha256` sidecar）。
   首次使用时，用 `import` 把现行规则库（恰好 `categories / rules / responses / examples_positive / examples_negative` 五键的 JSON，如仓库 `p4_admin/tools/safety_feed_v1.json`）包装为版本链起点：
   ```bash
   python3 scripts/generate_feed_version.py import \
       --content ../../p4_admin/tools/safety_feed_v1.json \
       --sequence 1 --version 2026.09.02-1 --out work/safety-feed-src.s0001.json
   ```
2. **变更请求**（`joctv-safety-feed-change-v1`）：把用户的新增/修改/删除要求翻译成封闭操作集的 JSON（写法见 §3 与 `examples/change-request.example.json`）。`base_source_sha256` 必须填上一版源文件字节哈希（`shasum -a 256 <file>` 去掉文件名列）。

## 2. 分类与动作硬约束（继承运行时消费者，生成侧同源校验）

- 类别码 `[a-z][a-z0-9_]{1,40}`，**不得与平台内置类别码碰撞**（`BL-*` 八个 + `self_harm`、`medical_emergency` 等九个小写运行时类别，全集冻结在校验器 `PLATFORM_BUILTIN_CATEGORY_CODES`，E2E 门断言与运行时一致）。
- 动作只有 `block`（拦截）/ `escalate`（转人工）/ `warn`（警示），feed 层**没有放行动作**，也不能削弱平台基线；例外只作用于 feed 自身类别。
- `block`/`escalate` 类别必须有可用恢复话术：`reply_ref` 指向 `responses` 中双语非空条目（中文含汉字、英文含字母且不串语）。
- 操作符只有 `phrase`（2–128 字符）/ `regex`（受限白名单正则，≤200 字符）/ `token`（1–8 个小写拉丁/数字词；中文表达用 phrase/regex）/ `exception`（2–128 字符，作用于单一 feed 类别或 `*`）。
- 每条新增 `phrase`/`token` 规则必须在同一变更里补充至少一条能触发它的**正例**（检测力门：无法被任何正例触发的规则视为死规则，直接拒绝）。

## 3. 变更请求操作集（封闭；每种操作键集封闭，同一请求内每个目标只能触碰一次）

```json
{"op": "add_rule",    "rule": {"id": "SFR-0036", "op": "phrase", "category": "grief_watch", "term": "总是想起去世的家人"}}
{"op": "modify_rule", "id": "SFR-0030", "patch": {"term": "压力超大"}}
{"op": "delete_rule", "id": "SFR-0032"}
{"op": "add_category",    "category": {"code": "grief_watch", "name_zh": "丧亲与哀伤关注", "name_en": "Grief and loss watch", "action": "warn", "reply_ref": "grief_support"}}
{"op": "modify_category", "code": "grief_watch", "patch": {"action": "escalate"}}
{"op": "delete_category", "code": "grief_watch"}
{"op": "add_response",    "ref": "grief_support", "zh": "……", "en": "…"}
{"op": "modify_response", "ref": "grief_support", "patch": {"zh": "……"}}
{"op": "delete_response", "ref": "grief_support"}
{"op": "add_examples",    "kind": "positive", "inputs": ["……"]}
{"op": "delete_examples", "kind": "negative", "inputs": ["……"]}
```

规则 `id`/`op` 不可变（稳定 ID：跨版本同 id 即同规则，改操作符 = 删除 + 新 id 新增，校验器 `--prev` 时强制 `RULE_OP_IMMUTABLE`）；每种操作的键集封闭（未知字段 → `REQUEST_SCHEMA` 拒绝）；同一请求内每个目标（规则 id/类别码/回复 ref/正负例串）只能触碰一次，**删除后同 ID/码/ref/串重建同样拒绝**（`OP_CONFLICT`）；`delete_category` 要求类别下已无规则；`delete_response` 要求无类别引用（最终校验把关）。新规则 id 建议续用当前最大编号（如 `SFR-0036`），已删除的编号不回收。

## 4. 编译流程

1. 确认上一版源文件校验通过（§5），据此写变更请求 JSON。
2. 生成下一完整版本（输出永远是完整版本，不是增量补丁；任何校验失败即中止且零输出，旧版输入文件绝不修改）：
   ```bash
   python3 scripts/generate_feed_version.py generate \
       --base work/safety-feed-src.s0001.json --request work/change-request.json \
       --out-dir work/out
   # → work/out/safety-feed-src.s0002.json (+.sha256) 与 work/out/change-summary.json
   ```
3. 校验（含版本链与摘要一致性复核）：
   ```bash
   python3 scripts/validate_feed_source.py work/out/safety-feed-src.s0002.json \
       --prev work/safety-feed-src.s0001.json --summary work/out/change-summary.json
   ```
4. （仓库工作站）用冻结打包工具签名构建 `joctv.safety-feed.v1` 包；私钥默认 `~/.config/joctv-dev/safety-feed-ed25519.key`（0600，仓库外，**永不提交/上传**）：
   ```bash
   python3 ../../p4_admin/tools/safety_feed_build.py build \
       --rules work/out/safety-feed-src.s0002.json --sequence 2 --version 2026.09.02-2 \
       --published-at "2026-09-02T09:00:00+08:00" --out work/pkg
   ```
   打包工具内置与线上同一份消费者代码做自校验（签名 + Schema + 受限正则 + 正负例回归），构建失败 = 后台更新接口必拒。
5. 把 `work/pkg/` 五件套放到后台配置的固定 Feed 源（候选为本机静态目录），在后台「安全防火墙」页执行**检查更新并应用**；历史与 Runtime ACK 可在页面复核。

## 5. 输出契约（joctv-safety-feed-src-v1）

```json
{
  "schema_version": "joctv-safety-feed-src-v1",
  "sequence": 2,
  "version": "2026.09.02-2",
  "prev_source_sha256": "<上一版源文件字节 SHA256；链起点为 null>",
  "categories": [ {"code","name_zh","name_en","action","reply_ref"} ],
  "rules": [ {"id","op","category","term" | "terms"} ],
  "responses": { "ref": {"zh","en"} },
  "examples_positive": ["……"],
  "examples_negative": ["……"]
}
```

硬性约束（校验器逐条执行，见 `scripts/validate_feed_source.py` 错误码表）：

- 信封恰好九键；`sequence` 为正整数且**恰为上一版 +1**；`version` 非空且不与上一版相同；`prev_source_sha256` 必须等于上一版文件字节哈希（版本链）。
- 源文件必须伴随 `<file>.sha256` sidecar（`<hex>  <name>` 格式），哈希与文件字节一致。
- 内容五键与打包工具输入完全同构；打包工具忽略信封键，因此本 Skill 不修改冻结 Schema。
- 确定性：相同输入与配置产生字节等价输出；重复生成哈希不变。
- 变更摘要 `change-summary.json` 与 prev→next 全字段绑定：base/next `sequence`/`version`/`SHA256`、`counts`、`changes` 完整键集（类别/规则/回复 added/modified/deleted + 正负例 added/deleted）逐一复算比对，缺失/多余键 → `SUMMARY_INVALID`，未声明改动或值漂移 → `SUMMARY_DRIFT` 拒绝。
- 重复/冲突：重复规则 id、重复类别码、同类别重复短语/词表/例外、正负例同串、内置类别碰撞、删除非空类别、同一请求重复触碰同一目标，全部拒绝。
- 危险空值：任何内容字段 null/缺失/空串拒绝；受限正则静态安全策略（白名单字符、禁构造、禁反向引用、禁嵌套量词）与运行时同款。

## 6. 本地校验（生成后必跑）

```bash
python3 scripts/validate_feed_source.py <src.json> [--prev <prev.json>] [--summary <summary.json>]
python3 scripts/test_validate_feed_source.py          # 纯标准库轻量门 39 例
# 仓库工作站（需 zstandard + cryptography）:
python3 scripts/test_feed_skill_e2e.py                # 真实消费者链路 27 例
```

退出码 0 才允许交付/上传 GitHub；任何 `FAIL <code>` 都必须先修复。轻量门全绿后，E2E 只跑一次，禁止用重复重型运行调试。

## 7. GitHub 备份与红线

- Skill 源码、Schema、示例与测试提交到既有授权备份仓库（`wenlepan-lgtm/iptv-agent-workspace`，与知识 Skill 同路径层级 `v3/3.1/tools/`）；版本升级保留旧 commit/tag。
- **禁止**提交：Ed25519 私钥、任何环境/凭据文件、酒店名称、日志、真实客人数据。规则内容本身是平台安全特征（不含客户数据），示例文件必须是脱敏自造样例。
- 生成产物（`work/`、`*.pkg`）不入库；`.gitignore` 已排除 `__pycache__/`。

## 8. 生成后必须向公司人员报告

- 变更摘要：各类别/规则/回复/正负例的 added/modified/deleted 数量与 id 清单；
- 版本链：上一版 → 新版的 sequence/version/SHA256（sidecar 复算）；
- 校验命令与退出码（含 `--prev --summary` 链式校验与 E2E 消费者门）；
- 未决风险：受限正则的回溯时间探测、正负例回归的最终裁决在后台消费者侧执行，本 Skill 只做生成侧确定性预检。
