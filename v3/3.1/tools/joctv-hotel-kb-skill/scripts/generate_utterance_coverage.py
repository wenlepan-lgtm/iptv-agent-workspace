#!/usr/bin/env python3
"""JOCTV 酒店问法覆盖生成器 (Skill 1.2.0)。

从已发布知识包 (joctv-hotel-kb-v1) + 通用酒店意图模板生成双语问法语料:
  - 每条问法绑定 language/intent/field/entry (可追溯) 或明确 NO_FACT;
  - 事实感知映射 (1.2.0): 只对 (entry, lang, intent) 已发布对应字段的组合生成
    binding=entry 问法; 该条目被问到但未发布该字段事实的问法 → binding=clarify
    (运行时预期: 路由到该条目后走 KB_MISSING_FIELD 安全兜底或确定性澄清,
    绝不从其他条目借事实回答);
  - 称呼来自源信号词 (base) 与新增同义称呼 (variant, 增强别名的唯一来源);
    非主题名且是其他条目信号词严格子串的称呼 = 公共简称, 不作 base 称呼
    (公共简称问法由运行时澄清, 不独占绑定任何条目);
  - 最终决策链预审 (1.2.0): clarify/no_fact 候选问法用网关 kb_route 确定性决策链
    副本 (scripts/kb_router_chain) 对增强别名发布态 topics 预审 — no_fact 问法必须
    落入安全兜底 (NOT_KNOWLEDGE / KB_NPU_PLANNER / KB_MISSING_FIELD), clarify 问法
    不得被其他条目独占直答; 不合格候选确定性跳过并计入生成报告 (公共简称/模板
    填充词的 stem 吸收泄漏一并根治);
  - 全程确定性 (固定 seed, 无时间戳入产物), 同输入同 seed → 同字节输出;
  - 不生成任何事实值 (时间/电话/价格数字不进问法)。

用法:
  python3 generate_utterance_coverage.py --package <kb.json> --out <dir> \
      [--templates ../templates/utterance_intent_templates.json] \
      [--seed 20260904] [--per-cell 32] [--clarify-per-cell 8] \
      [--min-unique 10000]
"""
import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_router_chain import (FACT_LEAK_DECISIONS, SAFE_FALLBACK_DECISIONS,  # noqa: E402
                             build_topics, final_decision)

CJK_RE = re.compile(r'[一-鿿]')
LATIN_RE = re.compile(r'[a-zA-Z]')
LOCALE = {"zh": "zh-CN", "en": "en-US"}
INTENT_FIELD = {
    "time": "time", "location": "location", "directions": "directions",
    "phone": "phone", "price": "notes", "policy": "notes", "booking": "notes",
    "availability": "overview", "overview": "overview",
}


def norm(lang: str, text: str) -> str:
    """归一化: 去全部非字母数字汉字字符 + 小写 (唯一性/近似冲突口径)。"""
    t = re.sub(r'[^一-鿿a-zA-Z0-9]', '', text or '')
    return t.lower() if lang == "en" else t


def is_zh_text(s: str) -> bool:
    return bool(CJK_RE.search(s or ""))


