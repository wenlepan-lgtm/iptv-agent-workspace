#!/usr/bin/env python3
"""JOCTV Knowledge JSON V1 确定性校验器。

用法（仓库根目录）：
    python3 tools/joctv-hotel-kb-skill/scripts/validate_knowledge_package.py <package.json>

校验内容：字段集、语言隔离（串语，含 zh 字段英文句子混写检测）、回答数（≥3）、
编号唯一、上下文类型、分类取值、命中词/回答重复、噪声（导航/按钮/modal/天气/
未解析占位符）、同包主题冲突。任何 FAIL 输出退出码 1；全部通过输出 OK 摘要并退出码 0。
仅使用 Python 标准库，可在公司工作站直接运行。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

SCHEMA_VERSION = "joctv-hotel-kb-v1"
LOCALE_POLICY = "bilingual_separate"
CATEGORIES = ["衣", "食", "住", "行", "游", "购", "玩", "服务", "其他"]
ROOT_KEYS = ["schema_version", "hotel_id", "package_name", "locale_policy", "entries"]
ENTRY_KEYS = ["id", "category", "topic", "keywords", "answers", "context", "enabled"]
CONTEXT_KEYS = ["time", "location", "directions", "phone", "notes"]
BILINGUAL_KEYS = ["zh", "en"]
MIN_ANSWERS = 3

# 站点抓取噪声：整段等于这些词（忽略大小写与首尾空白）视为噪声。
NOISE_EXACT = {
    "modal title",
    "back to top",
    "了解更多",
    "learn more",
    "查看更多",
    "view all",
    "查看所有",
    "立即预订",
    "reserve",
    "马上加入",
    "join now",
    "预订",
    "联系礼宾部",
    "contact concierge",
    "当地时间",
    "local time",
    "气温",
    "temperature",
}
# 任何字段值中包含这些片段（忽略大小写）即视为噪声。
NOISE_CONTAINS = ["modal title", "back to top"]
# 未解析的站内占位符，如 "[香港]"。
NOISE_PLACEHOLDER_RE = re.compile(r"\[[^\[\]\n]{1,20}\]")
# 独立的温度读数，如 "25℃"。
NOISE_TEMPERATURE_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*[℃°]\s*[CF]?\s*$")
HOTEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# —— zh 字段英文句子混写检测（确定性启发式）——
# 先剔除合法拉丁内容（邮箱/网址），再取"英文词"= 连续 ≥2 个 ASCII 字母的片段
# （单字母如 K11/B1/5A 不算词）。以下任一命中即判串语：
#   R1 同一连续英文词串 ≥4 个词（英文短语/句子主体，酒店名一般 ≤3 词）；
#   R2 同一连续英文词串出现 ≥2 个虚词（英文语法结构）；
#   R3 英文字母总数 > 中文字符数×1.5 且出现任一强虚词（英文占主体）。
LATIN_PERMITTED_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"  # 邮箱
    r"|https?://\S+"  # 网址
)
LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
# 虚词（冠词/系动词/介词/代词/连词等语法词）；强虚词 = 除冠词外的虚词。
FUNCTION_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "on", "at", "in", "of", "to", "for", "with", "from", "by",
    "and", "or", "but", "not", "no", "it", "its", "this", "that",
    "these", "those", "you", "your", "we", "our", "us", "they",
    "their", "please", "will", "can", "do", "does", "has", "have", "had",
}
STRONG_FUNCTION_WORDS = FUNCTION_WORDS - {"a", "an", "the"}


def zh_english_body_reason(text: str) -> str | None:
    """zh 文本主体为英文句子时返回原因字符串，否则返回 None。

    允许：酒店名（≤3 个连续英文词）、缩写（DJ/VIP）、单字母代号（K11/B1）、
    数字、电话、邮箱、网址。应用于 topic/keywords/answers/context 的全部 zh 字段。
    """

    body = LATIN_PERMITTED_RE.sub(" ", text)
    words = LATIN_WORD_RE.findall(body)
    if not words:
        return None
    # R1/R2：按连续英文词串（中间只隔非字母字符以外内容的词序列）分析。
    # 以原文本切分出"英文词串"：连续的 [字母词 + 空白/撇号/连字符] 段。
    for run_text in re.findall(r"[A-Za-z][A-Za-z'\- ]*[A-Za-z]|[A-Za-z]{2,}", body):
        run_words = LATIN_WORD_RE.findall(run_text)
        if len(run_words) >= 4:
            return f"连续英文词串≥4词（疑似英文句子混入zh字段）: {' '.join(run_words)!r}"
        functions = [w for w in run_words if w.lower() in FUNCTION_WORDS]
        if len(functions) >= 2:
            return f"英文词串含≥2个虚词（疑似英文句子混入zh字段）: {' '.join(run_words)!r}"
    # R3：英文字母占主体 + 出现强虚词。
    latin_letters = sum(len(w) for w in words)
    cjk_chars = sum(1 for ch in body if "一" <= ch <= "鿿" or ch == "〇")
    if latin_letters > cjk_chars * 1.5 and any(
        w.lower() in STRONG_FUNCTION_WORDS for w in words
    ):
        return "英文字母数远超中文且含英文虚词（英文为主体）"
    return None


class Issue:
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message

    def __str__(self) -> str:
        return f"FAIL {self.code} {self.path} {self.message}"


def has_cjk(text: str) -> bool:
    return any(
        "㐀" <= ch <= "䶿"
        or "一" <= ch <= "鿿"
        or "豈" <= ch <= "﫿"
        or ch == "〇"
        for ch in text
    )


def has_ascii_letter(text: str) -> bool:
    return any("a" <= ch <= "z" or "A" <= ch <= "Z" for ch in text)


def normalize(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
    return collapsed.casefold()


def _load(raw: str):
    """解析 JSON 并检测重复键（重复键属恶意/损坏输入，直接拒绝）。"""

    def hook(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                raise ValueError(f"duplicate JSON key: {key}")
            seen.add(key)
        return dict(pairs)

    return json.loads(raw, object_pairs_hook=hook)


def check_noise(value: str, path: str, issues: list) -> None:
    stripped = value.strip()
    if not stripped:
        return
    if stripped.casefold() in NOISE_EXACT:
        issues.append(Issue("E_NOISE", path, f"字段值是站点噪声短语: {stripped!r}"))
        return
    lower = stripped.casefold()
    for marker in NOISE_CONTAINS:
        if marker in lower:
            issues.append(Issue("E_NOISE", path, f"字段值包含噪声标记 {marker!r}"))
            return
    if NOISE_PLACEHOLDER_RE.search(stripped):
        issues.append(Issue("E_NOISE", path, f"字段值包含未解析占位符: {stripped!r}"))
        return
    if NOISE_TEMPERATURE_RE.match(stripped):
        issues.append(Issue("E_NOISE", path, f"字段值是天气读数: {stripped!r}"))


def check_language(kind: str, value: str, path: str, issues: list) -> None:
    """kind 为 'zh' 或 'en'；值为非空字符串时检查语言隔离。"""

    stripped = value.strip()
    if not stripped:
        return
    if kind == "zh":
        if not has_cjk(stripped):
            issues.append(Issue("E_LANG_MIX", path, "zh 字段不含中文（疑似把英文塞进中文字段）"))
        else:
            reason = zh_english_body_reason(stripped)
            if reason:
                issues.append(Issue("E_LANG_MIX", path, reason))
    if kind == "en":
        if has_cjk(stripped):
            issues.append(Issue("E_LANG_MIX", path, "en 字段包含中文（疑似把中文塞进英文字段）"))
        elif not has_ascii_letter(stripped):
            issues.append(Issue("E_LANG_MIX", path, "en 字段不含英文字母"))


def _check_bilingual_text(node, path: str, issues: list, allow_empty: bool) -> tuple[str, str]:
    if not isinstance(node, dict) or set(node) != set(BILINGUAL_KEYS):
        issues.append(Issue("E_FIELD_SHAPE", path, f"必须恰好包含键 {BILINGUAL_KEYS}"))
        return "", ""
    zh = node["zh"]
    en = node["en"]
    if not isinstance(zh, str) or not isinstance(en, str):
        issues.append(Issue("E_FIELD_SHAPE", path, "zh/en 值必须是字符串"))
        return "", ""
    check_language("zh", zh, f"{path}.zh", issues)
    check_language("en", en, f"{path}.en", issues)
    check_noise(zh, f"{path}.zh", issues)
    check_noise(en, f"{path}.en", issues)
    if not allow_empty and not zh.strip() and not en.strip():
        issues.append(Issue("E_EMPTY", path, "zh 与 en 不能同时为空"))
    return zh, en


def _check_string_list(value, kind: str, path: str, issues: list) -> list:
    if not isinstance(value, list):
        issues.append(Issue("E_FIELD_SHAPE", path, "必须是字符串数组"))
        return []
    cleaned = []
    seen = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item.strip():
            issues.append(Issue("E_FIELD_SHAPE", item_path, "必须是非空字符串"))
            continue
        check_language(kind, item, item_path, issues)
        check_noise(item, item_path, issues)
        key = normalize(item)
        if key in seen:
            issues.append(Issue("E_DUP", item_path, f"重复项: {item!r}"))
        seen.add(key)
        cleaned.append(item)
    return cleaned


def validate(package) -> list:
    issues: list = []
    if not isinstance(package, dict):
        issues.append(Issue("E_ROOT", "$", "包根必须是 JSON 对象"))
        return issues

    unknown = set(package) - set(ROOT_KEYS)
    if unknown:
        issues.append(Issue("E_ROOT_KEYS", "$", f"出现未知根字段: {sorted(unknown)}"))
    missing = set(ROOT_KEYS) - set(package)
    if missing:
        issues.append(Issue("E_ROOT_KEYS", "$", f"缺少根字段: {sorted(missing)}"))

    if package.get("schema_version") != SCHEMA_VERSION:
        issues.append(Issue("E_SCHEMA_VERSION", "$.schema_version", f"必须是 {SCHEMA_VERSION!r}"))
    hotel_id = package.get("hotel_id")
    if not isinstance(hotel_id, str) or not HOTEL_ID_RE.match(hotel_id):
        issues.append(Issue("E_HOTEL_ID", "$.hotel_id", "必须是 1-64 位字母/数字/下划线/连字符"))
    name = package.get("package_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        issues.append(Issue("E_PACKAGE_NAME", "$.package_name", "必须是非空字符串（≤128 字符）"))
    if package.get("locale_policy") != LOCALE_POLICY:
        issues.append(Issue("E_LOCALE_POLICY", "$.locale_policy", f"必须是 {LOCALE_POLICY!r}"))

    entries = package.get("entries")
    if not isinstance(entries, list) or not entries:
        issues.append(Issue("E_ENTRIES", "$.entries", "必须是非空数组"))
        return issues

    seen_ids = set()
    seen_topics = {"zh": {}, "en": {}}
    for index, entry in enumerate(entries):
        base = f"$.entries[{index}]"
        if not isinstance(entry, dict):
            issues.append(Issue("E_FIELD_SHAPE", base, "条目必须是 JSON 对象"))
            continue
        unknown = set(entry) - set(ENTRY_KEYS)
        if unknown:
            issues.append(Issue("E_ENTRY_KEYS", base, f"条目出现未知字段: {sorted(unknown)}"))
        missing = set(ENTRY_KEYS) - set(entry)
        if missing:
            issues.append(Issue("E_ENTRY_KEYS", base, f"条目缺少字段: {sorted(missing)}"))

        entry_id = entry.get("id")
        if isinstance(entry_id, bool) or not isinstance(entry_id, int) or entry_id < 1:
            issues.append(Issue("E_ID", f"{base}.id", "必须是 ≥1 的整数"))
        elif entry_id in seen_ids:
            issues.append(Issue("E_ID_DUP", f"{base}.id", f"编号重复: {entry_id}"))
        else:
            seen_ids.add(entry_id)

        if entry.get("category") not in CATEGORIES:
            issues.append(
                Issue("E_CATEGORY", f"{base}.category", f"必须是九类之一: {CATEGORIES}")
            )

        topic = entry.get("topic")
        if isinstance(topic, dict) and set(topic) == set(BILINGUAL_KEYS):
            for lang in BILINGUAL_KEYS:
                value = topic[lang]
                if isinstance(value, str) and value.strip():
                    key = normalize(value)
                    if key in seen_topics[lang]:
                        issues.append(
                            Issue(
                                "E_CONFLICT_TOPIC",
                                f"{base}.topic.{lang}",
                                f"主题与条目 {seen_topics[lang][key]} 冲突: {value!r}",
                            )
                        )
                    else:
                        seen_topics[lang][key] = entry_id
        _check_bilingual_text(topic, f"{base}.topic", issues, allow_empty=False)

        keywords = entry.get("keywords")
        keyword_counts = {"zh": 0, "en": 0}
        if isinstance(keywords, dict) and set(keywords) == set(BILINGUAL_KEYS):
            for lang in BILINGUAL_KEYS:
                items = _check_string_list(keywords[lang], lang, f"{base}.keywords.{lang}", issues)
                keyword_counts[lang] = len(items)
        else:
            issues.append(Issue("E_FIELD_SHAPE", f"{base}.keywords", f"必须恰好包含键 {BILINGUAL_KEYS}"))
        if keyword_counts["zh"] == 0 and keyword_counts["en"] == 0:
            issues.append(Issue("E_EMPTY", f"{base}.keywords", "中英命中词不能同时为空"))

        answers = entry.get("answers")
        answer_counts = {"zh": 0, "en": 0}
        if isinstance(answers, dict) and set(answers) == set(BILINGUAL_KEYS):
            for lang in BILINGUAL_KEYS:
                items = _check_string_list(answers[lang], lang, f"{base}.answers.{lang}", issues)
                answer_counts[lang] = len(items)
                if 0 < len(items) < MIN_ANSWERS:
                    issues.append(
                        Issue(
                            "E_ANSWERS_COUNT",
                            f"{base}.answers.{lang}",
                            f"已提供 {lang} 回答但只有 {len(items)} 条，至少需要 {MIN_ANSWERS} 条",
                        )
                    )
        else:
            issues.append(Issue("E_FIELD_SHAPE", f"{base}.answers", f"必须恰好包含键 {BILINGUAL_KEYS}"))
        if answer_counts["zh"] == 0 and answer_counts["en"] == 0:
            issues.append(Issue("E_ANSWERS_EMPTY", f"{base}.answers", "中英回答不能同时为空"))

        context = entry.get("context")
        if isinstance(context, dict):
            unknown = set(context) - set(CONTEXT_KEYS)
            if unknown:
                issues.append(Issue("E_CONTEXT_KEYS", f"{base}.context", f"上下文出现未知字段: {sorted(unknown)}"))
            missing = set(CONTEXT_KEYS) - set(context)
            if missing:
                issues.append(Issue("E_CONTEXT_KEYS", f"{base}.context", f"上下文缺少字段: {sorted(missing)}"))
            for key in ("time", "location", "directions", "notes"):
                _check_bilingual_text(context.get(key), f"{base}.context.{key}", issues, allow_empty=True)
            phone = context.get("phone")
            if not isinstance(phone, str) or len(phone) > 64:
                issues.append(Issue("E_PHONE", f"{base}.context.phone", "电话必须是 ≤64 字符的字符串，没有则留空"))
        else:
            issues.append(Issue("E_FIELD_SHAPE", f"{base}.context", "上下文必须是 JSON 对象"))

        if not isinstance(entry.get("enabled"), bool):
            issues.append(Issue("E_ENABLED", f"{base}.enabled", "必须是布尔值"))

    return issues


def summarize(package) -> list:
    """生成通过后的统计摘要（同时点名缺失语言的条目）。"""

    lines = []
    per_category: dict = {}
    zh_missing, en_missing = [], []
    answers_zh = answers_en = 0
    for entry in package["entries"]:
        per_category[entry["category"]] = per_category.get(entry["category"], 0) + 1
        if not entry["topic"]["zh"].strip() or not entry["answers"]["zh"]:
            zh_missing.append(entry["id"])
        if not entry["topic"]["en"].strip() or not entry["answers"]["en"]:
            en_missing.append(entry["id"])
        answers_zh += len(entry["answers"]["zh"])
        answers_en += len(entry["answers"]["en"])
    total = len(package["entries"])
    bilingual = total - len(set(zh_missing) | set(en_missing))
    lines.append(
        f"OK {SCHEMA_VERSION} entries={total} categories={len(per_category)} "
        f"bilingual={bilingual} zh_missing={len(zh_missing)} en_missing={len(en_missing)} "
        f"answers_zh={answers_zh} answers_en={answers_en}"
    )
    if zh_missing:
        lines.append(f"MISSING_LANG zh entries={zh_missing}")
    if en_missing:
        lines.append(f"MISSING_LANG en entries={en_missing}")
    for category in sorted(per_category):
        lines.append(f"CATEGORY {category} {per_category[category]}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="JOCTV Knowledge JSON V1 校验器")
    parser.add_argument("package", help="待校验的知识包 JSON 路径")
    args = parser.parse_args(argv)

    path = Path(args.package)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL E_IO {path} 无法读取: {exc}")
        return 2
    try:
        package = _load(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL E_JSON {path} JSON 解析失败: {exc}")
        return 1

    issues = validate(package)
    if issues:
        for issue in issues:
            print(issue)
        print(f"REJECTED {path} issues={len(issues)}")
        return 1
    for line in summarize(package):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
