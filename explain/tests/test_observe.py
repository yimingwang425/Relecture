"""观察项。**全部不进达标判定**，但每一条都要能被反向验证。"""

from __future__ import annotations

import explain  # noqa: F401
from a_rules.config import StyleConfig
from explain.observe import MIN_VERBATIM_CHARS, observe
from explain.review import RuleReviewer


class TestSegmentationEvasion:
    """口径 E10 · 分段规避。

    `paragraph_length` 量的是**平均**段落字数，偏短配置下模型可以靠多敲回车
    平凡满足它。审查看不见这个失败，所以要有观察量。
    """

    LONG = "这是一段用来测试分段规避的中文讲解文字，长度足够让平均段落字数落在偏长的一侧。" * 2

    def test_observes_total_and_count(self):
        o = observe(self.LONG + "\n\n" + self.LONG)
        assert o.paragraph_count == 2 and o.total_chars > 100

    def test_splitting_keeps_total_but_doubles_count(self):
        """**反向验证**：总字数不变、段数翻倍时，两个字段必须动。

        两个字段都不动，就说明观察项没接上——而 E10 的判读规则
        （「触发率近零 + 总字数不变 + 段数上升 = 达标靠分段实现」）
        整个建立在它们会动这件事上。
        """
        half = self.LONG[: len(self.LONG) // 2]
        rest = self.LONG[len(self.LONG) // 2 :]
        one = observe(self.LONG)
        two = observe(half + "\n\n" + rest)
        assert two.paragraph_count == one.paragraph_count * 2
        assert two.total_chars == one.total_chars

    def test_evasion_passes_the_review_which_is_the_point(self):
        """把这个失败钉死：**分段规避确实能通过审查。**

        这条测试断言的是一个**缺陷**而不是一个正确行为。写它是因为
        E10 的判读规则依赖这个缺陷存在；哪天它不再成立（比如补上了
        「总输出长度」判定），这条会红，提醒去改 E10 而不是删测试。
        """
        cfg = StyleConfig.from_dict(
            {"explain_language": "zh", "source_language": "en",
             "paragraph_length": {"min": 15, "max": 40}}
        )
        reviewer = RuleReviewer(cfg)
        body = "这是一段大约三十二个字的讲解文字用来验证分段规避确实可以通过审查判定。"
        chopped = "\n\n".join([body] * 12)
        assert reviewer.review(chopped).passed is True
        assert observe(chopped).total_chars > 300  # 总量并不小，只是被切碎了


class TestVerbatimProbe:
    SOURCE = (
        "A heat engine converts thermal energy into mechanical work, and the maximum "
        "efficiency is bounded by the Carnot limit."
    )

    def test_detects_unquoted_verbatim_copy(self):
        """弱口径的盲区：**不加引号地整段照抄**。

        `quote_length` 按引号猜，这种形态它完全看不见——而 v1 是英文课件 +
        中文讲解，模型偷懒直接贴原文的形态恰好就是这个。
        """
        text = f"下面说明热机效率。{self.SOURCE} 因此效率有上限。"
        o = observe(text, [("s020", self.SOURCE)])
        assert o.max_verbatim_chars >= MIN_VERBATIM_CHARS
        assert o.verbatim_source == "s020"

    def test_paraphrase_is_not_flagged(self):
        text = "热机把热能转成机械能，效率受卡诺极限约束，这是自己的话。"
        assert observe(text, [("s020", self.SOURCE)]).max_verbatim_chars == 0

    def test_short_overlap_below_threshold_ignored(self):
        # 术语与常用短语天然重合，不算照抄
        assert observe("热机 heat engine 很常见。", [("s", "heat engine")]).max_verbatim_chars == 0

    def test_history_slices_are_searched_too(self):
        """比对范围必须含**历史轮**的检索片。

        历史片仍在上下文里，模型照抄的来源不限于本轮。只比本轮会
        **系统性低估，且低估方向不可见**——探针漏掉的照抄，
        与「没有照抄」的输出长得一模一样。
        """
        text = f"讲解如下。{self.SOURCE}"
        this_round_only = observe(text, [("s999", "完全无关的另一段原文内容占位。")])
        with_history = observe(
            text, [("s999", "完全无关的另一段原文内容占位。"), ("s020_prev", self.SOURCE)]
        )
        assert this_round_only.max_verbatim_chars == 0
        assert with_history.max_verbatim_chars >= MIN_VERBATIM_CHARS
        assert with_history.verbatim_source == "s020_prev"

    def test_no_sources_means_no_probe(self):
        # 没有比对对象时如实报 0，而不是报一个看起来正常的数
        assert observe("任意文本").max_verbatim_chars == 0


class TestAnnotationCount:
    """`annotation_count`：v1 的 prompt 里没有任何一句要求写括注。

    这个数决定「P4 到底口径不口径敏感」这个话题是否成立——
    模型若几乎不写括注，三种排除口径就没有分歧，话题自己关掉。
    """

    def test_counts_parenthetical_annotations(self):
        o = observe("热机（heat engine）驱动涡轮机（turbine），效率有上限。")
        assert o.annotation_count == 2

    def test_zero_when_no_annotation(self):
        assert observe("热机驱动涡轮机，效率有上限。").annotation_count == 0

    def test_shares_the_matcher_with_the_exclusion_rule(self):
        """**必须与 D48 共用匹配器。**

        不共用就会出现「排除了 N 处、却报告了 M 次」这种自相矛盾的日志，
        而两个数都看起来正常——没人会去比对它们。
        """
        from a_rules.metrics.chinese_ratio import parenthetical_english_spans
        from a_rules.text import Document

        text = "热机（heat engine）与温度曲线（Figure 3）以及（见 p12 中文）。"
        assert observe(text).annotation_count == len(
            parenthetical_english_spans(Document.parse(text))
        )
