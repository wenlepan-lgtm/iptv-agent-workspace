---
name: joctv-hotel-kb-skill
description: JOCTV 酒店知识编译 Skill。把酒店提供的 Excel 或 TXT 资料编译成可导入后台的双语 JOCTV Knowledge JSON V1 包，并运行本地校验。当任务是把某家酒店的知识资料（官网 TXT、Excel 采集表）变成结构化中英文知识库包时使用本 Skill。
---

# JOCTV Hotel Knowledge Compiler Skill（酒店知识编译 Skill）

当前版本：`1.2.0`（以同目录 `VERSION` 为准）。版本升级必须保留旧 Git tag/commit；如果输出 JSON 发生不兼容变化，必须升级 Schema 主版本，不能只改提示规则后继续冒用旧版本。1.2.0：问法语料绑定升级为事实感知三分类（entry / clarify / no_fact），新增网关最终决策链副本与绝对安全门。

本 Skill 在**公司工作站**的 Codex / OpenCode+DeepSeek / Claude CLI+GLM 中加载使用。
它把酒店提供的资料一次性编译成字段清楚、双语隔离、可由 JOCTV 后台导入的知识包。
它**不在**酒店 AI 主机运行，不使用商业模型 API Key，不向公网模型发送酒店资料。

本 Skill 与酒店运行时 `system.kb_answer_planner` Prompt 是两类资产：本 Skill 负责离线生成知识 JSON；运行时 Prompt 负责从已发布事实卡中选择答案计划。两者不得互相替代，也不得把本 Skill 安装到酒店主机。

配套文件（相对本 SKILL.md）：

| 文件 | 作用 |
|---|---|
| `templates/酒店知识采集模板.xlsx` | 发给酒店人员填写的采集模板（填写说明 / 知识条目 / 分类表） |
| `templates/utterance_intent_templates.json` | 问法覆盖生成资源：9 类意图本体句式（中英）+ 同义称呼词典 + NO_FACT 主题 + 泛指词表 |
| `schema/joctv-hotel-kb-v1.schema.json` | JOCTV Knowledge JSON V1 结构契约 |
| `schema/joctv-hotel-utterance-v1.schema.json` | 问法覆盖语料（Utterance Corpus V1）结构契约 |
| `scripts/validate_knowledge_package.py` | 本地确定性校验器（字段 / 语言 / 答案数 / 编号 / 上下文 / 冲突 / 噪声） |
| `scripts/generate_utterance_coverage.py` | 问法覆盖生成器（确定性，固定 seed 可复跑） |
| `scripts/validate_utterance_coverage.py` | 问法语料确定性校验器（去重 / 近似冲突 / 串扰 / 事实边界 / 事实感知映射 / intent 语义派生） |
| `scripts/test_validate_utterance_coverage.py` | 问法语料校验器正反例自动测试（含事实映射 / intent 语义 / Schema 契约同步） |
| `scripts/kb_router_chain.py` | 网关 `kb_route` 确定性路由链逐字副本 + 增强别名移除规则 / 发布态 topics 构建 / 最终决策模型（生成器预审与评测器共用，单一口径） |
| `scripts/eval_route_holdout.py` | 留出集路由评测器（评分层 + 最终决策层绝对安全门 + 防漂移指纹；导出增强别名包） |
| `examples/脱敏示例.json` / `examples/脱敏示例.xlsx` | 脱敏示例（非真实酒店数据），可作为输出样例 |
| `scripts/make_excel_template.py` | 重新生成上述两个 xlsx 的脚本（需 openpyxl） |
| `scripts/test_validate_knowledge_package.py` | 校验器正反例自动测试 |

## 1. 输入

两种皆可，按任务提供哪种用哪种：

1. **Excel**：使用 `templates/酒店知识采集模板.xlsx` 的"知识条目"Sheet，一行一个主题。列含义见"填写说明"Sheet。核心列：分类、中文主题、英文主题、中文命中词、英文命中词、中文标准回答、英文标准回答、时间（中/英）、地点（中/英）、怎么去（中/英）、电话、其他备注（中/英）。
2. **TXT**：酒店官网、手册等文字材料。逐页阅读，提取事实；来源里的页面导航、按钮文字、天气小组件、弹窗标题（如 `modal title`、`Back To Top`、`了解更多`、`立即预订`、`LEARN MORE`、`RESERVE`）、促销口号、异地/其他酒店内容一律**不得**当成知识。