def load_templates(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def term_questionable(lang: str, term: str, tpl: dict) -> bool:
    if lang == "zh":
        return any(q in term for q in tpl["question_term_filter_zh"])
    words = set(re.findall(r"[a-z']+", term.lower()))
    return any(w in tpl["question_term_filter_en"] for w in words)


def term_verb(lang: str, term: str, tpl: dict) -> bool:
    if lang == "zh":
        return bool(term) and term[0] in tpl["verb_start_chars_zh"]
    words = re.findall(r"[a-z']+", term.lower())
    return bool(words) and words[0] in tpl["verb_start_words_en"]


def term_generic(lang: str, term: str, tpl: dict) -> bool:
    return term in tpl["generic_terms"].get(lang, [])


def intent_fact_available(e: dict, lang: str, intent: str) -> bool:
    """(entry, lang) 是否已发布 intent 对应字段的事实 (事实感知映射的判定口径)。
    time/location/directions → context 对应语言字段; phone → context.phone (不分语言);
    price/policy/booking → context.notes; availability/overview → 回答变体 answers。"""
    ctx = e.get("context") or {}
    if intent in ("time", "location", "directions"):
        return bool(str((ctx.get(intent) or {}).get(lang) or "").strip())
    if intent == "phone":
        return bool(str(ctx.get("phone") or "").strip())
    if intent in ("price", "policy", "booking"):
        return bool(str((ctx.get("notes") or {}).get(lang) or "").strip())
    return bool((e.get("answers") or {}).get(lang))


def build_signal_terms(pkg: dict, tpl: dict, raws: dict) -> tuple:
    """每 (entry_id, lang) 的称呼合格信号词: {id: {lang: [(term, kind='base', verb: bool)]}}。
    公共简称剔除 (1.2.0): 非主题名、且是其他条目信号词 (topic/keyword, 泛指词豁免)
    严格子串的称呼不作 base 称呼 — 此类问法运行时应确定性澄清, 不独占绑定。
    返回 (signals, dropped) — dropped 含原因, 进生成报告。"""
    out, dropped = {}, []
    for e in pkg["entries"]:
        per = {}
        for lang in ("zh", "en"):
            raw = [e["topic"][lang]] + list(e["keywords"][lang])
            terms = []
            for t in raw:
                t = (t or "").strip()
                if len(t) < 2 or len(t) > 24:
                    continue
                if term_questionable(lang, t, tpl) or term_generic(lang, t, tpl):
                    continue
                if lang == "zh" and not is_zh_text(t):
                    continue
                if lang == "en" and (not LATIN_RE.search(t) or is_zh_text(t)):
                    continue
                if t not in [x[0] for x in terms]:
                    terms.append((t, "base", term_verb(lang, t, tpl)))
            # 公共简称: 非本条目主题名 + 是其他条目信号词的严格子串
            topic_word = (e["topic"][lang] or "").strip()
            kept = []
            for t, _, verb in terms:
                low = t.lower() if lang == "en" else t
                is_public = t != topic_word and any(
                    low != f and (low in f if lang == "zh" else low in f.lower())
                    for other, oper in raws.items() if other != e["id"]
                    for f in oper[lang])
                if is_public:
                    dropped.append({"entry_id": e["id"], "lang": lang, "term": t,
                                    "reason": "public_short_form_of_other_entry"})
                else:
                    kept.append((t, "base", verb))
            per[lang] = kept
        out[e["id"]] = per
    return out, dropped


def build_raw_terms(pkg: dict, tpl: dict) -> dict:
    """全量原始信号词 (topic+keyword, 不过滤称呼资格) — 冲突检查口径, 减泛指词。
    泛指词 (generic_scope_aliases, 与网关 _KB_GENERIC_SCOPE_ALIASES 对齐+位置泛词)
    不构成对任何主题的独占证据, 冲突检查豁免。"""
    out = {}
    for e in pkg["entries"]:
        per = {}
        for lang in ("zh", "en"):
            raw = [e["topic"][lang]] + list(e["keywords"][lang])
            raw = [w for w in raw if w and len(w) >= 2
                   and w not in tpl["generic_scope_aliases"][lang]]
            per[lang] = [w.lower() for w in raw] if lang == "en" else raw
        out[e["id"]] = per
    return out


def build_variant_terms(pkg: dict, tpl: dict, signals: dict, raws: dict) -> tuple:
    """新增同义称呼: 全局独占 (与任何其他条目全量信号词/已分配 variant 不互为子串,
    同语言比较, en 小写)。返回 (variants, rejected) — rejected 含原因, 进生成报告。"""
    variants, rejected = {}, []
    assigned = {}  # (lang, variant) -> entry_id
    for e in pkg["entries"]:
        variants[e["id"]] = {"zh": [], "en": []}
    for e in pkg["entries"]:
        eid = e["id"]
        for lang in ("zh", "en"):
            pool = []
            for s in [e["topic"][lang]] + list(e["keywords"][lang]):
                for v in tpl["variant_lexicon"].get(lang, {}).get(s, []):
                    if v not in pool:
                        pool.append(v)
            if lang == "en":
                pool = [v.lower() for v in pool]
            limit = 6
            for v in pool:
                why = None
                vlen_ok = (2 <= len(v) <= 8) if lang == "zh" else (3 <= len(v) <= 24)
                if not vlen_ok:
                    why = "length"
                elif lang == "zh" and not is_zh_text(v):
                    why = "not_zh"
                elif lang == "en" and (not LATIN_RE.search(v) or is_zh_text(v)):
                    why = "not_en"
                elif term_questionable(lang, v, tpl) or term_generic(lang, v, tpl):
                    why = "question_or_generic"
                elif v in [x[0] for x in signals[eid][lang]]:
                    why = "already_base"
                else:
                    for other, oper in raws.items():
                        if other == eid:
                            continue
                        for f in oper[lang]:
                            if v == f or v in f or f in v:
                                why = f"conflict_with_entry_{other}:{f}"
                                break
                        if why:
                            break
                    if not why:
                        for (vl, vv), owner in assigned.items():
                            if vl == lang and (v == vv or v in vv or vv in v):
                                why = f"conflict_with_variant_of_entry_{owner}:{vv}"
                                break
                if why:
                    rejected.append({"entry_id": eid, "lang": lang, "term": v, "reason": why})
                    continue
                if len(variants[eid][lang]) >= limit:
                    rejected.append({"entry_id": eid, "lang": lang, "term": v,
                                     "reason": "per_entry_limit"})
                    continue
                variants[eid][lang].append(v)
                assigned[(lang, v)] = eid
    return variants, rejected


def filter_no_fact_topics(topics: list, lang: str, pkg: dict, raws: dict) -> tuple:
    """NO_FACT 主题不得包含任何条目独占信号词 (全量-泛指, 子串即拒)。"""
    kept, rejected = [], []
    for topic in topics:
        t = topic.lower() if lang == "en" else topic
        bad = None
        for eid, per in raws.items():
            for f in per[lang]:
                if f in t:
                    bad = f"contains_signal_of_entry_{eid}:{f}"
                    break
            if bad:
                break
        if bad:
            rejected.append({"topic": topic, "lang": lang, "reason": bad})
        else:
            kept.append(topic)
    return kept, rejected


def _prefix_head_clash(prefix: str, body: str, lang: str) -> bool:
    """礼貌前缀与句式头部重复 (如 "could you tell me"+"tell me…" /
    "帮我查一下"+"帮我预约…") 时组合不自然, 跳过。"""
    if not prefix:
        return False
    if lang == "zh":
        return any(body[:i] in prefix for i in (3, 2) if i <= len(body))
    words = body.split()
    return len(words) >= 2 and " ".join(words[:2]) in prefix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--templates", default=str(Path(__file__).resolve().parent.parent
                                                / "templates" / "utterance_intent_templates.json"))
    ap.add_argument("--seed", type=int, default=20260904)
    ap.add_argument("--per-cell", type=int, default=32)
    ap.add_argument("--clarify-per-cell", type=int, default=8)
    ap.add_argument("--no-fact-per-cell", type=int, default=3)
    ap.add_argument("--min-unique", type=int, default=10000)
    args = ap.parse_args()

    pkg_path = Path(args.package)
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    if pkg.get("schema_version") != "joctv-hotel-kb-v1":
        print("FAIL 源包必须是 joctv-hotel-kb-v1", file=sys.stderr)
        return 2
    tpl = load_templates(Path(args.templates))
    rng = random.Random(args.seed)

    raws = build_raw_terms(pkg, tpl)
    signals, public_dropped = build_signal_terms(pkg, tpl, raws)
    variants, var_rejected = build_variant_terms(pkg, tpl, signals, raws)
    nf_topics = {}
    nf_rejected = []
    for lang in ("zh", "en"):
        kept, rej = filter_no_fact_topics(tpl["no_fact_topics"][lang], lang, pkg, raws)
        nf_topics[lang] = kept
        nf_rejected += rej

    # 最终决策链预审用的发布态 topics (增强别名口径: +variant −公共简称/泛指词),
    # 与 eval_route_holdout 的 enhanced router 同构 (kb_router_chain.build_topics)
    variant_terms_export = {str(k): v for k, v in variants.items()
                            if v["zh"] or v["en"]}
    enh_topics = build_topics(pkg, {"variant_terms": variant_terms_export},
                              enhanced=True, tpl=tpl)
    topics_by_locale = {loc: [t for t in enh_topics if t["locale"] == loc]
                        for loc in ("zh-CN", "en-US")}
    precheck_rejected = {"clarify": [], "no_fact": []}

    used_norms = set()
    stripped_norms = set()   # 去尾部语气词后的归一 — 同义语气变体只留一条
    del_one_index = {}       # (lang, del-one 键) → norm — 生成时近似(编辑距离≤1)去重
    utterances = []
    dup_dropped = 0
    near_dropped = 0

    TAIL_PARTICLES_ZH = ("呢", "啊", "呀", "吗", "多谢", "谢谢")

    def _near_hit(lang: str, n: str) -> bool:
        if (lang, n) in del_one_index:
            return True
        for i in range(len(n)):
            if (lang, n[:i] + n[i + 1:]) in del_one_index:
                return True
        return False

    def emit(lang, text, binding, entry_id, category, intent, term_kind):
        nonlocal dup_dropped, near_dropped
        n = norm(lang, text)
        if not n or n in used_norms:
            dup_dropped += 1
            return False
        if _near_hit(lang, n):
            near_dropped += 1
            return False
        stripped = n
        if lang == "zh":
            for q in TAIL_PARTICLES_ZH:
                if stripped.endswith(q) and len(stripped) > len(q) + 2:
                    stripped = stripped[: -len(q)]
                    break
        if stripped in stripped_norms:
            near_dropped += 1
            return False
        used_norms.add(n)
        stripped_norms.add(stripped)
        del_one_index[(lang, n)] = n
        for i in range(len(n)):
            del_one_index[(lang, n[:i] + n[i + 1:])] = n
        utterances.append({"lang": lang, "text": text, "binding": binding,
                           "entry_id": entry_id, "category": category,
                           "intent": intent, "field": INTENT_FIELD[intent],
                           "term_kind": term_kind})
        return True

    prefix = tpl["prefixes"]
    suffix = tpl["suffixes"]
    over = 1.18  # 过采样再去重

    for e in pkg["entries"]:
        eid = e["id"]
        for lang in ("zh", "en"):
            if not e["topic"][lang]:
                continue
            base = [(t.lower() if lang == "en" else t, "base")
                    for t, _, _ in signals[eid][lang] if not term_verb(lang, t, tpl)]
            base_v = [(t.lower() if lang == "en" else t, "base")
                      for t, _, _ in signals[eid][lang] if term_verb(lang, t, tpl)]
            var = [(v.lower() if lang == "en" else v, "variant")
                   for v in variants[eid][lang] if not term_verb(lang, v, tpl)]
            var_v = [(v.lower() if lang == "en" else v, "variant")
                     for v in variants[eid][lang] if term_verb(lang, v, tpl)]
            for intent, spec in tpl["intents"].items():
                # 事实感知映射: 该 (entry, lang) 有对应已发布事实 → entry 问法;
                # 无对应事实 → clarify 问法 (问法合法, 但运行时预期走缺字段安全
                # 兜底/澄清, 不得从其他条目借事实回答)
                fact_present = intent_fact_available(e, lang, intent)
                binding = "entry" if fact_present else "clarify"
                want = args.per_cell if fact_present else args.clarify_per_cell
                # 组合 = 前缀×句式×称呼; 后缀不进组合空间 — 每个组合随机抽一个后缀,
                # 避免"仅换语气词(呀/啊/呢)的近似句"凑数 (failure_policy 红线)
                combos = []
                n_tpl = spec[lang].get("N", [])
                for p in prefix[lang]:
                    for t in n_tpl:
                        for term, kind in base + var:
                            body = t.replace("{t}", term)
                            if _prefix_head_clash(p, body, lang):
                                continue
                            combos.append((p, body, kind))
                v_tpl = spec[lang].get("V", [])
                for p in prefix[lang]:
                    for t in v_tpl:
                        for term, kind in base_v + var_v:
                            body = t.replace("{v}", term)
                            if _prefix_head_clash(p, body, lang):
                                continue
                            combos.append((p, body, kind))
                if not combos:
                    continue
                picked = []
                idx = list(range(len(combos)))
                rng.shuffle(idx)
                for i in idx:
                    if len(picked) >= int(want * over):
                        break
                    picked.append(combos[i])
                got = 0
                for p, body, kind in picked:
                    if got >= want:
                        break
                    s = rng.choice(suffix[lang])
                    if lang == "zh":
                        text = (p + body + s).strip()
                    else:
                        head = p + body
                        if s:
                            head = head + " " + s
                        text = head.strip()
                    if binding == "clarify":
                        # 决策链预审: 该问法在网关最终路由下若被其他条目独占直答
                        # (跨条目事实泄漏) 则不收; 绑定条目自身直答是诚实回答
                        d = final_decision(text, LOCALE[lang],
                                           topics_by_locale[LOCALE[lang]])
                        if d["decision"] in FACT_LEAK_DECISIONS \
                                and d.get("topic_id") != str(eid):
                            precheck_rejected["clarify"].append(
                                {"entry_id": eid, "lang": lang, "text": text,
                                 "intent": intent, "decision": d})
                            continue
                    if emit(lang, text, binding, eid, e["category"], intent, kind):
                        got += 1

    for lang in ("zh", "en"):
        for topic in nf_topics[lang]:
            if lang == "en":
                topic = topic.lower()
            for intent in tpl["no_fact_intents"]:
                spec = tpl["intents"][intent]
                n_tpl = spec[lang].get("N", [])
                combos = []
                for p in prefix[lang]:
                    for t in n_tpl:
                        if "{v}" in t:
                            continue
                        combos.append((p, t.replace("{t}", topic)))
                if not combos:
                    continue
                idx = list(range(len(combos)))
                rng.shuffle(idx)
                picked = [combos[i] for i in idx[:int(args.no_fact_per_cell * over)]]
                got = 0
                for p, body in picked:
                    if got >= args.no_fact_per_cell:
                        break
                    s = rng.choice(suffix[lang])
                    if lang == "zh":
                        text = (p + body + s).strip()
                    else:
                        head = p + body
                        text = (head + " " + s if s else head).strip()
                    # 决策链预审: NO_FACT 问法必须落入安全兜底 (非知识/规划器/
                    # 缺字段话术); 独占直答或错候选澄清 (公共简称/模板词 stem
                    # 吸收) 不收, 进生成报告
                    d = final_decision(text, LOCALE[lang],
                                       topics_by_locale[LOCALE[lang]])
                    if d["decision"] not in SAFE_FALLBACK_DECISIONS:
                        precheck_rejected["no_fact"].append(
                            {"topic": topic, "lang": lang, "text": text,
                             "intent": intent, "decision": d})
                        continue
                    if emit(lang, text, "no_fact", None, None, intent, "base"):
                        got += 1

    # 事实边界静态断言: 问法不得携带事实值 (模板/前后缀本应保证, 双保险)
    fact_patterns = [re.compile(p) for p in (
        r'\d{1,2}:\d{2}', r'\d{5,}', r'\d+\s*(元|块|楼|层)', r'(\$\s*\d+|\d+\s*(yuan|rmb|cny|dollars?))')]
    for u in utterances:
        for pat in fact_patterns:
            if pat.search(u["text"]):
                print(f"FAIL 生成问法携带事实值: {u['text']!r}", file=sys.stderr)
                return 2

    utterances.sort(key=lambda u: (u["lang"], u["binding"], u["entry_id"] or 0,
                                   u["intent"], u["text"]))
    for i, u in enumerate(utterances, 1):
        u["id"] = i
        u["field"] = INTENT_FIELD[u["intent"]]

    counts = {
        "total": len(utterances),
        "zh": sum(1 for u in utterances if u["lang"] == "zh"),
        "en": sum(1 for u in utterances if u["lang"] == "en"),
        "entry_bound": sum(1 for u in utterances if u["binding"] == "entry"),
        "clarify": sum(1 for u in utterances if u["binding"] == "clarify"),
        "no_fact": sum(1 for u in utterances if u["binding"] == "no_fact"),
    }
    pkg_sha = hashlib.sha256(pkg_path.read_bytes()).hexdigest()
    corpus = {
        "schema_version": "joctv-hotel-utterance-v1",
        "skill_version": "1.2.0",
        "hotel_id": pkg["hotel_id"],
        "source_package_name": pkg["package_name"],
        "source_package_sha256": pkg_sha,
        "seed": args.seed,
        "variant_terms": {str(k): v for k, v in variants.items() if v["zh"] or v["en"]},
        "counts": counts,
        "utterances": [{k: u[k] for k in ("id", "lang", "text", "binding", "entry_id",
                                          "category", "intent", "field", "term_kind")}
                       for u in utterances],
    }
    if counts["total"] < args.min_unique:
        print(f"FAIL 唯一问法 {counts['total']} < 最低要求 {args.min_unique}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = out_dir / "utterances_v1.json"
    corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")

    by_intent = {}
    for u in utterances:
        by_intent[u["intent"]] = by_intent.get(u["intent"], 0) + 1
    per_entry = []
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            n_base = sum(1 for u in utterances if u["entry_id"] == e["id"]
                         and u["lang"] == lang and u["term_kind"] == "base")
            n_var = sum(1 for u in utterances if u["entry_id"] == e["id"]
                        and u["lang"] == lang and u["term_kind"] == "variant")
            avail = [i for i in tpl["intents"] if intent_fact_available(e, lang, i)]
            absent = [i for i in tpl["intents"] if i not in avail]
            per_entry.append({"entry_id": e["id"], "lang": lang,
                              "base": n_base, "variant": n_var,
                              "fact_available_intents": avail,
                              "fact_absent_intents": absent,
                              "variants": variants[e["id"]][lang]})
    report = {
        "seed": args.seed,
        "package": str(pkg_path),
        "source_package_sha256": pkg_sha,
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "counts": counts,
        "by_intent": by_intent,
        "dup_dropped": dup_dropped,
        "near_dropped": near_dropped,
        "public_short_form_base_terms_dropped": public_dropped,
        "variant_terms_rejected": var_rejected,
        "no_fact_topics_rejected": nf_rejected,
        "no_fact_topics_used": nf_topics,
        "final_decision_precheck": {
            "chain": "kb_router_chain.final_decision (网关 kb_route 确定性层副本)",
            "clarify_rejected": {
                "count": len(precheck_rejected["clarify"]),
                "samples": precheck_rejected["clarify"][:20]},
            "no_fact_rejected": {
                "count": len(precheck_rejected["no_fact"]),
                "by_decision": {k: sum(1 for r in precheck_rejected["no_fact"]
                                       if r["decision"]["decision"] == k)
                                for k in sorted({r["decision"]["decision"] for r
                                                 in precheck_rejected["no_fact"]})},
                "samples": precheck_rejected["no_fact"][:20]},
        },
        "per_entry": per_entry,
    }
    (out_dir / "generation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"PASS 生成 {counts['total']} 条唯一问法 (zh={counts['zh']} en={counts['en']} "
          f"entry_bound={counts['entry_bound']} clarify={counts['clarify']} "
          f"no_fact={counts['no_fact']}); 去重丢弃 {dup_dropped}; "
          f"variant 拒绝 {len(var_rejected)} 项; 公共简称 base 剔除 {len(public_dropped)} 项; "
          f"决策链预审拒绝 clarify {len(precheck_rejected['clarify'])} / "
          f"no_fact {len(precheck_rejected['no_fact'])} 条")
    print(f"corpus: {corpus_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
