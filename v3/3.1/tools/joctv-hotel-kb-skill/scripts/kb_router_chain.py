#!/usr/bin/env python3
"""JOCTV 网关 KB 路由链共享副本 (Skill 1.2.0)。

网关 p4_admin/v3_candidate_backend.py 确定性路由层的逐字副本 + Skill 侧共享 helper
(增强别名包 keyword 移除规则 / 发布物化 topics 构建 / 最终决策模型)。生成器
(generate_utterance_coverage) 与评测器 (eval_route_holdout) 共用本模块, 保证
"生成时预审"与"评测断言"消费同一决策口径。

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

_KB_FIELD_PATTERNS = [
    ("directions", ["怎么去", "怎么走", "如何到达", "怎么到达", "路线", "怎么过去", "怎么到",
                    "directions", "how to get", "how do i get", "how can i get", "the way to"]),
    ("time", ["几点", "什么时间", "营业时间", "开放时间", "关门", "开门", "营业到", "到几点",
              "营业时段", "时间", "hours", "what time", "opening", "closing", "when is it open"]),
    ("location", ["在哪里", "在哪", "位置", "几楼", "哪个楼层", "楼层", "在几层",
                  "where", "located", "location"]),
    ("phone", ["电话", "联系方式", "号码", "联系电话", "phone", "telephone", "call", "number"]),
    ("capacity", ["座位", "容纳", "能坐"]),
    ("notes", ["备注", "注意", "限制", "政策", "预约", "教练", "特色", "推荐", "介绍", "评价",
               "提示", "notes", "policy", "reservation", "recommend", "introduction", "tips"]),
]
_KB_FIELD_RES = {"capacity": re.compile(r"\bseats?\b|\bseating\b|\bcapacity\b", re.I)}

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

_KB_STEM_FILLER = [
    "请问一下", "请问", "麻烦", "我想找", "我想", "找一下", "一下", "告诉我", "告诉",
    "您好", "谢谢", "营业时间", "营业时段", "营业", "开放", "开门", "关门", "位置",
    "地址", "电话", "号码", "联系方式", "楼层", "几楼", "路线", "时间", "介绍", "评价",
    "推荐", "政策", "限制", "注意", "预约", "提示", "吗", "呢", "呀", "啊",
    "怎么样", "怎么", "如何", "怎样", "哪里", "哪儿", "为什么", "为啥", "多少",
    "有没有", "是不是", "能不能", "能否", "可以",
]
_KB_STEM_EN_STOP = {
    "where", "what", "when", "how", "which", "is", "are", "the", "a", "an", "of",
    "to", "in", "on", "at", "do", "does", "you", "your", "me", "my", "i", "we",
    "have", "has", "please", "tell", "want", "know", "there", "it", "and", "or",
}

def _kb_extract_stems(text):
    """从问题抽取 ≥2 字主题短称 (确定性, 无分词词典): 先剔除字段问法词与客套/
    疑问填充词, 再取剩余连续 CJK 段 (≥2 字) 或英文词 (≥2 字母, 小写)。
    纯数字段 (楼层/时间碎片) 不是主题短称。"""
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
    for seg in re.split(r"[^一-鿿a-zA-Z0-9]+", stripped):
        seg = seg.strip()
        if len(seg) < 2 or seg.isdigit():
            continue
        if _kb_is_zh(seg):
            stems.append(seg)
        else:
            low_seg = seg.lower()
            if low_seg not in _KB_STEM_EN_STOP:
                stems.append(low_seg)
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
                "phone": "电话", "notes": "备注", "capacity": "座位"}

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
                    "capacity": f"{name}: {v}."}[field]
        if field == "time": return f"{name}的营业时间是{v}。"
        if field == "notes": return f"{name}的相关信息：{v}"
        if field == "capacity": return f"{name}的座位信息：{v}。"
        return f"{name}的{_KB_FIELD_ZH[field]}是{v}。"
    ans = topic["answers"]
    if ans: return ans[int(turn or 0) % len(ans)]
    return None

# ══ 网关逐字副本区结束 ══

# 独占直答 (返回已发布事实/概述回答的决策)
FACT_LEAK_DECISIONS = ("KB_DIRECT_FACT", "KB_DIRECT_OVERVIEW")
# NO_FACT 安全兜底决策: 非知识/低匹配规划器(自带 supported=false→KB_SAFE 与
# grounding 防火墙)/缺字段固定话术
SAFE_FALLBACK_DECISIONS = ("NOT_KNOWLEDGE", "KB_NPU_PLANNER", "KB_MISSING_FIELD")


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


def final_decision(text: str, locale: str, topics: list) -> dict:
    """网关 kb_route 确定性决策链的单轮模型。
    建模范围: 字段识别 → 直接最长匹配 → 公共短称受控候选 (0/1/≥2) → 直答 /
    缺字段兜底。单轮离线不模拟: 会话澄清待决态、上下文主题、比较级分支、
    NPU 规划器执行 (规划器为非独占多卡路径, supported=false → KB_SAFE,
    自带 grounding 防火墙, 见 kb_npu_fallback)。
    决策分类:
      KB_DIRECT_FACT     独占直答: 命中主题 + 所问字段有已发布值 (返回事实)
      KB_DIRECT_OVERVIEW 独占直答: 命中主题 + 概述回答变体
      KB_MISSING_FIELD   安全兜底: 命中主题但缺所问字段 (固定"暂未收录"话术, 零事实)
      KB_CLARIFY         确定性澄清 (≥2 候选, 列候选请客人选择)
      KB_NPU_PLANNER     低匹配兜底: 交 NPU 规划器 (非独占; 失败/不支持 → KB_SAFE)
      NOT_KNOWLEDGE      非知识问句, 交主路由
    """
    topic = _kb_match_topic(text, locale, topics)
    if topic is None:
        stems = _kb_extract_stems(text)
        cands = _kb_stem_candidates(stems, locale, topics) if stems else []
        if len(cands) == 1:
            topic = cands[0]
        elif len(cands) >= 2:
            return {"decision": "KB_CLARIFY",
                    "candidates": [t["id"] for t in cands]}
    field = _kb_detect_field(text)
    if topic is not None:
        if field:
            if field in topic["fields"]:
                return {"decision": "KB_DIRECT_FACT", "topic_id": topic["id"],
                        "field": field}
            return {"decision": "KB_MISSING_FIELD", "topic_id": topic["id"],
                    "field": field}
        if topic["answers"]:
            return {"decision": "KB_DIRECT_OVERVIEW", "topic_id": topic["id"]}
        return {"decision": "KB_MISSING_FIELD", "topic_id": topic["id"],
                "field": None}
    if field is None:
        return {"decision": "NOT_KNOWLEDGE"}
    return {"decision": "KB_NPU_PLANNER", "field": field}
