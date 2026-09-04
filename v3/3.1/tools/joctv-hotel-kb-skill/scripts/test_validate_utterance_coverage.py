#!/usr/bin/env python3
"""JOCTV 问法覆盖校验器正反例测试。

用法（仓库根目录）：
    python3 tools/joctv-hotel-kb-skill/scripts/test_validate_utterance_coverage.py
也可在 scripts/ 目录内直接运行。仅使用标准库。
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import validate_utterance_coverage as v  # noqa: E402


def make_pkg() -> dict:
    """最小双语知识包: 两条目 (健身房/早餐), 含交叉子串场景 (套房类省略, 保持最小)。"""
    return {
        "schema_version": "joctv-hotel-kb-v1",
        "hotel_id": "t1",
        "package_name": "测试知识包",
        "locale_policy": "bilingual_separate",
        "entries": [
            {
                "id": 1, "category": "玩",
                "topic": {"zh": "健身房", "en": "Fitness Center"},
                "keywords": {"zh": ["健身房", "健身"], "en": ["gym", "fitness"]},
                "answers": {
                    "zh": ["健身房在2楼，每天6:00到23:00开放。",
                           "2楼健身房每天6:00至23:00开放。",
                           "每天6:00到23:00可以在2楼使用健身房。"],
                    "en": ["The gym is on the 2nd floor, open daily 6:00-23:00.",
                           "Open daily 6:00-23:00, the gym is on the 2nd floor.",
                           "You can use the 2nd-floor gym daily from 6:00 to 23:00."],
                },
                "context": {
                    "time": {"zh": "每天 6:00-23:00", "en": "Daily 6:00-23:00"},
                    "location": {"zh": "2楼", "en": "2nd floor"},
                    "directions": {"zh": "", "en": ""},
                    "phone": "",
                    # notes 只含 policy 事实标记 (着装/要求), 不含 booking 标记
                    # (预约/预订/reserv/book…) — 1.3.0 intent 级事实能力测试锚点
                    "notes": {"zh": "着装要求：请穿运动服装与运动鞋；器械用后请归位。",
                              "en": "Dress code: sportswear and sports shoes; "
                                    "please return equipment after use."},
                },
                "enabled": True,
            },
            {
                "id": 2, "category": "食",
                "topic": {"zh": "早餐", "en": "Breakfast"},
                "keywords": {"zh": ["早餐"], "en": ["breakfast"]},
                "answers": {
                    "zh": ["自助早餐在1楼，每天7:00-10:30供应。",
                           "1楼每天7:00到10:30供应自助早餐。",
                           "每天早上7:00至10:30可在1楼用自助早餐。"],
                    "en": ["Breakfast is served on the 1st floor, 7:00-10:30 daily.",
                           "Served daily 7:00-10:30, breakfast is on the 1st floor.",
                           "You can have breakfast on the 1st floor daily 7:00-10:30."],
                },
                "context": {
                    "time": {"zh": "每天 7:00-10:30", "en": "Daily 7:00-10:30"},
                    "location": {"zh": "1楼", "en": "1st floor"},
                    "directions": {"zh": "", "en": ""},
                    "phone": "", "notes": {"zh": "", "en": ""},
                },
                "enabled": True,
            },
        ],
    }


def utt(i, lang, text, binding="entry", entry_id=1, category="玩", intent="time",
        field="time", term_kind="base"):
    return {"id": i, "lang": lang, "text": text, "binding": binding,
            "entry_id": entry_id, "category": category, "intent": intent,
            "field": field, "term_kind": term_kind}


def make_corpus(pkg_sha: str) -> dict:
    return {
        "schema_version": "joctv-hotel-utterance-v1",
        "skill_version": "1.3.0",
        "hotel_id": "t1",
        "source_package_name": pkg_name(),
        "source_package_sha256": pkg_sha,
        "seed": 20260904,
        "variant_terms": {"1": {"zh": ["健身中心"], "en": ["workout room"]},
                          "2": {"zh": [], "en": []}},
        "counts": {"total": 5, "zh": 3, "en": 2, "entry_bound": 2,
                   "clarify": 2, "no_fact": 1},
        "utterances": [
            utt(1, "zh", "健身房几点开门"),
            # 健身房未发布 directions 事实 → 问"怎么走"属 clarify (缺事实兜底路径)
            utt(2, "zh", "健身中心怎么走", binding="clarify", term_kind="variant",
                intent="directions", field="directions"),
            utt(3, "en", "what time does fitness center open", category="玩"),
            utt(4, "zh", "电影院怎么收费", binding="no_fact", entry_id=None,
                category=None, intent="price", field="price"),
            utt(5, "en", "how do i get to workout room", binding="clarify",
                term_kind="variant", intent="directions", field="directions"),
        ],
    }


def pkg_name() -> str:
    return "测试知识包"


def codes(issues) -> set:
    return {issue.code for issue in issues}


def run_validate(testcase, corpus: dict, pkg: dict = None, keep_sha: bool = False):
    pkg = pkg or make_pkg()
    import hashlib
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pkg.json"
        p.write_text(json.dumps(pkg, ensure_ascii=False), encoding="utf-8")
        corpus = copy.deepcopy(corpus)
        if not keep_sha:
            corpus["source_package_sha256"] = hashlib.sha256(
                p.read_bytes()).hexdigest()
        return v.validate(p, corpus, min_unique=1)


class ValidCorpus(unittest.TestCase):
    def test_minimal_corpus_passes(self):
        self.assertEqual(run_validate(self, make_corpus("x")), [])


class StructureTests(unittest.TestCase):
    def test_bad_schema_version(self):
        c = make_corpus("x")
        c["schema_version"] = "other-v1"
        self.assertIn("E_SCHEMA_VERSION", codes(run_validate(self, c)))

    def test_source_sha_mismatch(self):
        c = make_corpus("0" * 64)
        c["source_package_sha256"] = "f" * 64
        self.assertIn("E_SOURCE_SHA",
                      codes(run_validate(self, c, keep_sha=True)))

    def test_counts_mismatch(self):
        c = make_corpus("x")
        c["counts"]["total"] = 99
        self.assertIn("E_COUNT_MISMATCH", codes(run_validate(self, c)))

    def test_min_unique_gate(self):
        c = make_corpus("x")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pkg.json"
            p.write_text(json.dumps(make_pkg(), ensure_ascii=False), encoding="utf-8")
            import hashlib
            c["source_package_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
            issues = v.validate(p, c, min_unique=100)
        self.assertIn("E_MIN_UNIQUE", codes(issues))

    def test_field_intent_map(self):
        c = make_corpus("x")
        c["utterances"][0]["field"] = "notes"
        self.assertIn("E_FIELD", codes(run_validate(self, c)))


class LanguageTests(unittest.TestCase):
    def test_zh_without_hanzi(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "gym time open"
        self.assertIn("E_LANG_MIX", codes(run_validate(self, c)))

    def test_zh_english_sentence(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "where is the gym in this hotel open"
        self.assertIn("E_LANG_MIX", codes(run_validate(self, c)))

    def test_en_with_hanzi(self):
        c = make_corpus("x")
        c["utterances"][2]["text"] = "what time does 健身房 open"
        self.assertIn("E_LANG_MIX", codes(run_validate(self, c)))


class DedupTests(unittest.TestCase):
    def test_exact_dup_after_normalize(self):
        c = make_corpus("x")
        c["utterances"][1]["text"] = "健身房，几点 开门？"
        c["utterances"][1]["term_kind"] = "base"
        c["utterances"][1]["intent"] = "time"
        c["utterances"][1]["field"] = "time"
        got = codes(run_validate(self, c))
        self.assertIn("E_DUP", got)

    def test_near_dup_edit_distance_one(self):
        c = make_corpus("x")
        c["utterances"][1]["text"] = "健身中心几点开门呢"
        c["utterances"][1]["term_kind"] = "variant"
        c["utterances"][0]["text"] = "健身中心几点开门啊"
        c["utterances"][0]["term_kind"] = "variant"
        self.assertIn("E_NEAR_DUP", codes(run_validate(self, c)))


class BindingTests(unittest.TestCase):
    def test_entry_not_found(self):
        c = make_corpus("x")
        c["utterances"][0]["entry_id"] = 9
        self.assertIn("E_ENTRY_REF", codes(run_validate(self, c)))

    def test_no_signal_word(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "锻炼地方几点开门"
        self.assertIn("E_NO_SIGNAL", codes(run_validate(self, c)))

    def test_foreign_signal_uncovered(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房和早餐哪家好"
        c["utterances"][0]["entry_id"] = 1
        self.assertIn("E_FOREIGN_SIGNAL", codes(run_validate(self, c)))

    def test_foreign_signal_covered_by_longer_term(self):
        """子串覆盖豁免: 长信号词包含短信号词时长词胜出 (网关同口径)。"""
        c = make_corpus("x")
        c["variant_terms"]["1"]["zh"] = ["健身中心"]
        c["utterances"][0]["text"] = "健身中心几点开门"
        c["utterances"][0]["term_kind"] = "variant"
        self.assertNotIn("E_FOREIGN_SIGNAL", codes(run_validate(self, c)))

    def test_variant_kind_without_variant_term(self):
        c = make_corpus("x")
        c["utterances"][1]["text"] = "健身房怎么走"
        c["utterances"][1]["term_kind"] = "variant"
        self.assertIn("E_TERM_KIND", codes(run_validate(self, c)))

    def test_no_fact_leak(self):
        c = make_corpus("x")
        c["utterances"][3]["text"] = "健身房电影院怎么收费"
        self.assertIn("E_NO_FACT_LEAK", codes(run_validate(self, c)))

    def test_no_fact_shape(self):
        c = make_corpus("x")
        c["utterances"][3]["entry_id"] = 1
        self.assertIn("E_NO_FACT_SHAPE", codes(run_validate(self, c)))


class FactBoundaryTests(unittest.TestCase):
    def test_time_value_leak(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房6:00开门吗"
        self.assertIn("E_FACT_LEAK", codes(run_validate(self, c)))

    def test_phone_leak(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房电话2123302288多少"
        self.assertIn("E_FACT_LEAK", codes(run_validate(self, c)))

    def test_price_leak(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房100元一次吗"
        self.assertIn("E_FACT_LEAK", codes(run_validate(self, c)))

    def test_floor_leak(self):
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房在2楼吗"
        self.assertIn("E_FACT_LEAK", codes(run_validate(self, c)))


class VariantMetaTests(unittest.TestCase):
    def test_variant_conflict_with_other_entry(self):
        c = make_corpus("x")
        c["variant_terms"]["1"]["zh"] = ["早餐"]
        got = codes(run_validate(self, c))
        self.assertIn("E_VARIANT_CONFLICT", got)

    def test_variant_dup_with_base(self):
        c = make_corpus("x")
        c["variant_terms"]["1"]["zh"] = ["健身房"]
        self.assertIn("E_VARIANT_DUP", codes(run_validate(self, c)))

    def test_variant_assigned_twice(self):
        c = make_corpus("x")
        c["variant_terms"]["2"]["zh"] = ["健身中心"]
        self.assertIn("E_VARIANT_MULTI", codes(run_validate(self, c)))


class IdTests(unittest.TestCase):
    def test_non_sequential_ids(self):
        c = make_corpus("x")
        c["utterances"][3]["id"] = 5
        self.assertIn("E_ID_SEQ", codes(run_validate(self, c)))


class FactMappingTests(unittest.TestCase):
    """事实感知映射 (1.3.0): entry 绑定要求 intent 级事实已发布, clarify 绑定要求
    intent 级事实未发布 — notes 非空不再同时授权 price/policy/booking。"""

    def test_entry_binding_without_published_fact(self):
        # 早餐条目 (id=2) 无 directions 事实, 却以 entry 绑定问"怎么走" → FAIL
        c = make_corpus("x")
        c["utterances"][0]["text"] = "早餐怎么走"
        c["utterances"][0]["entry_id"] = 2
        c["utterances"][0]["category"] = "食"
        c["utterances"][0]["intent"] = "directions"
        c["utterances"][0]["field"] = "directions"
        c["counts"]["entry_bound"] = 3
        c["counts"]["clarify"] = 1
        self.assertIn("E_INTENT_FACT", codes(run_validate(self, c)))

    def test_clarify_binding_with_published_fact(self):
        # 健身房条目已发布 time 事实, 同一问法改挂 clarify → FAIL (clarify 只承载缺事实问法)
        c = make_corpus("x")
        c["utterances"][0]["binding"] = "clarify"
        c["counts"]["entry_bound"] = 1
        c["counts"]["clarify"] = 3
        self.assertIn("E_CLARIFY_FACT", codes(run_validate(self, c)))

    def test_clarify_must_reference_entry(self):
        c = make_corpus("x")
        c["utterances"][1]["entry_id"] = 9
        self.assertIn("E_ENTRY_REF", codes(run_validate(self, c)))

    def test_price_never_entry_boundable(self):
        # kb-v1 无结构化价格字段: 即使 notes 已发布, price 也不得 entry 绑定
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房怎么收费"
        c["utterances"][0]["intent"] = "price"
        c["utterances"][0]["field"] = "price"
        self.assertIn("E_INTENT_FACT", codes(run_validate(self, c)))

    def test_policy_marker_enables_entry_binding(self):
        # 健身房 notes 含 policy 标记 (着装/要求) → policy intent 可 entry 绑定
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房有什么规定"
        c["utterances"][0]["intent"] = "policy"
        c["utterances"][0]["field"] = "notes"
        self.assertNotIn("E_INTENT_FACT", codes(run_validate(self, c)))

    def test_booking_notes_without_marker_not_entry_fact(self):
        # notes 已发布但**不含 booking 标记** (无 预约/预订/reserv/book…) →
        # booking intent 不得 entry 绑定 (1.3.0: notes 非空不再授权 booking)
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房怎么预约"
        c["utterances"][0]["intent"] = "booking"
        c["utterances"][0]["field"] = "notes"
        self.assertIn("E_INTENT_FACT", codes(run_validate(self, c)))

    def test_booking_clarify_legal_when_marker_missing(self):
        # 同一问法挂 clarify 合法: intent 级 booking 事实未发布 (标记缺),
        # 声明字段 notes 已发布 → 允许同条目同字段诚实直答 (不冒充其他字段)
        c = make_corpus("x")
        c["utterances"][0]["text"] = "健身房怎么预约"
        c["utterances"][0]["binding"] = "clarify"
        c["utterances"][0]["intent"] = "booking"
        c["utterances"][0]["field"] = "notes"
        c["counts"]["entry_bound"] = 1
        c["counts"]["clarify"] = 3
        got = codes(run_validate(self, c))
        self.assertNotIn("E_INTENT_FACT", got)
        self.assertNotIn("E_CLARIFY_FACT", got)
        self.assertNotIn("E_ROUTE_CONSISTENCY", got)


class RouteConsistencyTests(unittest.TestCase):
    """最终路由一致性 (1.3.0, E_ROUTE_CONSISTENCY): 声明 intent/字段与
    route_single_turn 最终 decision 不符必须 FAIL (不是只看 topic 命中)。"""

    def test_entry_declared_field_mismatch(self):
        # 时间问法声明为 location: topic 命中但最终回答字段是 time → FAIL
        c = make_corpus("x")
        c["utterances"][0]["intent"] = "location"
        c["utterances"][0]["field"] = "location"
        self.assertIn("E_ROUTE_CONSISTENCY", codes(run_validate(self, c)))

    def test_clarify_declared_missing_field_must_not_direct_answer(self):
        # clarify (声明字段缺失) 的问法若被独占概述直答 → FAIL; 用已发布 time 事实
        # 的概述问法伪装 clarify: text="健身房怎么样" (overview 形状) 声明 directions
        c = make_corpus("x")
        c["utterances"][1]["text"] = "健身房怎么样"
        c["utterances"][1]["term_kind"] = "base"
        c["utterances"][1]["intent"] = "directions"
        c["utterances"][1]["field"] = "directions"
        got = codes(run_validate(self, c))
        self.assertIn("E_ROUTE_CONSISTENCY", got)

    def test_no_fact_routed_to_direct_answer(self):
        # no_fact 问法含条目信号词且该字段已发布 → 被独占直答, 非安全兜底 →
        # E_ROUTE_CONSISTENCY + E_NO_FACT_LEAK
        c = make_corpus("x")
        c["utterances"][3]["text"] = "健身房几点开门"
        c["utterances"][3]["intent"] = "time"
        c["utterances"][3]["field"] = "time"
        got = codes(run_validate(self, c))
        self.assertIn("E_NO_FACT_LEAK", got)
        self.assertIn("E_ROUTE_CONSISTENCY", got)


class IntentSemanticTests(unittest.TestCase):
    """intent 语义匹配 (1.2.0): text 必须可由所声明 intent 的模板派生。"""

    def test_time_text_labeled_as_location(self):
        c = make_corpus("x")
        c["utterances"][0]["intent"] = "location"
        c["utterances"][0]["field"] = "location"
        self.assertIn("E_INTENT_TEMPLATE", codes(run_validate(self, c)))

    def test_term_from_other_entry_not_derivable(self):
        # "早餐几点开门" 挂条目1: 称呼不在条目1集合 → 语义派生 FAIL (同时有 E_NO_SIGNAL)
        c = make_corpus("x")
        c["utterances"][0]["text"] = "早餐几点开门"
        self.assertIn("E_INTENT_TEMPLATE", codes(run_validate(self, c)))

    def test_no_fact_text_with_entry_intent_shape_ok(self):
        # no_fact 中段只做语言形态校验, 不要求条目称呼
        c = make_corpus("x")
        self.assertNotIn("E_INTENT_TEMPLATE", codes(run_validate(self, c)))


class SchemaContractTests(unittest.TestCase):
    """Schema 与校验器契约一致性 (1.2.0): 两边 required/枚举必须逐项相等。"""

    def setUp(self):
        self.schema = json.loads(
            (SCRIPT_DIR.parent / "schema" / "joctv-hotel-utterance-v1.schema.json")
            .read_text(encoding="utf-8"))

    def test_root_required_matches_validator(self):
        self.assertEqual(set(self.schema["required"]), set(v.ROOT_KEYS))

    def test_utterance_required_matches_validator(self):
        item = self.schema["properties"]["utterances"]["items"]
        self.assertEqual(set(item["required"]), set(v.UTT_KEYS))

    def test_counts_required_matches_validator(self):
        cnt = self.schema["properties"]["counts"]
        self.assertEqual(set(cnt["required"]), set(v.COUNT_KEYS))

    def test_binding_enum_matches_validator_acceptance(self):
        item = self.schema["properties"]["utterances"]["items"]
        self.assertEqual(set(item["properties"]["binding"]["enum"]),
                         {"entry", "clarify", "no_fact"})

    def test_intent_enum_matches_validator_field_map(self):
        item = self.schema["properties"]["utterances"]["items"]
        self.assertEqual(set(item["properties"]["intent"]["enum"]),
                         set(v.INTENT_FIELD.keys()))

    def test_field_enum_matches_validator_field_values(self):
        # 1.3.0: field 枚举 = intent→field 映射的值域 (availability/overview 归一为
        # "overview"; price 独立字段) — schema 与校验器不得漂移
        item = self.schema["properties"]["utterances"]["items"]
        self.assertEqual(set(item["properties"]["field"]["enum"]),
                         set(v.INTENT_FIELD.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
