"""max_quote_length 的单元测试。

这是合规约束不是风格偏好（PRD §12.1），所以违规必须留证据——
光有一个数字无法追责。
"""

import json

import pytest

from a_rules.config import StyleConfig
from a_rules.metrics.quote_length import max_quote_length
from a_rules.result import Reason


def cfg(**kw) -> StyleConfig:
    return StyleConfig.from_dict(kw)


class TestActivation:
    @pytest.mark.parametrize("pair", [("zh", "en"), ("en", "en")])
    def test_always_activated(self, pair):
        c = cfg(explain_language=pair[0], source_language=pair[1])
        assert max_quote_length("正文。", c).activated is True

    @pytest.mark.parametrize("text", ["正文没有引用。", "# 标题\n\n正文两段。\n\n又一段。"])
    def test_no_quote_is_zero_not_none(self, text):
        # 有正文但没有引用：是**确定的测量结果**（0 字），不是无法计算
        r = max_quote_length(text, StyleConfig())
        assert r.raw == 0 and r.passed is True and r.reason is None
        assert r.counts_toward_score is True

    @pytest.mark.parametrize("text", ["", "   ", "\n\n", "```\ncode\n```"])
    def test_blank_text_is_uncomputable_not_compliant(self, text):
        # 若此处返回 0/达标，一次生成失败会让本维度成为唯一计分项，
        # 聚合结果显示「全部达标」——空输出在漂移曲线上变成满分点
        r = max_quote_length(text, StyleConfig())
        assert r.raw is None and r.reason == Reason.EMPTY_TEXT
        assert r.counts_toward_score is False


class TestDetection:
    def test_blockquote(self):
        assert max_quote_length("> " + "引" * 30, StyleConfig()).raw == 30

    def test_blockquote_lines_merge_into_one(self):
        text = "> " + "引" * 30 + "\n> " + "文" * 30
        r = max_quote_length(text, StyleConfig())
        assert r.raw == 60 and r.details["quote_count"] == 1

    @pytest.mark.parametrize("open_, close", [("“", "”"), ("「", "」"), ("『", "』"), ('"', '"')])
    def test_quote_marks(self, open_, close):
        assert max_quote_length(f"课件写道{open_}{'引' * 30}{close}。", StyleConfig()).raw == 30

    def test_longest_wins(self):
        text = "「短引用」\n\n> " + "长" * 50 + "\n\n「中等长度的引用内容」"
        assert max_quote_length(text, StyleConfig()).raw == 50

    def test_unclosed_quote_not_counted(self):
        # 未闭合更可能是强调或笔误，不是引用
        assert max_quote_length("他说「这段没有闭合" + "字" * 200, StyleConfig()).raw == 0

    def test_quote_inside_code_block_not_counted(self):
        # 需带正文，否则整篇只有代码块会走 is_blank 分支
        text = "正文一段。\n\n```python\ns = \"" + "x" * 200 + "\"\n```"
        assert max_quote_length(text, StyleConfig()).raw == 0

    def test_quote_inside_inline_code_not_counted(self):
        assert max_quote_length("变量 `s = \"hello world\"` 如上", StyleConfig()).raw == 0

    def test_english_quote_counted_by_word(self):
        r = max_quote_length('课件写道"The learning rate controls the step size"。', StyleConfig())
        assert r.raw == 7

    def test_empty_quote_ignored(self):
        assert max_quote_length("「」和\"\"都是空的", StyleConfig()).raw == 0


class TestThreshold:
    @pytest.mark.parametrize("n, expected", [(99, True), (100, True), (101, False)])
    def test_boundary_at_limit(self, n, expected):
        # PRD §12.1：单段上限 100 字，边界值算达标
        r = max_quote_length("「" + "引" * n + "」", StyleConfig())
        assert r.raw == n and r.passed is expected

    def test_limit_is_configurable(self):
        r = max_quote_length("「" + "引" * 50 + "」", cfg(max_quote_chars=40))
        assert r.passed is False and r.details["limit"] == 40


class TestEvidence:
    def test_violation_carries_location_and_preview(self):
        text = "正文一段。\n\n> " + "引" * 150
        d = max_quote_length(text, StyleConfig()).details
        assert len(d["violations"]) == 1
        v = d["violations"][0]
        assert v["chars"] == 150 and v["line"] == 3 and v["kind"] == "blockquote"
        assert "引" in v["preview"]

    def test_compliant_quotes_not_in_violations(self):
        d = max_quote_length("「" + "引" * 50 + "」", StyleConfig()).details
        assert d["violations"] == [] and d["quote_count"] == 1

    def test_multiple_violations_sorted_by_length(self):
        text = "> " + "甲" * 120 + "\n\n> " + "乙" * 200
        v = max_quote_length(text, StyleConfig()).details["violations"]
        assert [x["chars"] for x in v] == [200, 120]

    def test_serializable(self):
        json.dumps(max_quote_length("「引用」", StyleConfig()).to_dict(), ensure_ascii=False)
