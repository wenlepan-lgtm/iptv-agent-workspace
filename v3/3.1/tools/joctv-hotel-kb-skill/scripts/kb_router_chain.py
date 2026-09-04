#!/usr/bin/env python3
"""JOCTV 网关 KB 路由链共享副本 (Skill 1.3.0)。

网关 p4_admin/v3_candidate_backend.py 确定性路由层的逐字副本 + Skill 侧共享 helper
(增强别名包 keyword 移除规则 / 发布物化 topics 构建 / 最终决策模型 / intent 级事实
能力)。生成器 (generate_utterance_coverage)、校验器 (validate_utterance_coverage)
与评测器 (eval_route_holdout) 共用本模块, 保证"生成断言""语料校验""留出评测"消费
同一决策口径 — 不存在第二套未建模的 final_decision 副本。

防漂移: eval_route_holdout.check_router_snapshot 用 ROUTER_WANT 从网关源码提取
同名函数/常量段计算 SHA, 与 ROUTER_SNAPSHOT_SHA256 比对; 网关变化时评测 FAIL,
本副本需重对齐。
"""
from __future__ import annotations

import re

# ══ 网关逐字副本区 (v3_candidate_backend.py; 修改前必须与网关重对齐) ══

_KB_QUERY_SYNONYMS_ZH = (
    # 口语→已发布词条用语 (查询侧归一, 让 bigram 命中词条真实用词; 只加通顺的
    # 全局同义映射, 不针对个例): 儿童类问法 → KB 统一用「小童」
    ("小朋友", "小童"), ("孩子", "小童"), ("儿童", "小童"), ("小孩", "小童"),
    ("泊车", "停车"), ("大巴", "接驳车"),
)

def _kb_cjk_bigrams(text):
    grams = set()
    t = text or ""
    for a, b in _KB_QUERY_SYNONYMS_ZH:
        if a in t:
            t = t.replace(a, b)
            t += a        # 原词 bigram 保留 (词条可能用原词)
    for run_ in re.findall(r'[一-鿿]{2,}', t):
        for i in range(len(run_) - 1):
            grams.add(run_[i:i + 2])
    return grams

def _kb_topic_text(t):
    return " ".join([t["name"]] + list(t["keywords"])
                    + [str(v) for v in t["fields"].values()] + list(t["answers"]))

