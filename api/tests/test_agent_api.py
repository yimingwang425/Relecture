"""`/explain` 与 `/explain/stream` 走多智能体链路（口径 F109–F114）。

`test_explain.py` / `test_stream.py` 里的替身模型不收 `tools`，它们走的是固定流水线
（`agent.mode == "pipeline"`）——那几百条断言守的是回落那条路。这里注入**会调工具的**
替身与路由器，守 agent 那条路从服务端外面看是什么样子。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.supabase import SupabaseClient
from explain.agent import TOOL_PAGE, TOOL_SEARCH
from explain.fake import ScriptedToolModel
from explain.llm import Completion, Usage
from explain.router import Router

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import FakeRest, row
from test_explain import SETTINGS
from test_stream import parse_sse, replay

Q = "Bloom filter 的误判率是怎么来的？"


class _RouterModel:
    name = "fake:claude-haiku-4-5"

    def __init__(self, raw: str) -> None:
        self.raw = raw

    def complete(self, system, messages):
        return Completion(text=self.raw, usage=Usage(30, 10), latency_ms=1.0, model=self.name)


def app_with(rest, jwks, model, *, route_raw='{"route": "course", "reply": ""}'):
    app = create_app(
        settings=SETTINGS,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(SETTINGS.rest_url, SETTINGS.supabase_publishable_key, transport=rest),
        model_factory=lambda: model,
        router_factory=(lambda: Router(_RouterModel(route_raw))) if route_raw is not None else None,
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def rest():
    return FakeRest({USER_A: [row(USER_A, detail_level="P0")]})


def post(client, keypair, path="/explain", **body):
    return client.post(path, json={"question": Q, **body},
                       headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"})


class TestAgentFromOutside:
    def test_tool_round_is_visible_in_the_response(self, keypair, jwks, rest):
        model = ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "Bloom filter false positive"})])])
        r = post(app_with(rest, jwks, model), keypair)
        assert r.status_code == 200, r.text
        body = r.json()
        a = body["agent"]
        assert a["mode"] == "agent"
        assert a["route"]["route"] == "course" and a["route"]["implementation"] == "model"
        assert a["tool_rounds"] == 1 and a["tools"][0]["name"] == TOOL_SEARCH
        assert body["text"] == model.answer
        # 路由 + 调工具那一步的钱计入单轮
        assert a["cost_usd"] > 0

    def test_event_carries_agent_fields_without_new_required_keys(self, keypair, jwks, rest):
        """事件构造对着**真的 agent 结果**断言。（不经路由：没给 course_id 时检索走替身，
        替身的 meta 没有 `embed_model`，`retrieval_executed` 本来就构造不出来——与本轮无关。）"""
        from api.events import REQUIRED_KEYS, build_explain_events
        from explain.agent import RetrieverTools, run_agent_round
        from explain.gap import GapResult
        from explain.pipeline import Trajectory
        from explain.review import RuleReviewer
        from explain.style_axes import style_config_for
        from explain.style_prompt import render_style_rules

        from test_events import make_context

        ctx = make_context()

        class Backend:
            def fetch(self, q):
                return ctx

        class Gap:
            def check(self, q, c):
                return GapResult(verdict="covered", implementation="model", cost_usd=0.001)

        config = style_config_for("P0")
        result = run_agent_round(
            round_index=1, question=Q, trajectory=Trajectory(), tools=RetrieverTools(Backend()),
            model=ScriptedToolModel(script=[("tool", [(TOOL_SEARCH, {"query": "x"})])]),
            reviewer=RuleReviewer(config), style_rules=render_style_rules(config),
            course_label="课", context=ctx, gap_checker=Gap())
        turn = next(e for e in build_explain_events(result, config=config, question=Q, course_id=None)
                    if e["event_type"] == "explain_turn")["payload"]
        assert turn["agent_mode"] == "agent"
        assert turn["agent_tool_calls"] == [TOOL_SEARCH]
        assert turn["agent_tool_rounds"] == 1
        assert turn["agent_cost_usd"] > 0
        assert turn["cost_usd"] >= turn["agent_cost_usd"]
        assert not any(k.startswith("agent_") for k in REQUIRED_KEYS["explain_turn"])

    def test_chat_route_answers_without_the_explainer(self, keypair, jwks, rest):
        model = ScriptedToolModel()
        c = app_with(rest, jwks, model, route_raw='{"route": "chat", "reply": "不客气！"}')
        body = post(c, keypair).json()
        assert body["text"] == "不客气！"
        assert model.calls == []
        assert body["retrieval"]["slice_count"] == 0       # 出处面板不显示无关的页
        assert body["slice_keys"] == []
        assert body["gap"]["gap_implementation"] == "null"

    def test_broken_router_means_course(self, keypair, jwks, rest):
        model = ScriptedToolModel()
        body = post(app_with(rest, jwks, model, route_raw="胡言乱语"), keypair).json()
        assert body["agent"]["route"]["route"] == "course"
        assert body["agent"]["route"]["implementation"] == "invalid"
        assert len(model.calls) == 1

    def test_agent_crash_falls_back_and_says_so(self, keypair, jwks, rest):
        model = ScriptedToolModel(script=[("raise", "上游 529")])
        r = post(app_with(rest, jwks, model), keypair)
        assert r.status_code == 200
        body = r.json()
        assert body["agent"]["mode"] == "fallback"
        assert "上游 529" in body["agent"]["fallback_error"]
        assert body["text"]      # 固定流水线接手，学生照样拿到讲解


class TestAgentStream:
    def test_stream_shows_route_and_tools_and_ends_like_json(self, keypair, jwks, rest):
        model = ScriptedToolModel(script=[("preamble+tool", "让我查一下。", [(TOOL_SEARCH, {"query": "x"})])])
        r = post(app_with(rest, jwks, model), keypair, path="/explain/stream")
        events = parse_sse(r.text)
        kinds = [e["type"] for e in events]
        assert kinds[-1] == "done"
        assert "route" in kinds
        assert any(e["type"] == "tool" and e["phase"] == "start" for e in events)
        done_tool = next(e for e in events if e["type"] == "tool" and e["phase"] == "done")
        assert done_tool["name"] == TOOL_SEARCH and "retrieval" in done_tool
        # 那几个字推没推出去**取决于线程调度**：兜底判断先回来、闸门先开，前言就已推出；
        # 工具先开始，前言就被扔掉、从没推出。两条都合法——要钉的是不变量：
        # **推出去了，就必须在工具开始之前收到 reset{reason:"tool"}**，最终屏幕上没有它。
        # （原来断言「从没推出去」，钉的是其中一种调度；2026-10-09 CI 上撞到了另一种。）
        idx_pre = [i for i, e in enumerate(events) if e["type"] == "delta" and "让我查一下" in e.get("text", "")]
        idx_tool = next(i for i, e in enumerate(events) if e["type"] == "tool" and e["phase"] == "start")
        if idx_pre:
            assert any(e["type"] == "reset" and e.get("reason") == "tool"
                       for e in events[idx_pre[-1]:idx_tool + 1]), "前言推出去了却没被收回"
        assert "让我查一下" not in replay(events)
        assert replay(events) == events[-1]["response"]["text"]
        json.dumps(events)   # 全部可序列化
