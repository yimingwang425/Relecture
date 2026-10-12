"""风格审查 agent（2026-10-12）。

## 这一组断言要守的

1. **偏好原样交给审查员**：B 类与语气用的是讲解 prompt 里同一份措辞；数字写明「只是参考」。
2. **只有审查员说 rewrite 才打回**，打回时它的修改意见原样进重写反馈。
3. **审查员失败不打断讲解**：报错、超时、输出读不懂都按通过处理，原因记下来。
4. **版权那一条仍是硬规则**：引用超长直接重写，不花审查员那次调用。
5. **钱和时间都算进单轮**：审查员每调一次，`usage` / `cost_usd` / 关键路径都要多出这一次。
6. **直接回复（寒暄等）不问审查员**。

审查员的模型是假的，这里**不断言模型判得准不准**，只断言链路。
"""

from __future__ import annotations

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
from explain.style_review import StyleJudge, StyleReviewer, render_preferences

from tests.conftest import SLICE_A, SLICE_B

Q = "热机效率是什么？"
B = {k: DEFAULTS[k] for k in ("use_examples", "structure", "depth")}
OK = '{"verdict": "ok", "notes": [{"aspect": "结构", "comment": "先给了结论"}], "feedback": ""}'
REWRITE = '{"verdict": "rewrite", "notes": [], "feedback": "第 1 段铺垫了三句才给结论，把结论提到开头。"}'


@dataclass
class JudgeModel:
    """按顺序吐出预设的输出；`"raise"` 抛异常，`"hang"` 睡过超时。"""

    outputs: list[str]
    name: str = "fake:claude-haiku-4-5"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion:
        self.calls.append({"system": system, "messages": messages})
        out = self.outputs[min(len(self.calls), len(self.outputs)) - 1]
        if out == "raise":
            raise RuntimeError("boom")
        if out == "hang":
            time.sleep(0.5)
        return Completion(text=out, usage=Usage(input_tokens=500, output_tokens=60),
                          latency_ms=800.0, model=self.name)


def reviewer(*outputs: str, timeout_s: float = 5.0, detail: str = "P2") -> tuple[StyleReviewer, JudgeModel]:
    m = JudgeModel(list(outputs))
    cfg = style_config_for(detail)
    return StyleReviewer(cfg, render_preferences(cfg, detail, B), StyleJudge(m, timeout_s=timeout_s)), m


TEXT = "热机效率是输出的功与吸收的热之比。\n\n它小于一，因为总有热量排给低温热源。"


class TestPreferences:
    def test_b_class_wording_is_the_same_as_the_explainer_gets(self):
        prefs = render_preferences(style_config_for("P3"), "P3", B)
        for axis, value in B.items():
            assert B_CLASS_FRAGMENTS[axis][value] in prefs
        assert "精简" in prefs and "只是参考" in prefs

    def test_tone_only_when_not_neutral(self):
        cfg = style_config_for("P0")
        assert "语气" not in render_preferences(cfg, "P0", B, "neutral")
        assert "语气" in render_preferences(cfg, "P0", B, "spoken")

    def test_judge_sees_preferences_question_and_text(self):
        r, m = reviewer(OK)
        r.review(TEXT, question=Q)
        user = m.calls[0]["messages"][0]["content"]
        assert r.preferences in user and Q in user and TEXT in user


class TestVerdict:
    def test_ok_passes(self):
        r, _ = reviewer(OK)
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.feedback == ""
        assert rv.judge.verdict == "ok" and rv.judge.notes[0]["aspect"] == "结构"

    def test_rewrite_fails_with_the_judges_own_words(self):
        r, _ = reviewer(REWRITE)
        rv = r.review(TEXT, question=Q)
        assert not rv.passed
        assert rv.failed_dimensions == ("style",)
        assert "把结论提到开头" in rv.feedback

    def test_paragraph_length_alone_no_longer_forces_a_rewrite(self):
        """P3 要每段 15–40 字；一段 200 字的讲解，审查员说 ok 就放行（段长只记录）。"""
        r, _ = reviewer(OK, detail="P3")
        long_text = "热机" * 100
        rv = r.review(long_text, question=Q)
        assert rv.passed
        assert rv.evaluation.by_name("paragraph_length").passed is False  # 照算，只是不再据此打回

    def test_rewrite_without_feedback_is_invalid_and_passes(self):
        r, _ = reviewer('{"verdict": "rewrite", "notes": [], "feedback": ""}')
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.judge.implementation == "invalid"


class TestFailureDoesNotBlock:
    def test_error(self):
        r, _ = reviewer("raise")
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.judge.implementation == "error" and "boom" in rv.judge.error

    def test_timeout_cost_is_unknown(self):
        r, _ = reviewer("hang", timeout_s=0.05)
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.judge.cost_usd is None
        assert rv.cost_usd == 0.0  # 加进单轮总成本时按 0，原值 None 在 judge 里

    def test_garbage(self):
        r, _ = reviewer("我觉得还行")
        rv = r.review(TEXT, question=Q)
        assert rv.passed and rv.judge.implementation == "invalid"

    def test_no_judge_configured(self):
        cfg = style_config_for("P2")
        rv = StyleReviewer(cfg, render_preferences(cfg, "P2", B)).review(TEXT, question=Q)
        assert rv.passed and rv.judge is None


class TestCopyrightStaysHard:
    def test_long_quote_rewrites_without_asking_the_judge(self):
        r, m = reviewer(OK)
        quote = "「" + "热机效率" * 40 + "」"  # 160 字
        rv = r.review(f"课件原文是这样写的：{quote}", question=Q)
        assert not rv.passed and rv.failed_dimensions == ("quote_length",)
        assert m.calls == []

    def test_judge_false_skips_the_model(self):
        r, m = reviewer(REWRITE)
        assert r.review(TEXT, question=Q, judge=False).passed
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
        # 审查员的意见进了重写那一次的最后一条 user 消息
        assert r.attempts[0].review.judge.verdict == "rewrite"
        assert r.final_review.judge.verdict == "ok"

    def test_judge_cost_and_latency_count(self):
        rv, _ = reviewer(OK)
        r = go(rv)
        judge = r.final_review.judge
        assert r.usage.input_tokens >= judge.usage.input_tokens
        assert r.cost_usd >= judge.cost_usd > 0.0
        assert r.agent.critical_path_ms >= judge.latency_ms

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
