"""阶段 3：`style_config` 那一行**第一次成为一次讲解生成的输入**。

## 本文件测到哪里为止

端到端那条链有四段：**库里那一行 → StyleConfig → prompt → 模型输出**。
前三段在这里离线钉死；**第四段（模型服从不服从 prompt）需要真调模型**，
在 `scripts/acceptance_p0_p3.py`，属人工验收。

但第四段不是唯一的下游：配置**同时驱动判定**（口径 X05）。
`TestConfigReachesTheJudgement` 用同一段固定文本在 P0 与 P3 下各判一次，
结论必须相反——那证明库里那一行真的进了 `RuleReviewer`，
**不需要模型参与**，也就不受「模型这次恰好听话」的影响。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.settings import Settings
from api.supabase import SupabaseClient

from conftest import AUDIENCE, ISSUER, USER_A, USER_B, StaticJwks
from fake_db import FakeRest, row

SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    supabase_publishable_key="sb_publishable_test",
    anthropic_api_key="sk-ant-fake-for-tests",
)


def build(jwks, rest, *, model=None, settings=SETTINGS) -> TestClient:
    from explain.fake import FakeExplainModel

    app = create_app(
        settings=settings,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(settings.rest_url, settings.supabase_publishable_key, transport=rest),
        model_factory=(lambda: model) if model is not None else (lambda: FakeExplainModel()),
    )
    return TestClient(app, raise_server_exceptions=False)


def ask(client, keypair, sub=USER_A, q="Bloom filter 的误判率是怎么来的？"):
    return client.post(
        "/explain",
        json={"question": q},
        headers={"Authorization": f"Bearer {keypair.sign(sub=sub)}"},
    )


@pytest.fixture
def rest():
    """两个账号：一个 P0，一个 P3。其余三条轴也不同，供 B 类断言用。"""
    return FakeRest({
        USER_A: [row(USER_A, detail_level="P0")],
        USER_B: [row(USER_B, detail_level="P3", use_examples="fewer",
                     structure="buildup_first", depth="derivation")],
    })


class TestConfigReachesThePrompt:
    """库里那一行 → prompt。**四条轴都要能在 prompt 里被看见。**"""

    def test_detail_level_changes_the_prompt(self, keypair, jwks, rest):
        c = build(jwks, rest)
        a = ask(c, keypair, USER_A).json()
        b = ask(c, keypair, USER_B).json()
        assert a["style_rules"] != b["style_rules"], "P0 与 P3 的 prompt 一模一样 —— 档位没进 prompt"
        assert "30–120 字" in a["style_rules"], a["style_rules"]
        assert "15–40 字" in b["style_rules"], b["style_rules"]

    def test_b_class_axes_reach_the_prompt(self, keypair, jwks, rest):
        """**口径 F45**：B 类不判定 ≠ B 类不进 prompt。

        不渲染的话，F1 那 6 步成对比较里有 4 步在采集一个对输出毫无影响的东西。
        """
        c = build(jwks, rest)
        a = ask(c, keypair, USER_A).json()["style_rules"]
        b = ask(c, keypair, USER_B).json()["style_rules"]
        assert "多举例子" in a and "少举例子" in b
        assert "先给结论" in a and "先铺垫后收结论" in b
        assert "讲到为什么" in a and "讲到推导" in b

    def test_prompt_has_both_classes_in_the_sample_shape(self, keypair, jwks, rest):
        """A 类三条 + 整篇长度 + B 类三条，拼法与 16 个样例同形（口径 F19）。

        2026-09-20 多了「整篇长度」那一行，所以是 **7** 条不是 6 条。
        这个数**不是装饰**：它是这条断言唯一的分辨力来源——
        `expected in rules` 在 expected 只有一行时照样成立。
        """
        from explain.style_axes import style_config_for
        from explain.style_prompt import render_all_style_rules

        rules = ask(build(jwks, rest), keypair, USER_A).json()["style_rules"]
        expected = render_all_style_rules(
            style_config_for("P0"),
            {"use_examples": "more", "structure": "conclusion_first", "depth": "why"},
        )
        assert expected in rules
        assert len([ln for ln in expected.splitlines() if ln.startswith("- ")]) == 7

    def test_fingerprint_differs_between_tiers(self, keypair, jwks, rest):
        """验收要靠它把「配置没生效」与「两次配置本来就一样」分开。"""
        c = build(jwks, rest)
        assert ask(c, keypair, USER_A).json()["config_fingerprint"] != \
               ask(c, keypair, USER_B).json()["config_fingerprint"]


class TestConfigReachesTheJudgement:
    """库里那一行 → `RuleReviewer`。**同一段文本，两个档位下结论相反。**

    这一条不需要模型参与，所以它不受「模型这次恰好听话」影响——
    它单独证明了配置真的驱动判定（口径 X05：配置同时驱动 prompt 与判定）。
    """

    def test_same_text_passes_p0_and_fails_p3(self, keypair, jwks, rest):
        from explain.fake import FakeExplainModel

        # 每段约 70 字：落在 P0 的 30–120 里，落在 P3 的 15–40 之外。
        # `corrected_chars` 也设成 70 —— 不让假模型「收到反馈就改好」，
        # 否则 P3 那条会因为重生成成功而变绿，测到的就不是判定了。
        stubborn = FakeExplainModel(target_chars=70, corrected_chars=70)
        c = build(jwks, rest, model=stubborn)

        a = ask(c, keypair, USER_A).json()
        b = ask(c, keypair, USER_B).json()

        assert a["all_activated_passed"] is True, f"P0 下 70 字/段应当达标：{a}"
        assert b["all_activated_passed"] is False, f"P3 下 70 字/段应当不达标：{b}"
        assert a["accepted"] is True and b["accepted"] is False
        # P3 不达标 → 触发重生成直到触顶（PRD §10 降级输出）
        assert b["regenerations"] > 0, "P3 下不达标却没有触发重生成"
        assert a["regenerations"] == 0

    def test_target_range_comes_from_the_row(self, keypair, jwks, rest):
        c = build(jwks, rest)
        assert ask(c, keypair, USER_A).json()["paragraph_length_target"] == {"min": 30, "max": 120}
        assert ask(c, keypair, USER_B).json()["paragraph_length_target"] == {"min": 15, "max": 40}


class TestRefusesToGuess:
    def test_unknown_tier_is_500_not_defaulted(self, keypair, jwks):
        """库里出现取值域外的档位 → **判失败，不回落默认值**。

        回落会让「用户选了 P3」与「库里有个我们不认识的值」在输出里长得一模一样。
        """
        c = build(StaticJwks(keypair), FakeRest({USER_A: [row(USER_A, detail_level="P1")]}))
        r = ask(c, keypair)
        assert r.status_code == 500 and "P1" in r.json()["detail"]

    def test_missing_model_key_is_503_and_says_so(self, keypair, jwks, rest):
        """缺 key 是部署配置问题，不是用户输入问题。"""
        bare = Settings(supabase_url=SETTINGS.supabase_url,
                        supabase_publishable_key=SETTINGS.supabase_publishable_key,
                        anthropic_api_key=None)
        app = create_app(
            settings=bare,
            verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
            supabase=SupabaseClient(bare.rest_url, bare.supabase_publishable_key, transport=rest),
        )
        r = TestClient(app, raise_server_exceptions=False).post(
            "/explain", json={"question": "x"},
            headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]

    def test_explain_needs_auth(self, jwks, rest):
        assert build(jwks, rest).post("/explain", json={"question": "x"}).status_code == 401


# ---------------------------------------------------------------- F5 · 会话


CONV = "cccccccc-1111-4111-8111-cccccccccccc"


def _conv_rest(**over):
    """带一个会话的假库。`messages` 可预置，用来造「已经问过两轮」的局面。"""
    return FakeRest(
        {USER_A: [row(USER_A, detail_level="P0")]},
        conversations={CONV: {"id": CONV, "course_id": None, "title": "week1"}},
        **over,
    )


def _ask_in(client, keypair, q="那第二点再展开讲讲", conversation_id=CONV, **extra):
    body = {"question": q, **extra}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return client.post("/explain", json=body,
                       headers={"Authorization": f"Bearer {keypair.sign(sub=USER_A)}"})


class TestConversationHistoryReachesTheModel:
    """**这一组要证的是「历史真的进了那次请求」**，不是「接口收下了这个字段」。

    收下字段而不用它的实现会让「支持多轮」看起来完全正常：响应 200、正文通顺，
    只是模型每一轮都在从头猜「第二点」指的是什么。这种失败没有任何报错。
    """

    class Recorder:
        """记下最后一次收到的 messages。其余行为与 FakeExplainModel 一致。"""

        def __init__(self):
            from explain.fake import FakeExplainModel

            self.inner = FakeExplainModel()
            self.seen: list[dict] | None = None
            self.name = self.inner.name
            self.params = self.inner.params

        def complete(self, system, messages):
            self.seen = list(messages)
            return self.inner.complete(system, messages)

        def request_signature(self, system, messages):
            return self.inner.request_signature(system, messages)

    def test_prior_turns_are_sent_as_history(self, jwks, keypair):
        rec = self.Recorder()
        rest = _conv_rest(messages=[
            {"conversation_id": CONV, "role": "user", "content": "特征值是什么", "turn_index": 1},
            {"conversation_id": CONV, "role": "assistant",
             "content": "它是只被缩放的那个方向对应的那个数", "turn_index": 1},
        ])
        r = _ask_in(build(jwks, rest, model=rec), keypair)
        assert r.status_code == 200, r.text

        assert rec.seen is not None
        roles = [m["role"] for m in rec.seen]
        # 历史两条在前、本轮在最后。**顺序也要断**：顺序反了模型看到的是
        # 「回答在提问之前」，而它仍然会给出一段通顺的正文。
        assert roles == ["user", "assistant", "user"], roles
        assert rec.seen[0]["content"] == "特征值是什么"
        assert "只被缩放" in rec.seen[1]["content"]

    def test_without_a_conversation_there_is_no_history(self, jwks, keypair):
        """**允许控制。** 少了它，一个「永远塞两条历史进去」的实现也会让上一条全绿。"""
        rec = self.Recorder()
        rest = _conv_rest(messages=[
            {"conversation_id": CONV, "role": "user", "content": "特征值是什么", "turn_index": 1},
            {"conversation_id": CONV, "role": "assistant", "content": "……", "turn_index": 1},
        ])
        r = _ask_in(build(jwks, rest, model=rec), keypair, conversation_id=None)
        assert r.status_code == 200, r.text
        assert [m["role"] for m in rec.seen] == ["user"]

    def test_history_of_another_conversation_does_not_leak_in(self, jwks, keypair):
        """另一个会话的消息**不进这一轮**。按 conversation_id 过滤是唯一的边界。"""
        rec = self.Recorder()
        rest = _conv_rest(messages=[
            {"conversation_id": "别的会话", "role": "user", "content": "别的课的问题", "turn_index": 1},
            {"conversation_id": "别的会话", "role": "assistant", "content": "别的答案", "turn_index": 1},
        ])
        r = _ask_in(build(jwks, rest, model=rec), keypair)
        assert r.status_code == 200, r.text
        assert [m["role"] for m in rec.seen] == ["user"]


class TestTheTurnIsPersisted:
    def test_question_and_answer_are_written_with_one_turn_index(self, jwks, keypair):
        rest = _conv_rest()
        r = _ask_in(build(jwks, rest), keypair, q="特征值是什么")
        assert r.status_code == 200, r.text
        assert r.json()["persisted"] == {"written": 2, "attempted": 2}

        written = [b for u, b in rest.writes if "/messages" in u]
        assert len(written) == 1
        rows = written[0]
        assert [x["role"] for x in rows] == ["user", "assistant"]
        # **一问一答共用同一个 turn_index。** 分开编号会让「平均会话轮次」当场翻倍，
        # 而那是北极星指标（PRD §3.1）。
        assert rows[0]["turn_index"] == rows[1]["turn_index"] == 1
        assert rows[0]["content"] == "特征值是什么"
        assert rows[1]["content"] == r.json()["text"]
        # round_id **只有 assistant 行有**；user 行为 null 是事实，不是遗漏（F50）。
        assert rows[1]["round_id"] == r.json()["round_id"]
        assert "round_id" not in rows[0]

    def test_turn_index_comes_from_the_database_not_the_request(self, jwks, keypair):
        """**请求里那个 turn_index 不作数。**

        前端刷新一次就会从 1 重新数。采信它的话「平均会话轮次」会静静地变小，
        而没有任何东西会报错——所以这里故意传一个错的进去。
        """
        rest = _conv_rest(messages=[
            {"conversation_id": CONV, "role": "user", "content": "第一问", "turn_index": 1},
            {"conversation_id": CONV, "role": "assistant", "content": "第一答", "turn_index": 1},
            {"conversation_id": CONV, "role": "user", "content": "第二问", "turn_index": 2},
            {"conversation_id": CONV, "role": "assistant", "content": "第二答", "turn_index": 2},
        ])
        r = _ask_in(build(jwks, rest), keypair, turn_index=1)
        assert r.status_code == 200, r.text
        assert r.json()["turn_index"] == 3
        rows = [b for u, b in rest.writes if "/messages" in u][0]
        assert rows[0]["turn_index"] == 3

    def test_nothing_is_written_without_a_conversation(self, jwks, keypair):
        """**允许控制**：不给 conversation_id 就是一次性问答，一行都不落。"""
        rest = _conv_rest()
        r = _ask_in(build(jwks, rest), keypair, conversation_id=None)
        assert r.status_code == 200, r.text
        assert r.json()["persisted"] == {"written": 0, "attempted": 0}
        assert not [u for u, _ in rest.writes if "/messages" in u]

    def test_a_write_failure_does_not_swallow_the_answer(self, jwks, keypair):
        """写库失败**不打断这次讲解**，但也不静默（口径 F42，同埋点那条）。

        反过来的实现——写不进去就 500——会让用户丢掉一段已经生成好、
        已经花过钱的讲解，而失败的是一件他并不关心的事。
        """
        rest = _conv_rest(fail_message_insert=True)
        r = _ask_in(build(jwks, rest), keypair)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["text"]
        assert body["persisted"]["written"] == 0
        assert "SupabaseError" in body["persisted"]["error"]

    def test_an_unknown_conversation_is_404(self, jwks, keypair):
        """不存在与「是别人的」**不区分**——区分了就泄露了 id 的存在性。"""
        rest = _conv_rest()
        r = _ask_in(build(jwks, rest), keypair,
                    conversation_id="00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404, r.text
        assert not [u for u, _ in rest.writes if "/messages" in u]


class TestFollowupNote:
    """追问的限定语（口径 F107）：**有历史才加，加在本轮用户消息里**。

    每条正向断言旁边都有反向控制：一个「每轮都加」的实现会让「追问时有」全绿，
    只有「第一轮没有」能把它抓出来。
    """

    Recorder = TestConversationHistoryReachesTheModel.Recorder

    def _history(self):
        return _conv_rest(messages=[
            {"conversation_id": CONV, "role": "user", "content": "特征值是什么", "turn_index": 1},
            {"conversation_id": CONV, "role": "assistant", "content": "只被缩放的那个方向", "turn_index": 1},
        ])

    def test_followup_turn_carries_the_note(self, jwks, keypair):
        from api.followup import FOLLOWUP_NOTE

        rec = self.Recorder()
        r = _ask_in(build(jwks, self._history(), model=rec), keypair, q="没懂，能简单点吗")
        assert r.status_code == 200, r.text
        assert FOLLOWUP_NOTE in rec.seen[-1]["content"]
        # 在**本轮**消息里，不在历史里：历史是上一轮原样，不该被改写。
        assert all(FOLLOWUP_NOTE not in m["content"] for m in rec.seen[:-1])

    def test_first_turn_has_no_note(self, jwks, keypair):
        """反向控制：会话里还没有历史 → 一个字都不加。"""
        from api.followup import FOLLOWUP_NOTE

        rec = self.Recorder()
        r = _ask_in(build(jwks, _conv_rest(), model=rec), keypair)
        assert r.status_code == 200, r.text
        assert FOLLOWUP_NOTE not in rec.seen[-1]["content"]

    def test_one_off_question_has_no_note(self, jwks, keypair):
        """反向控制：不带会话的一次性问答（`/explain` 跨课页）也不加。"""
        from api.followup import FOLLOWUP_NOTE

        rec = self.Recorder()
        r = _ask_in(build(jwks, self._history(), model=rec), keypair, conversation_id=None)
        assert r.status_code == 200, r.text
        assert FOLLOWUP_NOTE not in rec.seen[-1]["content"]

    def test_note_never_asks_for_longer_answers(self):
        """措辞约束（同 F58）：不许出现「更详细 / 更长」——与段落长度的判定方向相反。"""
        from api.followup import FOLLOWUP_NOTE

        assert not any(w in FOLLOWUP_NOTE for w in ("更详细", "详细一点", "更长", "展开讲"))
        assert "新问题" in FOLLOWUP_NOTE, "要让模型分得清：新问题照常回答，不是每轮都重讲"

    def test_prefix_function_is_the_only_switch(self):
        from api.followup import FOLLOWUP_NOTE, followup_prefix

        assert followup_prefix([]) == ""
        assert followup_prefix([{"role": "user", "content": "x"}]) == FOLLOWUP_NOTE
