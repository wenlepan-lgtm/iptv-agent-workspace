#!/usr/bin/env python3
"""JOCTV 问法覆盖语料 (joctv-hotel-utterance-v1) 确定性校验器 (Skill 1.3.0)。

用法:
    python3 validate_utterance_coverage.py --package <kb.json> --corpus <utterances_v1.json> \
        [--templates ../templates/utterance_intent_templates.json] [--min-unique 10000]

校验内容 (全部确定性, 仅标准库):
  结构/枚举/id 唯一; 源包 SHA 绑定; 语言隔离 (zh 含汉字且非英文句子, en 含字母且无汉字);
  归一化全局唯一 (去重); 近似冲突 (同语言编辑距离 ≤1); entry/clarify 绑定可追溯 (text 必含
  本条目信号词 topic/keyword/variant); 串扰 (其他条目信号词未被本条目信号词覆盖即 FAIL);
  intent 级事实感知映射 (1.3.0, 共享 kb_router_chain.intent_fact_available: entry → intent
  对应事实已发布 — price 恒缺字段, policy/booking 需 notes 含对应事实标记; clarify →
  intent 事实未发布); intent 语义匹配 (text 必须可由所声明 intent 的模板 + 合格称呼 +
  前后缀结构性派生); **最终路由一致性 (1.3.0, E_ROUTE_CONSISTENCY: 每条问法用
  kb_router_chain.route_single_turn 对增强别名发布态 topics 断言最终 decision + 回答
  字段与声明一致 — entry 精确独占直答; clarify 声明字段缺失时只允许缺字段话术/有效
  澄清/安全非独占路径; no_fact 只允许安全兜底)**; NO_FACT 纯净 (entry_id/category
  为空, text 不含任何条目信号词子串); 事实边界 (问法不携带 HH:MM/长数字/货币/楼层
  事实值); variant_terms 元数据独占性复核; 数量门槛。
任何 FAIL 输出退出码 1; 全部通过输出 OK 摘要并退出码 0。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_router_chain import (INTENT_RUNTIME_FIELD, build_topics,  # noqa: E402
                             intent_fact_available, route_expectation,
                             route_single_turn, runtime_field_published)

SCHEMA_VERSION = "joctv-hotel-utterance-v1"
ROOT_KEYS = ["schema_version", "skill_version", "hotel_id", "source_package_name",
             "source_package_sha256", "seed", "variant_terms", "counts", "utterances"]
UTT_KEYS = ["id", "lang", "text", "binding", "entry_id", "category", "intent",
            "field", "term_kind"]
COUNT_KEYS = ["total", "zh", "en", "entry_bound", "clarify", "no_fact"]
DEFAULT_TEMPLATES = Path(__file__).resolve().parent.parent / "templates" \
    / "utterance_intent_templates.json"
INTENT_FIELD = {i: (f or "overview") for i, f in INTENT_RUNTIME_FIELD.items()}
# intent → 语料 field 元数据 (availability/overview → "overview"; price 独立字段)
LOCALE = {"zh": "zh-CN", "en": "en-US"}
CJK_RE = re.compile(r'[一-鿿]')
EN_WORD_RE = re.compile(r'[A-Za-z]+')
FACT_PATTERNS = [
    (re.compile(r'\d{1,2}:\d{2}'), "时间值 HH:MM"),
    (re.compile(r'\d{5,}'), "长数字串(电话/账号)"),
    (re.compile(r'\d+\s*(元|块|楼|层)'), "数字+单位(价格/楼层)"),
    (re.compile(r'(\$\s*\d+|\d+\s*(yuan|rmb|cny|dollars?))', re.I), "货币值"),
]


def load_templates(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def generic_scope(tpl: dict) -> dict:
    """泛指范围词 (串扰检查豁免): 模板资源 generic_scope_aliases, 与网关
    _KB_GENERIC_SCOPE_ALIASES 对齐 + 位置类意图泛词 — 不构成对任何主题的独占证据。"""
    return {lang: set(words) for lang, words in tpl["generic_scope_aliases"].items()}


def template_shapes(tpl: dict) -> dict:
    """(intent, lang) → [(slot, 前段, 后段)]: 模板按 {t}/{v} 占位符切分的固定形状。"""
    shapes = {}
    for intent, spec in tpl["intents"].items():
        for lang in ("zh", "en"):
            rows = []
            for slot in ("N", "V"):
                for t in spec[lang].get(slot, []):
                    ph = "{v}" if slot == "V" else "{t}"
                    if ph in t:
                        pre, _, suf = t.partition(ph)
                        rows.append((slot, pre, suf))
            shapes[(intent, lang)] = rows
    return shapes


def derivable(text: str, lang: str, intent: str, shapes: dict, tpl: dict,
              term_sets=None, term_kind=None) -> bool:
    """text 是否可由该 intent 的模板结构性派生: 前缀 + (前段+称呼+后段) + 后缀。
    term_sets 给定时 (entry/clarify): {(slot, term_kind): [称呼]}, 中段称呼必须
    命中所声明 term_kind 的对应槽位称呼集合 (N=名词称呼, V=动宾称呼);
    未给定时 (no_fact) 中段只需满足语言形态 (≥2 且语言正确)。"""
    for p in tpl["prefixes"][lang]:
        if p and not text.startswith(p):
            continue
        rest = text[len(p):]
        for s in tpl["suffixes"][lang]:
            if s and not rest.endswith(s):
                continue
            body = rest[:len(rest) - len(s)] if s else rest
            body = body.strip()
            for slot, pre, suf in shapes.get((intent, lang), []):
                if not (body.startswith(pre) and body.endswith(suf)):
                    continue
                mid = body[len(pre):len(body) - len(suf)] if suf else \
                    body[len(pre):]
                if len(mid) < 2:
                    continue
                if lang == "zh" and not CJK_RE.search(mid):
                    continue
                if lang == "en" and not EN_WORD_RE.search(mid):
                    continue
                if term_sets is None:
                    return True
                want = term_sets.get((slot, term_kind)) or []
                if lang == "en":
                    if mid.lower() in [t.lower() for t in want]:
                        return True
                elif mid in want:
                    return True
    return False


class Issue:
    def __init__(self, code: str, path: str, message: str):
        self.code, self.path, self.message = code, path, message

    def __str__(self):
        return f"{self.code} {self.path} {self.message}"


def norm(lang: str, text: str) -> str:
    t = re.sub(r'[^一-鿿a-zA-Z0-9]', '', text or '')
    return t.lower() if lang == "en" else t


def ed_le_1(a: str, b: str) -> bool:
    """真实编辑距离 ≤1 (候选对验证, 避免转置假阳性)。"""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = sum(1 for x, y in zip(a, b) if x != y)
        return diff == 1
    if la < lb:
        a, b = b, a
        la, lb = lb, la
    # a 比 b 长 1: 尝试删除 a 的一个位置后等于 b
    for i in range(la):
        if a[:i] + a[i + 1:] == b:
            return True
    return False


def signal_map(pkg: dict, corpus: dict) -> tuple:
    """(entry_id, lang) → 信号词列表 (topic+keyword+variant)。variant_terms 来自语料声明。"""
    vt = corpus.get("variant_terms") or {}
    m = {}
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            words = [e["topic"][lang]] + list(e["keywords"][lang])
            words += [str(v) for v in (vt.get(str(e["id"])) or {}).get(lang, [])]
            m[(e["id"], lang)] = [w for w in words if w and len(w) >= 2]
    return m


def validate(pkg_path: Path, corpus: dict, min_unique: int,
             templates_path: Path = None) -> list:
    issues = []

    def err(code, path, msg):
        issues.append(Issue(code, path, msg))

    if corpus.get("schema_version") != SCHEMA_VERSION:
        err("E_SCHEMA_VERSION", "$.schema_version", f"必须是 {SCHEMA_VERSION}")
        return issues
    if set(k for k in corpus.keys()) != set(ROOT_KEYS):
        err("E_ROOT_KEYS", "$", f"根字段必须恰好为 {ROOT_KEYS}")
        return issues

    tpath = Path(templates_path) if templates_path else DEFAULT_TEMPLATES
    tpl = load_templates(tpath)
    shapes = template_shapes(tpl)
    gscope = generic_scope(tpl)

    def _verb(lang: str, term: str) -> bool:
        if lang == "zh":
            return bool(term) and term[0] in tpl["verb_start_chars_zh"]
        words = re.findall(r"[a-z']+", term.lower())
        return bool(words) and words[0] in tpl["verb_start_words_en"]

    sha = hashlib.sha256(pkg_path.read_bytes()).hexdigest()
    if corpus["source_package_sha256"] != sha:
        err("E_SOURCE_SHA", "$.source_package_sha256",
            f"与源包实际 SHA-256 不符 (语料 {corpus['source_package_sha256'][:12]}… "
            f"实际 {sha[:12]}…), 语料与知识源脱钩")
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in pkg["entries"]}
    utts = corpus["utterances"]
    sig = signal_map(pkg, corpus)

    # 最终路由断言用的增强别名发布态 topics (与生成器/评测器同构)
    enh_topics = build_topics(pkg, corpus, enhanced=True, tpl=tpl)
    topics_by_locale = {loc: [t for t in enh_topics if t["locale"] == loc]
                        for loc in ("zh-CN", "en-US")}

    # ── variant_terms 元数据独占性复核 (同语言, 泛词豁免, en 小写口径) ──
    seen_variant = {}
    for eid_s, per in (corpus.get("variant_terms") or {}).items():
        try:
            eid = int(eid_s)
        except ValueError:
            err("E_VARIANT_KEY", f"$.variant_terms.{eid_s}", "键必须是条目 id 字符串")
            continue
        if eid not in entries:
            err("E_VARIANT_ENTRY", f"$.variant_terms.{eid_s}", "条目不存在于源包")
            continue
        for lang in ("zh", "en"):
            low = (lambda s: s) if lang == "zh" else str.lower
            for v in per.get(lang, []):
                if low(v) in [low(w) for w in entries[eid]["keywords"][lang]] or \
                        low(v) == low(entries[eid]["topic"][lang]):
                    err("E_VARIANT_DUP", f"$.variant_terms.{eid_s}.{lang}",
                        f"{v!r} 与源信号词重复")
                for (oid, olang), words in sig.items():
                    if olang != lang or oid == eid:
                        continue
                    for w in words:
                        if w in gscope[lang]:
                            continue
                        if low(v) == low(w) or low(v) in low(w) or low(w) in low(v):
                            err("E_VARIANT_CONFLICT", f"$.variant_terms.{eid_s}.{lang}",
                                f"{v!r} 与条目 {oid} 信号词 {w!r} 冲突")
                if (lang, low(v)) in seen_variant:
                    err("E_VARIANT_MULTI", f"$.variant_terms.{eid_s}.{lang}",
                        f"{v!r} 同时分配给条目 {seen_variant[(lang, low(v))]}")
                seen_variant[(lang, low(v))] = eid

    # ── 逐条校验 ──
    seen_ids = set()
    seen_norm = {}
    del_keys = {}
    for u in utts:
        p = f"$.utterances[{u.get('id')}]"
        if set(u.keys()) != set(UTT_KEYS):
            err("E_UTT_KEYS", p, f"字段必须恰好为 {UTT_KEYS}")
            continue
        if not isinstance(u["id"], int) or u["id"] in seen_ids:
            err("E_ID", p, "id 必须为不重复整数")
        seen_ids.add(u["id"])
        lang, text = u["lang"], u["text"]
        if lang not in ("zh", "en"):
            err("E_LANG", p, f"非法语言 {lang!r}")
            continue
        if u["intent"] not in INTENT_FIELD:
            err("E_INTENT", p, f"非法意图 {u['intent']!r}")
            continue
        if u["field"] != INTENT_FIELD[u["intent"]]:
            err("E_FIELD", p, f"field {u['field']!r} 与 intent {u['intent']!r} 映射不符")
        if len(text) < 3 or len(text) > 96:
            err("E_TEXT_LEN", p, f"长度越界 ({len(text)})")
            continue
        if lang == "zh":
            if not CJK_RE.search(text):
                err("E_LANG_MIX", p, "zh 问法必须含汉字")
            run4 = re.findall(r'(?:[A-Za-z]+\s+){3}[A-Za-z]+', text)
            if run4:
                err("E_LANG_MIX", p, f"zh 问法含连续英文句段 {run4[0]!r}")
        else:
            if CJK_RE.search(text):
                err("E_LANG_MIX", p, "en 问法不得含汉字")
            if not EN_WORD_RE.search(text):
                err("E_LANG_MIX", p, "en 问法必须含英文字母")
        for pat, label in FACT_PATTERNS:
            if pat.search(text):
                err("E_FACT_LEAK", p, f"问法携带事实值 ({label})")

        # ── intent 语义匹配: text 必须可由所声明 intent 的模板结构性派生 ──
        eid_ref = u["entry_id"] if u["binding"] in ("entry", "clarify") else None
        ref_ok = isinstance(eid_ref, int) and eid_ref in entries
        term_sets = None
        if ref_ok:
            base_terms = [entries[eid_ref]["topic"][lang]] \
                + list(entries[eid_ref]["keywords"][lang])
            var_terms = [str(x) for x in (corpus["variant_terms"]
                                          .get(str(eid_ref)) or {}).get(lang, [])]
            term_sets = {
                ("N", "base"): [t for t in base_terms if t and not _verb(lang, t)],
                ("V", "base"): [t for t in base_terms if t and _verb(lang, t)],
                ("N", "variant"): [t for t in var_terms if t and not _verb(lang, t)],
                ("V", "variant"): [t for t in var_terms if t and _verb(lang, t)],
            }
        if u["intent"] in tpl["intents"] and not derivable(
                text, lang, u["intent"], shapes, tpl,
                term_sets=term_sets, term_kind=u["term_kind"]):
            err("E_INTENT_TEMPLATE", p,
                f"text 无法由 intent={u['intent']!r} 的模板+称呼+前后缀派生 "
                f"(intent 与问法语义不符或称呼不在本条目集合)")

        # ── intent 级事实感知映射: entry → intent 事实必须存在; clarify → 必须未发布 ──
        if ref_ok and u["binding"] == "entry":
            if not intent_fact_available(entries[eid_ref], lang, u["intent"]):
                err("E_INTENT_FACT", p,
                    f"binding=entry 但条目 {eid_ref} {lang} 未发布 "
                    f"{u['intent']} 对应事实 (应属 clarify/NO_FACT 路径)")
        elif ref_ok and u["binding"] == "clarify":
            if intent_fact_available(entries[eid_ref], lang, u["intent"]):
                err("E_CLARIFY_FACT", p,
                    f"binding=clarify 但条目 {eid_ref} {lang} 已发布 "
                    f"{u['intent']} 对应事实 (clarify 只承载缺事实问法)")

        # ── 最终路由一致性 (1.3.0): 网关确定性单轮链下 decision+回答字段与声明一致 ──
        d = route_single_turn(text, LOCALE[lang], topics_by_locale[LOCALE[lang]])
        fld_present = (runtime_field_published(entries[eid_ref], lang, u["intent"])
                       if ref_ok else False)
        if not route_expectation(u["binding"], u["intent"], eid_ref,
                                 runtime_field_present=fld_present)(d):
            err("E_ROUTE_CONSISTENCY", p,
                f"最终路由与声明不符: binding={u['binding']!r} "
                f"intent={u['intent']!r} field={u['field']!r} → {d}")

        n = norm(lang, text)
        if n in seen_norm:
            err("E_DUP", p, f"归一化重复: 与 id={seen_norm[n]} 相同 ({n[:24]}…)")
        else:
            seen_norm[n] = u["id"]
        # 近似冲突分桶: del-one 键(同长替换) + 全文键(跨长删除: 长串删一字符=短串)
        del_keys.setdefault((lang, n), []).append(u["id"])
        for i in range(len(n)):
            del_keys.setdefault((lang, n[:i] + n[i + 1:]), []).append(u["id"])

        binding = u["binding"]
        if binding in ("entry", "clarify"):
            eid = u["entry_id"]
            if not isinstance(eid, int) or eid not in entries:
                err("E_ENTRY_REF", p, f"绑定条目不存在: {eid!r}")
                continue
            if u["category"] != entries[eid]["category"]:
                err("E_CATEGORY", p, "category 与源条目不一致")
            own = sig[(eid, lang)]
            if not any(w in text or (lang == "en" and w.lower() in text.lower())
                       for w in own):
                err("E_NO_SIGNAL", p, "不包含本条目任何信号词 (topic/keyword/variant)")
            if u["term_kind"] == "variant":
                vts = [str(v) for v in (corpus["variant_terms"]
                                        .get(str(eid)) or {}).get(lang, [])]
                if not any(v in text or (lang == "en" and v.lower() in text.lower())
                           for v in vts):
                    err("E_TERM_KIND", p, "term_kind=variant 但不含任何 variant 称呼")
            for (oid, olang), words in sig.items():
                if olang != lang or oid == eid:
                    continue
                for w in words:
                    if w in gscope[lang]:
                        continue  # 泛指词不构成独占证据 (对齐网关口径)
                    hit = w in text or (lang == "en" and w.lower() in text.lower())
                    if not hit:
                        continue
                    if any(w in own_w or own_w in w
                           or (lang == "en" and (w.lower() in own_w.lower()
                                                 or own_w.lower() in w.lower()))
                           for own_w in own):
                        continue  # 被本条目更长信号词覆盖 (长词优先, 网关同权重口径)
                    err("E_FOREIGN_SIGNAL", p,
                        f"含其他条目 {oid} 信号词 {w!r} 且未被本条目信号词覆盖")
                    break
        elif binding == "no_fact":
            if u["entry_id"] is not None or u["category"] is not None:
                err("E_NO_FACT_SHAPE", p, "no_fact 必须 entry_id=null 且 category=null")
            for (oid, olang), words in sig.items():
                if olang != lang:
                    continue
                for w in words:
                    if w in gscope[lang]:
                        continue  # 泛指词 (在哪/位置/hotel 等) 不算泄漏
                    if w in text or (lang == "en" and w.lower() in text.lower()):
                        err("E_NO_FACT_LEAK", p,
                            f"NO_FACT 问法含条目 {oid} 信号词 {w!r} (负例不纯净)")
                        break
                else:
                    continue
                break
        else:
            err("E_BINDING", p, f"非法绑定 {binding!r}")

    # ── 近似冲突 (编辑距离 ≤1, del-one 邻域分桶 + 真距离验证) ──
    norm_by_id = {u["id"]: norm(u["lang"], u["text"]) for u in utts}
    near_checked = set()
    for key, ids in del_keys.items():
        if len(ids) < 2:
            continue
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                ia, ib = ids[a], ids[b]
                if ia > ib:
                    ia, ib = ib, ia
                if (ia, ib) in near_checked:
                    continue
                near_checked.add((ia, ib))
                na, nb = norm_by_id[ia], norm_by_id[ib]
                if na != nb and ed_le_1(na, nb):
                    err("E_NEAR_DUP", f"$.utterances[{ia}]",
                        f"与 id={ib} 编辑距离 ≤1 ({na[:20]}… ≈ {nb[:20]}…)")

    ids_sorted = sorted(seen_ids)
    if ids_sorted and (ids_sorted[0] != 1 or ids_sorted[-1] != len(ids_sorted)
                       or len(ids_sorted) != len(utts)):
        err("E_ID_SEQ", "$.utterances", "id 必须为 1..N 连续唯一")

    c = corpus["counts"]
    if set(c.keys()) != set(COUNT_KEYS):
        err("E_COUNTS", "$.counts", f"字段必须恰好为 {COUNT_KEYS}")
    else:
        real = {
            "total": len(utts),
            "zh": sum(1 for u in utts if u["lang"] == "zh"),
            "en": sum(1 for u in utts if u["lang"] == "en"),
            "entry_bound": sum(1 for u in utts if u["binding"] == "entry"),
            "clarify": sum(1 for u in utts if u["binding"] == "clarify"),
            "no_fact": sum(1 for u in utts if u["binding"] == "no_fact"),
        }
        for k, v in real.items():
            if c[k] != v:
                err("E_COUNT_MISMATCH", f"$.counts.{k}", f"声明 {c[k]} ≠ 实际 {v}")
        if real["total"] < min_unique:
            err("E_MIN_UNIQUE", "$.counts.total",
                f"唯一问法 {real['total']} < 门槛 {min_unique}")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--templates", default=str(DEFAULT_TEMPLATES))
    ap.add_argument("--min-unique", type=int, default=10000)
    args = ap.parse_args()

    pkg_path = Path(args.package)
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    issues = validate(pkg_path, corpus, args.min_unique,
                      templates_path=Path(args.templates))
    if issues:
        print(f"FAIL ({len(issues)} 处)")
        for i in issues[:40]:
            print("  " + str(i))
        if len(issues) > 40:
            print(f"  …另有 {len(issues) - 40} 处")
        return 1
    n = corpus["counts"]
    print(f"OK 唯一问法 {n['total']} (zh={n['zh']} en={n['en']} "
          f"entry_bound={n['entry_bound']} clarify={n['clarify']} "
          f"no_fact={n['no_fact']}), 门槛 {args.min_unique} 达标; "
          f"去重/近似冲突/串扰/事实边界/intent级事实映射/intent语义/最终路由一致性 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
