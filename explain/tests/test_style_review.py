"""风格审查员（LLM-as-judge，2026-10-13）。

审查员的模型是替身，这里**不断言模型判得准不准**，只断言：

1. **偏好原样交给它、不含字数**：B 类与语气用讲解 prompt 里同一份措辞；讲法不是长度。
2. **只有它说 rewrite 才打回**，它的修改意见原样进重写反馈。
3. **失败不打断讲解**：报错、超时、输出读不懂都按放行处理，原因记下来。
4. **版权那一条仍是硬规则**：引用超长直接重写，不花审查员那次调用。
5. **钱和时间计入单轮**；直接回复（寒暄等）不问它。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import explain  # noqa: F401
from explain.agent import RetrieverTools, run_agent_round
from explain.fake import ScriptedToolModel
from explain.gap import GapResult
from explain.llm import Completion, Usage
from explain.pipeline import Trajectory
from explain.retrieval_adapter import InMemoryRetriever
from explain.router import RouteResult
from explain.style_axes import DEFAULTS, style_config_for
from explain.style_prompt import B_CLASS_FRAGMENTS, render_all_style_rules
from explain.style_review import (
    STYLE_JUDGE_MODEL_NAME,
    STYLE_JUDGE_SYSTEM,
    StyleJudge,
    StyleReviewer,
    render_preferences,
)

from tests.conftest import SLICE_A, SLICE_B

Q = "热机效率是什么？"
B = {k: DEFAULTS[k] for k in ("use_examples", "structure", "depth")}
TEXT = ("热机效率是输出的功与吸收的热之比。\n\n"
        "比如一台发动机吸收 100 焦耳的热，做了 30 焦耳的功，效率就是 30%。\n\n"
        "它总小于一，因为总有热量要排给低温热源。")
OK = json.dumps({"verdict": "ok", "reason": "先给了结论，讲到为什么", "feedback": ""}, ensure_ascii=False)
REWRITE = json.dumps({"verdict": "rewrite", "reason": "结论在最后",
                      "feedback": "把结论提到第 1 段开头"}, ensure_ascii=False)


@dataclass
class JudgeModel:
    """按顺序吐出预设的输出；`"raise"` 抛异常，`"hang"` 睡过超时。"""

    outputs: list[str]
    name: str = "fake:claude-sonnet-5"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion:
        self.calls.append({"system": system, "messages": messages})
        out = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if out == "raise":
            raise RuntimeError("boom")
        if out == "hang":
            time.sleep(0.5)
        return Completion(text=out, usage=Usage(input_tokens=900, output_tokens=40),
                          latency_ms=1500.0, model=self.name)


def reviewer(*outputs: str, timeout_s: float = 5.0, detail: str = "P2") -> tuple[StyleReviewer, JudgeModel]:
    m = JudgeModel(list(outputs))
    cfg = style_config_for(detail)
    return StyleReviewer(cfg, render_preferences(cfg, detail, B), StyleJudge(m, timeout_s=timeout_s)), m


class TestInput:
    def test_b_class_wording_is_the_same_as_the_explainer_gets(self):
        prefs = render_preferences(style_config_for("P3"), "P3", B)
        for axis, value in B.items():
            assert B_CLASS_FRAGMENTS[axis][value] in prefs
        assert "精简" in prefs

    def test_no_character_counts_anywhere(self):
        """讲法不是长度：偏好里不出现字数，system 明说不按长度判。"""
        for level in ("P0", "P2", "P3"):
            prefs = render_preferences(style_config_for(level), level, B, "spoken")
            assert not re.search(r"\d+\s*字", prefs), prefs
        assert "讲法不是长度" in STYLE_JUDGE_SYSTEM

    def test_priorities_are_stated(self):
        assert "学生这一句的要求优先于存好的偏好" in STYLE_JUDGE_SYSTEM
        assert "讲法让位于「只讲课件」" in STYLE_JUDGE_SYSTEM
        assert "sonnet" in STYLE_JUDGE_MODEL_NAME

    def test_tone_only_when_chosen(self):
        cfg = style_config_for("P0")
        assert "语气" not in render_preferences(cfg, "P0", B, "neutral")
        assert "语气" in render_preferences(cfg, "P0", B, "spoken")

    def test_judge_sees_preferences_question_and_numbered_text(self):
        r, m = reviewer(OK)
        r.review(TEXT, question=Q)
        user = m.calls[0]["messages"][0]["content"]
        assert r.preferences in user and Q in user
        assert "[1] 热机效率" in user and "[3] 它总小于一" in user

    def test_one_call_per_review(self):
        r, m = reviewer(OK)
        r.review(TEXT, question=Q)
        assert len(m.calls) == 1


class TestVerdict:
    def test_ok_passes(self):
        r, _ = reviewer(OK)
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.feedback == ""
        assert rv.style_run.verdict == "ok" and rv.style_run.reason == "先给了结论，讲到为什么"

    def test_rewrite_fails_with_the_judges_own_words(self):
        r, _ = reviewer(REWRITE)
        rv = r.review(TEXT, question=Q)
        assert not rv.passed and rv.failed_dimensions == ("style",)
        assert "把结论提到第 1 段开头" in rv.feedback

    def test_paragraph_length_alone_no_longer_forces_a_rewrite(self):
        r, _ = reviewer(OK, detail="P3")
        rv = r.review("热机" * 100, question=Q)
        assert rv.passed
        assert rv.evaluation.by_name("paragraph_length").passed is False  # 照算，只是不再据此打回

    def test_rewrite_without_feedback_is_invalid_and_passes(self):
        r, _ = reviewer('{"verdict": "rewrite", "reason": "", "feedback": ""}')
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.style_run.status == "failed"


class TestFailureDoesNotBlock:
    def test_error(self):
        r, _ = reviewer("raise")
        rv = r.review(TEXT, question=Q)
        assert rv.passed and "boom" in rv.style_run.error

    def test_timeout_cost_is_unknown(self):
        r, _ = reviewer("hang", timeout_s=0.05)
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.style_run.cost_usd is None
        assert rv.cost_usd == 0.0  # 加进单轮总成本时按 0，原值 None 在 style_run 里

    def test_garbage(self):
        r, _ = reviewer("我觉得还行")
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.style_run.status == "failed"

    def test_no_judge_configured(self):
        cfg = style_config_for("P2")
        rv = StyleReviewer(cfg, render_preferences(cfg, "P2", B)).review(TEXT, question=Q)
        assert rv.passed and rv.style_run is None


class TestCopyrightStaysHard:
    def test_long_quote_rewrites_without_asking_the_judge(self):
        r, m = reviewer(OK)
        rv = r.review("课件原文是这样写的：「" + "热机效率" * 40 + "」", question=Q)
        assert not rv.passed and rv.failed_dimensions == ("quote_length",)
        assert m.calls == []

    def test_use_judge_false_skips_it(self):
        r, m = reviewer(REWRITE)
        assert r.review(TEXT, question=Q, use_judge=False).passed
        assert m.calls == []


# ---------------------------------------------------------------- 接进讲解 agent


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


class TestInTheRound:
    def test_rewrite_then_ok(self):
        rv, m = reviewer(REWRITE, OK)
        events: list[dict[str, Any]] = []
        r = go(rv, events=events)
        assert r.regenerations == 1 and r.accepted
        assert [e["reason"] for e in events if e["type"] == "reset"] == ["review"]
        assert len(m.calls) == 2
        assert r.attempts[0].review.style_run.verdict == "rewrite"
        assert r.final_review.style_run.verdict == "ok"

    def test_judge_cost_and_latency_count(self):
        rv, _ = reviewer(OK)
        r = go(rv)
        j = r.final_review.style_run
        assert r.usage.input_tokens >= j.usage.input_tokens
        assert r.cost_usd >= j.cost_usd > 0.0
        assert r.agent.critical_path_ms >= j.latency_ms

    def test_always_rewrite_hits_the_cap_and_is_flagged(self):
        rv, m = reviewer(REWRITE)
        r = go(rv)
        assert r.regenerations == 2 and not r.accepted
        assert len(m.calls) == 3

    def test_direct_reply_does_not_ask_the_judge(self):
        rv, m = reviewer(REWRITE)
        route = RouteResult(route="chat", reply="不客气！有问题随时问。", implementation="model")
        r = go(rv, route=route)
        assert r.final_text == "不客气！有问题随时问。"
        assert m.calls == []


class TestExplainerPriorities:
    def test_explainer_agent_section_states_the_priorities(self):
        from explain.agent import render_agent_section, tool_specs

        text = render_agent_section(tool_specs(can_read_page=True, can_check=True))
        assert "材料范围优先于讲法" in text and "优先于上面存好的讲解风格要求" in text
