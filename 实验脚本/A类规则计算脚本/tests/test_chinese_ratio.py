"""chinese_ratio 的单元测试。"""

import json

import pytest

from a_rules.config import StyleConfig
from a_rules.glossary import Glossary
from a_rules.metrics.chinese_ratio import chinese_ratio
from a_rules.result import Reason

HEAT = {"en": "heat engine", "zh": ["热机"]}


def cfg(**kw) -> StyleConfig:
    return StyleConfig.from_dict(kw)


class TestActivation:
    def test_monolingual_not_activated(self):
        r = chinese_ratio("中文", cfg(explain_language="zh", source_language="zh"))
        assert r.activated is False and r.reason == Reason.NOT_ACTIVATED

    def test_non_chinese_explain_language_not_activated(self):
        # 本模块度量的是汉字占比，讲解语言不是中文时这个数字没有意义
        r = chinese_ratio("テキスト", cfg(explain_language="ja", source_language="en"))
        assert r.activated is False

    def test_region_subtag_still_activates(self):
        assert chinese_ratio("中文", cfg(explain_language="zh-CN")).activated is True


class TestEdgeCases:
    @pytest.mark.parametrize("text", ["", "   ", "\n\n\t", "```python\nprint(1)\n```", "$$x^2$$"])
    def test_no_countable_content(self, text):
        # 返回 0.0 会被读成「纯英文，严重不达标」——误差方向危险
        r = chinese_ratio(text, StyleConfig())
        assert r.raw is None and r.passed is None and r.reason == Reason.EMPTY_TEXT
        assert r.counts_toward_score is False

    def test_digits_only_is_undefined(self):
        assert chinese_ratio("123 456 789", StyleConfig()).raw is None

    def test_pure_english(self):
        r = chinese_ratio("This explanation is entirely in English.", StyleConfig())
        assert r.raw == 0.0 and r.passed is False

    def test_pure_chinese(self):
        r = chinese_ratio("这段讲解完全是中文。", StyleConfig())
        assert r.raw == 1.0

    def test_traditional_chinese_counted(self):
        assert chinese_ratio("這段講解是繁體字", StyleConfig()).raw == 1.0

    def test_kana_not_counted_as_chinese(self):
        assert chinese_ratio("ひらがな katakana", StyleConfig()).raw == 0.0


class TestDenominator:
    def test_english_counted_by_word_not_letter(self):
        # 口径 D01：按字母计会让长英文词顶掉大量汉字，占比剧烈抖动
        r = chinese_ratio("中文 gradient descent", StyleConfig())
        assert r.raw == pytest.approx(2 / 4)  # 2 汉字 : 2 英文词

    def test_punctuation_and_digits_excluded_from_denominator(self):
        a = chinese_ratio("中文中文 word", StyleConfig()).raw
        b = chinese_ratio("中文，中文！ word 123", StyleConfig()).raw
        assert a == b

    def test_code_block_does_not_lower_ratio(self):
        plain = chinese_ratio("这是一段纯中文讲解内容。", StyleConfig()).raw
        withcode = chinese_ratio(
            "这是一段纯中文讲解内容。\n\n```python\ndef compute_gradient_descent(): pass\n```",
            StyleConfig(),
        ).raw
        assert plain == withcode == 1.0

    def test_inline_code_and_url_excluded(self):
        r = chinese_ratio("变量 `learning_rate` 见 https://example.com/notes 一文", StyleConfig())
        assert r.raw == 1.0

    def test_heading_and_quote_counted(self):
        r = chinese_ratio("# 标题\n\n> 引用内容\n\n正文", StyleConfig())
        assert r.raw == 1.0


class TestAnnotationExclusion:
    TEXT = "热机（heat engine）把热能转成机械能。"

    def test_annotated_english_excluded_by_default(self):
        # 口径 D15：translate_with_source 下模型被**要求**写括注，
        # 计入分母会让两个维度直接打架
        r = chinese_ratio(self.TEXT, StyleConfig(), Glossary.from_list([HEAT]))
        assert r.raw == 1.0
        assert r.details["excluded_annotation_words"] == 2

    def test_both_calibers_reported(self):
        r = chinese_ratio(self.TEXT, StyleConfig(), Glossary.from_list([HEAT]))
        assert r.details["ratio_excluding_annotations"] == 1.0
        assert r.details["ratio_including_annotations"] < 1.0

    def test_exclusion_can_be_switched_off(self):
        r = chinese_ratio(self.TEXT, cfg(exclude_annotated_terms=False), Glossary.from_list([HEAT]))
        assert r.raw < 1.0 and r.details["excluded_annotation_words"] == 0

    def test_no_glossary_means_no_exclusion(self):
        r = chinese_ratio(self.TEXT, StyleConfig(), None)
        assert r.raw < 1.0

    def test_unannotated_english_still_counted(self):
        # 只排除括注里的原文，正文中裸露的英文照常计入
        r = chinese_ratio("热机 heat engine 很重要", StyleConfig(), Glossary.from_list([HEAT]))
        assert r.raw < 1.0


class TestThreshold:
    @pytest.mark.parametrize("han, words, expected", [(85, 15, True), (95, 5, True), (100, 0, True), (84, 16, False)])
    def test_lower_bound_only(self, han, words, expected):
        text = "中" * han + (" " + " ".join(["word"] * words) if words else "")
        r = chinese_ratio(text, cfg(chinese_ratio={"min": 0.85}))
        assert r.passed is expected

    def test_pure_chinese_passes(self):
        # 不存在「中文太多」这种失败模式（口径 D41）
        assert chinese_ratio("全是中文没有一个英文单词", StyleConfig()).passed is True

    def test_target_string_shows_lower_bound(self):
        assert chinese_ratio("中文", StyleConfig()).target == "≥ 0.85"

    def test_serializable(self):
        json.dumps(chinese_ratio("中文 word", StyleConfig()).to_dict(), ensure_ascii=False)
