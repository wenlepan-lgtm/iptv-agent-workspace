#!/usr/bin/env python3
"""JOCTV 问法覆盖留出集路由评测器 (Skill 1.3.0)。

只读离线评测, 两层口径 (决策链/评分副本均来自 scripts/kb_router_chain, 与生成器
硬断言/校验器 E_ROUTE_CONSISTENCY 共用同一实现 — 不存在第二套未建模的决策副本):
  1) 评分层 (R0 口径, 网关 _kb_topk_scored 确定性评分副本): 固定随机留出集上对比
     baseline (源包 keywords) vs enhanced (keywords + variant 称呼 − 公共简称/泛指词)
     的 hit@1 / variant 子集 / NO_FACT 不回归 / 歧义探针不新增独占;
  2) 最终决策层 (1.3.0, 网关 kb_route 确定性**单轮全链**副本 route_single_turn:
     范围缺口裁决 → 字段识别 → 直接匹配 → 短称候选 → 比较级分支 → 直答/缺字段/
     规划器/非知识), 共享 route_expectation 断言"最终 decision + 回答字段与声明
     intent 一致":
       - FINAL_ROUTE_FIELD_CORRECT: 全量语料 entry 绑定问法精确独占直答
         (decision+topic+field 三者与声明一致 — 不是只看 topic hit@1);
       - NO_FACT_ABSOLUTE_SAFE / CLARIFY_ABSOLUTE_SAFE: 全量语料 no_fact/clarify
         绑定按声明断言安全 (clarify 声明字段缺失时只允许缺字段话术/有效澄清/
         安全非独占路径; intent 标记缺但声明字段已发布时允许同条目同字段直答);
       - NO_FACT_CHALLENGE_FULL / CLARIFY_CHALLENGE_FULL (1.3.0): **完整挑战空间
         确定性枚举** (全部 NO_FACT 主题 × 全部 no_fact intent 模板 × 前缀; 全部
         (entry, lang, intent) 缺事实组合 × 全部模板 × 称呼 × 前缀), 逐条断言,
         不抽样不删除 — 路由失败样本不可能被生成侧筛选掉 (生成器已改为硬断言
         无过滤, 本门独立复核);
       - AMBIGUITY_ABSOLUTE_NON_EXCLUSIVE: 全部歧义探针进入澄清或非独占路径;
       - CORRECT_NO_NEW_WRONG_ROUTE: baseline 正确直答样本在 enhanced 无新增
         错误条目直答。

决策/评分算法副本带源码快照 SHA 防漂移门 (网关变化时评测器 FAIL, 需重对齐
kb_router_chain); Skill 单独拷贝到工作站 (无 p4_admin) 时用 --allow-missing-router
跳过。

导出后台可直接导入的增强别名知识包 (joctv-hotel-kb-v1): answers/context/事实零变化,
keywords = 源 keywords + variant 称呼 − 公共简称/泛指词 (确定性规则, 见
kb_router_chain.removed_keywords)。不启动任何服务, 不访问生产, 只读消费语料与知识包。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_utterance_coverage import (_prefix_head_clash, build_raw_terms,  # noqa: E402
                                         build_signal_terms, build_variant_terms,
                                         combo_hijacks_other_entry,
                                         filter_no_fact_topics, term_verb)
from kb_router_chain import (FACT_LEAK_DECISIONS, SAFE_FALLBACK_DECISIONS,  # noqa: E402
                             _kb_topk_scored, build_topics, intent_fact_available,
                             removed_keywords, route_expectation, route_single_turn,
                             runtime_field_published)

ROUTER_SNAPSHOT_SHA256 = "06ae623517af9185283211073999dd841c6646e981c58906e642e5bef94150a0"
ROUTER_WANT = {
    # 评分层
    "_kb_cjk_bigrams", "_kb_topic_text", "_kb_topk_scored", "_KB_QUERY_SYNONYMS_ZH",
    # 最终决策层 (1.3.0 单轮全链)
    "_kb_is_zh", "_KB_FIELD_PATTERNS", "_KB_FIELD_RES", "_kb_detect_field",
    "_kb_match_topic", "_KB_STEM_FILLER", "_KB_STEM_EN_STOP", "_kb_extract_stems",
    "_kb_stem_candidates", "_kb_direct_answer", "_KB_FIELD_ZH",
    "_kb_strip_entity",
    "_KB_COMPARISON_RE", "_KB_COMPARISON_TOPICLESS_RE",
    # 范围缺口裁决 (V25-08R2)
    "_KB_GENERIC_SCOPE_ALIASES", "_KB_OUTSIDE_MARKERS", "_KB_OUTSIDE_RESOURCES",
    "_kb_outside_scope", "_kb_names_blob", "_kb_outside_covered",
    "_kb_parking_general", "_kb_specific_topic_named",
}

# 歧义探针: 公共简称 (同时是多个主题叫法, 不是任何条目信号词) — 不得被独占路由,
# 应走运行时确定性澄清/兜底。固定集合, 不随语料变化。
AMBIGUITY_PROBES = [
    ("zh", "酒廊几点开门"), ("zh", "餐厅哪一家好"), ("zh", "酒廊在哪里"),
    ("en", "where is the lounge"), ("en", "which restaurant is good"),
    ("en", "lounge opening hours"),
]


def check_router_snapshot(router_src_path: Path) -> tuple:
    """对比网关当前源码函数快照; 漂移 → FAIL (副本必须与网关逐字一致)。"""
    src = router_src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    segs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ROUTER_WANT:
            segs.append(''.join(lines[node.lineno - 1:node.end_lineno]))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ROUTER_WANT:
                    segs.append(''.join(lines[node.lineno - 1:node.end_lineno]))
    blob = '\n@@@\n'.join(sorted(segs))
    sha = hashlib.sha256(blob.encode()).hexdigest()
    return (sha == ROUTER_SNAPSHOT_SHA256), sha, len(segs)


def route(text: str, topics: list) -> list:
    scored = _kb_topk_scored(text, [t for t in topics])  # locale 过滤在调用侧
    return scored


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--templates", default=str(
        Path(__file__).resolve().parent.parent / "templates"
        / "utterance_intent_templates.json"))
    ap.add_argument("--router-source",
                    default=str(Path(__file__).resolve().parents[3]
                                / "p4_admin" / "v3_candidate_backend.py"))
    ap.add_argument("--allow-missing-router", action="store_true",
                    help="Skill 单独拷贝到工作站 (无 p4_admin) 时允许跳过防漂移检查")
    ap.add_argument("--holdout", type=float, default=0.12)
    ap.add_argument("--seed", type=int, default=20260905)
    args = ap.parse_args()

    pkg_path = Path(args.package)
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    tpl = json.loads(Path(args.templates).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    gates = []

    def gate(name, ok, detail):
        gates.append({"gate": name, "ok": bool(ok), "detail": detail})
        return ok

    # ── 防漂移门: 评分与决策链副本必须与网关当前实现逐字一致 ──
    router_path = Path(args.router_source)
    if router_path.is_file():
        snap_ok, snap_sha, nseg = check_router_snapshot(router_path)
        gate("ROUTER_SNAPSHOT", snap_ok,
             f"segs={nseg} sha={snap_sha[:16]}… (期望 {ROUTER_SNAPSHOT_SHA256[:16]}…)")
        if not snap_ok:
            print("FAIL ROUTER_SNAPSHOT: 网关路由函数已变化, 评测器副本需重对齐",
                  file=sys.stderr)
            return 2
    else:
        gate("ROUTER_SNAPSHOT", args.allow_missing_router,
             f"missing {router_path}"
             + (" (允许跳过)" if args.allow_missing_router else " (需 --allow-missing-router)"))
        if not args.allow_missing_router:
            return 2

    sha = hashlib.sha256(pkg_path.read_bytes()).hexdigest()
    gate("SOURCE_BINDING", corpus["source_package_sha256"] == sha,
         f"corpus 绑定 {corpus['source_package_sha256'][:12]}… vs 包 {sha[:12]}…")
    total = corpus["counts"]["total"]
    gate("MIN_UNIQUE", total >= 10000, f"total={total}")

    removals = removed_keywords(pkg, tpl)
    baseline = build_topics(pkg, corpus, enhanced=False)
    enhanced = build_topics(pkg, corpus, enhanced=True, tpl=tpl)
    gate("ENHANCED_ALIASES",
         sum(len(t["keywords"]) for t in enhanced)
         > sum(len(t["keywords"]) for t in baseline),
         f"baseline_kw={sum(len(t['keywords']) for t in baseline)} "
         f"enhanced_kw={sum(len(t['keywords']) for t in enhanced)} "
         f"(含公共简称/泛指词移除 {sum(len(v) for v in removals.values())} 项)")

    entries = {e["id"]: e for e in pkg["entries"]}
    locale_of = {"zh": "zh-CN", "en": "en-US"}
    topics_loc = lambda topics, loc: [t for t in topics if t["locale"] == loc]  # noqa: E731

    # ── 固定留出集: entry_bound 按 (entry_id, lang) 分层采样 ──
    rng = random.Random(args.seed)
    by_cell = {}
    for u in corpus["utterances"]:
        if u["binding"] == "entry":
            by_cell.setdefault((u["entry_id"], u["lang"]), []).append(u["id"])
    holdout_ids = set()
    for cell in sorted(by_cell):
        ids = sorted(by_cell[cell])
        k = max(1, round(len(ids) * args.holdout))
        holdout_ids.update(rng.sample(ids, k))
    holdout_ids_sha = hashlib.sha256(
        ",".join(str(i) for i in sorted(holdout_ids)).encode()).hexdigest()
    holdout = [u for u in corpus["utterances"] if u["id"] in holdout_ids]

    def evaluate(topics):
        stat = {"n": 0, "hit1": 0, "hit3": 0, "tie": 0,
                "n_variant": 0, "hit1_variant": 0, "n_base": 0, "hit1_base": 0}
        misses = []
        for u in holdout:
            ts = topics_loc(topics, locale_of[u["lang"]])
            scored = route(u["text"], ts)
            stat["n"] += 1
            if len(scored) >= 2 and scored[0][0] == scored[1][0]:
                stat["tie"] += 1
            top_ids = [t["id"] for _, _, t in scored[:3]]
            if str(u["entry_id"]) in top_ids[:1]:
                stat["hit1"] += 1
            if str(u["entry_id"]) in top_ids:
                stat["hit3"] += 1
            if u["term_kind"] == "variant":
                stat["n_variant"] += 1
                stat["hit1_variant"] += 1 if str(u["entry_id"]) in top_ids[:1] else 0
            else:
                stat["n_base"] += 1
                stat["hit1_base"] += 1 if str(u["entry_id"]) in top_ids[:1] else 0
            if str(u["entry_id"]) not in top_ids[:1] and len(misses) < 12:
                misses.append({"id": u["id"], "text": u["text"], "want": u["entry_id"],
                               "intent": u["intent"], "term_kind": u["term_kind"],
                               "top3": [(round(s, 2), t["id"], t["name"])
                                        for s, _, t in scored[:3]]})
        stat["hit1_rate"] = round(stat["hit1"] / stat["n"], 4) if stat["n"] else 0.0
        stat["hit3_rate"] = round(stat["hit3"] / stat["n"], 4) if stat["n"] else 0.0
        stat["hit1_variant_rate"] = (round(stat["hit1_variant"] / stat["n_variant"], 4)
                                     if stat["n_variant"] else None)
        stat["hit1_base_rate"] = (round(stat["hit1_base"] / stat["n_base"], 4)
                                  if stat["n_base"] else None)
        return stat, misses

    base_stat, base_misses = evaluate(baseline)
    enh_stat, enh_misses = evaluate(enhanced)

    gain = round(enh_stat["hit1_rate"] - base_stat["hit1_rate"], 4)
    gate("HIT1_IMPROVED", enh_stat["hit1_rate"] > base_stat["hit1_rate"],
         f"holdout n={holdout and len(holdout)} hit@1 {base_stat['hit1_rate']:.4f} → "
         f"{enh_stat['hit1_rate']:.4f} (+{gain:.4f})")
    gate("VARIANT_GAIN",
         enh_stat["hit1_variant_rate"] is not None
         and base_stat["hit1_variant_rate"] is not None
         and enh_stat["hit1_variant_rate"] > base_stat["hit1_variant_rate"],
         f"variant 子集 hit@1 {base_stat['hit1_variant_rate']} → "
         f"{enh_stat['hit1_variant_rate']}")

    # ── 评分层 NO_FACT 兜底 (不回归): 未知主题不得被独占路由 (无包含命中, score<1.0) ──
    def nofact_rate(topics):
        n = safe = 0
        for u in corpus["utterances"]:
            if u["binding"] != "no_fact":
                continue
            ts = topics_loc(topics, locale_of[u["lang"]])
            scored = route(u["text"], ts)
            n += 1
            if not scored or scored[0][0] < 1.0:
                safe += 1
        return (safe / n if n else 1.0), n

    base_safe, nf_n = nofact_rate(baseline)
    enh_safe, _ = nofact_rate(enhanced)
    gate("NO_FACT_NO_REGRESSION", enh_safe >= base_safe - 1e-9,
         f"n={nf_n} 评分层兜底率 {base_safe:.4f} → {enh_safe:.4f} (未被独占路由比例)")

    # ── 评分层歧义探针不新增独占 (baseline 已存源包缺陷记 findings) ──
    probe_rows = []
    exclusive_by_router = {"baseline": [], "enhanced": []}
    for lang, text in AMBIGUITY_PROBES:
        loc = locale_of[lang]
        for label, topics_x in (("baseline", baseline), ("enhanced", enhanced)):
            scored = route(text, topics_loc(topics_x, loc))
            top = (round(scored[0][0], 3), scored[0][2]["id"], scored[0][2]["name"]) \
                if scored else None
            exclusive = bool(top and top[0] >= 1.0)
            if exclusive:
                exclusive_by_router[label].append(text)
            probe_rows.append({"probe": text, "router": label, "top1": top,
                               "exclusive_route": exclusive})
    new_exclusive = [t for t in exclusive_by_router["enhanced"]
                     if t not in exclusive_by_router["baseline"]]
    gate("AMBIGUITY_NO_NEW_EXCLUSIVE", not new_exclusive,
         f"评分层探针 {len(AMBIGUITY_PROBES)}×2; baseline 独占 "
         f"{len(exclusive_by_router['baseline'])} 例, enhanced 新增独占 "
         f"{len(new_exclusive)} 例")

    # ── 最终决策层 (1.3.0, route_single_turn 单轮全链): 共享 route_expectation 断言 ──
    def chain_check(pred, topics):
        """按绑定断言语料问法的最终 decision+回答字段; 返回 (dist, violations,
        bound_field_direct)。bound_field_direct = clarify 声明字段已发布时的同条目
        同字段直答 (诚实回答, 不算泄漏)。"""
        dist, violations, bound_field_direct = {}, [], 0
        for u in corpus["utterances"]:
            if not pred(u):
                continue
            loc = locale_of[u["lang"]]
            d = route_single_turn(u["text"], loc, topics_loc(topics, loc))
            dist[d["decision"]] = dist.get(d["decision"], 0) + 1
            fld_present = (runtime_field_published(entries[u["entry_id"]], u["lang"],
                                                   u["intent"])
                           if u["entry_id"] in entries else False)
            if not route_expectation(u["binding"], u["intent"], u["entry_id"],
                                     runtime_field_present=fld_present)(d):
                violations.append({"id": u["id"], "text": u["text"],
                                   "binding": u["binding"], "intent": u["intent"],
                                   "field": u["field"], "decision": d})
            elif (u["binding"] == "clarify" and d["decision"] == "KB_DIRECT_FACT"):
                bound_field_direct += 1
        return dist, violations, bound_field_direct

    # entry 绑定: 最终 decision+topic+字段 三者精确一致 (KB30-04-01 核心)
    _, enh_entry_bad, _ = chain_check(lambda u: u["binding"] == "entry", enhanced)
    gate("FINAL_ROUTE_FIELD_CORRECT", not enh_entry_bad,
         f"全量 entry 绑定 n={sum(1 for u in corpus['utterances'] if u['binding'] == 'entry')} "
         f"最终路由字段/主题不一致 {len(enh_entry_bad)} 例 (decision+topic_id+field 与声明精确一致)")

    base_nf_dist, base_nf_bad, _ = chain_check(
        lambda u: u["binding"] == "no_fact", baseline)
    enh_nf_dist, enh_nf_bad, _ = chain_check(
        lambda u: u["binding"] == "no_fact", enhanced)
    gate("NO_FACT_ABSOLUTE_SAFE", not enh_nf_bad,
         f"决策层 n={sum(enh_nf_dist.values())} 非安全兜底 {len(enh_nf_bad)} 例 "
         f"(baseline {len(base_nf_bad)} 例); 分布 {enh_nf_dist}")

    base_cl_dist, base_cl_bad, _ = chain_check(
        lambda u: u["binding"] == "clarify", baseline)
    enh_cl_dist, enh_cl_bad, cl_bound_direct = chain_check(
        lambda u: u["binding"] == "clarify", enhanced)
    gate("CLARIFY_ABSOLUTE_SAFE", not enh_cl_bad,
         f"决策层 n={sum(enh_cl_dist.values())} 声明不符 {len(enh_cl_bad)} 例 "
         f"(声明字段缺失问法被独占直答/错字段缺字段话术; baseline {len(base_cl_bad)} 例; "
         f"声明字段已发布的同条目同字段直答 {cl_bound_direct} 例=诚实回答); "
         f"分布 {enh_cl_dist}")

    # ── 完整挑战空间枚举 (1.3.0): 不抽样、不删除, 独立于生成器的全量复核 ──
    # 枚举空间与生成器**合法组合空间**同口径 (单一资源规则): NO_FACT 主题先过
    # filter_no_fact_topics (含条目信号词碎片的主题被确定性拒绝并记录), clarify
    # 称呼只用生成器合格信号词 (base 过滤后的 signals + variants, 含公共简称/
    # 泛词/比较级词剔除) — 被剔除资源全部进报告 excluded_resources, 不静默删除。
    def _render(lang, prefix, tpl_text, term):
        body = tpl_text.replace("{t}", term).replace("{v}", term)
        if lang == "zh":
            return (prefix + body).strip()
        return (prefix + body).strip()

    raws = build_raw_terms(pkg, tpl)
    signals, dropped_signals = build_signal_terms(pkg, tpl, raws)
    variants_built, var_rejected_res = build_variant_terms(pkg, tpl, signals, raws)
    # corpus 导出的 variant_terms 必须与确定性重建一致 (导出包按 corpus 写; 漂移 =
    # 有人手改 corpus, 挑战空间与导出物脱节)
    vt_export = {str(k): v for k, v in variants_built.items() if v["zh"] or v["en"]}
    gate("VARIANT_TERMS_REBUILD_MATCH",
         vt_export == (corpus.get("variant_terms") or {}),
         f"corpus variant_terms 与 build_variant_terms 确定性重建逐项一致 "
         f"({len(vt_export)} entries)")
    nf_kept, nf_rejected_res = {}, []
    for lang in ("zh", "en"):
        kept, rej = filter_no_fact_topics(tpl["no_fact_topics"][lang], lang, pkg, raws)
        nf_kept[lang] = kept
        nf_rejected_res += rej
    var_terms = corpus.get("variant_terms") or {}

    # NO_FACT 挑战集: 合格 NO_FACT 主题 × 全部 no_fact intent N 模板 × 前缀
    nf_challenge = {"n": 0, "bad": [], "dist": {}}
    for lang in ("zh", "en"):
        topics_loc_list = topics_loc(enhanced, locale_of[lang])
        for topic in nf_kept[lang]:
            term = topic.lower() if lang == "en" else topic
            for intent in tpl["no_fact_intents"]:
                for tpl_text in tpl["intents"][intent][lang].get("N", []):
                    if "{v}" in tpl_text:
                        continue
                    for prefix in tpl["prefixes"][lang]:
                        text = _render(lang, prefix, tpl_text, term)
                        d = route_single_turn(text, locale_of[lang], topics_loc_list)
                        nf_challenge["n"] += 1
                        nf_challenge["dist"][d["decision"]] = \
                            nf_challenge["dist"].get(d["decision"], 0) + 1
                        if not route_expectation("no_fact", intent, None)(d):
                            if len(nf_challenge["bad"]) < 20:
                                nf_challenge["bad"].append(
                                    {"text": text, "intent": intent, "decision": d})
    gate("NO_FACT_CHALLENGE_FULL", not nf_challenge["bad"],
         f"完整 NO_FACT 挑战空间枚举 n={nf_challenge['n']} "
         f"(合格主题×全部模板×前缀, 无抽样); 非安全兜底 {len(nf_challenge['bad'])} 例; "
         f"分布 {nf_challenge['dist']}; 资源剔除 no_fact 主题 {len(nf_rejected_res)} 项"
         f"/信号词 {len(dropped_signals)} 项 (见 excluded_resources)")

    # entry 挑战集 (1.3.0): 全部 (entry, lang, intent) **事实已发布**组合 × 模板(N/V)
    # × 合格称呼 × 前缀 — 最终 decision+topic+field 必须与声明精确一致 (KB30-04-01
    # 全组合空间口径, 不止语料抽样)。组合劫持排除与生成器同规则同源。
    en_challenge = {"n": 0, "bad": [], "dist": {}}
    en_hijack_excluded = []
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            if not e["topic"][lang]:
                continue
            topics_loc_list = topics_loc(enhanced, locale_of[lang])
            n_terms = [(t.lower() if lang == "en" else t)
                       for t, _, verb in signals[e["id"]][lang] if not verb]
            v_terms = [(t.lower() if lang == "en" else t)
                       for t, _, verb in signals[e["id"]][lang] if verb]
            var_list = [str(v) for v in (var_terms.get(str(e["id"])) or {}).get(lang, [])]
            n_terms += [v for v in var_list if not term_verb(lang, v, tpl)]
            v_terms += [v for v in var_list if term_verb(lang, v, tpl)]
            if not n_terms and not v_terms:
                continue
            for intent, spec in tpl["intents"].items():
                if not intent_fact_available(e, lang, intent):
                    continue  # 只枚举事实已发布 (entry) 空间
                for slot, terms in (("N", n_terms), ("V", v_terms)):
                    for tpl_text in spec[lang].get(slot, []):
                        ph = "{v}" if slot == "V" else "{t}"
                        if ph not in tpl_text:
                            continue
                        for term in terms:
                            body = tpl_text.replace(ph, term)
                            hij = combo_hijacks_other_entry(lang, body, e["id"], raws)
                            if hij:
                                en_hijack_excluded.append(
                                    {"entry_id": e["id"], "lang": lang, "intent": intent,
                                     "term": term, "body": body, "reason": hij})
                                continue
                            for prefix in tpl["prefixes"][lang]:
                                if _prefix_head_clash(prefix, body, lang):
                                    continue
                                text = _render(lang, prefix, tpl_text, term)
                                d = route_single_turn(text, locale_of[lang], topics_loc_list)
                                en_challenge["n"] += 1
                                en_challenge["dist"][d["decision"]] = \
                                    en_challenge["dist"].get(d["decision"], 0) + 1
                                if not route_expectation("entry", intent, e["id"])(d):
                                    if len(en_challenge["bad"]) < 20:
                                        en_challenge["bad"].append(
                                            {"text": text, "intent": intent,
                                             "entry_id": e["id"], "decision": d})
    gate("ENTRY_CHALLENGE_FULL", not en_challenge["bad"],
         f"完整 entry 绑定挑战空间枚举 n={en_challenge['n']} "
         f"(全部事实已发布 entry×intent×模板×合格称呼×前缀, 无抽样); 声明不符 "
         f"{len(en_challenge['bad'])} 例 (decision+topic_id+field 精确一致); "
         f"分布 {en_challenge['dist']}; 组合劫持排除 {len(en_hijack_excluded)} 项")

    # clarify 挑战集: 全部 (entry, lang, intent) 缺事实组合 × 模板(N/V) × 合格称呼 × 前缀
    cl_challenge = {"n": 0, "bad": [], "dist": {}}
    cl_hijack_excluded = []
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            if not e["topic"][lang]:
                continue
            topics_loc_list = topics_loc(enhanced, locale_of[lang])
            n_terms = [(t.lower() if lang == "en" else t)
                       for t, _, verb in signals[e["id"]][lang] if not verb]
            v_terms = [(t.lower() if lang == "en" else t)
                       for t, _, verb in signals[e["id"]][lang] if verb]
            var_list = [str(v) for v in (var_terms.get(str(e["id"])) or {}).get(lang, [])]
            n_terms += [v for v in var_list if not term_verb(lang, v, tpl)]
            v_terms += [v for v in var_list if term_verb(lang, v, tpl)]
            if not n_terms and not v_terms:
                continue
            fld_pub = {i: runtime_field_published(e, lang, i) for i in tpl["intents"]}
            for intent, spec in tpl["intents"].items():
                if intent_fact_available(e, lang, intent):
                    continue  # 只枚举缺事实 (clarify) 空间
                for slot, terms in (("N", n_terms), ("V", v_terms)):
                    for tpl_text in spec[lang].get(slot, []):
                        ph = "{v}" if slot == "V" else "{t}"
                        if ph not in tpl_text:
                            continue
                        for term in terms:
                            body = tpl_text.replace(ph, term)
                            hij = combo_hijacks_other_entry(lang, body, e["id"], raws)
                            if hij:
                                cl_hijack_excluded.append(
                                    {"entry_id": e["id"], "lang": lang, "intent": intent,
                                     "term": term, "body": body, "reason": hij})
                                continue
                            for prefix in tpl["prefixes"][lang]:
                                if _prefix_head_clash(prefix, body, lang):
                                    continue  # 生成器组合规则同口径: 前缀与句式头部重复的组合不生成
                                text = _render(lang, prefix, tpl_text, term)
                                d = route_single_turn(text, locale_of[lang], topics_loc_list)
                                cl_challenge["n"] += 1
                                cl_challenge["dist"][d["decision"]] = \
                                    cl_challenge["dist"].get(d["decision"], 0) + 1
                                if not route_expectation("clarify", intent, e["id"],
                                                         runtime_field_present=fld_pub[intent])(d):
                                    if len(cl_challenge["bad"]) < 20:
                                        cl_challenge["bad"].append(
                                            {"text": text, "intent": intent,
                                             "entry_id": e["id"], "decision": d})
    gate("CLARIFY_CHALLENGE_FULL", not cl_challenge["bad"],
         f"完整缺字段挑战空间枚举 n={cl_challenge['n']} "
         f"(全部缺事实 entry×intent×模板×合格称呼×前缀, 无抽样); 声明不符 "
         f"{len(cl_challenge['bad'])} 例; 分布 {cl_challenge['dist']}; "
         f"组合劫持排除 {len(cl_hijack_excluded)} 项 (见 excluded_resources)")

    # ── 最终决策层: 歧义探针全部进入澄清或非独占路径 (不得独占直答) ──
    probe_chain = []
    probe_exclusive = []
    for lang, text in AMBIGUITY_PROBES:
        loc = locale_of[lang]
        for label, topics_x in (("baseline", baseline), ("enhanced", enhanced)):
            d = route_single_turn(text, loc, topics_loc(topics_x, loc))
            row = {"probe": text, "router": label, "decision": d["decision"],
                   "topic_id": d.get("topic_id"),
                   "candidates": d.get("candidates")}
            probe_chain.append(row)
            if label == "enhanced" and d["decision"] in FACT_LEAK_DECISIONS:
                probe_exclusive.append(row)
    gate("AMBIGUITY_ABSOLUTE_NON_EXCLUSIVE", not probe_exclusive,
         f"决策层探针 {len(AMBIGUITY_PROBES)}: enhanced 独占直答 "
         f"{len(probe_exclusive)} 例; 决策 "
         + ", ".join(f"{r['probe']}→{r['decision']}" for r in probe_chain
                     if r["router"] == "enhanced"))

    # ── 最终决策层: baseline 正确样本在 enhanced 无新增错误路由 ──
    # (正确样本 = baseline 独占直答命中绑定条目且字段一致; 错误 = enhanced 直答了其他条目)
    new_wrong = []
    for u in holdout:
        loc = locale_of[u["lang"]]
        bd = route_single_turn(u["text"], loc, topics_loc(baseline, loc))
        if bd["decision"] in FACT_LEAK_DECISIONS and bd.get("topic_id") == str(u["entry_id"]):
            ed = route_single_turn(u["text"], loc, topics_loc(enhanced, loc))
            if ed["decision"] in FACT_LEAK_DECISIONS \
                    and ed.get("topic_id") != str(u["entry_id"]):
                new_wrong.append({"id": u["id"], "text": u["text"],
                                  "want": u["entry_id"], "baseline": bd,
                                  "enhanced": ed})
    gate("CORRECT_NO_NEW_WRONG_ROUTE", not new_wrong,
         f"baseline 正确直答样本中 enhanced 改判错误条目 {len(new_wrong)} 例")

    findings = []
    for text in exclusive_by_router["baseline"]:
        row = next(r for r in probe_rows if r["probe"] == text and r["router"] == "baseline")
        findings.append({
            "finding": "AMBIGUOUS_PROBE_EXCLUSIVE_IN_SOURCE_PACKAGE",
            "probe": text, "baseline_top1": row["top1"],
            "root_cause": "源知识包 keyword 含公共简称/泛指词 (违反 Skill 公共简称"
                          "不得独占绑定规则), baseline 即独占路由",
            "resolution_r1": "增强别名包按确定性规则移除该类 keyword (仅 alias, "
                             "事实/主题名零变化), enhanced 决策层探针全部进入澄清/"
                             "非独占路径 (见 ambiguity_probes_decision_chain)"})
    if base_nf_bad:
        findings.append({
            "finding": "NO_FACT_EXCLUSIVE_IN_BASELINE",
            "n": len(base_nf_bad),
            "sample": base_nf_bad[:5],
            "root_cause": "baseline 源包泛指词/公共简称 keyword 独占吸收未知问法",
            "resolution_r1": "增强别名包移除该类 keyword; 生成器 1.3.0 已改为硬断言"
                             "无过滤 (违规即 exit 2), 本评测 NO_FACT_CHALLENGE_FULL "
                             "完整枚举独立复核 (分布 " + json.dumps(
                                 nf_challenge["dist"], ensure_ascii=False) + ")"})

    all_ok = all(g["ok"] for g in gates)

    # ── 增强别名知识包 (后台可直接导入; 事实零变化, keywords = +variant −公共简称/泛指词) ──
    enh_pkg = json.loads(json.dumps(pkg, ensure_ascii=False))
    enh_pkg["package_name"] = pkg["package_name"] + "（问法别名增强）"
    for e in enh_pkg["entries"]:
        extra = (corpus["variant_terms"].get(str(e["id"])) or {})
        for lang in ("zh", "en"):
            kws = e["keywords"][lang]
            rm = {d["keyword"] for d in removals.get((e["id"], lang), [])}
            kws[:] = [k for k in kws if k not in rm]
            for v in extra.get(lang, []):
                if v not in kws:
                    kws.append(v)
    pkg_out = out_dir / "alias_enhanced_package.json"
    pkg_out.write_text(json.dumps(enh_pkg, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")

    alias_export = {
        "schema_version": "joctv-hotel-alias-export-v1",
        "hotel_id": pkg["hotel_id"],
        "source_package_sha256": sha,
        "corpus_sha256": hashlib.sha256(Path(args.corpus).read_bytes()).hexdigest(),
        "utterances_generated": total,
        "aliases": [],
        "removed_keywords": [{"entry_id": k[0], "lang": k[1], "drops": v}
                             for k, v in sorted(removals.items())],
    }
    for e in pkg["entries"]:
        for lang in ("zh", "en"):
            new = (corpus["variant_terms"].get(str(e["id"])) or {}).get(lang, [])
            if not new:
                continue
            samples = [u["text"] for u in corpus["utterances"]
                       if u["entry_id"] == e["id"] and u["lang"] == lang
                       and u["term_kind"] == "variant"][:3]
            alias_export["aliases"].append({
                "entry_id": e["id"], "lang": lang, "topic": e["topic"][lang],
                "new_aliases": new, "sample_queries": samples})
    alias_out = out_dir / "new_aliases.json"
    alias_out.write_text(json.dumps(alias_export, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")

    report = {
        "seed": args.seed,
        "holdout_ratio": args.holdout,
        "holdout_size": len(holdout),
        "holdout_ids_sha256": holdout_ids_sha,
        "router_snapshot_sha256": ROUTER_SNAPSHOT_SHA256,
        "baseline_keywords_total": sum(len(t["keywords"]) for t in baseline),
        "enhanced_keywords_total": sum(len(t["keywords"]) for t in enhanced),
        "removed_keywords": {f"{k[0]}:{k[1]}": v for k, v in sorted(removals.items())},
        "baseline": base_stat,
        "enhanced": enh_stat,
        "hit1_gain": gain,
        "no_fact": {"n": nf_n, "baseline_safe_rate": round(base_safe, 4),
                    "enhanced_safe_rate": round(enh_safe, 4)},
        "decision_chain": {
            "modeled": "范围缺口裁决→字段识别→直接最长匹配→短称受控候选(0/1/≥2)"
                       "→比较级分支→直答/缺字段/规划器/非知识 (kb_router_chain."
                       "route_single_turn, 网关 kb_route 单轮全链副本)",
            "not_modeled": "会话澄清待决态/上下文主题补全/NPU规划器执行"
                           "(规划器非独占多卡路径, supported=false→KB_SAFE, "
                           "自带 grounding 防火墙)",
            "expectation": "共享 route_expectation: entry=decision+topic+field 精确"
                           "一致; clarify 声明字段缺失=只允许缺字段话术/有效澄清/"
                           "安全非独占路径 (声明字段已发布时允许同条目同字段直答); "
                           "no_fact=只允许安全兜底",
            "entry_enhanced_violations": enh_entry_bad[:10],
            "no_fact_baseline": base_nf_dist,
            "no_fact_enhanced": enh_nf_dist,
            "no_fact_baseline_violations": base_nf_bad[:10],
            "clarify_baseline": base_cl_dist,
            "clarify_enhanced": enh_cl_dist,
            "clarify_enhanced_bound_field_direct": cl_bound_direct,
            "clarify_enhanced_violations": enh_cl_bad[:10],
            "no_fact_challenge_full": nf_challenge,
            "entry_challenge_full": en_challenge,
            "clarify_challenge_full": cl_challenge,
            "ambiguity_probes": probe_chain,
            "baseline_correct_new_wrong": new_wrong[:10],
        },
        "ambiguity_probes": probe_rows,
        "excluded_resources": {
            "note": "挑战枚举与生成器合法组合空间同口径 (单一资源规则); 以下资源/组合被"
                    "确定性规则剔除, 全量记录在报告里, 不静默删除 — 剔除只依赖资源"
                    "文本本身 (子串/泛词/比较级/组合拼出他条目更长别名), 与路由结果无关",
            "no_fact_topics_rejected": nf_rejected_res,
            "signal_terms_dropped": dropped_signals,
            "variant_terms_rejected": var_rejected_res,
            "entry_combo_hijack_excluded": en_hijack_excluded,
            "clarify_combo_hijack_excluded": cl_hijack_excluded,
        },
        "findings": findings,
        "baseline_misses_sample": base_misses,
        "enhanced_misses_sample": enh_misses,
        "gates": gates,
        "exports": {"alias_enhanced_package": pkg_out.name,
                    "new_aliases": alias_out.name},
    }
    rep_out = out_dir / "holdout_eval_report.json"
    rep_out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")

    for g in gates:
        print(f"{'PASS' if g['ok'] else 'FAIL'}  {g['gate']}: {g['detail']}")
    print(f"\n留出集 n={len(holdout)}  hit@1 {base_stat['hit1_rate']:.4f} → "
          f"{enh_stat['hit1_rate']:.4f} (+{gain:.4f});  "
          f"variant 子集 {base_stat['hit1_variant_rate']} → "
          f"{enh_stat['hit1_variant_rate']};  评分层 NO_FACT 兜底 "
          f"{base_safe:.4f} → {enh_safe:.4f}")
    print(f"决策层: NO_FACT 分布 {enh_nf_dist}; clarify 分布 {enh_cl_dist}; "
          f"完整挑战空间 entry={en_challenge['n']} / no_fact={nf_challenge['n']} "
          f"/ clarify={cl_challenge['n']}")
    print(f"报告: {rep_out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
