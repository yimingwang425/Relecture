"""F3b · 兜底判断。每条对应 `F3b方案.md` 的一条口径。"""

from __future__ import annotations

import pytest

import explain  # noqa: F401
from explain.context import ContextPack, SkippedPageRef
from explain.gap import (
    VERDICTS,
    GapResult,
    ModelGapChecker,
    NullGapChecker,
    _parse,
    skipped_pages_hint,
)
from explain.prompt import build_user_turn, render_gap_note

from .conftest import SLICE_A, SLICE_B

SKIPPED = tuple(
    SkippedPageRef("MIT14_129S25_lec02.pdf", i, "image_only", "内容全在图里，解析服务读不了图")
    for i in (33, 34, 35, 36)
)


def _ctx(skipped=()) -> ContextPack:
    return ContextPack(question="q", slices=(SLICE_A, SLICE_B), skipped_pages=skipped)


class FakeGapModel:
    """按预设返回文本的假模型。不假装自己是判断器。"""

    name = "fake:claude-haiku-4-5"
    params: dict = {}

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    def complete(self, system, messages):
        from explain.llm import Completion, Usage

        self.calls.append(messages[-1]["content"])
        return Completion(self.text, Usage(input_tokens=100, output_tokens=30), 900.0, self.name)

    def request_signature(self, system, messages):
        return "x"


class TestOutputContract:
    """口径 G01 · 取值不合法即判无效样本，不做「就近解释」。"""

    def test_parses_plain_json(self):
        assert _parse('{"verdict":"partial","reason":"提了成本角度没给数字"}') == (
            "partial", "提了成本角度没给数字")

    def test_parses_fenced_json(self):
        # 实测 haiku 会加 ```json 围栏
        v, _ = _parse('```json\n{"verdict": "insufficient", "reason": "无关"}\n```')
        assert v == "insufficient"

    @pytest.mark.parametrize("bad", [
        '{"verdict":"not_mentioned"}',      # 旧词表，不在取值域内
        '{"verdict":"COVERED"}',            # 大小写不同即不合法
        '{"verdict":"covered 大概"}',        # 「就近解释」会把它读成 covered
        "片段不够回答这个问题",                # 没有 JSON
        '{"reason":"忘了给 verdict"}',
        "",
    ])
    def test_invalid_output_is_not_salvaged(self, bad):
        """**不做就近解释。**

        「就近解释」会把一个坏输出洗成好输出，而且不留痕迹——
        下游看到的是一个正常的类别，没人会知道模型其实没答对格式。
        """
        assert _parse(bad) == (None, None)

    def test_invalid_result_keeps_evidence_and_is_flagged(self):
        checker = ModelGapChecker(FakeGapModel("我觉得应该够吧"))
        r = checker.check("q", _ctx())
        assert r.verdict is None and r.is_invalid
        assert r.invalid_raw == "我觉得应该够吧"
        assert r.implementation == "model"

    def test_null_checker_is_distinguishable_from_invalid(self):
        """`verdict is None` 有两种含义，必须能分开。

        不分开的话，F3b 上线后无法回答「兜底判断从哪个批次开始真的在跑」。
        """
        null = NullGapChecker().check("q", _ctx())
        invalid = ModelGapChecker(FakeGapModel("坏输出")).check("q", _ctx())
        assert null.verdict is invalid.verdict is None
        assert null.is_invalid is False and invalid.is_invalid is True

    def test_no_confidence_score(self):
        """口径 G05：不输出置信度。

        未校准的置信度会被拿去设阈值——凭空造一个占位阈值（§3.2）。
        """
        r = ModelGapChecker(FakeGapModel('{"verdict":"covered"}')).check("q", _ctx())
        assert not hasattr(r, "confidence")
        assert "confidence" not in r.to_dict()


