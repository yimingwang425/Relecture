"""avg_paragraph_length 的单元测试。"""

import json

import pytest

from a_rules.config import StyleConfig
from a_rules.metrics.paragraph_length import avg_paragraph_length
from a_rules.result import Reason


def cfg(**kw) -> StyleConfig:
    return StyleConfig.from_dict(kw)


def rng(lo, hi) -> StyleConfig:
    return cfg(paragraph_length={"min": lo, "max": hi})


class TestActivation:
    @pytest.mark.parametrize("pair", [("zh", "en"), ("en", "en"), ("ja", "de")])
    def test_always_activated(self, pair):
        # 常驻维度，与讲解语言无关
        c = cfg(explain_language=pair[0], source_language=pair[1])
        assert avg_paragraph_length("一段话。", c).activated is True


class TestEdgeCases:
    @pytest.mark.parametrize("text", ["", "   ", "# 只有标题", "```\ncode\n```", "| 表 |\n| --- |"])
    def test_no_paragraph(self, text):
        r = avg_paragraph_length(text, StyleConfig())
        assert r.raw is None and r.reason == Reason.NO_PARAGRAPH
        assert r.counts_toward_score is False

    def test_single_paragraph(self):
        r = avg_paragraph_length("一" * 100, rng(60, 180))
        assert r.raw == 100 and r.passed is True

    def test_mean_of_multiple_paragraphs(self):
        r = avg_paragraph_length("一" * 50 + "\n\n" + "二" * 150, rng(60, 180))
        assert r.raw == 100

    def test_soft_wrapped_lines_are_one_paragraph(self):
        r = avg_paragraph_length("一" * 50 + "\n" + "二" * 50, rng(60, 180))
        assert r.details["count"] == 1 and r.raw == 100


class TestBlockKinds:
    def test_consecutive_list_items_merge(self):
        # 口径 D09：逐条 bullet 计段会把均值人为拉低
        text = "\n".join(f"- {'一' * 30}" for _ in range(4))
        r = avg_paragraph_length(text, rng(60, 180))
        assert r.details["count"] == 1 and r.raw == 120

    def test_heading_excluded(self):
        r = avg_paragraph_length("# 标题\n\n" + "一" * 100, rng(60, 180))
        assert r.details["count"] == 1 and r.raw == 100

    def test_table_excluded(self):
        r = avg_paragraph_length("| A | B |\n| - | - |\n\n" + "一" * 100, rng(60, 180))
        assert r.details["count"] == 1

    def test_blockquote_excluded(self):
        # 口径 D12：引用长度由被引原文决定，混进段落均值会污染详细程度维度
        r = avg_paragraph_length("> " + "引" * 200 + "\n\n" + "一" * 100, rng(60, 180))
        assert r.details["count"] == 1 and r.raw == 100

    def test_code_block_excluded_and_does_not_split(self):
        r = avg_paragraph_length("一" * 100 + "\n\n```\nx = 1\n```\n\n" + "二" * 100, rng(60, 180))
        assert r.details["count"] == 2 and r.raw == 100

    def test_inline_code_not_counted(self):
        r = avg_paragraph_length("一" * 100 + " `some_code_here`", rng(60, 180))
        assert r.raw == 100


class TestDistribution:
    def test_details_carry_full_distribution(self):
        text = "\n\n".join(["一" * n for n in (10, 100, 190)])
        d = avg_paragraph_length(text, rng(60, 180)).details
        assert d["median"] == 100 and d["min"] == 10 and d["max"] == 190
        assert d["count"] == 3 and d["lengths"] == [10, 100, 190]
        assert d["total_chars"] == 300

    def test_out_of_range_paragraphs_carry_evidence(self):
        text = "一" * 10 + "\n\n" + "二" * 100
        d = avg_paragraph_length(text, rng(60, 180)).details
        assert len(d["out_of_range"]) == 1
        assert d["out_of_range"][0]["chars"] == 10 and d["out_of_range"][0]["line"] == 1

    def test_mean_inside_range_while_paragraphs_are_not(self):
        # 均值达标不代表每段都达标——分布必须可见
        text = "一" * 10 + "\n\n" + "二" * 190
        r = avg_paragraph_length(text, rng(60, 180))
        assert r.passed is True and len(r.details["out_of_range"]) == 2


class TestThreshold:
    @pytest.mark.parametrize("n, expected", [(60, True), (180, True), (59, False), (181, False)])
    def test_boundaries_are_closed(self, n, expected):
        assert avg_paragraph_length("一" * n, rng(60, 180)).passed is expected

    def test_mixed_language_word_counting(self):
        # 汉字按字符、英文按词（口径 D04）
        r = avg_paragraph_length("学习率 learning rate 设为 0.01", rng(1, 100))
        assert r.raw == 8

    def test_serializable(self):
        json.dumps(avg_paragraph_length("一段话。", StyleConfig()).to_dict(), ensure_ascii=False)
