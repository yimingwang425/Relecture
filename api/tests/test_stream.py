"""`POST /explain/stream`（口径 F94）。

## 守的是三件事

1. **流的终点与 `/explain` 是同一个东西**：`done` 事件里的响应体与整段路由
   逐字段同形，且按「`reset` 清屏」重放 `delta` 得到的正文 == `done.text`。
   两条路由共用一段代码，这一条让「共用」可被核验，而不是靠约定。
2. **开流之前能判的错仍然是状态码**：开了流就是 200，之后只能用事件报错——
   「key 没配」若变成一个 200 的流，前端的重试判断就全错了。
3. **开流之后的失败不静默**：生成抛异常 → `error` 事件带 status，流正常结束。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.settings import Settings
from api.supabase import SupabaseClient

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import FakeRest, row
from test_explain import SETTINGS, build


def parse_sse(body: str) -> list[dict]:
    """把 SSE 文本拆成 `[{"type":..., **data}]`。注释行（保活）丢掉。"""
    out: list[dict] = []
    for block in body.split("\n\n"):
        kind, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                kind = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if kind is not None:
            out.append({"type": kind, **(data or {})})
    return out


def replay(events: list[dict]) -> str:
    text = ""
    for e in events:
        if e["type"] == "reset":
            text = ""
        elif e["type"] == "delta":
            text += e["text"]
    return text


@pytest.fixture
def rest():
    return FakeRest({USER_A: [row(USER_A, detail_level="P0")]})


def stream(client, keypair, q="Bloom filter 的误判率是怎么来的？"):
    return client.post("/explain/stream", json={"question": q},
                       headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"})


class TestStreamEndsWhereTheJsonRouteEnds:
    def test_done_has_the_same_shape_as_explain(self, keypair, jwks, rest):
        c = build(jwks, rest)
        r = stream(c, keypair)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.text)
        assert events[-1]["type"] == "done"
        done = events[-1]["response"]
        whole = c.post("/explain", json={"question": "Bloom filter 的误判率是怎么来的？"},
                       headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"}).json()
        assert set(done) == set(whole)
        assert done["text"] == whole["text"]          # 假模型是确定性的

    def test_replayed_deltas_equal_the_final_text(self, keypair, jwks, rest):
        events = parse_sse(stream(build(jwks, rest), keypair).text)
        assert replay(events) == events[-1]["response"]["text"]

    def test_reverse_control_dropping_deltas_breaks_replay(self, keypair, jwks, rest):
        """**反向验证**：少一段 delta，重放就对不上——上一条不是恒绿的。"""
        events = parse_sse(stream(build(jwks, rest), keypair).text)
        deltas = [i for i, e in enumerate(events) if e["type"] == "delta"]
        assert deltas, "一段 delta 都没有"
        broken = [e for i, e in enumerate(events) if i != deltas[0]]
        assert replay(broken) != events[-1]["response"]["text"]

    def test_event_order(self, keypair, jwks, rest):
        kinds = [e["type"] for e in parse_sse(stream(build(jwks, rest), keypair).text)]
        assert kinds[0] == "stage" and kinds[1] == "retrieval"
        assert kinds.index("retrieval") < kinds.index("delta")
        assert kinds.count("done") == 1 and kinds[-1] == "done"
        assert "gap" in kinds

    def test_first_token_latency_is_in_the_response(self, keypair, jwks, rest):
        done = parse_sse(stream(build(jwks, rest), keypair).text)[-1]["response"]
        lat = done["latency"]
        assert isinstance(lat["first_token_ms"], float)
        assert lat["first_token_ms"] <= lat["wall_ms"]


class TestErrorsBeforeAndAfterTheStreamOpens:
    def test_missing_key_is_503_not_a_200_stream(self, keypair, jwks, rest):
        settings = Settings(supabase_url="https://example.supabase.co",
                            supabase_publishable_key="sb_publishable_test",
                            anthropic_api_key=None)
        app = create_app(
            settings=settings,
            verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
            supabase=SupabaseClient(settings.rest_url, settings.supabase_publishable_key,
                                    transport=rest),
        )
        r = stream(TestClient(app, raise_server_exceptions=False), keypair)
        assert r.status_code == 503
        assert "ANTHROPIC_API_KEY" in r.json()["detail"]

    def test_failure_after_open_is_an_error_event(self, keypair, jwks, rest):
        class Broken:
            name = "fake:broken"

            def complete(self, system, messages):
                raise RuntimeError("upstream exploded")

            def request_signature(self, system, messages):
                return "x"

        r = stream(build(jwks, rest, model=Broken()), keypair)
        assert r.status_code == 200
        events = parse_sse(r.text)
        assert events[-1]["type"] == "error"
        assert events[-1]["status"] == 500
        assert "upstream exploded" in events[-1]["detail"]
        assert not any(e["type"] == "done" for e in events)

    def test_unauthenticated_is_401(self, jwks, rest):
        r = build(jwks, rest).post("/explain/stream", json={"question": "q"})
        assert r.status_code == 401


def test_settings_constant_is_shared():
    """`build()` 来自 `test_explain`——两份测试用的是同一套假服务端，不各配一份。"""
    assert SETTINGS.anthropic_api_key