## 2. 分类

每个主题必须归入且只归入一个分类。推荐九类（后台筛选与统计依赖，优先使用）：

`衣 / 食 / 住 / 行 / 游 / 购 / 玩 / 服务 / 其他`

归类示例：餐饮与酒廊→`食`；客房套房→`住`；地铁机场交通→`行`；景点→`游`；购物商圈→`购`；健身房泳池水疗→`玩`；洗衣熨烫→`衣`；政策、联系方式、会议婚宴等服务→`服务`；酒店整体概览→`其他`。

九类无法准确概括时允许**自定义分类**（与后台编辑器口径一致）：去掉首尾空白后非空、长度 ≤16 字符、不含控制字符；前后台会原样保留该值（不重命名为"其他"）。自定义分类应在生成报告中列出并说明归类理由。

## 3. 编译流程

对每一行 Excel / 每个资料主题：

1. **定主题**：中文主题与英文主题必须同义（如 `健身房 / Fitness Center`）。
2. **定命中词**：中文 2–4 个、英文 2–4 个（英文小写），是宾客会说的叫法（俗称、简称、英文名）。命中词只放进对应语言字段。
   - **公共简称不得独占绑定**：某简称若同时是多个主题的叫法（如「酒廊」同时可能指大堂酒廊和行政酒廊），**禁止**写进任何一个主题的命中词——写进即把歧义问法独占归给单一主题。此类公共短称由运行时确定性澄清（KB_CLARIFY 列出候选让宾客选择），不需要也不能靠命中词解决。
   - 只收录**该主题特有**的叫法；主题名本身的可区别短段（如「大堂酒廊」之于「凯旋大堂酒廊」）可以收录。
3. **写回答**：该主题的中文回答 **≥3 种**、英文回答 **≥3 种**，要求：
   - 每种回答都是**同一组事实的三种自然改写**：每一条都能**独立、完整**回答该主题，事实集合一致、中英文对齐，措辞自然、口语化、不重复，供 TTS 随机轮换使用；
   - **禁止用互补事实凑数**。例：游泳池主题的三条回答不能分别只讲"开放时间"、"儿童政策"、"池边服务"——轮换到哪条答案都不完整。正确做法：把该主题的核心事实（如 恒温室内泳池+每天6:30-23:00+仅住店宾客和会员+儿童须18岁成人陪同）在每条回答里都讲全，其他互补事实（池边可点饮料等）写入 `context.notes`；互补事实本身够大时拆成独立主题；
   - 只使用酒店资料中**明确提供**的事实；酒店只给了一种语言时，由工作站 Skill 翻译补齐另一种语言，翻译不得增删事实；
   - 时间、价格、地址、电话、政策、服务等**一律不得编造、估算或四舍五入**。资料里没有就留空或写"请咨询前台"。
4. **整理上下文**：把结构化事实放进 `context`：`time`（营业/服务时间）、`location`（楼层/区域/地址）、`directions`（怎么去）、`phone`（电话，纯字符串，不分语言）、`notes`（预约方式、限制、人数、着装等其他事实）。每项都是 `{zh, en}` 双语；没有就留空字符串。
5. **双语隔离**：中文内容只进 `zh` 字段，英文内容只进 `en` 字段。缺失某语言时该语言字段留空（空字符串 / 空数组），**禁止**把中文塞进英文字段或反之，禁止混写。
6. **噪声剔除**：以下内容不得进入包内任何字段：页面导航与按钮文字、`modal title`、天气/当地时间小组件、促销口号与会员广告、与该酒店无关的异地内容、未解析的占位符（如 `[香港]`）。
7. **来源冲突**：同一事实中英文资料不一致时，选两语言一致的口径；无法一致时省略该细节并在生成报告里说明，不得任选一边当成双语事实。