class TestRuleChannel:
    """口径 G03 · 第三类走规则。**原定的交集恒为空集，机制已换。**"""

    @pytest.mark.parametrize("verdict", ["insufficient", "partial"])
    def test_fires_on_any_non_covered_verdict(self, verdict):
        """口径 G03a：门槛从 `insufficient` 放宽到「非 covered」。

        校准实测：两道 `unreadable_page` 题里，Q029 判 `partial`、Q030 判
        `insufficient`。**Q029 判 partial 是合理的**——检索回来的邻近片段确实
        提到了那个主题，而模型看不到跳过页的内容，没有依据判 `insufficient`。
        门槛设在 `insufficient` 上，等于把通道建立在一个模型无法可靠给出的
        判断上，2 题漏 1 题——形同虚设（通则七 #5）。
        """
        assert skipped_pages_hint(_ctx(SKIPPED), verdict) == SKIPPED

    @pytest.mark.parametrize("verdict", ["covered", None])
    def test_silent_when_slices_suffice_or_no_judgement(self, verdict):
        # 片段够回答时再提「有些页读不了」是无关噪声；没判断时不该凭空提示
        assert skipped_pages_hint(_ctx(SKIPPED), verdict) == ()

    def test_silent_when_deck_has_no_skipped_pages(self):
        # 22.081J 39 页全可读——这条限定语不该出现
        assert skipped_pages_hint(_ctx(()), "insufficient") == ()

    def test_the_original_intersection_would_never_fire(self):
        """**钉住那个被否掉的机制为什么不成立。**

        `image_only` 页不产生切片，所以不可能出现在检索命中页里。
        这条断言演示的是：按原机制算，交集永远是空的——
        它看起来在做兜底，实际不防任何东西（通则七 #5）。
        """
        ctx = _ctx(SKIPPED)
        retrieved_pages = {p for s in ctx.slices for p in s.page_indices}
        skipped_pages = {p.index for p in ctx.skipped_pages}
        assert retrieved_pages & skipped_pages == set()
        # 而替代机制会触发
        assert skipped_pages_hint(ctx, "insufficient") != ()


class TestPromptInjection:
    """判断一 · 标注形态，不做门。给事实，不给模板文案。"""

    def test_covered_injects_nothing(self):
        assert render_gap_note(GapResult(verdict="covered")) == ""

    def test_invalid_injects_nothing(self):
        """无效样本不注入。**宁可不提示，也不要提示一个错的。**"""
        assert render_gap_note(GapResult(verdict=None, implementation="model")) == ""

    def test_insufficient_scopes_the_claim_to_the_slices(self):
        """口径 G02 在 prompt 侧的落点：能说「片段里没有」，不能说「课件里没有」。

        `multipage` 最好只到 6/9——让模型说「课件没讲」就是让它替检索背书。
        """
        note = render_gap_note(GapResult(verdict="insufficient"))
        assert "片段里" in note and "不能说「课件里没有」" in note

    def test_skipped_hint_says_read_not_absent(self):
        """PRD §6.2 / §10.1：`image_only` 的正确说法是「读不了」而非「没提到」。"""
        note = render_gap_note(GapResult(verdict="insufficient", skipped_hint=SKIPPED))
        assert "第 33、34、35、36 页" in note
        assert "我读不了图" in note and "而不是「课件里没有提到」" in note

    def test_note_lands_in_the_user_turn_not_the_system(self):
        """判断结果每轮不同，放 system 会让缓存每轮失效。"""
        turn = build_user_turn(_ctx(), "问题", GapResult(verdict="insufficient"))
        assert "【片段完整性提示】" in turn

    def test_no_gap_means_no_note(self):
        assert "片段完整性提示" not in build_user_turn(_ctx(), "问题", None)

    def test_partial_and_insufficient_read_differently(self):
        """**反向验证**：两个类别必须注入不同的话。

        若两者注入同一段文字，这个分类就没有在区分任何东西。
        """
        a = render_gap_note(GapResult(verdict="insufficient"))
        b = render_gap_note(GapResult(verdict="partial"))
        assert a and b and a != b


