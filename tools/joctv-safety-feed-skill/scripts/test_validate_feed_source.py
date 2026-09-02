#!/usr/bin/env python3
"""joctv-safety-feed-skill 轻量门 (纯标准库, 任意工作站可跑)。

P 组 (正例):
  P01 示例基线源文件校验通过 (含 sidecar)
  P02 基线 + 变更请求 → 生成结果与冻结期望逐字节一致
  P03 相同输入重复生成 → 字节等价 (确定性不变量)
  P04 生成结果 --prev --summary 校验通过 (版本链 + 摘要一致)
  P05 生成过程不修改旧版输入文件 (哈希前后一致)
  P06 失败请求零输出文件 (fail-closed 生成)

N 组 (负例, 每项断言精确错误码 — 检测力真实):
  版本:  N01 SEQUENCE_NOT_INCREMENTED   N02 VERSION_NOT_INCREMENTED
         N03 BASE_HASH_MISMATCH         N04 PREV_CHAIN_BROKEN
  稳定ID: N05 RULE_ID_INVALID           N06 TARGET_NOT_FOUND
         N07 OP_CONFLICT                N08 SUMMARY_DRIFT (未声明改动)
  重复:  N09 RULE_DUP/DUP_RULE_ID       N10 TERM_DUP
         N11 CATEGORY_COLLISION(内置)   N12 EXAMPLE_DUP
  语言:  N13 LANGUAGE_FIELD_MISSING (zh 无汉字)
  话术:  N14 REPLY_MISSING (block 无恢复话术)
  空值:  N15 NULL_VALUE (term=null)
  Schema: N16 REGEX_DANGEROUS (嵌套量词)  N17 SIDECAR_MISMATCH
  冲突:  N18 CATEGORY_NOT_EMPTY         N19 EXAMPLE_CONFLICT (正负例同串)
  检测力: N20 EXAMPLE_COVERAGE_MISSING (新规则无正例覆盖)
  请求键集: N21 REQUEST_SCHEMA (add_response 未知字段)
  稳定ID重建: N22 OP_CONFLICT (delete_rule 后同 ID 重建 phrase→regex)
         N23 OP_CONFLICT (delete_category 后同码重建)
         N24 OP_CONFLICT (delete_response 后同 ref 重建)
         N25 OP_CONFLICT (示例先增后删同串)
         N26 RULE_OP_IMMUTABLE (prev→next 共享 ID 改 op)
  摘要绑定: N27 SUMMARY_DRIFT (未声明正例漂移)
         N28 SUMMARY_DRIFT (next_source_sha256 篡改全0)
         N29 SUMMARY_DRIFT (next_sequence=999)
         N30 SUMMARY_DRIFT (counts 全 0)
         N31 SUMMARY_INVALID (changes 多余键)

运行: python3 scripts/test_validate_feed_source.py   (仓库内或 Skill 目录内均可)
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
GEN = HERE / "generate_feed_version.py"
VAL = HERE / "validate_feed_source.py"
BASE = SKILL / "examples" / "safety-feed-src.example.json"
REQ = SKILL / "examples" / "change-request.example.json"
EXPECTED_NEXT = SKILL / "examples" / "expected-next-source.example.json"
EXPECTED_SUMMARY = SKILL / "examples" / "expected-change-summary.example.json"

PASS: list[str] = []
FAIL: list[str] = []


def chk(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {name}: {'PASS' if ok else 'FAIL ' + str(detail)[:200]}")


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True)


def gen(base: Path, request: Path, out_dir: Path) -> subprocess.CompletedProcess:
    return run(GEN, "generate", "--base", str(base),
               "--request", str(request), "--out-dir", str(out_dir))


def val(src: Path, *extra: str) -> subprocess.CompletedProcess:
    return run(VAL, str(src), *extra)


def write_base(tmp: Path) -> tuple[Path, Path]:
    """复制基线与 sidecar 到临时目录, 返回 (base, base_copy_for_hash)。"""
    b = tmp / "base.json"
    shutil.copyfile(BASE, b)
    shutil.copyfile(BASE.with_name(BASE.name + ".sha256"),
                    b.with_name(b.name + ".sha256"))
    return b, b


def make_request(tmp: Path, *, ops_override=None, **fields) -> Path:
    req = json.loads(REQ.read_text(encoding="utf-8"))
    base_sha = hashlib.sha256(BASE.read_bytes()).hexdigest()
    req["base_source_sha256"] = base_sha
    for k, v in fields.items():
        req[k] = v
    if ops_override is not None:
        req["operations"] = ops_override
    p = tmp / "req.json"
    p.write_text(json.dumps(req, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def reseal(src: Path) -> None:
    d = hashlib.sha256(src.read_bytes()).hexdigest()
    src.with_name(src.name + ".sha256").write_text(
        f"{d}  {src.name}\n", encoding="utf-8")


def expect_fail(name: str, proc: subprocess.CompletedProcess, code: str) -> None:
    ok = proc.returncode == 1 and f"FAIL {code}" in proc.stderr
    chk(name, ok, f"rc={proc.returncode} stderr={proc.stderr.strip()[:160]}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v26_07_skill_") as td:
        tmp = Path(td)

        # ── 正例 ──
        r = val(BASE)
        chk("P01-base-valid", r.returncode == 0 and "PASS" in r.stdout,
            r.stderr.strip()[:160])

        req = make_request(tmp)
        out1, out2 = tmp / "g1", tmp / "g2"
        base_sha_before = hashlib.sha256(BASE.read_bytes()).hexdigest()
        g1 = gen(BASE, req, out1)
        ok = g1.returncode == 0 and (out1 / "safety-feed-src.s0002.json").is_file()
        chk("P02-generate", ok, g1.stderr.strip()[:160])
        if ok:
            produced = (out1 / "safety-feed-src.s0002.json").read_bytes()
            chk("P02-byte-exact",
                produced == EXPECTED_NEXT.read_bytes(),
                "生成结果与冻结期望不一致")
            chk("P02-summary-exact",
                (out1 / "change-summary.json").read_bytes()
                == EXPECTED_SUMMARY.read_bytes(),
                "变更摘要与冻结期望不一致")
        g2 = gen(BASE, req, out2)
        ok = g2.returncode == 0 and \
            (out2 / "safety-feed-src.s0002.json").read_bytes() == \
            (out1 / "safety-feed-src.s0002.json").read_bytes()
        chk("P03-deterministic", ok, g2.stderr.strip()[:160])

        r = val(out1 / "safety-feed-src.s0002.json",
                "--prev", str(BASE),
                "--summary", str(out1 / "change-summary.json"))
        chk("P04-chain-valid", r.returncode == 0, r.stderr.strip()[:160])

        chk("P05-base-untouched",
            hashlib.sha256(BASE.read_bytes()).hexdigest() == base_sha_before)

        bad_dir = tmp / "noout"
        bad_dir.mkdir()
        req_n06 = make_request(tmp, ops_override=[
            {"op": "delete_rule", "id": "SFR-9999"}])
        g = gen(BASE, req_n06, bad_dir)
        chk("P06-fail-closed-no-output",
            g.returncode == 1 and not any(bad_dir.iterdir()),
            f"rc={g.returncode} files={list(bad_dir.iterdir())}")

        # ── 版本 ──
        expect_fail("N01-seq-not-incremented",
                    gen(BASE, make_request(tmp, next_sequence=1), tmp / "n1"),
                    "SEQUENCE_NOT_INCREMENTED")
        expect_fail("N02-version-not-incremented",
                    gen(BASE, make_request(tmp, next_version="2026.09.02-1"),
                        tmp / "n2"),
                    "VERSION_NOT_INCREMENTED")
        r30 = make_request(tmp)
        req_obj = json.loads(r30.read_text(encoding="utf-8"))
        req_obj["base_source_sha256"] = "0" * 64
        (tmp / "n3req.json").write_text(
            json.dumps(req_obj, ensure_ascii=False, indent=2) + "\n", "utf-8")
        expect_fail("N03-base-hash-mismatch",
                    gen(BASE, tmp / "n3req.json", tmp / "n3"),
                    "BASE_HASH_MISMATCH")
        # N04: 手工构造 prev_source_sha256 错误的下一版
        nxt = json.loads((out1 / "safety-feed-src.s0002.json").read_text("utf-8"))
        nxt["prev_source_sha256"] = "1" * 64
        n4 = tmp / "n4.json"
        n4.write_text(json.dumps(nxt, ensure_ascii=False, indent=2) + "\n", "utf-8")
        reseal(n4)
        expect_fail("N04-prev-chain-broken",
                    val(n4, "--prev", str(BASE)), "PREV_CHAIN_BROKEN")

        # ── 稳定 ID ──
        expect_fail("N05-rule-id-invalid",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "XXX-1", "op": "phrase",
                            "category": "self_harm_expr", "term": "测试短语"}}]),
                        tmp / "n5"), "RULE_ID_INVALID")
        expect_fail("N06-target-not-found",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "modify_rule", "id": "SFR-9999",
                         "patch": {"term": "测试"}}]), tmp / "n6"),
                    "TARGET_NOT_FOUND")
        expect_fail("N07-op-conflict",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "modify_rule", "id": "SFR-0006",
                         "patch": {"term": "最近特别压抑"}},
                        {"op": "modify_rule", "id": "SFR-0006",
                         "patch": {"term": "最近格外压抑"}}]), tmp / "n7"),
                    "OP_CONFLICT")
        # N08: 改动未进摘要 (手改规则 + 重封 sidecar → 摘要漂移)
        drift = json.loads((out1 / "safety-feed-src.s0002.json").read_text("utf-8"))
        for rule in drift["rules"]:
            if rule["id"] == "SFR-0002":
                rule["term"] = "我是大家的负担"
        n8 = tmp / "n8.json"
        n8.write_text(json.dumps(drift, ensure_ascii=False, indent=2) + "\n", "utf-8")
        reseal(n8)
        expect_fail("N08-summary-drift",
                    val(n8, "--prev", str(BASE),
                        "--summary", str(out1 / "change-summary.json")),
                    "SUMMARY_DRIFT")

        # ── 重复/碰撞 ──
        expect_fail("N09-rule-dup",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0001", "op": "phrase",
                            "category": "self_harm_expr", "term": "重复编号短语"}}]),
                        tmp / "n9"), "RULE_DUP")
        expect_fail("N10-term-dup",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0100", "op": "phrase",
                            "category": "self_harm_expr", "term": "我想消失"}}]),
                        tmp / "n10"), "TERM_DUP")
        expect_fail("N11-category-collision",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_category", "category": {
                            "code": "self_harm", "name_zh": "碰撞类别",
                            "name_en": "Collision", "action": "warn",
                            "reply_ref": "warm_suggest"}}]), tmp / "n11"),
                    "CATEGORY_COLLISION")
        expect_fail("N12-example-dup",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_examples", "kind": "positive",
                         "inputs": ["我不想活了"]}], next_sequence=2),
                        tmp / "n12"), "EXAMPLE_DUP")

        # ── 语言字段 ──
        expect_fail("N13-language-field",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_category", "category": {
                            "code": "grief_watch", "name_zh": "Grief Watch",
                            "name_en": "Grief Watch (example)", "action": "warn",
                            "reply_ref": "warm_suggest"}}]), tmp / "n13"),
                    "LANGUAGE_FIELD_MISSING")

        # ── 恢复话术 ──
        expect_fail("N14-reply-missing",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_category", "category": {
                            "code": "grief_watch", "name_zh": "哀伤关注",
                            "name_en": "Grief watch", "action": "block",
                            "reply_ref": "grief_support"}}]), tmp / "n14"),
                    "REPLY_MISSING")

        # ── 危险空值 ──
        expect_fail("N15-null-value",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0101", "op": "phrase",
                            "category": "self_harm_expr", "term": None}}]),
                        tmp / "n15"), "NULL_VALUE")

        # ── Schema/受限正则 ──
        expect_fail("N16-regex-dangerous",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0102", "op": "regex",
                            "category": "self_harm_expr", "term": "(a+)+$"}}]),
                        tmp / "n16"), "REGEX_DANGEROUS")
        tam = tmp / "tamper.json"
        shutil.copyfile(BASE, tam)
        tam.with_name(tam.name + ".sha256").write_text(
            "0" * 64 + f"  {tam.name}\n", encoding="utf-8")
        expect_fail("N17-sidecar-mismatch", val(tam), "SIDECAR_MISMATCH")

        # ── 冲突 ──
        expect_fail("N18-category-not-empty",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "delete_category", "code": "self_harm_expr"}]),
                        tmp / "n18"), "CATEGORY_NOT_EMPTY")
        expect_fail("N19-example-conflict",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0103", "op": "phrase",
                            "category": "low_mood_watch", "term": "压力测试短语"}},
                        {"op": "add_examples", "kind": "positive",
                         "inputs": ["压力测试短语场景"]},
                        {"op": "add_examples", "kind": "negative",
                         "inputs": ["压力测试短语场景"]}], next_sequence=2),
                        tmp / "n19"), "EXAMPLE_CONFLICT")

        # ── 检测力 ──
        expect_fail("N20-coverage-missing",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0104", "op": "phrase",
                            "category": "low_mood_watch",
                            "term": "完全没有正例覆盖的短语"}}]), tmp / "n20"),
                    "EXAMPLE_COVERAGE_MISSING")

        # ── 请求 Schema: 每种操作键集封闭 (未知字段到达写入阶段前拒绝) ──
        expect_fail("N21-request-unknown-field",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_response", "ref": "grief_support",
                         "zh": "听到这些我很心疼。", "en": "I am so sorry.",
                         "unexpected_field": 1}]), tmp / "n21"),
                    "REQUEST_SCHEMA")

        # ── 稳定 ID: 删除后同 ID 重建 (phrase→regex) 不得绕过 ──
        expect_fail("N22-rule-rebuild-same-id",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "delete_rule", "id": "SFR-0001"},
                        {"op": "add_rule", "rule": {
                            "id": "SFR-0001", "op": "regex",
                            "category": "self_harm_expr", "term": "我想消失"}}]),
                        tmp / "n22"),
                    "OP_CONFLICT")

        # ── 类别同类重复触碰: 清空规则 → 删除类别 → 同码重建 ──
        expect_fail("N23-category-rebuild-same-code",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "delete_rule", "id": "SFR-0006"},
                        {"op": "delete_rule", "id": "SFR-0007"},
                        {"op": "delete_rule", "id": "SFR-0009"},
                        {"op": "delete_category", "code": "low_mood_watch"},
                        {"op": "add_category", "category": {
                            "code": "low_mood_watch", "name_zh": "重建类别",
                            "name_en": "Rebuilt watch", "action": "warn",
                            "reply_ref": "warm_suggest"}}]), tmp / "n23"),
                    "OP_CONFLICT")

        # ── 回复同类重复触碰: 删除后同 ref 重建 ──
        expect_fail("N24-response-rebuild-same-ref",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "delete_response", "ref": "warm_suggest"},
                        {"op": "add_response", "ref": "warm_suggest",
                         "zh": "重建话术。", "en": "Rebuilt reply."}]),
                        tmp / "n24"),
                    "OP_CONFLICT")

        # ── 示例同类重复触碰: 先增后删同串 ──
        expect_fail("N25-example-touch-twice",
                    gen(BASE, make_request(tmp, ops_override=[
                        {"op": "add_examples", "kind": "positive",
                         "inputs": ["一条全新的正例句子"]},
                        {"op": "delete_examples", "kind": "positive",
                         "inputs": ["一条全新的正例句子"]}], next_sequence=2),
                        tmp / "n25"),
                    "OP_CONFLICT")

        # ── 稳定 ID: prev→next 共享规则 ID 改操作符 ──
        opflip = json.loads((out1 / "safety-feed-src.s0002.json").read_text("utf-8"))
        for rule in opflip["rules"]:
            if rule["id"] == "SFR-0006":
                rule["op"] = "regex"   # phrase → regex, 内容域仍合法
        n26 = tmp / "n26.json"
        n26.write_text(json.dumps(opflip, ensure_ascii=False, indent=2) + "\n", "utf-8")
        reseal(n26)
        expect_fail("N26-rule-op-immutable",
                    val(n26, "--prev", str(BASE)), "RULE_OP_IMMUTABLE")

        # ── 摘要绑定: 未声明的正例漂移 (旧校验放行, 现必须拒) ──
        exdrift = json.loads((out1 / "safety-feed-src.s0002.json").read_text("utf-8"))
        exdrift["examples_positive"].append("这是未在摘要声明的正例")
        n27 = tmp / "n27.json"
        n27.write_text(json.dumps(exdrift, ensure_ascii=False, indent=2) + "\n", "utf-8")
        reseal(n27)
        expect_fail("N27-summary-example-drift",
                    val(n27, "--prev", str(BASE),
                        "--summary", str(out1 / "change-summary.json")),
                    "SUMMARY_DRIFT")

        # ── 摘要绑定: 版本链/counts/键集篡改 ──
        def tampered_summary(mutate) -> Path:
            s = json.loads((out1 / "change-summary.json").read_text("utf-8"))
            mutate(s)
            p = tmp / f"sum-{id(mutate)}.json"
            p.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", "utf-8")
            return p

        expect_fail("N28-summary-next-sha-tamper",
                    val(out1 / "safety-feed-src.s0002.json", "--prev", str(BASE),
                        "--summary", str(tampered_summary(
                            lambda s: s.__setitem__("next_source_sha256", "0" * 64)))),
                    "SUMMARY_DRIFT")
        expect_fail("N29-summary-next-seq-tamper",
                    val(out1 / "safety-feed-src.s0002.json", "--prev", str(BASE),
                        "--summary", str(tampered_summary(
                            lambda s: s.__setitem__("next_sequence", 999)))),
                    "SUMMARY_DRIFT")
        expect_fail("N30-summary-counts-tamper",
                    val(out1 / "safety-feed-src.s0002.json", "--prev", str(BASE),
                        "--summary", str(tampered_summary(
                            lambda s: s.__setitem__(
                                "counts", {k: 0 for k in s["counts"]})))),
                    "SUMMARY_DRIFT")
        expect_fail("N31-summary-changes-extra-key",
                    val(out1 / "safety-feed-src.s0002.json", "--prev", str(BASE),
                        "--summary", str(tampered_summary(
                            lambda s: s["changes"].__setitem__("rules_bogus", [])))),
                    "SUMMARY_INVALID")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
