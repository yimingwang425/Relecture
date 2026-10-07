"""兜底判断与首版生成并行（口径 F93）+ 流式事件（口径 F94）。

## 这一组断言要守的只有一件事：**并行之后，发给模型的东西与串行时逐字节相同**

投机生成按「没有限定语」拼消息。判断不要求限定语时这一版留用，要求时作废重来。
所以每一种判断结果下，最终的 `request_signature`、写进历史的那条消息、
真正被审查的那一版，都必须与串行实现（判完再拼）一致——
期望值由 `build_user_turn(ctx, q, gap)` **直接算**，不从 `run_round` 的产出里取
（判据两端不同源）。

另有两条反向控制：把闸门拆掉（投机正文直接放出去）、把作废那一步拆掉
（判断要求限定语时仍留用投机版），对应的断言必须变红。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

import explain  # noqa: F401
from a_rules.config import StyleConfig
from explain import pipeline as P
from explain.gap import GapResult, GuardedGapChecker
from explain.llm import Completion, Usage, signature
from explain.pipeline import Trajectory, run_round
from explain.prompt import build_system, build_user_turn
from explain.review import RuleReviewer
from explain.style_prompt import render_style_rules

NOTE_MARK = "【片段完整性提示】"


@dataclass
class StreamingFake:
    """**照生产 `AnthropicModel.stream` 的签名写的**流式假模型。

    正文里写明它看到的消息带没带限定语——这样「用户看到的是不是作废那一版」
    可以从流里直接读出来，不靠猜。每段之间睡一下，让叫停真的有机会发生。
    """

    name: str = "fake:claude-sonnet-5"
    chunks: int = 8
    delay_s: float = 0.0
    params: dict[str, Any] = field(default_factory=lambda: {"effort": "medium"})
    calls: list[str] = field(default_factory=list)

    def _text_for(self, messages: list[dict[str, Any]]) -> str:
        last = str(messages[-1]["content"])
        tag = "带限定语" if NOTE_MARK in last else "无限定语"
        # 每段六十字上下，四段 → 段长落在默认区间（30–120）里，不触发重生成
        para = (f"这一版{tag}。第二十页讲的是热机效率的定义：热机把热能转成机械功，"
                "卡诺效率等于一减去低温与高温的绝对温度之比。")
        return "\n\n".join([para] * 4)

    def stream(self, system, messages, *, on_delta=None, cancel=None) -> Completion:
        text = self._text_for(messages)
        self.calls.append(str(messages[-1]["content"]))
        step = max(1, len(text) // self.chunks)
        sent = ""
        stop = "end_turn"
        for i in range(0, len(text), step):
            if cancel is not None and cancel.is_set():
                stop = "cancelled"
                break
            piece = text[i:i + step]
            sent += piece
            if on_delta is not None:
                on_delta(piece)
            if self.delay_s:
                time.sleep(self.delay_s)
        return Completion(text=sent, usage=Usage(input_tokens=100, output_tokens=len(sent)),
                          latency_ms=1.0, model=self.name, stop_reason=stop)

    def complete(self, system, messages) -> Completion:
        return self.stream(system, messages)

    def request_signature(self, system, messages) -> str:
        return signature(self.name, self.params, system, messages)


@dataclass
class ScriptedGap:
    verdict: str | None
    delay_s: float = 0.0
    implementation: str = "model"
    resolved_at: list[float] = field(default_factory=list)

    def check(self, question, context) -> GapResult:
        if self.delay_s:
            time.sleep(self.delay_s)
        self.resolved_at.append(time.perf_counter())
        return GapResult(verdict=self.verdict, implementation=self.implementation,  # type: ignore[arg-type]
                         latency_ms=self.delay_s * 1000, cost_usd=0.001,
                         skipped_hint=tuple(context.skipped_pages) if self.verdict not in (None, "covered") else ())


def _go(config, retriever, model, gap, events=None, **kw):
    return run_round(
        round_index=1, question="热机效率是什么？", trajectory=kw.pop("trajectory", Trajectory()),
        retriever=retriever, model=model, reviewer=RuleReviewer(config),
        style_rules=render_style_rules(config), course_label="22.081J",
        review_enabled=True, gap_checker=gap,
        on_event=(events.append if events is not None else None), **kw,
    )


def _serial_signature(config, retriever, model, gap_result) -> str:
    """串行实现会发出去的那条请求的指纹。**直接由 prompt 层算，不经 run_round。**"""
    ctx = retriever.fetch("热机效率是什么？")
    system = build_system(render_style_rules(config), ctx, "22.081J")
    turn = build_user_turn(ctx, "热机效率是什么？", gap_result)
    return model.request_signature(system, [{"role": "user", "content": turn}])


def _text_of(events) -> str:
    out = ""
    for e in events:
        if e["type"] == "reset":
            out = ""
        elif e["type"] == "delta":
            out += e["text"]
    return out


class TestEquivalenceWithSerial:
    @pytest.mark.parametrize("verdict", ["covered", "insufficient", "partial", None])
    def test_final_request_is_byte_identical(self, config, retriever, verdict):
        model = StreamingFake()
        gap = ScriptedGap(verdict)
        r = _go(config, retriever, model, gap)
        expected = _serial_signature(config, retriever, model, gap.check("q", retriever.fetch("q")))
        assert r.request_signature == expected

    @pytest.mark.parametrize("verdict", ["covered", "insufficient", "partial", None])
    def test_history_gets_what_was_actually_sent(self, config, retriever, verdict):
        model = StreamingFake()
        traj = Trajectory()
        r = _go(config, retriever, model, ScriptedGap(verdict), trajectory=traj)
        # 写进历史的那条 = 最终被审查那一版收到的那条
        assert traj.history[0]["content"] == model.calls[-1]
        assert (NOTE_MARK in traj.history[0]["content"]) == (verdict in ("insufficient", "partial"))
        assert traj.history[1]["content"] == r.final_text


class TestSpeculation:
    def test_covered_reuses_the_speculative_version(self, config, retriever):
        model = StreamingFake()
        r = _go(config, retriever, model, ScriptedGap("covered"))
        assert len(model.calls) == 1, "covered 时不该再生成一次"
        assert r.discarded == ()
        assert "无限定语" in r.final_text

    @pytest.mark.parametrize("verdict", ["insufficient", "partial"])
    def test_non_covered_discards_and_regenerates_with_note(self, config, retriever, verdict):
        model = StreamingFake()
        r = _go(config, retriever, model, ScriptedGap(verdict))
        assert len(model.calls) == 2
        assert len(r.discarded) == 1
        assert "带限定语" in r.final_text
        # 作废那一版**不是一版讲解**：不计重生成
        assert r.regenerations == 0

    def test_discarded_cost_is_counted_in_the_round(self, config, retriever):
        """口径 F71 的同一条：花了的钱都计入单轮，作废的也一样。"""
        r = _go(config, retriever, StreamingFake(), ScriptedGap("insufficient"))
        spent = sum(a.completion.cost for a in r.attempts) + r.gap.cost_usd
        assert r.cost_usd == pytest.approx(spent + r.discarded[0].cost)
        assert r.usage.input_tokens == 200  # 两次调用各 100

    def test_slow_judge_cancels_the_speculative_stream(self, config, retriever):
        """判断回来时投机那一版还在流——它必须被**叫停**，不是跑完再扔。"""
        model = StreamingFake(chunks=40, delay_s=0.01)
        r = _go(config, retriever, model, ScriptedGap("insufficient", delay_s=0.05))
        assert r.discarded[0].stop_reason == "cancelled"
        full = len(model._text_for([{"content": ""}]))
        assert len(r.discarded[0].text) < full

    def test_judge_exception_propagates_without_guard(self, config, retriever):
        """实验侧不套 GuardedGapChecker：判断失败**响亮地停**（同改动前）。"""

        class Boom:
            def check(self, q, c):
                raise RuntimeError("judge down")

        with pytest.raises(RuntimeError, match="judge down"):
            _go(config, retriever, StreamingFake(), Boom())

    def test_guarded_failure_keeps_the_speculative_version(self, config, retriever):
        """生产套 Guarded：判断失败 → 无限定语 → 投机那一版就是正式的。"""

        class Boom:
            model = None

            def check(self, q, c):
                raise RuntimeError("judge down")

        model = StreamingFake()
        r = _go(config, retriever, model, GuardedGapChecker(Boom()))
        assert r.gap.implementation == "error" and r.discarded == ()
        assert len(model.calls) == 1


class TestGate:
    def test_nothing_reaches_the_user_before_the_judge_returns(self, config, retriever):
        gap = ScriptedGap("covered", delay_s=0.05)
        stamps: list[float] = []

        def on_event(e):
            if e["type"] == "delta":
                stamps.append(time.perf_counter())

        run_round(round_index=1, question="热机效率是什么？", trajectory=Trajectory(),
                  retriever=retriever, model=StreamingFake(), reviewer=RuleReviewer(config),
                  style_rules=render_style_rules(config), course_label="22.081J",
                  review_enabled=True, gap_checker=gap, on_event=on_event)
        assert stamps, "一段都没流出来"
        assert min(stamps) >= gap.resolved_at[0]

    def test_discarded_text_never_reaches_the_user(self, config, retriever):
        events: list[dict] = []
        _go(config, retriever, StreamingFake(), ScriptedGap("insufficient", delay_s=0.02), events)
        streamed = "".join(e["text"] for e in events if e["type"] == "delta")
        assert "无限定语" not in streamed
        assert "带限定语" in streamed

    def test_reverse_control_without_gate_leaks(self, config, retriever, monkeypatch):
        """**反向验证**：把闸门拆掉（来了就放），上一条必须红。"""

        def leaky_feed(self, text):
            self._emit(text)

        monkeypatch.setattr(P._Gate, "feed", leaky_feed)
        events: list[dict] = []
        _go(config, retriever, StreamingFake(), ScriptedGap("insufficient", delay_s=0.02), events)
        streamed = "".join(e["text"] for e in events if e["type"] == "delta")
        assert "无限定语" in streamed

    def test_reverse_control_without_discard_breaks_equivalence(self, config, retriever, monkeypatch):
        """**反向验证**：判断要求限定语时仍留用投机版 → 签名与串行不同。"""
        monkeypatch.setattr(P, "render_gap_note", lambda gap: "")
        model = StreamingFake()
        gap = ScriptedGap("insufficient")
        r = _go(config, retriever, model, gap)
        expected = _serial_signature(config, retriever, model, gap.check("q", retriever.fetch("q")))
        assert r.request_signature != expected

    def test_gap_event_is_emitted(self, config, retriever):
        events: list[dict] = []
        _go(config, retriever, StreamingFake(), ScriptedGap("partial"), events)
        gaps = [e for e in events if e["type"] == "gap"]
        assert len(gaps) == 1 and gaps[0]["gap_verdict"] == "partial"


class TestStreamingReview:
    TIGHT = {"explain_language": "zh", "source_language": "en",
             "paragraph_length": {"min": 15, "max": 25}}

    def test_regeneration_emits_reset_and_stream_ends_on_the_final_text(self, retriever):
        from explain.fake import FakeExplainModel

        cfg = StyleConfig.from_dict(self.TIGHT)
        events: list[dict] = []
        r = _go(cfg, retriever, FakeExplainModel(target_chars=90, corrected_chars=20),
                ScriptedGap("covered"), events)
        assert r.regenerations == 1
        resets = [e for e in events if e["type"] == "reset"]
        assert len(resets) == 1 and resets[0]["reason"] == "review"
        # 前端按「reset 清屏」重放事件，得到的必须就是最终答案
        assert _text_of(events) == r.final_text

    def test_reverse_control_without_reset_shows_both_versions(self, retriever, monkeypatch):
        """**反向验证**：不发 reset 的话，重放出来的是两版拼在一起。"""
        from explain.fake import FakeExplainModel

        cfg = StyleConfig.from_dict(self.TIGHT)
        events: list[dict] = []
        r = _go(cfg, retriever, FakeExplainModel(target_chars=90, corrected_chars=20),
                ScriptedGap("covered"), events)
        without_reset = [e for e in events if e["type"] != "reset"]
        assert _text_of(without_reset) != r.final_text

    def test_first_token_latency_is_measured_not_invented(self, config, retriever):
        r = _go(config, retriever, StreamingFake(), ScriptedGap("covered", delay_s=0.03))
        assert r.first_token_latency_ms is not None
        assert r.first_token_latency_ms >= 30 * 0.9
        assert r.wall_ms >= r.first_token_latency_ms

    def test_non_streaming_model_still_works(self, config, retriever):
        """只实现 `complete()` 的模型（实验侧的替身）照样走通，流只有一段。"""
        from explain.fake import FakeExplainModel

        events: list[dict] = []
        r = _go(config, retriever, FakeExplainModel(), ScriptedGap("covered"), events)
        assert _text_of(events) == r.final_text


def test_gate_states_are_one_way():
    out: list[str] = []
    g = P._Gate(out.append)
    g.feed("a")
    assert out == []
    g.open()
    g.feed("b")
    assert out == ["a", "b"]
    g2 = P._Gate(out.append)
    g2.feed("x")
    g2.drop()
    g2.feed("y")
    assert out == ["a", "b"] and g2.cancel.is_set()
    assert threading.Event  # 只为让 import 不被当成多余
