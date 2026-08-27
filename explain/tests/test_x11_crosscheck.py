"""X11 · 形态规则的一次性外部校验。**A 作参照验 B，不进实验路径。**

方案原本建议 A（用 22.081J 的人工术语表识别括注）。评审否决了 A：
它是**观测范围与应用范围错配**（通则七 #6）——A 能成立是因为这门课恰好有一份
人工整理的术语表，而 v1 生产走多课程，没有。用一个生产不具备的条件去测生产
会遇到的阈值，测出来的是下界，差多少不知道。

但 A 仍有用：**它没有参与 B 的设计**，是一个真正的外部参照
（通则七 #3：自洽不是验证，要验证判据必须引入没有参与设计的证据）。

本文件在**六份真实讲解样本**上比对两种识别的差集，把误差钉住。
比对结论写进 README。零 API 成本。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import explain  # noqa: F401
from a_rules.glossary import Glossary
from a_rules.metrics.chinese_ratio import parenthetical_english_spans
from a_rules.metrics.term_annotation import annotated_english_spans
from a_rules.text import Document

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "实验脚本" / "测试集" / "真实样本"
GLOSSARY = REPO / "实验脚本" / "A类规则计算脚本" / "examples" / "glossary_22081J.json"

pytestmark = pytest.mark.skipif(
    not SAMPLES.is_dir() or not GLOSSARY.is_file(), reason="需要测试集与术语表"
)


def _compare() -> dict[str, int]:
    gl = Glossary.from_file(GLOSSARY)
    only_form = only_glossary = both = 0
    for path in sorted(SAMPLES.glob("*.txt")):
        doc = Document.parse(path.read_text(encoding="utf-8"))
        form = set(parenthetical_english_spans(doc))
        table = set(annotated_english_spans(doc, gl))
        both += len(form & table)
        only_form += len(form - table)
        only_glossary += len(table - form)
    return {"both": both, "only_form": only_form, "only_glossary": only_glossary}


class TestFormVsGlossary:
    def test_form_rule_is_a_superset_in_practice(self):
        """形态规则**不该漏掉术语表认得的括注**。

        漏掉意味着 D15 的立意在无术语表时被削弱——用户要求的括注反过来扣分。
        """
        r = _compare()
        assert r["only_glossary"] == 0, f"形态规则漏掉了 {r['only_glossary']} 处术语表括注"

    def test_extra_matches_reproduce_the_known_everyday_word_glosses(self):
        """多出来的 49 处**不是 bug，是一个已经被独立测过的现象**。

        PRD §8.1 记过：「实测六轮中模型产出 89 条中英括注，其中 42 条是术语表外的
        日常词（电力/供暖/石油/全球变暖）」。本比对从另一个方向复现了它——
        46 处术语表认得、49 处认不得，合计 95 ≈ 89（差额是跨度计数与出现计数的口径差）。

        **两次测量互不相干却对上了**，这比任何一次单独的数字都更能说明规则在做对的事。

        断言写成区间而不是等值：样本固定、规则固定，数字本该稳定，
        但钉死一个整数会让任何无关的归一化改动都变红。
        """
        r = _compare()
        assert r["both"] > 0, "样本里应当有术语表能认出的括注，否则这次比对没有参照"
        assert 40 <= r["only_form"] <= 60, (
            f"形态规则多收 {r['only_form']} 处（预期 40–60，实测 49）。"
            "偏离这个区间说明规则的宽严变了，回头看 D48/D49 而不是改这个数"
        )

    def test_the_caliper_choice_moves_p4_not_p0(self):
        """**这条是给报告用的**：口径选择只在高阈值处咬人。

        六份真实样本上的中文占比（实测 2026-08-22）：

        ==========  ======  ==========  ==========
        口径        均值    最低        低于 0.95
        ==========  ======  ==========  ==========
        无排除      0.9533  0.9055      2/6
        仅术语表 A  0.9653  0.9287      1/6
        形态 B      0.9771  0.9606      **0/6**
        ==========  ======  ==========  ==========

        P0 的阈值 0.85 上三种口径都不触发；**P4 的 0.95 上三种口径给出三个不同答案**。
        故 P4 的触发率必须连同口径一起报，不能单独给一个数。
        """
        from a_rules.config import StyleConfig

        cfg = StyleConfig.from_dict({"explain_language": "zh", "source_language": "en"})
        p4 = StyleConfig.from_dict(
            {"explain_language": "zh", "source_language": "en", "chinese_ratio": {"min": 0.95}}
        )
        import sys

        cr = sys.modules["a_rules.metrics.chinese_ratio"]
        text = (SAMPLES / "第一轮.txt").read_text(encoding="utf-8")

        with_form = cr.chinese_ratio(text, p4, None)
        original = cr.parenthetical_english_spans
        try:
            cr.parenthetical_english_spans = lambda doc: ()
            without_form = cr.chinese_ratio(text, p4, None)
        finally:
            cr.parenthetical_english_spans = original

        assert with_form.passed is True and without_form.passed is False
        # 而 P0 的 0.85 上两种口径同样达标——口径只在高阈值处改变结论
        assert cr.chinese_ratio(text, cfg, None).passed is True