class TestCostAndLatencyAccounting:
    """口径 G08 · 判断成本单独记账，但计入单轮成本。"""

    def test_gap_cost_and_latency_recorded(self):
        r = ModelGapChecker(FakeGapModel('{"verdict":"covered","reason":"有"}')).check("q", _ctx())
        d = r.to_dict()
        assert d["gap_latency_ms"] == 900.0 and d["gap_model"] == "fake:claude-haiku-4-5"
        assert d["gap_cost_usd"] > 0  # 假模型也走通成本核算链路

    def test_failed_judgement_cost_is_unknown_not_zero(self):
        """口径 F73 / F75：判断没跑完时 `gap_cost_usd` 是 **null**。

        `cost_usd` 属性仍是 0.0（要被加进单轮总数），对外那一格不是。
        判据两端不同源：期望值是字面量 None，不是从 `cost_usd` 推出来的。
        """
        failed = GapResult(verdict=None, implementation="error", error="TimeoutError: x")
        assert failed.cost_usd == 0.0
        assert failed.to_dict()["gap_cost_usd"] is None
        # 反方向：NullGapChecker 那条（没做判断）确实没花钱，仍是 0.0，不被一起改成 null。
        assert NullGapChecker().check("q", _ctx()).to_dict()["gap_cost_usd"] == 0.0

    def test_round_latency_includes_the_extra_hop(self):
        """判断与首版生成**并行**（口径 F93），关键路径是两者较慢的那个。

        判断 1500 ms、生成近乎 0 ms 时，用户等的是 1500 ms——
        `round_latency_ms` 必须含它（不能因为并行就把它算没了），
        另出 `generation_latency_ms` 以便分离增量。

        2026-10-07 前这条断的是「总和」（串行）。改的是口径，不是放宽：
        下面 `test_parallel_takes_the_max_not_the_sum` 钉住了「不再是总和」那一半。
        """
        from explain.fake import FakeExplainModel
        from explain.pipeline import Trajectory, run_round
        from explain.retrieval_adapter import InMemoryRetriever
        from explain.review import RuleReviewer
        from explain.style_prompt import render_style_rules
        from a_rules.config import StyleConfig

        cfg = StyleConfig.from_dict({"explain_language": "zh", "source_language": "en"})

        class Slow(NullGapChecker):
            def check(self, question, context):
                return GapResult(verdict="covered", latency_ms=1500.0,
                                 cost_usd=0.002, implementation="model")

        r = run_round(
            round_index=1, question="q", trajectory=Trajectory(),
            retriever=InMemoryRetriever(default_slices=(SLICE_A,)),
            model=FakeExplainModel(), reviewer=RuleReviewer(cfg),
            style_rules=render_style_rules(cfg), course_label="t",
            review_enabled=True, gap_checker=Slow(),
        )
        first = r.attempts[0].completion.latency_ms
        assert r.latency_ms == pytest.approx(max(1500.0, first) + r.generation_latency_ms - first)
        assert r.latency_ms >= 1500.0
        assert r.cost_usd > sum(a.completion.cost for a in r.attempts)

    def test_parallel_takes_the_max_not_the_sum(self):
        """**反向一半**：判断 1500、生成 2000 → 关键路径 2000，不是 3500。"""
        from explain.llm import Completion, Usage
        from explain.pipeline import Attempt, RoundResult
        from explain.context import ContextPack
        from explain.observe import observe

        class _R:
            passed = True
            latency_ms = 0.0   # 规则审查不花时间（风格审查 agent 的耗时另算，见 Review.latency_ms）

        gen = Completion(text="x", usage=Usage(), latency_ms=2000.0, model="fake:m")
        gap = GapResult(verdict="covered", latency_ms=1500.0, implementation="model")
        r = RoundResult(round_index=1, question="q", context=ContextPack(question="q", slices=()),
                        gap=gap, attempts=(Attempt(0, gen, _R()),), final_text="x",
                        accepted=True, request_signature="s",
                        observations=observe("x", []), system_text="")
        assert r.latency_ms == pytest.approx(2000.0)
        # 作废了投机那一版时：max(判断, 投机) + 全部正式各版
        spec = Completion(text="", usage=Usage(), latency_ms=1500.0, model="fake:m",
                          stop_reason="cancelled")
        r2 = RoundResult(round_index=1, question="q", context=ContextPack(question="q", slices=()),
                         gap=gap, attempts=(Attempt(0, gen, _R()),), final_text="x",
                         accepted=True, request_signature="s",
                         observations=observe("x", []), system_text="",
                         discarded=(spec,))
        assert r2.latency_ms == pytest.approx(1500.0 + 2000.0)


class TestVerdictVocabulary:
    def test_only_three_values(self):
        assert VERDICTS == ("covered", "insufficient", "partial")

    def test_in_figure_is_not_a_model_output(self):
        """「内容在图里」**不在模型的取值域里**——它走规则通道。

        给了模型这个选项，它就会开始猜「是不是在图里」，而它没有依据：
        跳过页的内容它一个字也看不到。又一个假断言的入口。
        """
        assert "in_figure" not in VERDICTS
