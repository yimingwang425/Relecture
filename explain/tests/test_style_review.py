"""风格审查 agent（2026-10-12）。

替身是 `ScriptedToolModel`——照生产 `AnthropicModel.stream` 的签名写的、会调工具的假模型。
这里钉的是**循环本身**，与 `test_memory.py` 同一个口径：

1. **模型决定看什么**：量不量讲解、回不回看最近几轮，由它的工具调用决定；工具结果原样交回。
2. **提交由代码校验**：段号要存在、引文要真的在那一段；不合格以 `is_error` 退回，改了再交。
3. **限步、最后一步强制提交、失败不抛**：失败按放行处理，原因记下来。
4. **接进讲解**：只有 agent 交出 rewrite 才打回；它的引文与修改意见进重写反馈；
   钱和时间计入单轮；直接回复不叫它；版权那一条仍是硬规则、不花它的调用。

真模型会不会这样用工具，替身证明不了。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import explain  # noqa: F401
from explain.agent import RetrieverTools, run_agent_round
from explain.fake import ScriptedToolModel
from explain.gap import GapResult
from explain.pipeline import Trajectory
from explain.retrieval_adapter import InMemoryRetriever
from explain.router import RouteResult
from explain.style_axes import DEFAULTS, style_config_for
from explain.style_prompt import B_CLASS_FRAGMENTS, render_all_style_rules
from explain.style_review import (
    MAX_STEPS,
    TOOL_MEASURE,
    TOOL_RECENT,
    TOOL_SUBMIT,
    StyleReviewAgent,
    StyleReviewer,
    check_submission,
    render_preferences,
    split_paragraphs,
)

from tests.conftest import SLICE_A, SLICE_B

Q = "热机效率是什么？"
B = {k: DEFAULTS[k] for k in ("use_examples", "structure", "depth")}
TEXT = ("热机效率是输出的功与吸收的热之比。\n\n"
        "比如一台发动机吸收 100 焦耳的热，做了 30 焦耳的功，效率就是 30%。\n\n"
        "它总小于一，因为总有热量要排给低温热源。")
PARAS = split_paragraphs(TEXT)
HISTORY = ({"role": "user", "content": "卡诺循环是什么？"},
           {"role": "assistant", "content": "卡诺循环由两个等温过程和两个绝热过程组成。"})

OK = (TOOL_SUBMIT, {"verdict": "ok", "notes": [{"aspect": "结构", "comment": "第一句就给了结论"}], "issues": []})


def rewrite(paragraph: int = 1, quote: str = "热机效率是输出的功", aspect: str = "structure"):
    return (TOOL_SUBMIT, {"verdict": "rewrite", "notes": [], "issues": [
        {"aspect": aspect, "paragraph": paragraph, "quote": quote, "fix": "先铺垫问题，把结论放到最后一段"}]})


def make(script, **kw) -> tuple[StyleReviewAgent, ScriptedToolModel]:
    model = ScriptedToolModel(script=script, name="fake:claude-haiku-4-5")
    return StyleReviewAgent(model, **kw), model


def run(script, *, text=TEXT, history=HISTORY, **kw):
    a, model = make(script, **kw)
    prefs = render_preferences(style_config_for("P2"), "P2", B)
    return a.review(preferences=prefs, question=Q, text=text, history=history), model


# ---------------------------------------------------------------- 输入


class TestPriorities:
    """讲法让位于「只讲课件」、这一句的要求优先于存好的偏好——讲解 agent 与审查 agent 两边都写了。"""

    def test_reviewer_knows_style_yields_to_grounding(self):
        from explain.style_review import STYLE_REVIEW_MODEL_NAME, STYLE_REVIEW_SYSTEM

        assert "讲法让位于「只讲课件」" in STYLE_REVIEW_SYSTEM and "不要按字数卡" in STYLE_REVIEW_SYSTEM
        assert "sonnet" in STYLE_REVIEW_MODEL_NAME

    def test_explainer_agent_section_states_the_priorities(self):
        from explain.agent import render_agent_section, tool_specs

        text = render_agent_section(tool_specs(can_read_page=True, can_check=True))
        assert "材料范围优先于讲法" in text and "优先于上面存好的讲解风格要求" in text


class TestBrief:
    def test_b_class_wording_is_the_same_as_the_explainer_gets(self):
        prefs = render_preferences(style_config_for("P3"), "P3", B)
        for axis, value in B.items():
            assert B_CLASS_FRAGMENTS[axis][value] in prefs
        assert "精简" in prefs and "只是参考" in prefs

    def test_tone_only_when_chosen(self):
        cfg = style_config_for("P0")
        assert "语气" not in render_preferences(cfg, "P0", B, "neutral")
        assert "语气" in render_preferences(cfg, "P0", B, "spoken")

    def test_first_message_numbers_the_paragraphs(self):
        r, model = run([("tool", [OK])])
        first = model.calls[0]["messages"][0]["content"]
        assert "[1] 热机效率" in first and "[3] 它总小于一" in first and Q in first


# ---------------------------------------------------------------- 循环


class TestTheLoop:
    def test_submits_directly_when_it_can_tell(self):
        r, model = run([("tool", [OK])])
        assert r.verdict == "ok" and r.status == "submitted" and len(model.calls) == 1
        assert [t.name for t in r.tools] == [TOOL_SUBMIT]

    def test_looks_then_submits(self):
        r, model = run([
            ("tool", [(TOOL_MEASURE, {}), (TOOL_RECENT, {"n": 1})]),
            ("tool", [OK]),
            ("tool", [rewrite()]),     # 不该被走到
        ])
        assert r.verdict == "ok" and len(model.calls) == 2
        assert [t.name for t in r.tools] == [TOOL_MEASURE, TOOL_RECENT, TOOL_SUBMIT]
        # 两个工具的结果真的交回了模型，且在**同一条** user 消息里
        back = model.calls[1]["messages"][-1]["content"]
        assert len(back) == 2
        assert "共 3 段" in back[0]["content"] and "举例" in back[0]["content"]
        assert "卡诺循环是什么" in back[1]["content"]

    def test_wrong_quote_goes_back_and_is_fixed(self):
        r, model = run([
            ("tool", [rewrite(paragraph=1, quote="总有热量要排给低温热源")]),   # 这句在第 3 段
            ("tool", [rewrite(paragraph=3, quote="总有热量要排给低温热源")]),
        ])
        assert r.verdict == "rewrite" and r.rejected == 1
        back = model.calls[1]["messages"][-1]["content"]
        assert back[0]["is_error"] is True and "它在第 3 段" in back[0]["content"]
        assert r.issues[0]["paragraph"] == 3
        assert "第 3 段" in r.feedback and "总有热量要排给低温热源" in r.feedback

    def test_stopping_without_a_verdict_is_not_done(self):
        r, model = run([("text", "这一版挺好的。"), ("tool", [OK])])
        assert r.verdict == "ok"
        assert TOOL_SUBMIT in model.calls[1]["messages"][-1]["content"]

    def test_step_cap_and_forced_submit_on_the_last_step(self):
        r, model = run([("tool", [(TOOL_MEASURE, {})])] * 10)
        assert r.verdict is None and r.status == "failed" and str(MAX_STEPS) in r.error
        assert len(model.calls) == MAX_STEPS
        assert model.calls[-1]["tool_choice"] == {"type": "tool", "name": TOOL_SUBMIT}
        # 反向：前面几步**不**强制——否则「每一步都逼它交」也会让上一条全绿
        assert all(c["tool_choice"] is None for c in model.calls[:-1])

    def test_model_failure_never_raises(self):
        r, _ = run([("raise", "boom")])
        assert r.verdict is None and "boom" in r.error

    def test_first_turn_has_no_history(self):
        r, model = run([("tool", [(TOOL_RECENT, {"n": 2})]), ("tool", [OK])], history=())
        assert "第一轮" in model.calls[1]["messages"][-1]["content"][0]["content"]


class TestSubmissionCheck:
    def check(self, args, tone_set=False):
        return check_submission(args, PARAS, tone_set=tone_set)

    def test_good_rewrite_passes(self):
        res, problems = self.check(rewrite()[1])
        assert problems == [] and res["verdict"] == "rewrite"

    def test_quote_must_really_be_there(self):
        _, problems = self.check(rewrite(quote="热机效率等于卡诺效率")[1])
        assert any("讲解里没有这句话" in p for p in problems)

    def test_markdown_and_whitespace_dont_break_the_match(self):
        res, _ = self.check(rewrite(quote="热机 **效率** 是输出的功")[1])
        assert res is not None

    def test_empty_looking_quote_is_rejected(self):
        _, problems = self.check(rewrite(quote="**")[1])
        assert any("quote 太短" in p for p in problems)

    def test_one_letter_quote_and_placeholder_fix_are_rejected(self):
        """评测里真的出现过：引文 "e"、修改意见 "placeholder"，旧校验让它过了。"""
        args = rewrite(quote="热")[1]
        args["issues"][0]["fix"] = "改"
        _, problems = self.check(args)
        assert any("quote 太短" in p for p in problems) and any("fix 太短" in p for p in problems)

    def test_at_most_three_issues(self):
        args = rewrite()[1]
        args["issues"] = args["issues"] * 4
        _, problems = self.check(args)
        assert any("至多 3 条" in p for p in problems)

    def test_paragraph_must_exist(self):
        _, problems = self.check(rewrite(paragraph=9)[1])
        assert any("不存在" in p for p in problems)

    def test_rewrite_needs_issues_and_ok_must_have_none(self):
        _, p1 = self.check({"verdict": "rewrite", "notes": [], "issues": []})
        _, p2 = self.check({"verdict": "ok", "notes": [], "issues": rewrite()[1]["issues"]})
        assert p1 and p2

    def test_tone_only_when_the_student_chose_one(self):
        _, problems = self.check(rewrite(aspect="tone")[1])
        assert any("没有选语气" in p for p in problems)
        res, _ = self.check(rewrite(aspect="tone")[1], tone_set=True)
        assert res is not None


# ---------------------------------------------------------------- 接进审查


def reviewer(script, detail="P2") -> tuple[StyleReviewer, ScriptedToolModel]:
    a, model = make(script)
    cfg = style_config_for(detail)
    return StyleReviewer(cfg, render_preferences(cfg, detail, B), a, history=HISTORY), model


class TestReviewer:
    def test_paragraph_length_alone_no_longer_forces_a_rewrite(self):
        """P3 要每段 15–40 字；一段 200 字的讲解，agent 说 ok 就放行（段长只记录）。"""
        rv, _ = reviewer([("tool", [OK])], detail="P3")
        out = rv.review("热机" * 100, question=Q)
        assert out.passed
        assert out.evaluation.by_name("paragraph_length").passed is False

    def test_long_quote_rewrites_without_calling_the_agent(self):
        rv, model = reviewer([("tool", [OK])])
        out = rv.review("课件原文是这样写的：「" + "热机效率" * 40 + "」", question=Q)
        assert not out.passed and out.failed_dimensions == ("quote_length",)
        assert model.calls == []

    def test_use_agent_false_skips_it(self):
        rv, model = reviewer([("tool", [rewrite()])])
        assert rv.review(TEXT, question=Q, use_agent=False).passed
        assert model.calls == []

    def test_agent_failure_lets_it_through(self):
        rv, _ = reviewer([("raise", "boom")])
        out = rv.review(TEXT, question=Q)
        assert out.passed and out.style_run.status == "failed"


@dataclass
class Gap:
    def check(self, question, context) -> GapResult:
        return GapResult(verdict="covered", reason="r", implementation="model", cost_usd=0.0, latency_ms=5.0)


def go(rv: StyleReviewer, *, route=None, events=None):
    backend = InMemoryRetriever(default_slices=(SLICE_A, SLICE_B))
    return run_agent_round(
        round_index=1, question=Q, trajectory=Trajectory(), tools=RetrieverTools(backend),
        model=ScriptedToolModel(), reviewer=rv,
        style_rules=render_all_style_rules(rv.config, B), course_label="22.081J",
        context=backend.fetch(Q), gap_checker=Gap(), route=route,
        on_event=(events.append if events is not None else None),
    )


# ScriptedToolModel 的默认讲解：四段同一句话。引文从里面照抄。
ANSWER_QUOTE = "第二十页讲的是热机效率的定义"


class TestInTheRound:
    def test_rewrite_then_ok(self):
        rv, model = reviewer([("tool", [rewrite(quote=ANSWER_QUOTE)]), ("tool", [OK])])
        events: list[dict[str, Any]] = []
        r = go(rv, events=events)
        assert r.regenerations == 1 and r.accepted
        assert [e["reason"] for e in events if e["type"] == "reset"] == ["review"]
        assert r.attempts[0].review.style_run.verdict == "rewrite"
        assert r.final_review.style_run.verdict == "ok"
        assert ANSWER_QUOTE in r.attempts[0].review.feedback

    def test_agent_cost_and_latency_count(self):
        rv, _ = reviewer([("tool", [(TOOL_MEASURE, {})]), ("tool", [OK])])
        r = go(rv)
        run_ = r.final_review.style_run
        assert len(run_.steps) == 2
        assert r.usage.input_tokens >= run_.usage.input_tokens > 0
        assert r.cost_usd >= run_.cost_usd > 0.0
        assert r.agent.critical_path_ms >= run_.latency_ms

    def test_always_rewrite_hits_the_cap_and_is_flagged(self):
        rv, _ = reviewer([("tool", [rewrite(quote=ANSWER_QUOTE)])] * 10)
        r = go(rv)
        assert r.regenerations == 2 and not r.accepted

    def test_direct_reply_does_not_call_the_agent(self):
        rv, model = reviewer([("tool", [rewrite()])])
        route = RouteResult(route="chat", reply="不客气！有问题随时问。", implementation="model")
        r = go(rv, route=route)
        assert r.final_text == "不客气！有问题随时问。"
        assert model.calls == []
