"""链路口径。每条测试对应 `F3a方案.md` §三 / §六 里的一条。"""

from __future__ import annotations

import pytest

import explain  # noqa: F401
from a_rules.config import StyleConfig
from explain.context import ContextPack
from explain.fake import FakeExplainModel
from explain.gap import NullGapChecker
from explain.pipeline import Trajectory, run_round
from explain.prompt import build_system, build_user_turn
from explain.review import RuleReviewer
from explain.style_prompt import render_style_rules

from .conftest import SLICE_A


def _round(config, retriever, *, review_enabled=True, model=None, question="热机效率是什么？",
           trajectory=None, **kw):
    return run_round(
        round_index=kw.pop("round_index", 1),
        question=question,
        trajectory=trajectory or Trajectory(),
        retriever=retriever,
        model=model or FakeExplainModel(),
        reviewer=RuleReviewer(config),
        style_rules=render_style_rules(config),
        course_label="22.081J",
        review_enabled=review_enabled,
        **kw,
    )


class TestThreeDimensionReview:
    def test_only_three_dimensions_counted(self, config, retriever):
        r = _round(config, retriever)
        assert [d.name for d in r.final_review.evaluation.scored] == [
            "chinese_ratio",
            "paragraph_length",
            "quote_length",
        ]

    def test_feedback_never_mentions_terms(self, config, retriever):
        """术语维度下线后，反馈里不该再出现术语要求。

        出现就意味着模型被逼着花输出 token 做一件审查根本不看的事——
        既花钱又污染归因。
        """
        tight = StyleConfig.from_dict(
            {"explain_language": "zh", "source_language": "en",
             "paragraph_length": {"min": 15, "max": 25}}
        )
        r = _round(tight, retriever, model=FakeExplainModel(target_chars=90, corrected_chars=90))
        assert r.final_review.passed is False
        assert "术语" not in r.final_review.feedback


class TestRegeneration:
    TIGHT = {"explain_language": "zh", "source_language": "en",
             "paragraph_length": {"min": 15, "max": 25}}

    def test_feedback_driven_regeneration_can_converge(self, retriever):
        # 反馈驱动而非重试：假模型收到反馈后向目标区间靠，验证的是这条路径存在
        cfg = StyleConfig.from_dict(self.TIGHT)
        r = _round(cfg, retriever, model=FakeExplainModel(target_chars=90, corrected_chars=20))
        assert r.accepted is True and r.regenerations == 1

    def test_caps_at_two_regenerations(self, retriever):
        cfg = StyleConfig.from_dict(self.TIGHT)
        r = _round(cfg, retriever, model=FakeExplainModel(target_chars=90, corrected_chars=90))
        # PRD §10：连续不通过则降级输出，不无限重试
        assert r.regenerations == 2 and r.accepted is False and r.final_text

    def test_without_review_never_regenerates_but_still_scores(self, retriever):
        """无审查组**照常计算指标，只记录不干预**——不算就没有对照。"""
        cfg = StyleConfig.from_dict(self.TIGHT)
        r = _round(cfg, retriever, review_enabled=False,
                   model=FakeExplainModel(target_chars=90, corrected_chars=90))
        assert r.regenerations == 0
        assert r.accepted is False
        assert r.final_review.evaluation.all_activated_passed is False

    def test_correction_roundtrip_stays_out_of_history(self, retriever):
        """修正往返不进历史（口径 1）。

        进了历史，有审查组的上下文就更长，**输入长度会变成审查之外的第二个变量**。
        """
        cfg = StyleConfig.from_dict(self.TIGHT)
        traj = Trajectory()
        _round(cfg, retriever, model=FakeExplainModel(target_chars=90, corrected_chars=90),
               trajectory=traj)
        assert len(traj.history) == 2  # 一问一答，不含两次修正往返
        assert all("未通过风格审查" not in m["content"] for m in traj.history)