## 4. 输出契约（JOCTV Knowledge JSON V1）

```json
{
  "schema_version": "joctv-hotel-kb-v1",
  "hotel_id": "<后台酒店ID，如 1>",
  "package_name": "<包名，如 示例酒店知识库>",
  "locale_policy": "bilingual_separate",
  "entries": [
    {
      "id": 1,
      "category": "玩",
      "topic": {"zh": "健身房", "en": "Fitness Center"},
      "keywords": {"zh": ["健身房", "健身中心"], "en": ["gym", "fitness center"]},
      "answers": {"zh": ["……", "……", "……"], "en": ["...", "...", "..."]},
      "context": {
        "time": {"zh": "……", "en": "..."},
        "location": {"zh": "……", "en": "..."},
        "directions": {"zh": "……", "en": "..."},
        "phone": "86 (21) 2330 2288",
        "notes": {"zh": "……", "en": "..."}
      },
      "enabled": true
    }
  ]
}
```

硬性约束（校验器逐条执行）：

- 根字段固定五个，不许多、不许少；`schema_version` 必须是 `joctv-hotel-kb-v1`，`locale_policy` 必须是 `bilingual_separate`。
- `entries[].id` 为不重复正整数；`category` 推荐九类之一，也允许自定义分类：strip 后非空、≤16 字符、不含控制字符（C0/C1）。
- 每条目：主题至少一种语言非空；命中词至少一种语言非空；回答至少一种语言非空；**已提供的语言回答数 ≥3**、不重复且为**同一组事实的自然改写**（每条都能独立完整回答该主题）。
- `zh` 非空字符串必须含汉字，且**主体不得是英文句子**：允许酒店名（≤3 个连续英文词，如 Cachet Al Fresco）、缩写（DJ/VIP）、单字母代号（K11/B1）、数字、电话、邮箱等必要拉丁字符；连续英文词串 ≥4 词、同串出现 ≥2 个英文虚词（the/is/on/of 等）、或英文字母数远超中文（>1.5 倍）且含虚词，都会被判定串语拒绝（适用于 topic/keywords/answers/context 的全部 zh 字段）。`en` 非空字符串必须含英文字母且不得含汉字（防串语）。
- `context` 五个键固定（`time/location/directions/phone/notes`），不得塞其他键。
- 同包内归一化后的中文主题（或英文主题）不得重复（确定性冲突预检）。
- 业务 JSON 不保存原文证据、网页位置、模型推理过程或内部 Prompt。

## 5. 本地校验（生成后必跑）

仓库根目录：

```bash
python3 tools/joctv-hotel-kb-skill/scripts/validate_knowledge_package.py <package.json>
python3 tools/joctv-hotel-kb-skill/scripts/test_validate_knowledge_package.py
```

Skill 目录内（复制到工作站单独使用时）：

```bash
python3 scripts/validate_knowledge_package.py <package.json>
```

校验通过（退出码 0）才允许交付或导入后台；任何 `FAIL` 都必须先修复。

## 6. 生成后必须向公司人员报告

- 条目总数、九类各自覆盖数、至少覆盖 6 类；使用了自定义分类时，逐个列出自定义分类值、条目数与归类理由；
- 双语完整条目数；缺失语言条目的编号与原因（确实缺失才允许留空，必须点名报告）；
- 被剔除的噪声类别（导航/天气/modal/促销/异地）与被省略的来源冲突事实清单；
- 校验命令与结果。

## 7. 问法覆盖生成（1.1.0 新增；1.2.0 事实感知映射 + 最终决策链预审/绝对安全门）

在已编译知识包之上生成**问法/同义表达覆盖语料**，用于路由离线评测与别名增强。问法是宾客表达，**不是答案**：每条绑定三分类之一，回答仍只来自已发布事实，未知内容运行时安全兜底。

