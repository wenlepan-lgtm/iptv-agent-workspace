#!/usr/bin/env python3
"""JOCTV 酒店问法覆盖生成器 (Skill 1.3.0)。

从已发布知识包 (joctv-hotel-kb-v1) + 通用酒店意图模板生成双语问法语料:
  - 每条问法绑定 language/intent/field/entry (可追溯) 或明确 NO_FACT;
  - intent 级事实感知映射 (1.3.0, KB30-04-01): 共享 kb_router_chain.intent_fact_available
    — price 恒为缺字段 (kb-v1 无结构化价格字段); policy/booking 需 notes 已发布且
    含对应事实标记 (规定/着装/年龄… vs 预约/预订/退订…), 不再"notes 非空同时授权
    三种事实"; 只有 intent 事实已发布的组合生成 binding=entry;
  - 最终路由硬断言 (1.3.0, KB30-04-02): 每条**发出**的问法用网关 kb_route 确定性
    单轮链副本 (kb_router_chain.route_single_turn) 对增强别名发布态 topics 断言
    最终 decision + 回答字段与声明一致 (entry 精确独占直答; clarify 声明字段缺失时
    只允许 KB_MISSING_FIELD/有效澄清/安全非独占路径; no_fact 只允许安全兜底)。
    **违规即生成失败 (退出码 2), 不做任何安全过滤/跳过** — 语料不存在"先筛后验"
    的自证循环; 完整挑战空间的枚举验证在 eval_route_holdout (全模板空间, 无抽样);
  - 称呼来自源信号词 (base) 与新增同义称呼 (variant, 增强别名的唯一来源);
    公共简称/泛指范围词/比较级词称呼确定性剔除 (进生成报告);
  - 全程确定性 (固定 seed, 无时间戳入产物), 同输入同 seed → 同字节输出;
  - 不生成任何事实值 (时间/电话/价格数字不进问法)。

用法:
  python3 generate_utterance_coverage.py --package <kb.json> --out <dir> \
      [--templates ../templates/utterance_intent_templates.json] \
      [--seed 20260904] [--per-cell 32] [--clarify-per-cell 8] \
      [--no-fact-per-cell 3] [--min-unique 10000]
"""
import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_router_chain import (INTENT_RUNTIME_FIELD, _KB_COMPARISON_RE,  # noqa: E402
                             build_topics, intent_fact_available,
                             route_expectation, route_single_turn,
                             runtime_field_published)

CJK_RE = re.compile(r'[一-鿿]')
LATIN_RE = re.compile(r'[a-zA-Z]')
LOCALE = {"zh": "zh-CN", "en": "en-US"}


def corpus_field(intent: str):
    """语料 field 元数据: intent 的运行时回答字段; availability/overview → "overview"
    (概述回答变体, 与 schema 枚举一致; 路由断言内部用 INTENT_RUNTIME_FIELD 的 None)。"""
    return INTENT_RUNTIME_FIELD[intent] or "overview"


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


