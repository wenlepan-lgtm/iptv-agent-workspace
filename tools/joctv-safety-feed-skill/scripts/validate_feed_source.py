#!/usr/bin/env python3
"""JOCTV 安全规则库源文件确定性校验器 (joctv-safety-feed-skill, 公司工作站侧)。

校验对象是 Skill 的版本化完整源文件 (joctv-safety-feed-src-v1):

    {schema_version, sequence, version, prev_source_sha256,
     categories, rules, responses, examples_positive, examples_negative}

前四个是版本信封 (打包工具 p4_admin/tools/safety_feed_build.py 只消费后五个
内容键, 信封键被原样忽略 — 因此本 Skill 不修改 V26_06 已冻结的
joctv.safety-feed.v1 / joctv.safety-rule.v1 Schema)。

校验项 (与任务箱 V26-07 一一对应, 全部确定性、零网络、零第三方依赖):

  版本递增    --prev 时: sequence 必须恰为上一版 +1, version 必须变化;
  稳定 ID     规则 id 形如 SFR-xxx 且全局唯一; --prev 时共享 id 的规则不得
              改变 op (RULE_OP_IMMUTABLE, 改操作符 = 删除 + 新 id 新增);
  摘要绑定    --prev + --summary 时复算 prev→src 差异 (含正负例 added/
              deleted) 与摘要全字段比对: base/next sequence、version、
              SHA256、counts、changes 完整键集, 缺失/多余/漂移一律拒绝;
  语言字段    name_zh/name_en、回复 zh/en 必须双语齐备且不串语;
  重复/冲突   重复规则 id、重复类别码、同类别重复短语/词表/例外、
              正负例同串冲突、与平台内置类别码碰撞;
  恢复话术    block/escalate 类别的 reply_ref 必须能解析到非空双语回复;
  危险空值    任何内容字段出现 null/缺失/空串一律拒绝;
  Schema      字段域、操作符白名单、受限正则安全策略 (运行时同款静态规则);
  SHA256      .sha256 sidecar 必须存在且与文件字节一致; prev_source_sha256
              必须等于上一版文件字节哈希 (版本链);
  检测力      --prev 时新增 phrase/token 规则必须有正例覆盖 (证明新规则
              真的能被示例触发, 而不是不可检测的死规则)。

受限正则策略与平台内置类别码是运行时 (p4_admin/api/safety_rule_feed.py)
约束的镜像副本; scripts/test_feed_skill_e2e.py 在仓库侧断言两处集合一致,
镜像漂移会被 E2E 门拒绝。

用法:
  python3 scripts/validate_feed_source.py SRC.json
  python3 scripts/validate_feed_source.py SRC.json --prev PREV.json
  python3 scripts/validate_feed_source.py SRC.json --prev PREV.json \
      --summary change-summary.json

退出码: 0 = 全部通过; 1 = FAIL <code> (见 stderr)。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

SRC_SCHEMA = "joctv-safety-feed-src-v1"
SUMMARY_SCHEMA = "joctv-safety-feed-change-summary-v1"

# 变更摘要顶层键集 (封闭; 生成器按同一次序输出, 校验器要求恰好齐全)
SUMMARY_KEYS = ("schema_version", "base_sequence", "next_sequence",
                "base_version", "next_version", "base_source_sha256",
                "next_source_sha256", "reason", "operations_applied",
                "changes", "counts")
SUMMARY_COUNT_KEYS = ("categories", "rules", "responses",
                      "examples_positive", "examples_negative")

ENVELOPE_KEYS = ("schema_version", "sequence", "version", "prev_source_sha256",
                 "categories", "rules", "responses",
                 "examples_positive", "examples_negative")
CONTENT_KEYS = ENVELOPE_KEYS[4:]

RULE_ID_RE = re.compile(r"SFR-[A-Za-z0-9_\-]{1,40}")
CATEGORY_CODE_RE = re.compile(r"[a-z][a-z0-9_]{1,40}")
RESPONSE_REF_RE = re.compile(r"[a-z0-9_]{1,64}")
KNOWN_OPS = ("phrase", "regex", "token", "exception")
ACTIONS = ("block", "escalate", "warn")
CJK_RE = re.compile(r"[一-鿿]")
EN_LETTER_RE = re.compile(r"[A-Za-z]")

MAX_TERM_CHARS = 128
MAX_TOKEN_TERMS = 8

# 平台内置类别码 (冻结镜像; 与运行时 _BUILTIN_CATEGORY_CODES 一致,
# 由 scripts/test_feed_skill_e2e.py 逐项断言防漂移)。feed 类别码不得碰撞。
PLATFORM_BUILTIN_CATEGORY_CODES = frozenset({
    "BL-ILLEGAL", "BL-JAILBREAK", "BL-MED-FIN", "BL-MINOR-HARM", "BL-PRIVACY",
    "BL-PROMPT-LEAK", "BL-SELF-HARM", "BL-SYS-OPS", "cross_hotel",
    "external_injection", "illegal", "medical_emergency", "minor_protection",
    "prompt_injection", "secret_leak", "self_harm", "tool_overreach",
})

# ── 运行时同款归一化/词元镜像 (api/safety_platform_baseline.normalize_text) ──
_STRIP_RE = re.compile(r"[\s'’`´]")
_WORD_SPLIT = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    return _STRIP_RE.sub("", s)


def word_tokens(text: str) -> frozenset:
    return frozenset(_WORD_SPLIT.findall(str(text or "").lower()))


# ── 受限正则安全策略 (运行时 validate_restricted_regex 的静态镜像;
#    回溯时间探测属运行时职责, 由消费者最终兜底) ──
_RX_FORBIDDEN_TOKENS = ("(?=", "(?!", "(?<=", "(?<", "(?P", "(?#", "(?i",
                        "(?s", "(?m", "(?x", "(?a", "(?u", "(?L")
_RX_ALLOWED_CHAR = re.compile(r"^[A-Za-z0-9一-鿿()\[\]{}|*+?.^$\\,\-:]*$")
_RX_FORBIDDEN_ESCAPE = re.compile(r"\\(?![-dwsS.]\\w{0,0})")
_NESTED_QUANT = re.compile(r"[*)+}]\s*[+*{]")


class SkillError(Exception):
    """确定性校验失败; code 进错误码表, message 面向使用者。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _check_restricted_regex_static(term: str) -> None:
    if not term or len(term) > 200:
        raise SkillError("REGEX_INVALID", f"受限正则为空或超长 (≤200 字符)")
    if not _RX_ALLOWED_CHAR.fullmatch(term):
        raise SkillError("REGEX_DANGEROUS", f"受限正则含白名单外字符: {term[:60]}")
    for tok in _RX_FORBIDDEN_TOKENS:
        if tok in term:
            raise SkillError("REGEX_DANGEROUS",
                             f"受限正则含禁止构造 ({tok}): {term[:60]}")
    if re.search(r"\\", term) and _RX_FORBIDDEN_ESCAPE.search(
            re.sub(r"\\[-dwsS.]", "", term)):
        raise SkillError("REGEX_DANGEROUS",
                         f"受限正则含未允许的反斜杠转义: {term[:60]}")
    if re.search(r"\\[1-9]", term):
        raise SkillError("REGEX_DANGEROUS",
                         f"受限正则含反向引用: {term[:60]}")
    if _NESTED_QUANT.search(term):
        raise SkillError("REGEX_DANGEROUS",
                         f"受限正则疑似嵌套量词 (灾难性回溯风险): {term[:60]}")
    if re.fullmatch(r"\{(\d+,?)(\d*)\}", term) or re.search(r"\{\d{5,}", term):
        raise SkillError("REGEX_DANGEROUS",
                         f"受限正则量词上限过大: {term[:60]}")
    try:
        re.compile(term)
    except re.error as e:
        raise SkillError("REGEX_INVALID",
                         f"受限正则无法编译: {str(e)[:80]}") from e


