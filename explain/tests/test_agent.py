"""多智能体讲解（口径 F109–F114）。

## 这一组断言要守的

1. **模型决定查不查、查什么**：给了工具调用就真的执行，结果原样交回；不给就直接作答。
2. **把关留在代码里**：审查不在工具清单里；不达标照样重写；轮数到上限强制作答。
3. **下游不用改**：agent 查到的片并进 ContextPack，`hit_documents` 与 `slices` 仍逐项对齐。
4. **回落不静默**：agent 链路抛异常 → 固定流水线接手，`mode="fallback"`，花掉的钱计入单轮。
5. **流式事件说得清**：调工具前漏出来的字被 `reset` 收回，重放 delta 得到的就是最终正文。

模型是 `ScriptedToolModel`（照生产 `AnthropicModel.stream` 的签名写）——它只遵守
`tool_choice` 一条真实约束，其余全是脚本，所以这里**不断言模型的任何能力**，只断言链路。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

import explain  # noqa: F401
from explain.agent import (
    MAX_TOOL_ROUNDS,
    TOOL_COVERAGE,
    TOOL_PAGE,
    TOOL_SEARCH,
    RetrieverTools,
    _Collected,
    run_agent_round,
    run_with_fallback,
    supports_tools,
    tool_specs,
)
from explain.context import ContextPack, ContextSlice
from explain.fake import FakeExplainModel, ScriptedToolModel
from explain.gap import GapResult, NullGapChecker
from explain.pipeline import Trajectory, run_round
from explain.retrieval_adapter import InMemoryRetriever
from explain.review import RuleReviewer
from explain.router import Router, RouteResult
from explain.llm import Completion, Usage
from explain.style_prompt import render_style_rules

from tests.conftest import SLICE_A, SLICE_B

Q = "热机效率是什么？"

SLICE_C = ContextSlice(
    source_file="MIT22_081JF10_lec01b.pdf", slice_id="s034", page_indices=(34,),
    title="CARNOT CYCLE", text="The Carnot cycle consists of two isothermal and two adiabatic steps.",
    char_count=68, score=0.6,
)
SLICE_C2 = ContextSlice(
    source_file="MIT22_081JF10_lec01b.pdf", slice_id="s035", page_indices=(34,),
    title="CARNOT CYCLE (cont.)", text="Efficiency depends only on the reservoir temperatures.",
    char_count=54, score=None,
)


@pytest.fixture
def backend() -> InMemoryRetriever:
    return InMemoryRetriever(
        default_slices=(SLICE_A, SLICE_B),
        by_question={"Carnot cycle steps": (SLICE_C, SLICE_A)},
    )


@dataclass
class Gap:
    verdict: str | None = "covered"
    seen: list[ContextPack] = field(default_factory=list)

    def check(self, question, context) -> GapResult:
        self.seen.append(context)
        return GapResult(verdict=self.verdict, reason="r", implementation="model",  # type: ignore[arg-type]
                         cost_usd=0.002, latency_ms=5.0)


def go(config, backend, model, *, gap=None, events=None, route=None, trajectory=None, **kw):
    ctx = backend.fetch(Q)
    return run_agent_round(
        round_index=1, question=Q, trajectory=trajectory or Trajectory(),
        tools=RetrieverTools(backend), model=model, reviewer=RuleReviewer(config),
        style_rules=render_style_rules(config), course_label="22.081J", context=ctx,
        gap_checker=gap if gap is not None else Gap(), route=route,
        on_event=(events.append if events is not None else None), **kw,
    )


def replay(events) -> str:
    out = ""
    for e in events:
        if e["type"] == "reset":
            out = ""
        elif e["type"] == "delta":
            out += e["text"]
    return out


class TestModelDecides:
    def test_no_tool_call_answers_directly(self, config, backend):
        m = ScriptedToolModel()
        r = go(config, backend, m)
        assert r.agent.mode == "agent"
        assert r.agent.steps == () and r.agent.tools == ()
        assert len(m.calls) == 1
        assert [s.slice_id for s in r.context.slices] == ["s020", "s021"]
        assert r.accepted

    def test_search_result_goes_back_and_joins_the_context(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "Carnot cycle steps"})])])
        events: list[dict] = []
        r = go(config, backend, m, events=events)
        assert len(r.agent.steps) == 1
        run = r.agent.tools[0]
        assert run.name == TOOL_SEARCH and run.ok and run.new_slices == 1
        # 交回模型的：上一步的 tool_use 原样 + 一条 user 消息装着 tool_result
        second = m.calls[1]["messages"]
        assert second[-2]["role"] == "assistant"
        assert second[-2]["content"][0]["type"] == "tool_use"
        result = second[-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == second[-2]["content"][0]["id"]
        assert "two isothermal" in result["content"]
        # 已给过的那片不再重复正文
        assert "前面已经给过" in result["content"]
        # 并进 ContextPack，下游据此显示出处
        assert [s.slice_id for s in r.context.slices] == ["s020", "s021", "s034"]
        assert r.context.meta["agent_retrieval"]["slices_added"] == 1
        kinds = [(e["type"], e.get("phase")) for e in events]
        assert ("tool", "start") in kinds and ("tool", "done") in kinds
        done = next(e for e in events if e["type"] == "tool" and e.get("phase") == "done")
        assert done["retrieval"]["slice_count"] == 3

    def test_tools_are_offered_every_step_and_never_include_review(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "x"})])])
        go(config, backend, m)
        names = m.calls[0]["tools"]
        assert names == m.calls[1]["tools"]          # 一轮之内不变（缓存前缀）
        assert set(names) == {TOOL_SEARCH, TOOL_PAGE, TOOL_COVERAGE}
        # PRD §7.2：审查不能是讲解 agent 可以选择不调的东西
        assert not any("review" in n or "审查" in n for n in names)

    def test_read_page_returns_every_slice_on_that_page(self, config, backend):
        backend.by_question["more"] = (SLICE_C2,)
        m = ScriptedToolModel(script=[("tool", [(TOOL_PAGE, {"source_file": SLICE_C.source_file, "page": 34})])])
        r = go(config, backend, m)
        content = m.calls[1]["messages"][-1]["content"][0]["content"]
        assert "two isothermal" in content and "reservoir temperatures" in content
        assert {"s034", "s035"} <= {s.slice_id for s in r.context.slices}

    def test_bad_tool_input_is_reported_back_not_raised(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_PAGE, {"source_file": "x", "page": "三"})])])
        r = go(config, backend, m)
        res = m.calls[1]["messages"][-1]["content"][0]
        assert res.get("is_error") is True and "page" in res["content"]
        assert r.agent.tools[0].ok is False
        assert r.accepted

    def test_coverage_judges_the_accumulated_slices(self, config, backend):
        gap = Gap()
        m = ScriptedToolModel(script=[
            ("tool", [(TOOL_SEARCH, {"query": "Carnot cycle steps"})]),
            ("tool", [(TOOL_COVERAGE, {})]),
        ])
        r = go(config, backend, m, gap=gap)
        # 第一次是代码强制的首轮判断（2 片），第二次是模型叫的（3 片）
        assert [len(c.slices) for c in gap.seen] == [2, 3]
        assert r.agent.tools[1].cost_usd == pytest.approx(0.002)
        assert "判断：covered" in m.calls[2]["messages"][-1]["content"][0]["content"]

    def test_parallel_calls_come_back_in_one_message(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "a"}), (TOOL_SEARCH, {"query": "b"})])])
        go(config, backend, m)
        last = m.calls[1]["messages"][-1]
        assert last["role"] == "user" and [b["type"] for b in last["content"]] == ["tool_result"] * 2


class TestCodeKeepsControl:
    def test_round_cap_forces_an_answer(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": f"q{i}"})]) for i in range(10)])
        r = go(config, backend, m)
        assert len(r.agent.steps) == MAX_TOOL_ROUNDS
        assert r.agent.hit_round_cap is True
        assert m.calls[MAX_TOOL_ROUNDS]["tool_choice"] == {"type": "none"}
        assert all(c["tool_choice"] is None for c in m.calls[:MAX_TOOL_ROUNDS])
        assert r.final_text and r.accepted

    def test_review_still_regenerates_and_without_tools(self, config, backend):
        bad = "很长" * 400  # 一整段，段长远超区间
        m = ScriptedToolModel(script=[("text", bad)])
        events: list[dict] = []
        r = go(config, backend, m, events=events)
        assert r.regenerations == 1 and r.accepted
        assert m.calls[1]["tool_choice"] == {"type": "none"}
        assert "未通过风格审查" in str(m.calls[1]["messages"][-1]["content"])
        assert any(e["type"] == "reset" and e["reason"] == "review" for e in events)
        assert replay(events) == r.final_text

    def test_insufficient_discards_speculation_and_restarts_with_note(self, config, backend):
        m = ScriptedToolModel(script=[("text", "投机版"), ("tool", [(TOOL_SEARCH, {"query": "Carnot cycle steps"})])])
        events: list[dict] = []
        r = go(config, backend, m, gap=Gap("insufficient"), events=events)
        # 投机那一版被作废（多半在吐字之前就被叫停了，`stop_reason="cancelled"`）
        assert len(r.discarded) == 1 and r.discarded[0] is not r.attempts[0].completion
        assert "【片段完整性提示】" in m.calls[1]["messages"][-1]["content"]
        assert "投机版" not in replay(events)
        assert replay(events) == r.final_text
        assert len(r.agent.steps) == 1

    def test_preamble_before_a_tool_call_is_taken_back(self, config, backend):
        m = ScriptedToolModel(script=[("preamble+tool", "我先去查一下。", [(TOOL_SEARCH, {"query": "x"})])])
        events: list[dict] = []
        r = go(config, backend, m, events=events)
        assert any(e["type"] == "reset" and e["reason"] == "tool" for e in events)
        assert replay(events) == r.final_text
        assert "我先去查" not in r.final_text

    def test_history_only_gets_question_and_final_answer(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "x"})])])
        tr = Trajectory()
        r = go(config, backend, m, trajectory=tr)
        assert [h["role"] for h in tr.history] == ["user", "assistant"]
        assert tr.history[1]["content"] == r.final_text
        assert isinstance(tr.history[0]["content"], str)

    def test_costs_and_critical_path_include_tool_steps(self, config, backend):
        m = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "x"})])])
        r = go(config, backend, m)
        step_cost = r.agent.steps[0].cost
        attempt_cost = r.attempts[0].completion.cost
        assert r.cost_usd == pytest.approx(step_cost + attempt_cost + r.gap.cost_usd + r.agent.cost_usd - step_cost)
        assert r.usage.input_tokens == 200
        # max(判断 5ms, 第 1 步 2ms) + 工具 + 第 2 步 2ms
        assert r.latency_ms >= 5.0 + 2.0
        assert r.latency_ms == r.agent.critical_path_ms


class TestDownstreamUnchanged:
    def test_hit_documents_stay_aligned(self):
        first = ContextPack(question=Q, slices=(SLICE_A,),
                            meta={"hit_documents": [{"document_id": "d1", "slice_id": "s020"}],
                                  "llm_cost_usd": 0.001})
        c = _Collected(first)
        c.add(ContextPack(question="x", slices=(SLICE_A, SLICE_C),
                          meta={"hit_documents": [{"document_id": "d1", "slice_id": "s020"},
                                                  {"document_id": "d1", "slice_id": "s034"}],
                                "llm_cost_usd": 0.002}))
        p = c.pack()
        assert [h["slice_id"] for h in p.meta["hit_documents"]] == ["s020", "s034"]
        assert len(p.meta["hit_documents"]) == len(p.slices)
        assert p.meta["llm_cost_usd"] == pytest.approx(0.003)

    def test_misaligned_hits_produce_none_rather_than_wrong_ones(self):
        first = ContextPack(question=Q, slices=(SLICE_A,),
                            meta={"hit_documents": [{"document_id": "d1", "slice_id": "s020"}]})
        c = _Collected(first)
        c.add(ContextPack(question="x", slices=(SLICE_C,), meta={}))
        assert "hit_documents" not in c.pack().meta

    def test_unknown_retrieval_cost_stays_unknown(self):
        c = _Collected(ContextPack(question=Q, slices=(), meta={"llm_cost_usd": 0.001}))
        c.add(ContextPack(question="x", slices=(), meta={"llm_cost_usd": None}))
        assert c.pack().meta["llm_cost_usd"] is None


class TestRoute:
    def test_chat_answers_without_calling_the_explainer(self, config, backend):
        m = ScriptedToolModel()
        route = RouteResult(route="chat", reply="不客气，有问题随时问。", cost_usd=0.0003,
                            completion=Completion(text="不客气，有问题随时问。", usage=Usage(10, 10),
                                                  latency_ms=300.0, model="fake:claude-haiku-4-5"))
        events: list[dict] = []
        r = go(config, backend, m, route=route, events=events)
        assert m.calls == []
        assert r.final_text == "不客气，有问题随时问。" and replay(events) == r.final_text
        assert r.gap.implementation == "null"
        # 路由那次调用就是第 1 版：钱只记一次
        assert r.cost_usd == pytest.approx(r.attempts[0].completion.cost)
        assert events[0]["type"] == "route" and events[0]["route"] == "chat"

    def test_course_route_cost_is_counted(self, config, backend):
        route = RouteResult(route="course", cost_usd=0.0004,
                            completion=None)
        r = go(config, backend, ScriptedToolModel(), route=route)
        assert r.agent.cost_usd == pytest.approx(0.0004)

    @pytest.mark.parametrize("raw,expected,impl", [
        ('{"route": "chat", "reply": "你好！"}', "chat", "model"),
        ('{"route": "course", "reply": ""}', "course", "model"),
        ('{"route": "chat", "reply": ""}', "course", "invalid"),      # chat 却没回复
        ('{"route": "smalltalk", "reply": "hi"}', "course", "invalid"),
        ("不是 JSON", "course", "invalid"),
    ])
    def test_router_parses_and_falls_back_to_course(self, raw, expected, impl):
        class M:
            name = "fake:claude-haiku-4-5"

            def complete(self, system, messages):
                return Completion(text=raw, usage=Usage(10, 5), latency_ms=1.0, model=self.name)

        r = Router(M()).route("谢谢")
        assert (r.route, r.implementation) == (expected, impl)

    def test_router_timeout_is_course_with_unknown_cost(self):
        hold = threading.Event()

        class Slow:
            name = "fake:claude-haiku-4-5"

            def complete(self, system, messages):
                hold.wait(2)
                return Completion(text="{}", usage=Usage(), latency_ms=1.0, model=self.name)

        r = Router(Slow(), timeout_s=0.05).route("hi")
        hold.set()
        assert (r.route, r.implementation, r.cost_usd) == ("course", "error", None)


class TestFallback:
    def test_model_without_tools_runs_the_fixed_pipeline(self, config, backend):
        m = FakeExplainModel()
        assert not supports_tools(m)
        common = dict(round_index=1, question=Q, model=m, reviewer=RuleReviewer(config),
                      style_rules=render_style_rules(config), course_label="22.081J",
                      context=backend.fetch(Q), gap_checker=Gap(), review_enabled=True)
        r = run_with_fallback(trajectory=Trajectory(), retriever=backend, tools=RetrieverTools(backend),
                              **{k: v for k, v in common.items() if k != "review_enabled"})
        ref = run_round(trajectory=Trajectory(), retriever=backend, **common)
        assert r.agent.mode == "pipeline"
        assert r.final_text == ref.final_text
        assert r.request_signature == ref.request_signature

    def test_agent_failure_falls_back_and_counts_what_was_spent(self, config, backend):
        class Flaky(ScriptedToolModel):
            def stream(self, system, messages, *, on_delta=None, cancel=None, tools=None,
                       tool_choice=None, on_tool=None):
                if tools is not None and len(self.calls) >= 1:
                    self.calls.append({})
                    raise RuntimeError("连接被重置")
                return super().stream(system, messages, on_delta=on_delta, cancel=cancel,
                                      tools=tools, tool_choice=tool_choice, on_tool=on_tool)

        m = Flaky(script=[("preamble+tool", "我查一下", [(TOOL_SEARCH, {"query": "x"})])])
        events: list[dict] = []
        tr = Trajectory()
        r = run_with_fallback(
            question=Q, trajectory=tr, retriever=backend, tools=RetrieverTools(backend), model=m,
            reviewer=RuleReviewer(config), style_rules=render_style_rules(config),
            course_label="22.081J", context=backend.fetch(Q), gap_checker=Gap(),
            on_event=events.append)
        assert r.agent.mode == "fallback"
        assert "连接被重置" in r.agent.fallback_error
        # 回落前那一步花了钱、用户没看到 → discarded，计入单轮
        assert any(d.tool_calls for d in r.discarded)
        assert r.cost_usd >= sum(d.cost for d in r.discarded)
        assert any(e["type"] == "agent_fallback" for e in events)
        assert replay(events) == r.final_text
        # 历史只记回落那一轮的一问一答，不重复
        assert [h["role"] for h in tr.history] == ["user", "assistant"]

    def test_null_gap_checker_hides_the_coverage_tool(self):
        specs = tool_specs(can_read_page=False, can_check=not isinstance(NullGapChecker(), NullGapChecker))
        assert [s["name"] for s in specs] == [TOOL_SEARCH]
