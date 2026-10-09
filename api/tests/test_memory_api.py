"""前情笔记从服务端外面看是什么样子（口径 F116–F118）。

三件事要从外面钉住，每件都会静默出错：
1. **该整理时真的整理了、落库了**，不该整理时一个字不动；
2. **下一轮真的用上了笔记**：笔记覆盖的轮次不再取原文，笔记在请求里；
3. **整理失败、迁移没跑，讲解照常**——但失败从响应里看得见（F42 / F53）。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.supabase import SupabaseClient
from explain.fake import FakeExplainModel, ScriptedToolModel
from explain.memory import MEMORY_OPEN, TOOL_READ, TOOL_WRITE, MemoryAgent

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import FakeRest, row
from test_explain import SETTINGS, TestConversationHistoryReachesTheModel
from test_stream import parse_sse

CONV = "cccccccc-2222-4222-8222-cccccccccccc"
Recorder = TestConversationHistoryReachesTheModel.Recorder


def prior(n: int, start: int = 1) -> list[dict]:
    out = []
    for i in range(start, start + n):
        out.append({"owner": USER_A, "conversation_id": CONV, "role": "user",
                    "content": f"第{i}问：特征值{i}", "turn_index": i, "round_id": None, "meta": {}})
        out.append({"owner": USER_A, "conversation_id": CONV, "role": "assistant",
                    "content": f"第{i}答。" + "讲解。" * 50 + f"例子{i}号", "turn_index": i,
                    "round_id": None,
                    "meta": {"detail_level": "P2" if i < 4 else "P0",
                             "confusion": {"injected": i == 2}}})
    return out


def fake_rest(n_prior: int, **over) -> FakeRest:
    return FakeRest({USER_A: [row(USER_A, detail_level="P0")]},
                    conversations={CONV: {"id": CONV, "course_id": None, "title": "week1"}},
                    messages=prior(n_prior), **over)


NOTE = {
    "fold_through_turn": 7,
    "summary": "在学特征值，讲到对角化。",
    "concepts": [{"name": "特征值", "turns": [1, 2], "status": "struggling", "note": "第2轮没懂，用过拉伸的例子"}],
    "open_questions": [], "preferences": ["多举例"], "dropped_concepts": [],
}


def memory_factory(script, box=None):
    def make():
        m = ScriptedToolModel(script=script, name="fake:claude-haiku-4-5")
        if box is not None:
            box.append(m)
        return MemoryAgent(m)
    return make


def build(rest, jwks, *, model=None, memory=None) -> TestClient:
    app = create_app(
        settings=SETTINGS,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(SETTINGS.rest_url, SETTINGS.supabase_publishable_key, transport=rest),
        model_factory=(lambda: model) if model is not None else (lambda: FakeExplainModel()),
        memory_factory=memory,
    )
    return TestClient(app, raise_server_exceptions=False)


def ask(client, keypair, path="/explain", q="那第二点再展开讲讲"):
    return client.post(path, json={"question": q, "conversation_id": CONV},
                       headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"})


GOOD = [("tool", [(TOOL_READ, {"from_turn": 1, "to_turn": 4})]), ("tool", [(TOOL_WRITE, NOTE)])]


class TestWhenItRuns:
    def test_below_the_line_nothing_happens(self, jwks, keypair):
        """允许控制：短会话不整理、不写库。少了它，「每轮都整理」也会让下面全绿。"""
        rest = fake_rest(5)
        box: list = []
        body = ask(build(rest, jwks, memory=memory_factory(GOOD, box)), keypair).json()
        assert body["memory"]["compaction"]["status"] == "not_needed"
        assert body["memory"]["through_turn"] is None
        assert rest.memories == [] and box == []

    def test_above_the_line_it_compacts_and_persists(self, jwks, keypair):
        rest = fake_rest(10)
        box: list = []
        r = ask(build(rest, jwks, memory=memory_factory(GOOD, box)), keypair)
        assert r.status_code == 200, r.text
        c = r.json()["memory"]["compaction"]
        assert c["status"] == "written" and c["persisted"] is True, c
        assert c["through_turn"] == 7 and c["plan"]["lo"] == 1 and c["plan"]["hi"] == 7
        assert [t["name"] for t in c["tools"]] == [TOOL_READ, TOOL_WRITE]

        [saved] = rest.memories
        assert saved["owner"] == USER_A and saved["status"] == "written"
        assert saved["through_turn"] == 7 and saved["memory"]["summary"] == NOTE["summary"]
        assert saved["meta"]["triggered_by_turn"] == 11
        json.dumps(saved)
        # 信号真的从 messages.meta 进了 agent 的目录
        brief = box[0].calls[0]["messages"][0]["content"]
        assert "调整了讲法" in brief and "没看懂" in brief
        # 读到的全文真的交回了模型
        assert "例子3号" in str(box[0].calls[1]["messages"])

    def test_old_long_conversation_starts_from_turn_one(self, jwks, keypair):
        """**按编号数，不按读回来的行数。** 历史只取最近 40 条——按行数数的话，
        一个 30 轮的老会话会从第 11 轮开始整理，前 10 轮永远丢了。"""
        rest = fake_rest(30)
        note = {**NOTE, "fold_through_turn": 12}
        c = ask(build(rest, jwks, memory=memory_factory([("tool", [(TOOL_WRITE, note)])])),
                keypair).json()["memory"]["compaction"]
        assert c["plan"]["lo"] == 1 and c["plan"]["hi"] == 12
        assert c["status"] == "written"


class TestNextRoundUsesTheNote:
    def test_folded_turns_are_gone_and_the_note_is_in(self, jwks, keypair):
        rest = fake_rest(10)
        ask(build(rest, jwks, memory=memory_factory(GOOD)), keypair)
        assert rest.memories

        rec = Recorder()
        body = ask(build(rest, jwks, model=rec, memory=memory_factory(GOOD)), keypair).json()
        assert body["memory"]["through_turn"] == 7 and body["memory"]["chars"] > 0
        sent = rec.seen
        assert sent[0]["role"] == "user" and sent[0]["content"].startswith(MEMORY_OPEN)
        assert "拉伸的例子" in sent[0]["content"] and sent[0]["content"].endswith("第8问：特征值8")
        texts = [m["content"] for m in sent]
        assert not any(t.startswith("第7答") or t == "第1问：特征值1" for t in texts)
        # 原文只取笔记之后的（库这一侧真的按 turn_index 过滤了）
        assert any("turn_index=gt.7" in u for u, _ in rest.seen)

    def test_without_a_note_history_is_unchanged(self, jwks, keypair):
        """允许控制：没有笔记时历史原样，第一条就是第 1 问。"""
        rec = Recorder()
        ask(build(fake_rest(3), jwks, model=rec), keypair)
        assert rec.seen[0]["content"] == "第1问：特征值1"


class TestFailuresDoNotTouchTheAnswer:
    def test_agent_failure_is_recorded_not_raised(self, jwks, keypair):
        rest = fake_rest(10)
        r = ask(build(rest, jwks, memory=memory_factory([("raise", "上游 529")])), keypair)
        assert r.status_code == 200 and r.json()["text"]
        c = r.json()["memory"]["compaction"]
        assert c["status"] == "failed" and "529" in c["error"]
        [saved] = rest.memories
        assert saved["status"] == "failed" and saved["memory"] is None and saved["through_turn"] is None

    def test_missing_table_is_visible_and_harmless(self, jwks, keypair):
        """构建先上、迁移没跑：讲解照常，**两处都说出来**（读笔记失败、写笔记失败）。"""
        rest = fake_rest(10, memories_table_missing=True)
        r = ask(build(rest, jwks, memory=memory_factory(GOOD)), keypair)
        assert r.status_code == 200 and r.json()["text"]
        m = r.json()["memory"]
        assert "PGRST205" in m["load_error"]
        assert m["compaction"]["persisted"] is False and "PGRST205" in m["compaction"]["persist_error"]
        assert r.json()["persisted"]["written"] == 2      # 消息照常落库

    def test_unavailable_without_a_model(self, jwks, keypair):
        c = ask(build(fake_rest(10), jwks), keypair).json()["memory"]["compaction"]
        assert c["status"] == "unavailable" and c["plan"]["hi"] == 7


class TestStream:
    def test_compaction_runs_after_done(self, jwks, keypair):
        rest = fake_rest(10)
        events = parse_sse(ask(build(rest, jwks, memory=memory_factory(GOOD)), keypair,
                               path="/explain/stream").text)
        kinds = [e["type"] for e in events]
        done = kinds.index("done")
        # done 里只说「要整理」；整理在 done **之后**，结果另发一条
        assert events[done]["response"]["memory"]["compaction"]["status"] == "pending"
        assert kinds[done + 1] == "memory" and kinds[-1] == "memory"
        assert events[-1]["compaction"]["status"] == "written"
        assert rest.memories and rest.memories[0]["through_turn"] == 7