def build_signal_terms(pkg: dict, tpl: dict, raws: dict) -> tuple:
    """每 (entry_id, lang) 的称呼合格信号词: {id: {lang: [(term, kind='base', verb: bool)]}}。
    确定性剔除 (全部进生成报告):
      - 疑问/泛词称呼 (问法碎片不作主题短称);
      - 泛指范围词 (generic_scope_aliases — 增强别名包会移除该类 keyword, 语料不得
        引用发布态不存在 的称呼);
      - 公共简称 (非主题名且是其他条目信号词严格子串 — 运行时确定性澄清);
      - 比较级词称呼 (最大/最近/更… — 比较级问句网关一律交规划器, 无法作 entry
        精确直答断言)。
    返回 (signals, dropped)。"""
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
            topic_word = (e["topic"][lang] or "").strip()
            kept = []
            for t, _, verb in terms:
                low = t.lower() if lang == "en" else t
                why = None
                if t in tpl["generic_scope_aliases"][lang]:
                    why = "generic_scope_alias"
                elif _KB_COMPARISON_RE.search(t):
                    why = "comparison_word"
                elif t != topic_word and any(
                        low != f and (low in f if lang == "zh" else low in f.lower())
                        for other, oper in raws.items() if other != e["id"]
                        for f in oper[lang]):
                    why = "public_short_form_of_other_entry"
                if why:
                    dropped.append({"entry_id": e["id"], "lang": lang, "term": t,
                                    "reason": why})
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
    同语言比较, en 小写); 比较级词/泛词/疑问词称呼拒绝。返回 (variants, rejected)。"""
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
                elif v in tpl["generic_scope_aliases"][lang]:
                    why = "generic_scope_alias"
                elif _KB_COMPARISON_RE.search(v):
                    why = "comparison_word"
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


def combo_hijacks_other_entry(lang: str, body: str, eid, raws: dict):
    """组合级标签一致性 (V30-04 R1): 渲染后的句身含**其他条目**比本条目更长的命中词
    时, 该句按构造以其他条目为最具体提及 (网关最长别名匹配语义 — 如「川水疗」+
    「预约要注意什么」拼出「水疗预约」, 更具体的水疗预约条目理应获胜), 声明绑定
    本条目属标签错误, 组合确定性排除。纯资源文本规则 (称呼×模板×他条目别名),
    与路由实现/路由结果无关; 排除项全量进生成/评测报告。返回原因或 None。"""
    own = raws[eid][lang]
    low = body.lower() if lang == "en" else body
    own_best = max((len(o) for o in own if o in low), default=0)
    for other, per in raws.items():
        if other == eid:
            continue
        for f in per[lang]:
            if len(f) > own_best and f in low:
                return f"hijacked_by_entry_{other}:{f}"
    return None


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
    ap.add_argument("--per-cell", type=int, default=40)
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

    # 最终路由断言用的发布态 topics (增强别名口径: +variant −公共简称/泛指词),
    # 与 eval_route_holdout 的 enhanced router 同构 (kb_router_chain.build_topics)
    variant_terms_export = {str(k): v for k, v in variants.items()
                            if v["zh"] or v["en"]}
    enh_topics = build_topics(pkg, {"variant_terms": variant_terms_export},
                              enhanced=True, tpl=tpl)
    topics_by_locale = {loc: [t for t in enh_topics if t["locale"] == loc]
                        for loc in ("zh-CN", "en-US")}
    # 硬断言账本: 违规收集后统一 FAIL (不做安全过滤/跳过 — 无自证循环)
    route_violations = []
    route_asserted = {"entry": 0, "clarify": 0, "no_fact": 0}

    used_norms = set()
    stripped_norms = set()   # 去尾部语气词后的归一 — 同义语气变体只留一条
    del_one_index = {}       # (lang, del-one 键) → norm — 生成时近似(编辑距离≤1)去重
    utterances = []
    dup_dropped = 0
    near_dropped = 0
    hijack_excluded = []     # 组合级标签一致性排除 (combo_hijacks_other_entry)

    TAIL_PARTICLES_ZH = ("呢", "啊", "呀", "吗", "多谢", "谢谢")

    def _near_hit(lang: str, n: str) -> bool:
        if (lang, n) in del_one_index:
            return True
        for i in range(len(n)):
            if (lang, n[:i] + n[i + 1:]) in del_one_index:
                return True
        return False

    def emit(lang, text, binding, entry_id, category, intent, term_kind,
             runtime_field_present=False):
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
        # 最终路由硬断言 (KB30-04-01/02): 发出的每条问法在网关确定性单轮链下,
        # 最终 decision + 回答字段必须与声明一致; 违规收集, 生成结束统一 FAIL
        d = route_single_turn(text, LOCALE[lang], topics_by_locale[LOCALE[lang]])
        ok = route_expectation(binding, intent, entry_id,
                               runtime_field_present=runtime_field_present)(d)
        if not ok:
            route_violations.append({"lang": lang, "text": text, "binding": binding,
                                     "entry_id": entry_id, "intent": intent,
                                     "runtime_decision": d})
            return False
        route_asserted[binding] += 1
        used_norms.add(n)
        stripped_norms.add(stripped)
        del_one_index[(lang, n)] = n
        for i in range(len(n)):
            del_one_index[(lang, n[:i] + n[i + 1:])] = n
        utterances.append({"lang": lang, "text": text, "binding": binding,
                           "entry_id": entry_id, "category": category,
                           "intent": intent, "field": corpus_field(intent),
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
                # intent 级事实感知映射: intent 对应事实已发布 → entry 问法;
                # 未发布 (字段缺 / policy、booking 标记缺) → clarify 问法
                fact_present = intent_fact_available(e, lang, intent)
                binding = "entry" if fact_present else "clarify"
                fld_present = runtime_field_published(e, lang, intent)
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
                            hij = combo_hijacks_other_entry(lang, body, eid, raws)
                            if hij:
                                hijack_excluded.append(
                                    {"entry_id": eid, "lang": lang, "intent": intent,
                                     "term": term, "body": body, "reason": hij})
                                continue
                            combos.append((p, body, kind))
                v_tpl = spec[lang].get("V", [])
                for p in prefix[lang]:
                    for t in v_tpl:
                        for term, kind in base_v + var_v:
                            body = t.replace("{v}", term)
                            if _prefix_head_clash(p, body, lang):
                                continue
                            hij = combo_hijacks_other_entry(lang, body, eid, raws)
                            if hij:
                                hijack_excluded.append(
                                    {"entry_id": eid, "lang": lang, "intent": intent,
                                     "term": term, "body": body, "reason": hij})
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
                    if emit(lang, text, binding, eid, e["category"], intent, kind,
                            runtime_field_present=fld_present):
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
                    if emit(lang, text, "no_fact", None, None, intent, "base"):
                        got += 1

    if route_violations:
        # 硬失败: 声明与网关最终路由不一致 (不允许过滤后宣称安全)
        print(f"FAIL 最终路由断言违规 {len(route_violations)} 条 "
              f"(声明 intent/字段 与 route_single_turn 决策不一致):", file=sys.stderr)
        for v in route_violations[:30]:
            print(f"  [{v['binding']}/{v['intent']}] {v['text']!r} -> "
                  f"{v['runtime_decision']}", file=sys.stderr)
        return 2

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
        u["field"] = corpus_field(u["intent"])

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
        "skill_version": "1.3.0",
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
            fld_pub = {i: runtime_field_published(e, lang, i) for i in tpl["intents"]}
            per_entry.append({"entry_id": e["id"], "lang": lang,
                              "base": n_base, "variant": n_var,
                              "fact_available_intents": avail,
                              "runtime_field_published": fld_pub,
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
        "signal_terms_dropped": public_dropped,
        "variant_terms_rejected": var_rejected,
        "no_fact_topics_rejected": nf_rejected,
        "no_fact_topics_used": nf_topics,
        "combo_hijack_excluded": hijack_excluded,
        "final_route_assert": {
            "chain": "kb_router_chain.route_single_turn (网关 kb_route 确定性单轮链副本)",
            "policy": "硬断言无过滤: 每条发出问法的最终 decision+回答字段必须与声明一致; "
                      "违规即生成失败 (exit 2)。完整挑战空间枚举验证见 eval_route_holdout。",
            "asserted": route_asserted,
            "violations": len(route_violations),
        },
        "per_entry": per_entry,
    }
    (out_dir / "generation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"PASS 生成 {counts['total']} 条唯一问法 (zh={counts['zh']} en={counts['en']} "
          f"entry_bound={counts['entry_bound']} clarify={counts['clarify']} "
          f"no_fact={counts['no_fact']}); 去重丢弃 {dup_dropped}+{near_dropped}; "
          f"variant 拒绝 {len(var_rejected)} 项; 信号词剔除 {len(public_dropped)} 项; "
          f"组合劫持排除 {len(hijack_excluded)} 项; "
          f"最终路由硬断言 {route_asserted} 违规 0")
    print(f"corpus: {corpus_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
