#!/usr/bin/env python3
"""JOCTV 安全规则库下一版本生成器 (joctv-safety-feed-skill, 公司工作站侧)。

从「上一完整版本源文件 + 一份机器可读变更请求」确定性地生成「下一完整
版本源文件 + 变更摘要 + SHA256 sidecar」。输出永远是完整版本 (不是增量
补丁), 相同输入与配置产生字节等价结果; 任何校验失败即中止且不写任何输出
文件 (fail-closed, 旧版输入文件绝不修改)。

源文件格式 joctv-safety-feed-src-v1 (信封 + 内容五键)。内容五键与
p4_admin/tools/safety_feed_build.py 的 SRC 输入完全同构 — 生成结果可以
直接交给该冻结打包工具构建签名包, 本 Skill 不修改 V26_06 已冻结 Schema。

变更请求格式 joctv-safety-feed-change-v1:

    {schema_version, base_source_sha256, base_sequence,
     next_sequence, next_version, reason?, operations: [...]}

操作集合 (封闭; 每种操作的键集封闭, 同一请求内重复触碰同一目标 — 规则 id/
类别码/回复 ref/正负例串 — → OP_CONFLICT, 删除后同 ID/码/ref/串重建同样拒绝):

    add_rule    {rule: {id, op, category, term|terms}}
    modify_rule {id, patch: {term|terms|category}}      (id/op 不可变)
    delete_rule {id}
    add_category    {category: {code,name_zh,name_en,action,reply_ref}}
    modify_category {code, patch: {name_zh|name_en|action|reply_ref}}
    delete_category {code}          (仍被规则引用 → CATEGORY_NOT_EMPTY)
    add_response    {ref, zh, en}
    modify_response {ref, patch: {zh|en}}
    delete_response {ref}           (仍被类别引用 → 最终校验 REPLY_MISSING)
    add_examples    {kind: positive|negative, inputs: [..]}
    delete_examples {kind, inputs: [..]}   (精确匹配, 不存在 → TARGET_NOT_FOUND)

用法:
  generate_feed_version.py import --content BARE.json --sequence N \
      --version V --out SRC.json
  generate_feed_version.py generate --base SRC.json --request REQ.json \
      --out-dir DIR

import: 把无信封的现行规则库 JSON (恰好 categories/rules/responses/
examples_positive/examples_negative 五键) 包装为 sequence=N 的版本链起点。
generate: 输出 safety-feed-src.s{N:04d}.json (+.sha256) 与 change-summary.json。

退出码: 0 = 成功; 1 = FAIL <code>; 2 = 用法错误。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_feed_source import (  # noqa: E402
    CATEGORY_CODE_RE, CONTENT_KEYS, RULE_ID_RE, SRC_SCHEMA, SUMMARY_SCHEMA,
    PLATFORM_BUILTIN_CATEGORY_CODES, RESPONSE_REF_RE, SkillError,
    compute_diff, normalize_text, validate_content, validate_envelope,
    word_tokens)

CHANGE_SCHEMA = "joctv-safety-feed-change-v1"
REQUEST_KEYS = ("schema_version", "base_source_sha256", "base_sequence",
                "next_sequence", "next_version", "reason", "operations")
RULE_KEY_ORDER = ("id", "op", "category", "term", "terms")
CATEGORY_KEY_ORDER = ("code", "name_zh", "name_en", "action", "reply_ref")

# 每种操作的精确键集 (含 op 本身; 与 joctv-safety-feed-change-v1.schema.json
# 的 additionalProperties=false 定义一致, 未知/缺失字段一律 REQUEST_SCHEMA 拒绝)
OP_KEYS = {
    "add_rule": frozenset({"op", "rule"}),
    "modify_rule": frozenset({"op", "id", "patch"}),
    "delete_rule": frozenset({"op", "id"}),
    "add_category": frozenset({"op", "category"}),
    "modify_category": frozenset({"op", "code", "patch"}),
    "delete_category": frozenset({"op", "code"}),
    "add_response": frozenset({"op", "ref", "zh", "en"}),
    "modify_response": frozenset({"op", "ref", "patch"}),
    "delete_response": frozenset({"op", "ref"}),
    "add_examples": frozenset({"op", "kind", "inputs"}),
    "delete_examples": frozenset({"op", "kind", "inputs"}),
}


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _write_sealed(path: Path, obj) -> str:
    data = _dump(obj)
    path.write_text(data, encoding="utf-8")
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def _load_json(path: Path, code: str) -> dict:
    if not path.is_file():
        raise SkillError(code, f"文件不存在: {path}")
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SkillError(code, f"JSON 无法解析: {path} ({e})") from e
    if not isinstance(obj, dict):
        raise SkillError(code, f"必须是 JSON 对象: {path}")
    return obj


def _load_base(path: Path) -> dict:
    src = _load_json(path, "BASE_JSON")
    validate_envelope(src, where=path.name)
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise SkillError("SIDECAR_MISSING",
                         f"上一版源文件缺少 SHA256 sidecar: {path.name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if sidecar.read_text(encoding="utf-8").split()[0].strip() != digest:
        raise SkillError("SIDECAR_MISMATCH",
                         f"上一版 sidecar 哈希与文件字节不一致: {path.name}")
    validate_content(src, where=path.name)
    return src


# ── import: 无信封规则库 → 版本链起点 ─────────────────────────────
def cmd_import(args) -> int:
    content = _load_json(Path(args.content), "CONTENT_JSON")
    missing = [k for k in CONTENT_KEYS if k not in content]
    extra = set(content) - set(CONTENT_KEYS)
    if missing or extra:
        raise SkillError("CONTENT_SCHEMA",
                         f"内容文件必须恰好包含五键 {list(CONTENT_KEYS)}; "
                         f"缺 {missing} 多 {sorted(extra)}")
    out = Path(args.out)
    src = {"schema_version": SRC_SCHEMA,
           "sequence": int(args.sequence),
           "version": str(args.version),
           "prev_source_sha256": None,
           **{k: content[k] for k in CONTENT_KEYS}}
    validate_envelope(src, where=out.name)
    validate_content(src, where=out.name)
    digest = _write_sealed(out, src)
    print(f"imported: {out} sequence={src['sequence']} "
          f"version={src['version']} sha256={digest[:12]}")
    return 0


# ── generate: 上一完整版 + 变更请求 → 下一完整版 ──────────────────
def cmd_generate(args) -> int:
    base_path = Path(args.base)
    base = _load_base(base_path)
    base_digest = hashlib.sha256(base_path.read_bytes()).hexdigest()

    req = _load_json(Path(args.request), "REQUEST_JSON")
    if req.get("schema_version") != CHANGE_SCHEMA:
        raise SkillError("REQUEST_SCHEMA",
                         f"schema_version 必须是 {CHANGE_SCHEMA}")
    unknown = set(req) - set(REQUEST_KEYS)
    if unknown:
        raise SkillError("REQUEST_SCHEMA", f"请求含未声明字段 {sorted(unknown)}")
    if req.get("base_source_sha256") != base_digest:
        raise SkillError("BASE_HASH_MISMATCH",
                         "base_source_sha256 与上一版文件字节哈希不一致")
    if req.get("base_sequence") != base["sequence"]:
        raise SkillError("BASE_SEQUENCE_MISMATCH",
                         f"base_sequence ({req.get('base_sequence')}) 与上一版 "
                         f"sequence ({base['sequence']}) 不一致")
    next_seq = req.get("next_sequence")
    if not isinstance(next_seq, int) or isinstance(next_seq, bool):
        raise SkillError("SEQUENCE_INVALID", "next_sequence 必须是整数")
    if next_seq != base["sequence"] + 1:
        raise SkillError("SEQUENCE_NOT_INCREMENTED",
                         f"next_sequence 必须恰为上一版 +1 "
                         f"({base['sequence']} → {next_seq})")
    next_ver = req.get("next_version")
    if not isinstance(next_ver, str) or not next_ver.strip():
        raise SkillError("REQUEST_SCHEMA", "next_version 必须是非空字符串")
    if next_ver == base["version"]:
        raise SkillError("VERSION_NOT_INCREMENTED", "next_version 与上一版相同")
    ops = req.get("operations")
    if not isinstance(ops, list) or not ops:
        raise SkillError("REQUEST_SCHEMA", "operations 必须是非空列表")

    # 在深拷贝上按序应用; 唯一性/存在性即时校验, 内容域由最终全量校验把关
    next_src = copy.deepcopy({k: base[k] for k in CONTENT_KEYS})
    touched_rules: set[str] = set()
    touched_cats: set[str] = set()
    touched_resp: set[str] = set()
    touched_examples: set[tuple[str, str]] = set()
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise SkillError("REQUEST_SCHEMA", f"operations[{i}] 必须是对象")
        kind = op.get("op")
        where = f"operations[{i}]"
        allowed_keys = OP_KEYS.get(kind)
        if allowed_keys is None:
            raise SkillError("REQUEST_SCHEMA", f"{where}: 未知操作 {str(kind)[:30]}")
        if set(op) != allowed_keys:
            raise SkillError(
                "REQUEST_SCHEMA",
                f"{where}: {kind} 字段必须恰好为 {sorted(allowed_keys)}; "
                f"实际 {sorted(op)}")

        if kind == "add_rule":
            rule = op.get("rule")
            if not isinstance(rule, dict):
                raise SkillError("REQUEST_SCHEMA", f"{where}.rule 必须是对象")
            rid = rule.get("id")
            if not isinstance(rid, str) or not RULE_ID_RE.fullmatch(rid or ""):
                raise SkillError("RULE_ID_INVALID", f"规则 id 非法: {str(rid)[:40]}")
            if rid in touched_rules:
                raise SkillError("OP_CONFLICT",
                                 f"同一请求内重复触碰规则 (含删除后同 ID 重建): {rid}")
            if any(r.get("id") == rid for r in next_src["rules"]):
                raise SkillError("RULE_DUP", f"规则 id 已存在 (含本次新增): {rid}")
            rop = rule.get("op")
            if rop not in ("phrase", "regex", "token", "exception"):
                raise SkillError("OP_UNKNOWN", f"{where}: 未知操作符 {str(rop)[:30]}")
            need_keys = ({"id", "op", "category", "terms"} if rop == "token"
                         else {"id", "op", "category", "term"})
            if set(rule) != need_keys:
                raise SkillError("REQUEST_SCHEMA",
                                 f"{where}: 规则字段必须恰好为 {sorted(need_keys)}; "
                                 f"实际 {sorted(rule)}")
            ordered = {k: rule[k] for k in RULE_KEY_ORDER if k in rule}
            if rop != "exception" and ordered["category"] not in \
                    {c["code"] for c in next_src["categories"]}:
                raise SkillError("CATEGORY_UNKNOWN",
                                 f"{where}: 规则引用未定义类别 {ordered['category']}")
            if rop == "exception" and ordered.get("category") != "*" and \
                    ordered["category"] not in {c["code"] for c in next_src["categories"]}:
                raise SkillError("CATEGORY_UNKNOWN",
                                 f"{where}: 例外作用类别未定义 {ordered['category']}")
            next_src["rules"].append(ordered)
            touched_rules.add(rid)

        elif kind == "modify_rule":
            rid = op.get("id")
            patch = op.get("patch")
            if not isinstance(rid, str) or not isinstance(patch, dict) or not patch:
                raise SkillError("REQUEST_SCHEMA",
                                 f"{where}: 需要 id 与非空 patch")
            if rid in touched_rules:
                raise SkillError("OP_CONFLICT", f"同一请求内重复触碰规则: {rid}")
            target = next((r for r in next_src["rules"] if r.get("id") == rid), None)
            if target is None:
                raise SkillError("TARGET_NOT_FOUND", f"要修改的规则不存在: {rid}")
            if set(patch) - {"term", "terms", "category"}:
                raise SkillError("FIELD_IMMUTABLE",
                                 f"规则仅可修改 term/terms/category (id/op 稳定): {rid}")
            for k, v in patch.items():
                if v is None:
                    raise SkillError("NULL_VALUE", f"危险空值: {rid}.{k}")
                target[k] = v
            touched_rules.add(rid)

        elif kind == "delete_rule":
            rid = op.get("id")
            if not isinstance(rid, str):
                raise SkillError("REQUEST_SCHEMA", f"{where}: 需要 id")
            if rid in touched_rules:
                raise SkillError("OP_CONFLICT", f"同一请求内重复触碰规则: {rid}")
            before = len(next_src["rules"])
            next_src["rules"] = [r for r in next_src["rules"] if r.get("id") != rid]
            if len(next_src["rules"]) == before:
                raise SkillError("TARGET_NOT_FOUND", f"要删除的规则不存在: {rid}")
            touched_rules.add(rid)

        elif kind == "add_category":
            cat = op.get("category")
            if not isinstance(cat, dict):
                raise SkillError("REQUEST_SCHEMA", f"{where}.category 必须是对象")
            code = cat.get("code")
            if not isinstance(code, str) or not CATEGORY_CODE_RE.fullmatch(code or ""):
                raise SkillError("CATEGORY_CODE_INVALID", f"类别码非法: {str(code)[:40]}")
            if code in touched_cats:
                raise SkillError("OP_CONFLICT",
                                 f"同一请求内重复触碰类别 (含删除后同码重建): {code}")
            if code in PLATFORM_BUILTIN_CATEGORY_CODES:
                raise SkillError("CATEGORY_COLLISION",
                                 f"类别码与平台内置类别碰撞 (不可覆盖): {code}")
            if any(c["code"] == code for c in next_src["categories"]):
                raise SkillError("CATEGORY_DUP", f"类别码已存在: {code}")
            if set(cat) != set(CATEGORY_KEY_ORDER):
                raise SkillError("REQUEST_SCHEMA",
                                 f"{where}: 类别字段必须恰好为 {list(CATEGORY_KEY_ORDER)}")
            next_src["categories"].append(
                {k: cat[k] for k in CATEGORY_KEY_ORDER})
            touched_cats.add(code)

        elif kind == "modify_category":
            code, patch = op.get("code"), op.get("patch")
            if not isinstance(code, str) or not isinstance(patch, dict) or not patch:
                raise SkillError("REQUEST_SCHEMA", f"{where}: 需要 code 与非空 patch")
            if code in touched_cats:
                raise SkillError("OP_CONFLICT", f"同一请求内重复触碰类别: {code}")
            target = next((c for c in next_src["categories"]
                           if c.get("code") == code), None)
            if target is None:
                raise SkillError("TARGET_NOT_FOUND", f"要修改的类别不存在: {code}")
            if set(patch) - {"name_zh", "name_en", "action", "reply_ref"}:
                raise SkillError("FIELD_IMMUTABLE",
                                 f"类别仅可修改 name_zh/name_en/action/reply_ref: {code}")
            for k, v in patch.items():
                if v is None:
                    raise SkillError("NULL_VALUE", f"危险空值: {code}.{k}")
                target[k] = v
            touched_cats.add(code)

        elif kind == "delete_category":
            code = op.get("code")
            if not isinstance(code, str):
                raise SkillError("REQUEST_SCHEMA", f"{where}: 需要 code")
            if code in touched_cats:
                raise SkillError("OP_CONFLICT", f"同一请求内重复触碰类别: {code}")
            if not any(c.get("code") == code for c in next_src["categories"]):
                raise SkillError("TARGET_NOT_FOUND", f"要删除的类别不存在: {code}")
            refs = [r.get("id") for r in next_src["rules"]
                    if r.get("category") == code]
            if refs:
                raise SkillError("CATEGORY_NOT_EMPTY",
                                 f"类别 {code} 仍被规则引用, 先删除/迁移规则: "
                                 f"{sorted(str(x) for x in refs)[:5]}")
            next_src["categories"] = [c for c in next_src["categories"]
                                      if c.get("code") != code]
            touched_cats.add(code)

        elif kind == "add_response":
            ref = op.get("ref")
            if not isinstance(ref, str) or not RESPONSE_REF_RE.fullmatch(ref or ""):
                raise SkillError("RESPONSE_REF_INVALID",
                                 f"回复引用名非法: {str(ref)[:40]}")
            if ref in touched_resp:
                raise SkillError("OP_CONFLICT",
                                 f"同一请求内重复触碰回复 (含删除后同 ref 重建): {ref}")
            if ref in next_src["responses"]:
                raise SkillError("RESPONSE_DUP", f"回复引用已存在: {ref}")
            zh, en = op.get("zh"), op.get("en")
            if zh is None or en is None:
                raise SkillError("NULL_VALUE",
                                 f"恢复话术必须双语齐备 (zh/en): {ref}")
            next_src["responses"][ref] = {"zh": zh, "en": en}
            touched_resp.add(ref)

        elif kind == "modify_response":
            ref, patch = op.get("ref"), op.get("patch")
            if not isinstance(ref, str) or not isinstance(patch, dict) or not patch:
                raise SkillError("REQUEST_SCHEMA", f"{where}: 需要 ref 与非空 patch")
            if ref in touched_resp:
                raise SkillError("OP_CONFLICT", f"同一请求内重复触碰回复: {ref}")
            if ref not in next_src["responses"]:
                raise SkillError("TARGET_NOT_FOUND", f"要修改的回复不存在: {ref}")
            if set(patch) - {"zh", "en"}:
                raise SkillError("FIELD_IMMUTABLE", f"回复仅可修改 zh/en: {ref}")
            for k, v in patch.items():
                if v is None:
                    raise SkillError("NULL_VALUE", f"危险空值: {ref}.{k}")
                next_src["responses"][ref][k] = v
            touched_resp.add(ref)

        elif kind == "delete_response":
            ref = op.get("ref")
            if not isinstance(ref, str):
                raise SkillError("REQUEST_SCHEMA", f"{where}: 需要 ref")
            if ref in touched_resp:
                raise SkillError("OP_CONFLICT", f"同一请求内重复触碰回复: {ref}")
            if ref not in next_src["responses"]:
                raise SkillError("TARGET_NOT_FOUND", f"要删除的回复不存在: {ref}")
            del next_src["responses"][ref]
            touched_resp.add(ref)

        elif kind in ("add_examples", "delete_examples"):
            ekind = op.get("kind")
            if ekind not in ("positive", "negative"):
                raise SkillError("REQUEST_SCHEMA",
                                 f"{where}: kind 必须是 positive/negative")
            key = f"examples_{ekind}"
            inputs = op.get("inputs")
            if not isinstance(inputs, list) or not inputs \
                    or not all(isinstance(x, str) and x for x in inputs):
                raise SkillError("REQUEST_SCHEMA",
                                 f"{where}: inputs 必须是非空字符串列表")
            clash = [x for x in inputs if (ekind, x) in touched_examples]
            if clash:
                raise SkillError("OP_CONFLICT",
                                 f"同一请求内重复触碰示例 ({key}): {clash[0][:40]}")
            if kind == "add_examples":
                dup = [x for x in inputs if x in next_src[key]]
                if dup:
                    raise SkillError("EXAMPLE_DUP", f"{key} 内重复示例: {dup[0][:40]}")
                next_src[key].extend(inputs)
            else:
                missing = [x for x in inputs if x not in next_src[key]]
                if missing:
                    raise SkillError("TARGET_NOT_FOUND",
                                     f"{key} 中不存在要删除的示例: {missing[0][:40]}")
                next_src[key] = [x for x in next_src[key] if x not in inputs]
            touched_examples.update((ekind, x) for x in inputs)

        else:   # 不可达 (OP_KEYS 未含的 kind 已在上面拒绝); 保留 fail-closed
            raise SkillError("REQUEST_SCHEMA", f"{where}: 未知操作 {str(kind)[:30]}")

    out_src = {"schema_version": SRC_SCHEMA,
               "sequence": next_seq,
               "version": next_ver,
               "prev_source_sha256": base_digest,
               **next_src}
    validate_envelope(out_src, where="<next>")
    counts = validate_content(out_src, where="<next>")
    # 检测力门: 新增 phrase/token 规则必须有正例覆盖 (与校验器同一实现)
    prev_ids = {r.get("id") for r in base["rules"]}
    pos_norm = [normalize_text(p) for p in out_src["examples_positive"]]
    for r in out_src["rules"]:
        if r.get("id") in prev_ids:
            continue
        if r.get("op") == "phrase":
            term = normalize_text(r.get("term") or "")
            if term and not any(term in p for p in pos_norm):
                raise SkillError("EXAMPLE_COVERAGE_MISSING",
                                 f"新增短语规则没有正例覆盖: {r['id']} "
                                 f"{str(r.get('term'))[:40]}")
        elif r.get("op") == "token":
            need = {normalize_text(t) for t in (r.get("terms") or [])}
            if need and not any(need <= word_tokens(p)
                                for p in out_src["examples_positive"]):
                raise SkillError("EXAMPLE_COVERAGE_MISSING",
                                 f"新增 token 规则没有正例覆盖: {r['id']} "
                                 f"{' '.join(r.get('terms') or [])[:40]}")

    diff = compute_diff(base, out_src)
    declared = {k: sorted(v) for k, v in diff.items()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"safety-feed-src.s{next_seq:04d}.json"
    digest = _write_sealed(out_path, out_src)
    summary = {"schema_version": SUMMARY_SCHEMA,
               "base_sequence": base["sequence"],
               "next_sequence": next_seq,
               "base_version": base["version"],
               "next_version": next_ver,
               "base_source_sha256": base_digest,
               "next_source_sha256": digest,
               "reason": str(req.get("reason") or ""),
               "operations_applied": len(ops),
               "changes": declared,
               "counts": {"categories": counts["categories"],
                          "rules": counts["rules"],
                          "responses": counts["responses"],
                          "examples_positive": counts["examples_positive"],
                          "examples_negative": counts["examples_negative"]}}
    (out_dir / "change-summary.json").write_text(_dump(summary), encoding="utf-8")
    print(f"generated: {out_path} sequence={next_seq} version={next_ver} "
          f"sha256={digest[:12]}")
    print(f"summary: {out_dir / 'change-summary.json'} "
          f"ops={len(ops)} rules={counts['rules']} "
          f"categories={counts['categories']}")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    import argparse
    p = argparse.ArgumentParser(description="JOCTV 安全规则库下一版本生成器")
    sub = p.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--content", required=True)
    imp.add_argument("--sequence", type=int, required=True)
    imp.add_argument("--version", required=True)
    imp.add_argument("--out", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--base", required=True)
    gen.add_argument("--request", required=True)
    gen.add_argument("--out-dir", required=True, dest="out_dir")
    ns = p.parse_args(args)
    try:
        return cmd_import(ns) if ns.cmd == "import" else cmd_generate(ns)
    except SkillError as e:
        print(f"FAIL {e.code}: {e.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
