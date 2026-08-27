"""chinese_ratio 的单元测试。"""

import json
import sys

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

    def test_no_glossary_still_excludes_by_form(self):
        """口径 X11（2026-08-22）：无术语表时改用形态规则，不再退回「不排除」。

        旧行为（`glossary is None` → 一律不排除）会让 v1 多课程场景下的中文占比
        被系统性压低——**那不是模型的行为变化，是分母的口径变化**，
        而它在 0.95 附近足以翻转达标判定。
        """
        r = chinese_ratio(self.TEXT, StyleConfig(), None)
        assert r.raw == 1.0
        assert r.details["excluded_annotation_words"] == 2

    def test_unannotated_english_still_counted(self):
        # 只排除括注里的原文，正文中裸露的英文照常计入
        r = chinese_ratio("热机 heat engine 很重要", StyleConfig(), Glossary.from_list([HEAT]))
        assert r.raw < 1.0


class TestFormBasedExclusion:
    """口径 X11 · 形态规则（不依赖术语表）。"""

    def test_non_glossary_term_also_excluded(self):
        # 形态规则认的是形态，不是术语表条目——这正是它存在的理由
        r = chinese_ratio("涡轮机（turbine）驱动发电机（generator）。", StyleConfig(), None)
        assert r.raw == 1.0

    def test_annotation_containing_han_is_not_excluded(self):
        # 「（见 p12）」是正文补充说明，不是原文标注，本就该计入分母
        r = chinese_ratio("热机的效率（见 p12 figure）很低。", StyleConfig(), None)
        assert r.raw < 1.0

    def test_parenthesis_not_preceded_by_han_is_untouched(self):
        # 不加「括号前必须是汉字」这条，纯英文句子会被整段掏空
        r = chinese_ratio("这句是中文。heat engine (thermal machine) is english.", StyleConfig(), None)
        assert r.details["excluded_annotation_words"] == 0

    def test_numeric_only_annotation_is_ignored(self):
        # 纯数字括注不影响英文词计数，排不排一样，不必纳入
        r = chinese_ratio("这份课件（2010）讲热机。", StyleConfig(), None)
        assert r.details["excluded_annotation_words"] == 0

    def test_known_false_positive_is_registered_not_fixed(self):
        """口径 X12：`（Figure 3）` 这类非术语括注也会被排除。

        **登记，不修。** 误差方向单向（抬高中文占比 = 判定更宽松），
        为消它引入白名单反而要维护一张没有依据的名单。
        本测试的作用是把这个已知误收面钉住——它变了要有人知道。
        """
        r = chinese_ratio("温度曲线（Figure 3）说明了这一点。", StyleConfig(), None)
        assert r.details["excluded_annotation_words"] == 1  # Figure；3 是数字组，不计英文词

    def test_form_exclusion_is_not_a_no_op(self):
        """**反向验证**（通则七 #4）：把形态排除换成平凡实现，数字必须变。

        不变就说明这条排除根本没生效，而它的失效是静默的——
        `chinese_ratio` 照样出一个看起来正常的数。
        """
        # 注意：`a_rules.metrics` 的 __init__ 把同名函数导出了，
        # `import a_rules.metrics.chinese_ratio as mod` 会拿到**函数**而不是模块。
        # 取 sys.modules 是唯一不受这层遮蔽影响的写法。
        mod = sys.modules["a_rules.metrics.chinese_ratio"]

        text = "涡轮机（turbine）驱动发电机（generator）。"
        real = chinese_ratio(text, StyleConfig(), None).raw
        original = mod.parenthetical_english_spans
        try:
            mod.parenthetical_english_spans = lambda doc: ()
            neutered = chinese_ratio(text, StyleConfig(), None).raw
        finally:
            mod.parenthetical_english_spans = original
        assert real == 1.0 and neutered < real


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
