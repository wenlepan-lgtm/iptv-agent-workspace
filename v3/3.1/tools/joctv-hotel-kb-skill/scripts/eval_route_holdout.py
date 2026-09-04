#!/usr/bin/env python3
"""JOCTV 问法覆盖留出集路由评测器 (Skill 1.2.0)。

只读离线评测, 两层口径 (决策链/评分副本均来自 scripts/kb_router_chain, 与生成器
的最终决策链预审共用同一实现):
  1) 评分层 (R0 口径, 网关 _kb_topk_scored 确定性评分副本): 固定随机留出集上对比
     baseline (源包 keywords) vs enhanced (keywords + variant 称呼 − 公共简称/泛指词)
     的 hit@1 / variant 子集 / NO_FACT 不回归 / 歧义探针不新增独占;
  2) 最终决策层 (R1, Codex KB30-04-02-ABSOLUTE-SAFETY): 网关 kb_route 确定性决策链
     副本 (字段识别 → 直接匹配 → 公共短称澄清 → 直答/缺字段兜底), 断言绝对行为:
       - NO_FACT_ABSOLUTE_SAFE: 全部 NO_FACT 问法落入安全兜底
         (NOT_KNOWLEDGE / KB_NPU_PLANNER / KB_MISSING_FIELD) — 独占直答
         (返回事实/概述) 与错候选澄清都不算安全兜底;
       - CLARIFY_ABSOLUTE_SAFE: 全部 clarify 问法不被**其他条目**独占直答
         (绑定条目自身已发布字段的直答是诚实回答, 不算泄漏);
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
from kb_router_chain import (FACT_LEAK_DECISIONS, SAFE_FALLBACK_DECISIONS,  # noqa: E402
                             _kb_topk_scored, build_topics, final_decision,
                             removed_keywords)

ROUTER_SNAPSHOT_SHA256 = "c80d4a312cd876f1637ac8d58c573a3908cb1c7b3372aa00c402827d553c0bc5"
ROUTER_WANT = {
    # 评分层
    "_kb_cjk_bigrams", "_kb_topic_text", "_kb_topk_scored", "_KB_QUERY_SYNONYMS_ZH",
    # 最终决策层 (R1)
    "_kb_is_zh", "_KB_FIELD_PATTERNS", "_KB_FIELD_RES", "_kb_detect_field",
    "_kb_match_topic", "_KB_STEM_FILLER", "_KB_STEM_EN_STOP", "_kb_extract_stems",
    "_kb_stem_candidates", "_kb_direct_answer", "_KB_FIELD_ZH",
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
            ts = [t for t in topics if t["locale"] == ("zh-CN" if u["lang"] == "zh" else "en-US")]
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
            ts = [t for t in topics if t["locale"] == ("zh-CN" if u["lang"] == "zh" else "en-US")]
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
        loc = "zh-CN" if lang == "zh" else "en-US"
        for label, topics_x in (("baseline", baseline), ("enhanced", enhanced)):
            scored = route(text, [t for t in topics_x if t["locale"] == loc])
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

    # ── 最终决策层 (R1): 绝对安全门 ──
    def chain_stats(pred, topics, label):
        """label=no_fact: 非安全兜底决策即违规; label=clarify: 跨条目独占直答即违规
        (绑定条目自身已发布字段的直答=诚实回答, 计 bound_direct 不算泄漏)。"""
        dist, leaks, bound_direct = {}, [], 0
        for u in corpus["utterances"]:
            if not pred(u):
                continue
            loc = "zh-CN" if u["lang"] == "zh" else "en-US"
            d = final_decision(u["text"], loc, topics)
            dist[d["decision"]] = dist.get(d["decision"], 0) + 1
            if label == "no_fact":
                if d["decision"] not in SAFE_FALLBACK_DECISIONS:
                    leaks.append({"id": u["id"], "text": u["text"], "decision": d})
            elif d["decision"] in FACT_LEAK_DECISIONS:
                if d.get("topic_id") == str(u["entry_id"]):
                    bound_direct += 1
                else:
                    leaks.append({"id": u["id"], "text": u["text"], "decision": d,
                                  "bound_entry": u["entry_id"]})
        return dist, leaks, bound_direct

    base_nf_dist, base_nf_leaks, _ = chain_stats(
        lambda u: u["binding"] == "no_fact", baseline, "no_fact")
    enh_nf_dist, enh_nf_leaks, _ = chain_stats(
        lambda u: u["binding"] == "no_fact", enhanced, "no_fact")
    gate("NO_FACT_ABSOLUTE_SAFE", not enh_nf_leaks,
         f"决策层 n={sum(enh_nf_dist.values())} 非安全兜底 {len(enh_nf_leaks)} 例 "
         f"(baseline {len(base_nf_leaks)} 例); 分布 {enh_nf_dist}")

    enh_cl_dist, enh_cl_leaks, cl_bound_direct = chain_stats(
        lambda u: u["binding"] == "clarify", enhanced, "clarify")
    base_cl_dist, _, _ = chain_stats(
        lambda u: u["binding"] == "clarify", baseline, "clarify")
    gate("CLARIFY_ABSOLUTE_SAFE", not enh_cl_leaks,
         f"决策层 n={sum(enh_cl_dist.values())} 跨条目事实/概述泄漏 "
         f"{len(enh_cl_leaks)} 例 (绑定条目自身直答 {cl_bound_direct} 例=诚实回答); "
         f"分布 {enh_cl_dist}")

    # ── 最终决策层: 歧义探针全部进入澄清或非独占路径 (不得独占直答) ──
    probe_chain = []
    probe_exclusive = []
    for lang, text in AMBIGUITY_PROBES:
        loc = "zh-CN" if lang == "zh" else "en-US"
        for label, topics_x in (("baseline", baseline), ("enhanced", enhanced)):
            d = final_decision(text, loc, topics_x)
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
    # (正确样本 = baseline 独占直答命中绑定条目; 错误 = enhanced 直答了其他条目)
    new_wrong = []
    for u in holdout:
        loc = "zh-CN" if u["lang"] == "zh" else "en-US"
        bd = final_decision(u["text"], loc, baseline)
        if bd["decision"] in FACT_LEAK_DECISIONS and bd.get("topic_id") == str(u["entry_id"]):
            ed = final_decision(u["text"], loc, enhanced)
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
    if base_nf_leaks:
        findings.append({
            "finding": "NO_FACT_EXCLUSIVE_IN_BASELINE",
            "n": len(base_nf_leaks),
            "sample": base_nf_leaks[:5],
            "root_cause": "baseline 源包泛指词/公共简称 keyword 独占吸收未知问法",
            "resolution_r1": "增强别名包移除该类 keyword + 生成时决策链预审 "
                             f"(仅收安全兜底路径), 决策层 0 非安全兜底 (分布 {enh_nf_dist})"})

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
            "modeled": "字段识别→直接最长匹配→短称受控候选(0/1/≥2)→直答/缺字段兜底",
            "not_modeled": "会话澄清待决态/上下文主题/比较级分支/NPU规划器执行"
                           "(规划器非独占多卡路径, supported=false→KB_SAFE, "
                           "自带 grounding 防火墙)",
            "no_fact_baseline": base_nf_dist,
            "no_fact_enhanced": enh_nf_dist,
            "no_fact_baseline_leaks": base_nf_leaks[:10],
            "clarify_baseline": base_cl_dist,
            "clarify_enhanced": enh_cl_dist,
            "clarify_enhanced_bound_entity_direct": cl_bound_direct,
            "clarify_enhanced_leaks": enh_cl_leaks[:10],
            "ambiguity_probes": probe_chain,
            "baseline_correct_new_wrong": new_wrong[:10],
        },
        "ambiguity_probes": probe_rows,
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
    print(f"决策层: NO_FACT 分布 {enh_nf_dist}; clarify 分布 {enh_cl_dist}")
    print(f"报告: {rep_out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