def _require_str(value, path: str, *, allow_empty=False) -> str:
    if value is None:
        raise SkillError("NULL_VALUE", f"字段为 null (危险空值): {path}")
    if not isinstance(value, str):
        raise SkillError("TYPE_INVALID", f"字段必须是字符串: {path}")
    if not allow_empty and not value.strip():
        raise SkillError("NULL_VALUE", f"字段为空字符串 (危险空值): {path}")
    return value


def _zh_field(value, path: str) -> str:
    s = _require_str(value, path)
    if not CJK_RE.search(s):
        raise SkillError("LANGUAGE_FIELD_MISSING",
                         f"中文字段不含汉字: {path}")
    return s


def _en_field(value, path: str) -> str:
    s = _require_str(value, path)
    if not EN_LETTER_RE.search(s):
        raise SkillError("LANGUAGE_FIELD_MISSING",
                         f"英文字段不含英文字母: {path}")
    if CJK_RE.search(s):
        raise SkillError("LANGUAGE_FIELD_MISSING",
                         f"英文字段混入汉字 (串语): {path}")
    return s


def _reject_nulls(node, path: str) -> None:
    if node is None:
        raise SkillError("NULL_VALUE", f"出现 null (危险空值): {path}")
    if isinstance(node, dict):
        for k, v in node.items():
            _reject_nulls(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _reject_nulls(v, f"{path}[{i}]")


def load_source(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SkillError("SRC_JSON", f"源文件无法解析为 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise SkillError("SRC_JSON", "源文件必须是 JSON 对象")
    return obj


def check_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise SkillError("SIDECAR_MISSING", f"缺少 SHA256 sidecar: {sidecar.name}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    first = sidecar.read_text(encoding="utf-8").split()[0].strip()
    if first != digest:
        raise SkillError("SIDECAR_MISMATCH",
                         f"sidecar 哈希与文件字节不一致: {path.name}")
    return digest


def validate_content(src: dict, *, where: str) -> dict:
    """内容五键全量校验; 返回统计摘要 (供 CLI/生成器复用)。"""
    cats = src["categories"]
    rules = src["rules"]
    responses = src["responses"]
    pos = src["examples_positive"]
    neg = src["examples_negative"]
    if not isinstance(cats, list) or not cats:
        raise SkillError("SRC_SCHEMA", f"{where}: categories 必须是非空列表")
    if not isinstance(rules, list):
        raise SkillError("SRC_SCHEMA", f"{where}: rules 必须是列表")
    if not isinstance(responses, dict) or not responses:
        raise SkillError("SRC_SCHEMA", f"{where}: responses 必须是非空对象")
    for name, arr in (("examples_positive", pos), ("examples_negative", neg)):
        if not isinstance(arr, list):
            raise SkillError("SRC_SCHEMA", f"{where}: {name} 必须是列表")
    _reject_nulls({"categories": cats, "rules": rules,
                   "responses": responses}, where)

    # ── responses: 恢复话术双语齐备 ──
    for ref, resp in responses.items():
        if not isinstance(ref, str) or not RESPONSE_REF_RE.fullmatch(ref or ""):
            raise SkillError("RESPONSE_REF_INVALID", f"回复引用名非法: {str(ref)[:40]}")
        if not isinstance(resp, dict):
            raise SkillError("SRC_SCHEMA", f"回复必须是对象: {ref}")
        _zh_field(resp.get("zh"), f"responses.{ref}.zh")
        _en_field(resp.get("en"), f"responses.{ref}.en")
        extra = set(resp) - {"zh", "en"}
        if extra:
            raise SkillError("SRC_SCHEMA", f"回复含多余键 {sorted(extra)}: {ref}")

    # ── categories ──
    codes: set[str] = set()
    for i, c in enumerate(cats):
        if not isinstance(c, dict):
            raise SkillError("SRC_SCHEMA", f"类别必须是对象: categories[{i}]")
        code = _require_str(c.get("code"), f"categories[{i}].code")
        if not CATEGORY_CODE_RE.fullmatch(code):
            raise SkillError("CATEGORY_CODE_INVALID", f"类别码非法: {code}")
        if code in PLATFORM_BUILTIN_CATEGORY_CODES:
            raise SkillError("CATEGORY_COLLISION",
                             f"类别码与平台内置类别碰撞 (不可覆盖): {code}")
        if code in codes:
            raise SkillError("CATEGORY_DUP", f"类别码重复: {code}")
        codes.add(code)
        _zh_field(c.get("name_zh"), f"categories[{i}].name_zh")
        _en_field(c.get("name_en"), f"categories[{i}].name_en")
        action = c.get("action")
        if action not in ACTIONS:
            raise SkillError("ACTION_INVALID",
                             f"类别动作非法 (仅 拦截/警示/转人工, 无放行): {str(action)[:20]}")
        reply_ref = c.get("reply_ref")
        if action in ("block", "escalate"):
            if not isinstance(reply_ref, str) or reply_ref not in responses:
                raise SkillError(
                    "REPLY_MISSING",
                    f"类别 {code} 缺少可用的中文/英文恢复话术 (reply_ref)")
        elif reply_ref is not None and reply_ref not in responses:
            raise SkillError("REPLY_REF_UNKNOWN",
                             f"类别 {code} 引用了不存在的回复: {str(reply_ref)[:40]}")
        extra = set(c) - {"code", "name_zh", "name_en", "action", "reply_ref"}
        if extra:
            raise SkillError("SRC_SCHEMA", f"类别含多余键 {sorted(extra)}: {code}")

    # ── rules ──
    ids: set[str] = set()
    phrases: dict[str, set[str]] = {}
    tokens: dict[str, set[tuple]] = {}
    exceptions: set[tuple] = set()
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise SkillError("SRC_SCHEMA", f"规则必须是对象: rules[{i}]")
        rid = _require_str(r.get("id"), f"rules[{i}].id")
        if not RULE_ID_RE.fullmatch(rid):
            raise SkillError("RULE_ID_INVALID", f"规则 id 非法: {rid}")
        if rid in ids:
            raise SkillError("RULE_DUP", f"规则 id 重复: {rid}")
        ids.add(rid)
        op = r.get("op")
        if op not in KNOWN_OPS:
            raise SkillError("OP_UNKNOWN", f"未知操作符: {str(op)[:30]}")
        if op == "exception":
            term = _require_str(r.get("term"), f"rules[{i}].term")
            if not (2 <= len(term) <= MAX_TERM_CHARS):
                raise SkillError("TERM_INVALID", f"例外词非法: {term[:40]}")
            scope = _require_str(r.get("category"), f"rules[{i}].category")
            if scope != "*" and scope not in codes:
                raise SkillError("CATEGORY_UNKNOWN",
                                 f"例外作用类别未定义: {scope}")
            key = (scope, normalize_text(term))
            if key in exceptions:
                raise SkillError("TERM_DUP", f"同类别例外词重复: {term[:40]}")
            exceptions.add(key)
            extra = set(r) - {"id", "op", "category", "term"}
        elif op == "regex":
            term = _require_str(r.get("term"), f"rules[{i}].term")
            _check_restricted_regex_static(term)
            code = _require_str(r.get("category"), f"rules[{i}].category")
            if code not in codes:
                raise SkillError("CATEGORY_UNKNOWN", f"规则引用未定义类别: {code}")
            extra = set(r) - {"id", "op", "category", "term"}
        elif op == "phrase":
            term = _require_str(r.get("term"), f"rules[{i}].term")
            if not (2 <= len(term) <= MAX_TERM_CHARS):
                raise SkillError("TERM_INVALID", f"短语非法: {term[:40]}")
            code = _require_str(r.get("category"), f"rules[{i}].category")
            if code not in codes:
                raise SkillError("CATEGORY_UNKNOWN", f"规则引用未定义类别: {code}")
            norm = normalize_text(term)
            if norm in phrases.setdefault(code, set()):
                raise SkillError("TERM_DUP", f"同类别短语重复: {term[:40]}")
            phrases[code].add(norm)
            extra = set(r) - {"id", "op", "category", "term"}
        else:   # token
            terms = r.get("terms")
            if terms is None:
                raise SkillError("NULL_VALUE",
                                 f"字段为 null (危险空值): rules[{i}].terms")
            if not isinstance(terms, list) or not (1 <= len(terms) <= MAX_TOKEN_TERMS) \
                    or not all(isinstance(t, str) and 1 <= len(t) <= 48 for t in terms):
                raise SkillError("TERM_INVALID", f"token 词表非法: {str(terms)[:60]}")
            code = _require_str(r.get("category"), f"rules[{i}].category")
            if code not in codes:
                raise SkillError("CATEGORY_UNKNOWN", f"规则引用未定义类别: {code}")
            for t in terms:
                # token 词元必须是小写拉丁/数字词 (中文表达用 phrase/regex)
                if not re.fullmatch(r"[a-z0-9]{1,48}", normalize_text(t)):
                    raise SkillError("TERM_INVALID",
                                     f"token 词元必须是小写拉丁/数字词: {t[:40]}")
            key = tuple(sorted(normalize_text(t) for t in terms))
            if key in tokens.setdefault(code, set()):
                raise SkillError("TERM_DUP", f"同类别 token 词表重复: {' '.join(terms)[:40]}")
            tokens[code].add(key)
            extra = set(r) - {"id", "op", "category", "terms"}
        if extra:
            raise SkillError("SRC_SCHEMA", f"规则含多余键 {sorted(extra)}: {rid}")

    # ── examples ──
    for name, arr in (("examples_positive", pos), ("examples_negative", neg)):
        seen: set[str] = set()
        for i, x in enumerate(arr):
            s = _require_str(x, f"{name}[{i}]")
            if not (2 <= len(s) <= 400):
                raise SkillError("EXAMPLE_INVALID", f"示例长度非法 (2–400): {s[:40]}")
            n = normalize_text(s)
            if n in seen:
                raise SkillError("EXAMPLE_DUP", f"{name} 内重复示例: {s[:40]}")
            seen.add(n)
    pos_norm = {normalize_text(x) for x in pos}
    neg_norm = {normalize_text(x) for x in neg}
    clash = pos_norm & neg_norm
    if clash:
        raise SkillError("EXAMPLE_CONFLICT",
                         f"同一输入同时是正例和负例 (回归门必挂): "
                         f"{sorted(clash)[:2]}")

    return {"categories": len(cats), "rules": len(rules),
            "responses": len(responses),
            "examples_positive": len(pos), "examples_negative": len(neg)}


def validate_envelope(src: dict, *, where: str) -> None:
    if src.get("schema_version") != SRC_SCHEMA:
        raise SkillError("SRC_SCHEMA",
                         f"{where}: schema_version 必须是 {SRC_SCHEMA}")
    missing = [k for k in ENVELOPE_KEYS if k not in src]
    if missing:
        raise SkillError("SRC_SCHEMA", f"{where}: 缺少字段 {missing}")
    unknown = set(src) - set(ENVELOPE_KEYS)
    if unknown:
        raise SkillError("SRC_SCHEMA", f"{where}: 含未声明字段 {sorted(unknown)}")
    seq = src.get("sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise SkillError("SEQUENCE_INVALID", f"{where}: sequence 非法")
    if not isinstance(src.get("version"), str) or not src["version"].strip():
        raise SkillError("SRC_SCHEMA", f"{where}: version 必须是非空字符串")
    prev_sha = src.get("prev_source_sha256")
    if prev_sha is not None and (
            not isinstance(prev_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", prev_sha)):
        raise SkillError("SRC_SCHEMA", f"{where}: prev_source_sha256 非法")


def validate_source(path: Path, *, prev_path: Path | None = None,
                    summary_path: Path | None = None) -> dict:
    """完整校验: 信封 + sidecar + 内容 + (可选) 版本链/摘要一致性。"""
    src = load_source(path)
    validate_envelope(src, where=path.name)
    digest = check_sidecar(path)
    counts = validate_content(src, where=path.name)

    prev_src: dict | None = None
    if prev_path is not None:
        prev = load_source(prev_path)
        validate_envelope(prev, where=prev_path.name)
        check_sidecar(prev_path)
        validate_content(prev, where=prev_path.name)
        prev_src = prev
        prev_digest = hashlib.sha256(prev_path.read_bytes()).hexdigest()
        if src.get("prev_source_sha256") != prev_digest:
            raise SkillError("PREV_CHAIN_BROKEN",
                             "prev_source_sha256 与上一版文件字节哈希不一致")
        if src["sequence"] != prev["sequence"] + 1:
            raise SkillError("SEQUENCE_NOT_INCREMENTED",
                             f"sequence 必须恰为上一版 +1 "
                             f"({prev['sequence']} → {src['sequence']})")
        if src["version"] == prev["version"]:
            raise SkillError("VERSION_NOT_INCREMENTED",
                             "version 与上一版相同")

        # 稳定 ID: 共享规则 id 跨版本不得改变 op (改操作符 = 删除 + 新 id 新增)
        prev_ops = {r.get("id"): r.get("op") for r in prev["rules"]
                    if isinstance(r, dict)}
        for r in src["rules"]:
            if not isinstance(r, dict):
                continue
            pid = r.get("id")
            if pid in prev_ops and r.get("op") != prev_ops[pid]:
                raise SkillError(
                    "RULE_OP_IMMUTABLE",
                    f"规则 {pid} 操作符跨版本不可变 "
                    f"({prev_ops[pid]} → {r.get('op')}); "
                    f"如需改操作符请删除后用新 id 新增")

        # 检测力: 新增 phrase/token 规则必须有正例覆盖
        prev_ids = {r.get("id") for r in prev["rules"] if isinstance(r, dict)}
        pos_norm = [normalize_text(p) for p in src["examples_positive"]]
        for r in src["rules"]:
            if not isinstance(r, dict) or r.get("id") in prev_ids:
                continue
            if r.get("op") == "phrase":
                term = normalize_text(r.get("term") or "")
                if term and not any(term in p for p in pos_norm):
                    raise SkillError(
                        "EXAMPLE_COVERAGE_MISSING",
                        f"新增短语规则没有正例覆盖: {r.get('id')} "
                        f"{str(r.get('term'))[:40]}")
            elif r.get("op") == "token":
                need = {normalize_text(t) for t in (r.get("terms") or [])}
                if need and not any(need <= word_tokens(p)
                                    for p in src["examples_positive"]):
                    raise SkillError(
                        "EXAMPLE_COVERAGE_MISSING",
                        f"新增 token 规则没有正例覆盖: {r.get('id')} "
                        f"{' '.join(r.get('terms') or [])[:40]}")

        if summary_path is not None:
            _check_summary(prev_src, src, summary_path,
                           prev_digest=prev_digest, src_digest=digest,
                           counts=counts)

    return {"sequence": src["sequence"], "version": src["version"],
            "sha256": digest, **counts}


def _check_summary(prev: dict, src: dict, summary_path: Path, *,
                   prev_digest: str, src_digest: str, counts: dict) -> None:
    """SUMMARY 绑定: 所有可由 prev/src 复算的字段逐一复算比对。

    覆盖 base/next sequence、version、SHA256、counts、changes 完整键集
    (含正负例 added/deleted); 缺失/多余键 → SUMMARY_INVALID, 值漂移 →
    SUMMARY_DRIFT。operations_applied/reason 无法由 prev/src 复算 (不同请求
    可产生相同结果), 仅做类型门。
    """
    try:
        s = json.loads(summary_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SkillError("SUMMARY_INVALID", f"变更摘要无法解析: {e}") from e
    if not isinstance(s, dict) or s.get("schema_version") != SUMMARY_SCHEMA:
        raise SkillError("SUMMARY_INVALID",
                         f"变更摘要 schema_version 必须是 {SUMMARY_SCHEMA}")
    missing = [k for k in SUMMARY_KEYS if k not in s]
    extra = set(s) - set(SUMMARY_KEYS)
    if missing or extra:
        raise SkillError("SUMMARY_INVALID",
                         f"变更摘要字段必须恰好为 {list(SUMMARY_KEYS)}; "
                         f"缺 {missing} 多 {sorted(extra)}")
    for key, want in (("base_sequence", prev["sequence"]),
                      ("next_sequence", src["sequence"]),
                      ("base_version", prev["version"]),
                      ("next_version", src["version"]),
                      ("base_source_sha256", prev_digest),
                      ("next_source_sha256", src_digest)):
        if s[key] != want:
            raise SkillError(
                "SUMMARY_DRIFT",
                f"变更摘要 {key} 与复算值不一致: "
                f"摘要={str(s[key])[:24]} 实际={str(want)[:24]}")
    stated_counts = s["counts"]
    if not isinstance(stated_counts, dict) or \
            set(stated_counts) != set(SUMMARY_COUNT_KEYS):
        raise SkillError("SUMMARY_INVALID",
                         f"变更摘要 counts 键集必须恰好为 "
                         f"{sorted(SUMMARY_COUNT_KEYS)}")
    for key in SUMMARY_COUNT_KEYS:
        if stated_counts[key] != counts[key]:
            raise SkillError(
                "SUMMARY_DRIFT",
                f"变更摘要 counts.{key} 与复算值不一致: "
                f"摘要={stated_counts[key]} 实际={counts[key]}")
    ops_applied = s["operations_applied"]
    if not isinstance(ops_applied, int) or isinstance(ops_applied, bool) \
            or ops_applied < 1:
        raise SkillError("SUMMARY_INVALID", "operations_applied 必须是正整数")
    if not isinstance(s["reason"], str):
        raise SkillError("SUMMARY_INVALID", "reason 必须是字符串")
    diff = compute_diff(prev, src)
    declared = s["changes"]
    if not isinstance(declared, dict) or set(declared) != set(diff):
        stated = sorted(declared) if isinstance(declared, dict) \
            else type(declared).__name__
        raise SkillError(
            "SUMMARY_INVALID",
            f"changes 键集必须恰好为 {sorted(diff)}; 实际 {stated}")
    for key, actual in diff.items():
        stated_list = declared[key]
        stated_set = set(stated_list) if isinstance(stated_list, list) \
            else None
        if stated_set is None or stated_set != actual:
            shown = sorted(stated_list)[:6] if isinstance(stated_list, list) \
                else str(stated_list)[:40]
            raise SkillError(
                "SUMMARY_DRIFT",
                f"变更摘要与实际差异不一致 ({key}): 摘要={shown} "
                f"实际={sorted(actual)[:6]}")


def compute_diff(prev: dict, src: dict) -> dict[str, set]:
    """确定性差异: 类别/规则/回复的 added/modified/deleted id 集合,
    以及正负例的 added/deleted 字符串集合 (示例为原子串, 无 modified)。"""
    out: dict[str, set] = {}
    p_cats = {c.get("code"): c for c in prev["categories"]}
    s_cats = {c.get("code"): c for c in src["categories"]}
    out["categories_added"] = set(s_cats) - set(p_cats)
    out["categories_deleted"] = set(p_cats) - set(s_cats)
    out["categories_modified"] = {c for c in p_cats.keys() & s_cats.keys()
                                  if p_cats[c] != s_cats[c]}
    p_rules = {r.get("id"): r for r in prev["rules"]}
    s_rules = {r.get("id"): r for r in src["rules"]}
    out["rules_added"] = set(s_rules) - set(p_rules)
    out["rules_deleted"] = set(p_rules) - set(s_rules)
    out["rules_modified"] = {i for i in p_rules.keys() & s_rules.keys()
                             if p_rules[i] != s_rules[i]}
    p_resp = prev["responses"]
    s_resp = src["responses"]
    out["responses_added"] = set(s_resp) - set(p_resp)
    out["responses_deleted"] = set(p_resp) - set(s_resp)
    out["responses_modified"] = {k for k in p_resp.keys() & s_resp.keys()
                                 if p_resp[k] != s_resp[k]}
    for kind in ("positive", "negative"):
        key = f"examples_{kind}"
        out[f"{key}_added"] = set(src[key]) - set(prev[key])
        out[f"{key}_deleted"] = set(prev[key]) - set(src[key])
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    prev = summary = None
    if "--prev" in args:
        i = args.index("--prev")
        prev = Path(args[i + 1])
        del args[i:i + 2]
    if "--summary" in args:
        i = args.index("--summary")
        summary = Path(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: validate_feed_source.py SRC.json "
              "[--prev PREV.json] [--summary SUMMARY.json]", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"FAIL SRC_MISSING: 文件不存在 {path}", file=sys.stderr)
        return 1
    try:
        r = validate_source(path, prev_path=prev, summary_path=summary)
    except SkillError as e:
        print(f"FAIL {e.code}: {e.message}", file=sys.stderr)
        return 1
    print(f"PASS {path.name} sequence={r['sequence']} version={r['version']} "
          f"rules={r['rules']} categories={r['categories']} "
          f"sha256={r['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