class TestContextSharing:
    def test_same_question_same_signature(self, retriever):
        a = retriever.fetch("热机效率是什么？")
        b = retriever.fetch("热机效率是什么？")
        assert a.signature() == b.signature()

    def test_two_conditions_share_one_context(self, config, retriever):
        """口径 X02：两组共用同一份 ContextPack，签名可核验。

        这是本轮**能把检索接进对照实验**的原因——第 0 周为保住对照
        干脆不含检索（「两组可能取到不同片段，连输入都不一样」）。
        """
        ctx = retriever.fetch("热机效率是什么？")
        with_r = _round(config, retriever, context=ctx)
        without_r = _round(config, retriever, context=ctx, review_enabled=False)
        assert with_r.context.signature() == without_r.context.signature()

    def test_signature_changes_when_slices_change(self, retriever):
        """**反向验证**：换了片，签名必须变。

        不变的话这条守卫是空的——它会对任何两次检索都说「一样」。
        """
        base = retriever.fetch("热机效率是什么？")
        fewer = ContextPack(question=base.question, slices=base.slices[:1])
        assert base.signature() != fewer.signature()

    def test_first_round_inputs_identical_across_conditions(self, config, retriever):
        # sanity check 的硬检查形态：两组第 1 轮请求指纹逐字节相同
        ctx = retriever.fetch("热机效率是什么？")
        a = _round(config, retriever, context=ctx)
        b = _round(config, retriever, context=ctx, review_enabled=False)
        assert a.request_signature == b.request_signature

    def test_style_difference_would_break_input_identity(self, config, retriever):
        """**反向验证**：少注入一句风格约束，指纹必须不同。

        指纹对配置不敏感的话，「两组输入相同」这条断言就不测任何东西。
        """
        ctx = retriever.fetch("热机效率是什么？")
        full = build_system(render_style_rules(config), ctx, "22.081J")
        crippled = build_system("- 段落长度：随便写。", ctx, "22.081J")
        model = FakeExplainModel()
        msgs = [{"role": "user", "content": build_user_turn(ctx, ctx.question)}]
        assert model.request_signature(full, msgs) != model.request_signature(crippled, msgs)


class TestGapInsertionPoint:
    def test_null_checker_returns_none_not_covered(self, config, retriever):
        """**刻意不返回 `covered`。**

        返回 `covered` 等于断言「片够回答」，而本轮根本没做这个判断——
        那是一条假断言（通则七 #1）。`None` 如实表示「未判断」。
        """
        r = _round(config, retriever, gap_checker=NullGapChecker())
        assert r.gap.verdict is None
        assert r.gap.cost_usd == 0.0
        assert r.gap.implementation == "null"

    def test_gap_is_called_every_round(self, config, retriever):
        calls: list[str] = []

        class Spy:
            def check(self, question, context):
                calls.append(question)
                return NullGapChecker().check(question, context)

        _round(config, retriever, gap_checker=Spy())
        assert calls == ["热机效率是什么？"]  # 每轮一次，不是仅在检索为空时


class TestSkippedPagesChannel:
    def test_skipped_pages_reach_the_system_prompt(self, config, retriever):
        r = _round(config, retriever)
        assert "第 33 页" in r.system_text and "image_only" in r.system_text

    def test_system_says_unreadable_is_not_unmentioned(self, config, retriever):
        """PRD §6.2 / §10.1：`image_only` 的正确说法是「读不了」而非「没提到」。

        本轮不写兜底文案，但这条信息必须在上下文里——否则模型面对图里的内容
        最可能的行为是基于标题编一段像样的讲解，**而它看起来完全成功**。
        """
        r = _round(config, retriever)
        assert "不要说「课件里没有提到」" in r.system_text

    def test_skipped_pages_land_in_the_context_payload(self, config, retriever):
        r = _round(config, retriever)
        payload = r.context.to_dict()["skipped_pages_in_scope"]
        assert payload == [
            {
                "source_file": "MIT22_081JF10_lec01b.pdf",
                "index": 33,
                "kind": "image_only",
                "reason": "不着墨文本占比过低",
            }
        ]


class TestPromptShape:
    def test_retrieved_slices_are_not_in_system(self, config, retriever):
        """检索片放 user turn，不放 system（口径 X04）。

        放进 system 会让缓存每轮失效——等于把唯一还能缓存的东西也毁掉。
        """
        r = _round(config, retriever)
        assert SLICE_A.text not in r.system_text
        assert SLICE_A.text in build_user_turn(r.context, r.question)

    def test_user_turn_carries_pages_for_attribution(self, config, retriever):
        turn = build_user_turn(retriever.fetch("q"), "q")
        assert "第 20 页" in turn and "REAL HEAT ENGINES" in turn

    def test_empty_retrieval_is_stated_explicitly(self, config):
        from explain.retrieval_adapter import InMemoryRetriever

        empty = InMemoryRetriever(default_slices=())
        turn = build_user_turn(empty.fetch("q"), "q")
        assert "没有检索到任何课件片段" in turn

    def test_system_warns_that_slices_may_be_incomplete(self, config, retriever):
        """口径 X06 / F2 §12 第 3 条：`multipage` 最好只到 6/9，
        **而缺的部分不带任何标记**。"""
        r = _round(config, retriever)
        assert "不保证包含" in r.system_text and "不要用通用知识补全" in r.system_text