def _kb_topk_scored(text, topics, active_topic_id=None):
    lower = text.lower()
    qgrams = _kb_cjk_bigrams(text)
    scored = []
    for i, t in enumerate(topics):
        score = 0.0
        for n in [t["name"]] + t["keywords"]:
            if not n or len(n) < 2: continue
            if n in text or n.lower() in lower:
                score += 1.0 + len(n) / 10.0
        if active_topic_id and t["id"] == active_topic_id: score += 2.0
        if qgrams:
            score += 0.12 * len(qgrams & _kb_cjk_bigrams(_kb_topic_text(t)))
        else:
            for w in re.findall(r'[a-zA-Z]{3,}', text)[:8]:
                wl = w.lower()
                if wl in _kb_topic_text(t).lower(): score += 0.12
                elif wl in ("kids", "kid") and "children" in _kb_topic_text(t).lower():
                    score += 0.12
        scored.append((score, i, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored

def _kb_is_zh(s):
    return bool(re.search(r'[一-鿿]', s or ""))

# ── 字段/语言/主题/重置 确定性识别 ──
# V29-08: 座位/容量问法 (中英确定性识别) — 真实复现 "还有几个座位啊" 误入通用 LLM 的
# 缺口字段。置于 notes 前: 问座位/容量时优先按容量字段归属, 不落入泛 notes。
# V30-04 R1 (KB30-04-01/02): price 成为独立可问字段 — kb-v1 无结构化价格字段,
# 命中主题后走 KB_MISSING_FIELD 诚实话术, 不再用概述/notes 冒充价格回答;
# 补齐 booking/policy (预订/规定/book/reserve…)、时间 (什么时候/when is…) 与
# 英文楼层 (floor) 问法词, 使所问字段在确定性层可识别 (字段级路由正确性)。
_KB_FIELD_PATTERNS = [
    ("directions", ["怎么去", "怎么走", "如何到达", "怎么到达", "路线", "怎么过去", "怎么到",
                    "directions", "how to get", "how do i get", "how can i get", "the way to",
                    "which way", "way to", "direction"]),
    ("time", ["几点", "什么时间", "什么时候", "营业时间", "开放时间", "关门", "开门", "营业到", "到几点",
              "营业时段", "时间", "hours", "what time", "opening", "closing", "when is it open",
              "when is", "when does"]),
    ("location", ["在哪里", "在哪", "位置", "几楼", "哪个楼层", "楼层", "在几层",
                  "where", "located", "location", "floor"]),
    ("phone", ["电话", "联系方式", "号码", "联系电话", "phone", "telephone", "call", "number"]),
    ("capacity", ["座位", "容纳", "能坐"]),
    ("price", ["多少钱", "价格", "收费", "免费", "费用", "计费", "押金", "定金", "付费",
               "要钱", "最低消费"]),
    ("notes", ["备注", "注意", "限制", "政策", "预约", "教练", "特色", "推荐", "介绍", "评价",
               "提示", "预订", "取消", "规定", "要求", "须知", "登记", "会员", "着装", "年龄", "订位",
               "notes", "policy", "reservation", "recommend", "introduction", "tips",
               "book", "reserve", "booking", "restrictions", "dress code", "age limit",
               "register", "members only", "rules", "requirements"]),
]
# V29-08 R1 (CODEX REVISE): 英文容量词必须词边界匹配 — 无边界 "seat" 子串使
# "Tell me about Seattle" 误判 capacity 并借用 active_topic 容量事实;
# 中文问法保持子串规则不变, 字段优先级顺序不变。
# V30-04 R1: 英文价格词同样词边界匹配 (how much/price/cost/charge/fee/free/
# deposit/pay/spend/rate); price 置于 notes 前, 价格问法不得落入概述/notes 冒充。
_KB_FIELD_RES = {"capacity": re.compile(r"\bseats?\b|\bseating\b|\bcapacity\b", re.I),
                 "price": re.compile(r"\bhow much\b|\bprice\b|\bcosts?\b|\bcharg\w*\b|\bfees?\b"
                                     r"|\bfree\b|\bdeposit\b|\bpay\b|\bspend\b|\brate\b", re.I)}

def _kb_detect_field(text):
    low = text.lower()
    for field, pats in _KB_FIELD_PATTERNS:
        for p in pats:
            if p in low or p in text: return field
        rex = _KB_FIELD_RES.get(field)
        if rex is not None and rex.search(text): return field
    return None

def _kb_match_topic(text, locale, topics):
    """最长名称/命中词包含匹配 (ascii 大小写不敏感); 无命中 → None。"""
    lower = text.lower()
    best = None; best_len = 0
    for t in topics:
        if t["locale"] != locale: continue
        for n in [t["name"]] + t["keywords"]:
            if not n or len(n) < 2: continue
            if n in text or n.lower() in lower:
                if len(n) > best_len: best_len = len(n); best = t
    return best

def _kb_strip_entity(text, topic):
    """V30-04 R1 (KB30-04-01 字段级路由正确性): 从问句剔除已直接命中主题的名称/
    别名出现 (实体提及剥离, 长词优先)。实体名内含字段词 (别名「酒店位置与地铁」/
    「club floor」/「行政楼层酒廊」/「outdoor seating」含 位置/floor/楼层/seating)
    不得劫持所问字段 —「酒店位置的电话是多少」应识别 phone 而非 location,
    「行政楼层酒廊怎么样」应走概述而非 location 直答。未命中主题时原样返回
    (未知实体的名称词无从剔除, 字段词仍按问句语义归属)。"""
    if topic is None:
        return text
    out = text or ""
    for n in sorted([topic["name"]] + topic["keywords"], key=len, reverse=True):
        if not n or len(n) < 2:
            continue
        out = re.sub(re.escape(n), " ", out, flags=re.I)
    return out

_KB_STEM_FILLER = [
    "请问一下", "请问", "麻烦", "我想找", "我想", "找一下", "一下", "告诉我", "告诉",
    "您好", "谢谢", "营业时间", "营业时段", "营业", "开放", "开门", "关门", "位置",
    "地址", "电话", "号码", "联系方式", "楼层", "几楼", "路线", "时间", "介绍", "评价",
    "推荐", "政策", "限制", "注意", "预约", "提示", "吗", "呢", "呀", "啊",
    # V27-03 (CHAIN27-03-02 串主题根因): 疑问功能词绝不能作主题短称 —
    # "怎么预约" 抽出 stem "怎么" 后被别名"怎么去酒店"(酒店位置与地铁)严格包含,
    # 省略主语追问被劫持到无关主题。这些词只出现在问法里, 不会是实体短称。
    "怎么样", "怎么", "如何", "怎样", "哪里", "哪儿", "为什么", "为啥", "多少",
    "有没有", "是不是", "能不能", "能否", "可以",
    # V30-04 R1: 客套动词短语 (帮我查一下/帮我预订) 不作主题短称
    "帮我", "查一下",
]
_KB_STEM_EN_STOP = {
    "where", "what", "when", "how", "which", "is", "are", "the", "a", "an", "of",
    "to", "in", "on", "at", "do", "does", "you", "your", "me", "my", "i", "we",
    "have", "has", "please", "tell", "want", "know", "there", "it", "and", "or",
    # V30-04 R1: 情态/时间副词等疑问功能词 — 单独出现必是问法碎片 (如 "can i book
    # cinema" 的 can 曾被 "cantonese" 吸收), 绝不是实体短称
    "can", "may", "might", "will", "would", "should", "just", "still", "now",
    "today", "right", "currently", "temporarily", "moment", "yet", "also",
    "ask", "excuse", "hey", "hi", "wondering", "offer",
}

def _kb_extract_stems(text):
    """从问题抽取 ≥2 字主题短称 (确定性, 无分词词典): 先剔除字段问法词与客套/
    疑问填充词, 再取剩余连续 CJK 段 (≥2 字) 或英文词组 (≥2 字母, 小写)。
    纯数字段 (楼层/时间碎片) 不是主题短称。
    V30-04 R1: 英文短称按"连续非停用词词组"整体抽取 — 问句里的单个常见词
    (room/club/court/can…) 只是更长表达的碎片, 词组级匹配使其不再被其他条目
    更长别名吸收 (未知设施问法被错误独占/错候选澄清的根因); 完整单词短称
    (lounge/spa) 与中文短称行为不变 (直接别名命中先于本路径, 不受影响)。"""
    stripped = text or ""
    for _, pats in _KB_FIELD_PATTERNS:
        for p in pats:
            if p in stripped:
                stripped = stripped.replace(p, " ")
    for rex in _KB_FIELD_RES.values():
        stripped = rex.sub(" ", stripped)   # 英文字段词按词边界剔除, 不作主题短称
    for w in _KB_STEM_FILLER:
        if w in stripped:
            stripped = stripped.replace(w, " ")
    stems = []
    for seg in re.split(r"[^一-鿿a-zA-Z0-9 ]+", stripped):
        seg = seg.strip()
        if not seg or seg.isdigit():
            continue
        if _kb_is_zh(seg):
            # 中英混排时空格分隔的概念各自成短称 (与旧逐词行为一致)
            for sub in seg.split():
                if len(sub) >= 2 and not sub.isdigit():
                    stems.append(sub)
        else:
            # V30-04 R1: 英文词组只留 ≥3 字母词 — 2 字母碎片 (如 "ev charging" 的
            # ev ⊂ elevated/events) 是更长表达的片段, 不是实体短称, 吸收即误澄清
            words = [w for w in seg.lower().split()
                     if w not in _KB_STEM_EN_STOP and len(w) >= 3]
            if words:
                stems.append(" ".join(words))
        if len(stems) >= 6:
            break
    return stems

def _kb_stem_candidates(stems, locale, topics):
    """受控包含候选搜索: 短称被已发布主题名/命中词**严格包含** (短称短于该名称/
    命中词) 的主题为候选; 按索引顺序去重。等长即直接匹配职责, 不在此重复。"""
    out, seen = [], set()
    for t in topics:
        if t["locale"] != locale:
            continue
        names = [n for n in [t["name"]] + t["keywords"] if n and len(n) >= 2]
        hit = False
        for stem in stems:
            ls = stem.lower()
            if any(len(stem) < len(n) and (stem in n or ls in n.lower()) for n in names):
                hit = True
                break
        if hit and t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
    return out

_KB_FIELD_ZH = {"time": "营业时间", "location": "位置", "directions": "路线",
                "phone": "电话", "notes": "备注", "capacity": "座位", "price": "价格"}

def _kb_direct_answer(topic, field, locale, turn):
    """确定性直接回答: 所问字段有值 → 模板句; 无字段 (概述问) → 轮换标准回答变体;
    问了字段但词条缺该字段 → None (§26.0.8 缺字段路径, 交 NPU 兜底)。"""
    name = topic["name"]; f = topic["fields"]
    if field:
        v = f.get(field)
        if not v: return None
        if locale == "en-US":
            return {"time": f"The {name} is open {v}.",
                    "location": f"The {name} is at {v}.",
                    "directions": f"Directions to the {name}: {v}.",
                    "phone": f"The {name} phone number is {v}.",
                    "notes": f"{name}: {v}",
                    # V29-08: 容量值是逐字引用的已发布子句, 用中性模板承载
                    "capacity": f"{name}: {v}."}[field]
        if field == "time": return f"{name}的营业时间是{v}。"
        if field == "notes": return f"{name}的相关信息：{v}"
        if field == "capacity": return f"{name}的座位信息：{v}。"
        return f"{name}的{_KB_FIELD_ZH[field]}是{v}。"
    ans = topic["answers"]
    if ans: return ans[int(turn or 0) % len(ans)]
    return None

_KB_COMPARISON_RE = re.compile(
    r"最大|最小|最近|最宽敞|最豪华|最便宜|哪个最|哪一家最|哪一个最|哪个更|哪一家更|哪一个更"
    r"|更大|更小|更近|更宽敞|更豪华|更便宜|更安静|更合适|更方便"
    r"|biggest|largest|smallest|nearest|closest|most spacious|which is the", re.I)
# 无主题时的比较级收窄口径: 「最近」兼作时间副词 ("最近天气怎么样" 必须落回通用
# 路由), 只在已命中主题的问句里按距离比较处理
_KB_COMPARISON_TOPICLESS_RE = re.compile(
    r"最大|最小|最宽敞|最豪华|哪个最|哪一家最|哪一个最|哪个更|哪一家更|哪一个更"
    r"|biggest|largest|smallest|most spacious|which is the", re.I)

# ── V25-08R2 (可用性缺口): 确定性范围识别 (酒店外/附近资源 · 通用停车政策) ──
_KB_OUTSIDE_MARKERS = {"zh-CN": ("附近", "周边", "周围", "酒店外", "酒店以外", "外面"),
                       "en-US": ("nearby", "near the hotel", "around the hotel",
                                 "outside the hotel", "close to the hotel", "in the area")}
_KB_OUTSIDE_RESOURCES = (
    # (key, zh 触发词, en 触发词, zh 标签, en 标签)
    ("restaurant", ("餐厅", "吃饭", "美食", "用餐", "饭馆", "好吃", "吃"),
     ("restaurant", "dining", "dinner", "lunch", "breakfast", "brunch", "supper",
      "eat", "food", "cafe"), "餐厅", "restaurants"),
    ("parking", ("停车", "泊车"), ("parking", "valet", "car park", "park"), "停车场", "parking"),
    ("shopping", ("购物", "商场", "超市", "逛街"), ("shopping", "mall", "supermarket", "shop"),
     "购物商场", "shopping"),
    ("attraction", ("景点", "好玩", "名胜", "景区"), ("attraction", "sights", "landmark"),
     "景点", "attractions"),
    ("pharmacy", ("药店", "药房"), ("pharmacy", "drugstore", "chemist"), "药店", "pharmacies"),
    ("medical", ("医院", "诊所"), ("hospital", "clinic"), "医院", "hospitals and clinics"),
)
# 泛范围/泛酒店别名: 只命名酒店自身或范围, 不指向具体主题 — 酒店外资源问句被这类
# 别名命中 (如 en "... near the hotel" 命中 Hotel Overview 别名 "hotel") 时视为未点名
# 具体主题, 缺口裁决优先; 具体主题名/别名命中 (如 唐阁/景点) 则走正常路由。
_KB_GENERIC_SCOPE_ALIASES = {
    "酒店", "酒店介绍", "酒店概览", "新天地朗廷", "朗廷", "附近玩",
    "hotel", "overview", "hotel overview", "langham", "the langham", "nearby"}

def _kb_outside_scope(text, locale):
    """识别"酒店外/附近"资源问句: 同时含外部范围标记与资源域词 → (key, 本地化标签)。
    en 资源词按词首边界匹配 (避免 "eat" 命中 "weather" 一类子串误报)。"""
    low = (text or "").lower()
    markers = _KB_OUTSIDE_MARKERS.get(locale) or _KB_OUTSIDE_MARKERS["zh-CN"]
    if not any(m in low for m in markers):
        return None
    for key, zh_w, en_w, zh_lab, en_lab in _KB_OUTSIDE_RESOURCES:
        if locale == "en-US":
            if any(re.search(r"\b" + re.escape(w), low) for w in en_w):
                return key, en_lab
        elif any(w in text for w in zh_w):
            return key, zh_lab
    return None

def _kb_names_blob(topic):
    """主题名称+别名连接文本 — 范围覆盖检查口径: 词条正文顺带提及 (如购物 notes 提到
    商圈餐厅) 不构成该资源的外部条目资料, 只有名称/别名层面才是该资源的条目。"""
    return " ".join([topic["name"]] + list(topic["keywords"]))

def _kb_outside_covered(topics, locale, resource_key):
    """已发布词条是否覆盖该资源的酒店外部信息: 某词条名称/别名同时含外部标记与该
    资源域词 (如「周边景点」「周边购物」)。未来发布外部餐厅词条后, 该类问句自动
    回到正常路由 — 缺口话术只陈述"当前知识库暂无", 不硬编码数据边界。"""
    res = next((r for r in _KB_OUTSIDE_RESOURCES if r[0] == resource_key), None)
    if res is None:
        return False
    markers = _KB_OUTSIDE_MARKERS.get(locale) or _KB_OUTSIDE_MARKERS["zh-CN"]
    words = res[2] if locale == "en-US" else res[1]
    for t in topics:
        if t["locale"] != locale:
            continue
        blob = _kb_names_blob(t).lower()
        if not any(m in blob for m in markers):
            continue
        if locale == "en-US":
            if any(re.search(r"\b" + re.escape(w), blob) for w in words):
                return True
        elif any(w in blob for w in words):
            return True
    return False

def _kb_parking_general(text, locale, topics):
    """通用停车政策问句且知识库无酒店级停车词条 (名称/别名含停车词) → True。
    条目级停车问句 (点名水疗/套餐主题) 不进入本判断: 礼遇事实只在条目级发布,
    不得在通用停车问句下被扩展成酒店停车政策。"""
    words = ("parking", "valet", "car park") if locale == "en-US" else ("停车", "泊车")
    low = (text or "").lower()
    if locale == "en-US":
        if not any(re.search(r"\b" + re.escape(w), low) for w in words):
            return False
    elif not any(w in text for w in words):
        return False
    for t in topics:
        if t["locale"] != locale:
            continue
        blob = _kb_names_blob(t)
        if locale == "en-US":
            if any(re.search(r"\b" + re.escape(w), blob.lower()) for w in words):
                return False
        elif any(w in blob for w in words):
            return False
    return True

def _kb_specific_topic_named(text, locale, topics):
    """问题是否点名了某个具体主题 (非泛酒店/泛范围别名命中): 返回该主题或 None。
    酒店外/停车缺口裁决只拦截"泛指"问句; 客人点名的具体主题 (唐阁/水疗政策/景点)
    始终走正常路由 — 已发布事实按主题自身归属回答, 不因问句带"附近"而拦截。"""
    lower = text.lower()
    for t in topics:
        if t["locale"] != locale:
            continue
        for n in [t["name"]] + t["keywords"]:
            if not n or len(n) < 2:
                continue
            if (n in text or n.lower() in lower) and n.lower() not in _KB_GENERIC_SCOPE_ALIASES:
                return t
    return None

# ══ 网关逐字副本区结束 ══

# 独占直答 (返回已发布事实/概述回答的决策)
FACT_LEAK_DECISIONS = ("KB_DIRECT_FACT", "KB_DIRECT_OVERVIEW")
# NO_FACT 安全兜底决策: 非知识/低匹配规划器(自带 supported=false→KB_SAFE 与
# grounding 防火墙)/缺字段固定话术/范围缺口固定话术
SAFE_FALLBACK_DECISIONS = ("NOT_KNOWLEDGE", "KB_NPU_PLANNER", "KB_MISSING_FIELD",
                           "KB_SAFE_SCOPE_GAP")
# clarify (声明字段缺失) 允许的运行时结局: 缺字段话术 / 确定性澄清 / 安全非独占路径;
# 任何独占直答 (含本条目概述/其他字段) 都算冒充回答
CLARIFY_SAFE_DECISIONS = ("KB_MISSING_FIELD", "KB_CLARIFY", "NOT_KNOWLEDGE",
                          "KB_NPU_PLANNER", "KB_SAFE_SCOPE_GAP")


def removed_keywords(pkg: dict, tpl: dict) -> dict:
    """增强别名包需要移除的源 keyword (确定性规则, 只动 alias 不动事实/主题名):
      1. 泛指范围词 (templates generic_scope_aliases, 与网关 _KB_GENERIC_SCOPE_ALIASES
         对齐) — 纯泛酒店/泛位置问法词, 不得独占绑定任何主题;
      2. 公共简称 — 是其他条目信号词 (topic/keyword, 泛词豁免) 严格子串的 alias,
         多主题共享叫法应由运行时澄清, 不独占绑定。
    返回 {(entry_id, lang): [{"keyword", "reason"}]}。"""
    generic = set(tpl["generic_scope_aliases"]["zh"]) | \
        set(tpl["generic_scope_aliases"]["en"])
    raws = {}
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            raw = [e["topic"][lang]] + list(e["keywords"][lang])
            raws[(e["id"], lang)] = [w for w in raw
                                     if w and len(w) >= 2 and w not in generic]
    out = {}
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            drops = []
            topic_word = (e["topic"][lang] or "").strip()
            for kw in e["keywords"][lang]:
                why = None
                if kw in generic:
                    why = "generic_scope_alias"
                else:
                    low = kw.lower() if lang == "en" else kw
                    for (oid, olang), words in raws.items():
                        if olang != lang or oid == e["id"]:
                            continue
                        for w in words:
                            wl = w.lower() if lang == "en" else w
                            if len(low) < len(wl) and low in wl:
                                why = f"public_short_form_of_entry_{oid}"
                                break
                        if why:
                            break
                if why and kw != topic_word:
                    drops.append({"keyword": kw, "reason": why})
            if drops:
                out[(e["id"], lang)] = drops
    return out


def build_topics(pkg: dict, corpus: dict, enhanced: bool, tpl: dict = None) -> list:
    """模拟发布物化 (_kb_build_topics entities 路径) 的同构 topics:
    id/locale/name/keywords/fields/answers; enhanced 时 keywords 追加 variant_terms
    并移除公共简称/泛指词 (与被评测/导入的 alias_enhanced_package 一致)。"""
    vt = corpus.get("variant_terms") or {}
    removals = removed_keywords(pkg, tpl) if (enhanced and tpl) else {}
    topics = []
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            name = e["topic"][lang]
            if not name:
                continue
            kws = list(dict.fromkeys(e["keywords"][lang]))
            if enhanced:
                rm = {d["keyword"] for d in removals.get((e["id"], lang), [])}
                kws = [k for k in kws if k not in rm]
                kws += [v for v in (vt.get(str(e["id"])) or {}).get(lang, [])
                        if v not in kws]
            fields = {}
            ctx = e["context"]
            for k in ("time", "location", "directions", "notes"):
                v = ctx[k][lang]
                if v:
                    fields[k] = v
            if ctx.get("phone"):
                fields["phone"] = ctx["phone"]
            topics.append({"id": str(e["id"]),
                           "locale": "zh-CN" if lang == "zh" else "en-US",
                           "name": name, "keywords": kws, "fields": fields,
                           "answers": list(e["answers"][lang])})
    return topics


def route_single_turn(text: str, locale: str, topics: list) -> dict:
    """网关 kb_route 确定性决策链的完整单轮模型 (V30-04 R1)。

    建模范围 = kb_route 在"新会话首轮"(无待澄清态/无上下文主题/无 TTL 重置) 下
    的全部确定性分支: 范围缺口裁决 (V25-08R2 酒店外资源/通用停车, 先于主题匹配)
    → 字段识别 → 直接最长匹配 → 公共短称受控候选 (0/1/≥2, 比较级豁免) →
    比较级分支 → 直答/缺字段兜底 → 低匹配规划器/非知识。
    单轮离线不模拟 (与运行时执行的差异, 均为非独占或多轮状态): 会话澄清待决态、
    上下文主题补全、NPU 规划器执行 (规划器非独占多卡路径, supported=false →
    KB_SAFE, 自带 grounding 防火墙, 见 kb_npu_fallback)。
    决策分类 (与生成器/校验器/评测器的断言口径一致):
      KB_DIRECT_FACT     独占直答: 命中主题 + 所问字段有已发布值 (返回该字段事实)
      KB_DIRECT_OVERVIEW 独占直答: 命中主题 + 概述回答变体
      KB_MISSING_FIELD   安全兜底: 命中主题但缺所问字段 (固定"暂未收录"话术, 零事实)
      KB_CLARIFY         确定性澄清 (≥2 候选, 列候选请客人选择)
      KB_SAFE_SCOPE_GAP  安全兜底: 酒店外/通用停车范围缺口固定话术 (KB_SAFE)
      KB_NPU_PLANNER     低匹配/比较级兜底: 交 NPU 规划器 (非独占)
      NOT_KNOWLEDGE      非知识问句, 交主路由
    """
    # V25-08R2: 范围缺口确定性裁决 (先于主题匹配; 点名具体主题不拦截)
    gap = _kb_outside_scope(text, locale)
    if gap is not None and not _kb_outside_covered(topics, locale, gap[0]) \
            and _kb_specific_topic_named(text, locale, topics) is None:
        return {"decision": "KB_SAFE_SCOPE_GAP", "gap": gap[0]}
    if gap is None and _kb_parking_general(text, locale, topics) \
            and _kb_specific_topic_named(text, locale, topics) is None:
        return {"decision": "KB_SAFE_SCOPE_GAP", "gap": "parking"}
    # V30-04 R1: 直接主题匹配 → 实体提及剥离 → 字段识别 (与网关 kb_route 同序)
    topic = _kb_match_topic(text, locale, topics)
    field = _kb_detect_field(_kb_strip_entity(text, topic))
    if topic is None:
        stems = _kb_extract_stems(text)
        cands = _kb_stem_candidates(stems, locale, topics) if stems else []
        if len(cands) == 1:
            topic = cands[0]
        elif len(cands) >= 2 and not _KB_COMPARISON_TOPICLESS_RE.search(text):
            return {"decision": "KB_CLARIFY",
                    "candidates": [t["id"] for t in cands]}
    if topic is None and not field:
        # 比较级问题无主题无字段时按 notes 语义进规划器 (不落 NOT_KNOWLEDGE 交
        # LLM 自由生成); 收窄口径避免时间副词误收 — 与网关同规则
        if _KB_COMPARISON_TOPICLESS_RE.search(text):
            field = "notes"
        else:
            return {"decision": "NOT_KNOWLEDGE"}
    if topic is not None:
        # 比较级问句不走直接模板 (模板无法安全表达比较关系), 交规划器选卡
        if not _KB_COMPARISON_RE.search(text):
            answer = _kb_direct_answer(topic, field, locale, 0)
            if answer is not None:
                return {"decision": "KB_DIRECT_FACT" if field else "KB_DIRECT_OVERVIEW",
                        "topic_id": topic["id"], "field": field}
            return {"decision": "KB_MISSING_FIELD", "topic_id": topic["id"],
                    "field": field}
        return {"decision": "KB_NPU_PLANNER", "field": field, "reason": "comparison"}
    return {"decision": "KB_NPU_PLANNER", "field": field, "reason": "low_match"}


# ══ intent 级事实能力 (V30-04 R1, KB30-04-01) ══
# intent → 运行时回答字段: 声明 intent 的问法最终必须由该字段的已发布值回答
# (availability/overview 无字段 → 概述回答变体)。price 为独立可问字段。
INTENT_RUNTIME_FIELD = {
    "time": "time", "location": "location", "directions": "directions",
    "phone": "phone", "price": "price", "policy": "notes", "booking": "notes",
    "availability": None, "overview": None,
}
# notes 承载 policy/booking 两类 intent 的事实标记: notes 已发布且其文本含对应
# 标记, 该 intent 的事实才算存在 — 回答字段内容必须真正承载所问 intent 的事实,
# 不再"notes 非空同时授权 price/policy/booking 三种事实"。
NOTES_INTENT_MARKERS = {
    "policy": re.compile(r"政策|规定|限制|要求|须知|年龄|着装|会员|登记|允许|禁止|只限|仅限"
                         r"|保留|押金|政策|policy|rules?|restrictions?|requirements?|dress"
                         r"|age|members|allowed|only|held|deposit", re.I),
    "booking": re.compile(r"预约|预订|订位|订座|退订|保留|reserv|book|cancel|held", re.I),
}


def intent_fact_available(e: dict, lang: str, intent: str) -> bool:
    """(entry, lang) 是否已发布 intent 对应字段的事实 — intent 级判定 (共享唯一口径,
    生成器/校验器/评测器同源 import):
      time/location/directions → context 对应语言字段; phone → context.phone;
      price → 恒 False (joctv-hotel-kb-v1 无结构化价格字段 — 价格问法一律走
               KB_MISSING_FIELD/规划器安全兜底, 不得用 notes/概述冒充);
      policy/booking → notes 已发布且文本含该 intent 事实标记 (着装/规定/年龄…
               vs 预约/预订/退订…);
      availability/overview → 回答变体 answers。"""
    ctx = e.get("context") or {}
    if intent in ("time", "location", "directions"):
        return bool(str((ctx.get(intent) or {}).get(lang) or "").strip())
    if intent == "phone":
        return bool(str(ctx.get("phone") or "").strip())
    if intent in ("policy", "booking"):
        notes = str((ctx.get("notes") or {}).get(lang) or "").strip()
        return bool(notes) and bool(NOTES_INTENT_MARKERS[intent].search(notes))
    if intent == "price":
        return False
    return bool((e.get("answers") or {}).get(lang))


def runtime_field_published(e: dict, lang: str, intent: str) -> bool:
    """(entry, lang) 的**声明运行时字段**是否已发布 (字段级, 与网关 fields 同口径):
    time/location/directions/phone → context 对应字段; price → 恒 False (kb-v1 无
    schema 价格字段); policy/booking → notes 非空 (与 intent 级标记判定无关 —
    声明字段已发布时网关用该字段回答, 属同条目同字段诚实回答); availability/
    overview → answers。clarify 断言据此区分"声明字段缺失"(严格安全集)与
    "intent 标记缺但字段已发布"(允许同条目同字段直答)。"""
    fld = INTENT_RUNTIME_FIELD[intent]
    if fld is None:
        return intent_fact_available(e, lang, intent)
    if fld == "notes":
        return bool(str(((e.get("context") or {}).get("notes") or {}).get(lang) or "").strip())
    return intent_fact_available(e, lang, intent)


def route_expectation(binding: str, intent: str, entry_id,
                      runtime_field_present: bool = False) -> callable:
    """生成/校验/评测共用的"声明 ↔ 最终决策"断言器: 返回 fn(decision_dict) -> ok。
      entry   → 独占直答且字段/主题与声明一致 (availability/overview → 概述直答);
      clarify → **声明字段缺失时** (runtime_field_present=False) 只允许
                KB_MISSING_FIELD(字段=声明字段)/有效澄清/安全非独占路径, 任何独占
                直答 (含本条目概述/其他字段) 都是冒充回答; **声明字段已发布但
                intent 级事实标记缺** (policy/booking notes 无对应标记) 时, 同条目
                同声明字段的直答是诚实回答 (回答字段与声明一致, 不借条目不换字段),
                其余独占直答仍算冒充;
      no_fact → 只允许安全兜底 (错候选澄清不算)。"""
    fld = INTENT_RUNTIME_FIELD[intent]

    def _fn(d: dict) -> bool:
        dec = d.get("decision")
        if binding == "entry":
            want = "KB_DIRECT_OVERVIEW" if fld is None else "KB_DIRECT_FACT"
            return (dec == want and d.get("topic_id") == str(entry_id)
                    and d.get("field") == fld)
        if binding == "clarify":
            if dec in FACT_LEAK_DECISIONS:
                return (runtime_field_present and dec == "KB_DIRECT_FACT"
                        and d.get("topic_id") == str(entry_id)
                        and d.get("field") == fld)
            if dec == "KB_MISSING_FIELD" and d.get("field") != fld:
                return False
            return dec in CLARIFY_SAFE_DECISIONS
        return dec in SAFE_FALLBACK_DECISIONS

    return _fn