- `binding=entry`：问法指向某条目，且该条目**已发布**此 intent 对应字段的事实（time/location/directions→context 对应语言字段；phone→context.phone；price/policy/booking→context.notes；availability/overview→answers）。运行时预期独占直答。
- `binding=clarify`：问法点名某条目，但该条目**未发布**此 intent 对应字段的事实（运行时预期 KB_MISSING_FIELD"暂未收录"安全兜底或确定性澄清，**不得**从其他条目借事实）。
- `binding=no_fact`：知识库外主题问法（运行时预期非知识/低匹配规划器路径 → KB_SAFE 兜底）。

### 7.1 输入与产物

输入：一份通过校验的 `joctv-hotel-kb-v1` 知识包 + `templates/utterance_intent_templates.json`（通用酒店意图本体，不含任何酒店事实）。

```bash
# 1. 生成 (确定性: 同包同 seed 同字节输出; 含最终决策链预审)
python3 tools/joctv-hotel-kb-skill/scripts/generate_utterance_coverage.py \
    --package <kb.json> --out <outdir> [--seed 20260904] [--per-cell 32] \
    [--clarify-per-cell 8]

# 2. 校验 (去重/近似冲突/串扰/事实边界/事实感知映射/intent 语义派生/variant 独占性)
python3 tools/joctv-hotel-kb-skill/scripts/validate_utterance_coverage.py \
    --package <kb.json> --corpus <outdir>/utterances_v1.json --min-unique 10000

# 3. 正反例测试
python3 tools/joctv-hotel-kb-skill/scripts/test_validate_utterance_coverage.py

# 4. 留出集路由评测 (仓库根目录运行; 生成 4 个产物文件到 <outdir>)
python3 tools/joctv-hotel-kb-skill/scripts/eval_route_holdout.py \
    --package <kb.json> --corpus <outdir>/utterances_v1.json --out <outdir>
```

产物（写入 `--out` 目录）：

| 文件 | 作用 |
|---|---|
| `utterances_v1.json` | 问法语料（`joctv-hotel-utterance-v1`）：每条含 lang/text/binding(entry\|clarify\|no_fact)/entry_id/category/intent/field/term_kind(base\|variant)，并带源包 SHA 绑定与 variant_terms 声明 |
| `generation_report.json` | 生成统计：逐条目 base/variant 数与事实可用/缺失 intent 清单、被拒 variant 及原因、被拒 NO_FACT 主题、去重丢弃数、决策链预审拒绝明细（clarify/no_fact） |
| `holdout_eval_report.json` | 留出集评测：baseline vs enhanced 命中率、NO_FACT 兜底不回归、歧义探针、gates、findings |
| `alias_enhanced_package.json` | **后台可直接导入**的增强别名知识包（`joctv-hotel-kb-v1`；answers/context/事实零变化，仅 keywords 追加新称呼，走现有知识导入端点） |
| `new_aliases.json` | 别名/表达资产清单（每条目每语言新增别名 + 代表问法样例） |

### 7.2 生成规则

