"""出处核对从服务端外面看是什么样子（口径 F121–F123）。

要从外面钉住的几件事，每件都会静默出错：
1. **讲课件的轮真的核了**，结果在 `done` 之后作为 `verify` 事件到，且落库、挂在这一轮上；
2. **不该核的轮不核**：寒暄 / 非学习 / 危机，没有课件片段——原因从响应里看得见；
3. **核对失败、迁移没跑，讲解照常**——失败与「没存上」从结果里看得见（F42 / F53）。

核对员本身的判断逻辑在 `explain/tests/test_verify.py`；这里的替身只管「被调用了没有、拿到了什么」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.supabase import SupabaseClient
from explain.fake import FakeExplainModel
from explain.router import CRISIS_REPLY, RouteResult
from explain.verify import Claim, VerifyRun

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import FakeRest, row
from test_explain import SETTINGS
from test_stream import parse_sse

CONV = "cccccccc-3333-4333-8333-cccccccccccc"


class Checker:
    """记下每次调用拿到的讲解与上下文；返回一份固定的判定。"""

    def __init__(self, retriever, box, *, status="written", raises=False):
        self.retriever, self.box, self.status, self.raises = retriever, box, status, raises

    def verify(self, *, answer, context):
        self.box.append({"answer": answer, "context": context, "retriever": self.retriever})
        if self.raises:
            raise RuntimeError("接线坏了")
        claims = (Claim("特征值是拉伸倍数", "fact", 2, "supported", "eigenvalue is the stretch", 2, "deck.pdf"),
                  Claim("就像橡皮筋", "supplement", None, "supplement"))
        return VerifyRun(status=self.status, claims=claims if self.status == "written" else (),
                         error=None if self.status == "written" else "6 步之内没有交出",
                         model="fake:claude-haiku-4-5")


def fake_rest(**over) -> FakeRest:
    return FakeRest({USER_A: [row(USER_A, detail_level="P0")]},
                    conversations={CONV: {"id": CONV, "course_id": None, "title": "week1"}}, **over)


def build(rest, jwks, *, box=None, checker_kw=None, router=None, factory=True) -> TestClient:
    app = create_app(
        settings=SETTINGS,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(SETTINGS.rest_url, SETTINGS.supabase_publishable_key, transport=rest),
        model_factory=lambda: FakeExplainModel(),
        router_factory=router,
        verify_factory=(lambda r: Checker(r, box, **(checker_kw or {}))) if factory else None,
    )
    return TestClient(app, raise_server_exceptions=False)


def ask(client, keypair, path="/explain", conv=CONV, q="特征值是什么？"):
    body = {"question": q, **({"conversation_id": conv} if conv else {})}
    return client.post(path, json=body, headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"})


def fixed_router(route, reply=""):
    class R:
        def route(self, question, history=None):
            return RouteResult(route=route, implementation="model", reply=reply, cost_usd=0.0)
    return lambda: R()


class TestCourseRoundIsChecked:
    def test_json_route_returns_the_result_and_stores_it(self, jwks, keypair):
        rest, box = fake_rest(), []
        out = ask(build(rest, jwks, box=box), keypair).json()
        v = out["verification"]
        assert v["status"] == "written" and v["persisted"] is True, v
        assert v["counts"] == {"supported": 1, "wrong_page": 0, "contradicted": 0, "not_found": 0, "supplement": 1}
        # 核的是**这一轮学生看到的那段**，用的是这一轮的上下文
        assert box[0]["answer"] == out["text"]
        assert len(box[0]["context"].slices) == len(out["retrieval"]["slices"]) > 0
        # 挂在这一轮上
        (stored,) = rest.checks
        assert stored["round_id"] == out["round_id"] and stored["conversation_id"] == CONV
        assert stored["owner"] == USER_A and stored["result"]["counts"]["supported"] == 1

    def test_stream_sends_verify_after_done(self, jwks, keypair):
        rest, box = fake_rest(), []
        r = ask(build(rest, jwks, box=box), keypair, path="/explain/stream")
        events = parse_sse(r.text)
        kinds = [e["type"] for e in events]
        assert kinds.index("verify") > kinds.index("done")
        by = {e["type"]: e for e in events}
        done = by["done"]["response"]
        verify = by["verify"]["verification"]
        assert done["verification"] == {"status": "pending"}       # done 里只有计划
        assert verify["status"] == "written" and verify["persisted"] is True
        assert rest.checks[0]["round_id"] == done["round_id"]

    def test_failed_check_is_stored_too(self, jwks, keypair):
        """失败也记一行：花掉的钱与原因要查得到。"""
        rest = fake_rest()
        v = ask(build(rest, jwks, box=[], checker_kw={"status": "failed"}), keypair).json()["verification"]
        assert v["status"] == "failed" and v["persisted"] is True
        assert rest.checks[0]["status"] == "failed"


class TestNotChecked:
    def test_direct_routes_are_skipped(self, jwks, keypair):
        for route, reply in (("chat", "不客气！"), ("off_topic", "想先缓一缓也没关系。"),
                             ("crisis", CRISIS_REPLY)):
            rest, box = fake_rest(), []
            out = ask(build(rest, jwks, box=box, router=fixed_router(route, reply)), keypair).json()
            assert out["verification"]["status"] == "skipped", route
            assert route in out["verification"]["reason"]
            assert box == [] and rest.checks == []

    def test_course_route_is_checked(self, jwks, keypair):
        """允许控制：路由明确判 course 时照核——否则「一律跳过」也会让上面全绿。"""
        box = []
        out = ask(build(fake_rest(), jwks, box=box, router=fixed_router("course")), keypair).json()
        assert out["verification"]["status"] == "written" and len(box) == 1

    def test_no_model_is_unavailable_not_silent(self, jwks, keypair):
        out = ask(build(fake_rest(), jwks, factory=False), keypair).json()
        assert out["verification"]["status"] == "unavailable" and out["verification"]["reason"]


class TestFailuresDoNotBreakTheRound:
    def test_missing_table_is_visible(self, jwks, keypair):
        """迁移没跑：核对照常给页面，只是没存上——而且说出来。"""
        out = ask(build(fake_rest(checks_table_missing=True), jwks, box=[]), keypair).json()
        assert out["text"] and out["persisted"]["written"] == 2
        v = out["verification"]
        assert v["status"] == "written" and v["persisted"] is False and "answer_checks" in v["persist_error"]

    def test_checker_crash_does_not_break_the_round(self, jwks, keypair):
        r = ask(build(fake_rest(), jwks, box=[], checker_kw={"raises": True}), keypair, path="/explain/stream")
        events = {e["type"]: e for e in parse_sse(r.text)}
        assert events["done"]["response"]["text"]
        assert events["verify"]["verification"]["status"] == "failed"
        assert "error" not in events

    def test_without_conversation_checks_but_does_not_store(self, jwks, keypair):
        """没有会话：库里的触发器要求这一轮的讲解先在，存了也会被拒——只核不存，并说明。"""
        rest = fake_rest()
        v = ask(build(rest, jwks, box=[]), keypair, conv=None).json()["verification"]
        assert v["status"] == "written" and v["persisted"] is False and v["persist_error"]
        assert rest.checks == []

    def test_round_not_persisted_is_not_stored(self, jwks, keypair):
        """这一轮的问答没落库：核对不去写——写了也挂不上（触发器要求讲解先在），只是白白多一次失败的请求。"""
        rest = fake_rest(fail_message_insert=True)
        v = ask(build(rest, jwks, box=[]), keypair).json()["verification"]
        assert v["status"] == "written" and v["persisted"] is False
        assert not any("/answer_checks" in url for url, _ in rest.writes)