- **意图本体**：9 类通用意图 time/location/directions/phone/price/policy/booking/availability/overview，各自映射到知识字段（`intent_fields`）；句式模板按名词称呼（N）与动宾称呼（V）分槽，礼貌前缀与句式头部重复的组合自动跳过。
- **事实感知映射（1.2.0）**：只有该 (entry, lang) 已发布对应字段事实的组合才生成 `entry` 问法；问法点名条目但对应字段未发布 → `clarify`（默认每格 8 条，`--clarify-per-cell`）。校验器双向反例强制：entry 绑定事实缺失 = E_INTENT_FACT，clarify 绑定事实已发布 = E_CLARIFY_FACT。
- **intent 语义派生（1.2.0）**：每条问法 text 必须可由所声明 intent 的模板结构（前缀 + 模板前段 + 称呼 + 模板后段 + 后缀）派生，称呼必须命中该条目对应 (slot, term_kind) 集合；不匹配 = E_INTENT_TEMPLATE。
- **最终决策链预审（1.2.0）**：clarify / no_fact 候选问法用 `kb_router_chain.final_decision`（网关 `kb_route` 确定性层逐字副本）对增强别名发布态 topics 预审：no_fact 必须落入安全兜底（NOT_KNOWLEDGE / KB_NPU_PLANNER / KB_MISSING_FIELD），clarify 不得被**其他条目**独占直答；不合格候选确定性跳过并计入生成报告（公共简称/模板填充词的 stem 吸收泄漏一并根治）。
- **称呼来源**：`base` = 源条目信号词（topic/keyword，过滤疑问短语与泛词；**公共简称剔除**：非主题名且是其他条目信号词严格子串的称呼不作 base，此类问法运行时确定性澄清）；`variant` = 同义称呼词典命中且通过**全局独占性**检查（与任何其他条目信号词/已分配 variant 不互为子串、非公共简称、非疑问/泛词）。variant 是增强别名的唯一来源。
- **泛指词口径**：`generic_scope_aliases` 与网关 `_KB_GENERIC_SCOPE_ALIASES` 对齐并补充位置类意图泛词（在哪/位置/地址 等）——它们不构成对任何主题的独占证据，冲突检查豁免；增强别名包按确定性规则移除该类源 keyword（只动 alias，不动事实/主题名）。
- **NO_FACT**：KB 之外的通用酒店主题（电影院/桑拿房/货币兑换 等），用于安全兜底负例；不得包含任何条目信号词子串，且必须通过决策链预审落入安全兜底。
- **反凑数**：后缀不进组合空间（每组合只抽一个后缀，"呀/啊/呢"互为近似的变体只保留一条）；全局近似去重（编辑距离 ≤1 一律不重复发出）；纯单复数 variant 不收录。
- **事实边界**：模板与前后缀零事实值；问法禁止携带 HH:MM、≥5 位数字、货币值、楼层/价格数字（校验器逐条强制）。
- 每条问法必须包含绑定条目的至少一个信号词（可追溯到源条目）；出现的其他条目信号词必须被本条目更长信号词覆盖（长词优先，与网关包含匹配权重同口径），否则判串扰 FAIL。

### 7.3 评测口径

两层口径，算法副本集中在 `scripts/kb_router_chain.py`（与生成器预审共用同一实现），内置源码快照 SHA 防漂移门（网关算法变化时评测 FAIL，需重对齐副本）；Skill 单独拷贝到工作站时用 `--allow-missing-router` 跳过。

- **评分层**（网关 `_kb_topk_scored` 逐字副本）：留出集 entry 绑定问法按 (entry_id, lang) 分层随机留出 12%（固定 seed），对比 baseline（源包 keywords）vs enhanced（+variant −公共简称/泛指词）的 hit@1 / variant 子集 / NO_FACT 兜底率不回归 / 歧义探针不新增独占（baseline 已存在的源包 keyword 公共简称缺陷如实记 findings）。
- **最终决策层**（网关 `kb_route` 确定性决策链逐字副本，1.2.0）：四个绝对安全门——
  - `NO_FACT_ABSOLUTE_SAFE`：全部 no_fact 问法落入安全兜底（NOT_KNOWLEDGE / KB_NPU_PLANNER / KB_MISSING_FIELD）；独占直答与错候选澄清都算违规；
  - `CLARIFY_ABSOLUTE_SAFE`：全部 clarify 问法不被**其他条目**独占直答（绑定条目自身已发布字段的直答是诚实回答）；
  - `AMBIGUITY_ABSOLUTE_NON_EXCLUSIVE`：全部歧义探针进入澄清或非独占路径；
  - `CORRECT_NO_NEW_WRONG_ROUTE`：baseline 正确直答样本在 enhanced 无新增错误条目直答。

### 7.4 交付边界

- 语料与别名包是**路由资产**，不是酒店答案；禁止把生成问法当事实发布。
- `alias_enhanced_package.json` 仅追加/移除 keywords（事实/主题名零变化）；导入后台属知识编辑流程（maker-checker），由人工确认后执行。
- 评测器只读消费知识包与语料，不访问生产、不启动服务。
- **公共仓库边界（1.2.0）**：本 Skill 源码（scripts / schema / templates / SKILL.md / VERSION / 测试）发布到公司公共 Git 仓库；`outputs/`（真实酒店语料与别名包产物）和真实酒店知识包**不得**推送公共仓库——`.gitignore` 已排除 `outputs/`。
