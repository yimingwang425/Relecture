"""F4 · 「这部分没看懂」：切片锚点、交集注入、幂等、埋点、跨课继承。

## 本组里最重要的一对是 `TestInjectionFires` 与 `TestInjectionIsNotAlwaysOn`

> **一个恒触发的限定语，和一个永远不触发的规则一样糟**，而前者更难发现——
> 它的输出看起来完全正常，只是每一轮都在多讲一句。

所以每一条正向断言都配一条**反向控制**，就写在它旁边，跑一次就都执行过：
把被测逻辑换成平凡实现（注入恒空 / 交集恒真 / 配置按课分行），
上一条必须红。不会红的断言是空的（通则七 #4）。

## 「无交集」那一条为什么用**两份讲义的同一个 slice_id**

课程 A 的 `DOC_A1#s001` 与课程 B 的 `DOC_B1#s001`——
2026-09-12 真库上就是这个形态（一门课两份讲义都有 `s001`）。
只拿 `slice_id` 当键的话这两片会撞成同一个身份，于是标记了 A 会让 B 也注入，
**而输出看起来完全正常**。用这一对做「无交集」的样本，
这条断言就同时钉住了口径 F66（键是两段）。

## 这里证明不了什么（口径 F34）

假 PostgREST 的 `&&` 是我用 Python 写的，它按我以为的语义实现。
「PostgREST 的 `ov.` 真的是 `&&`」「RLS 真的挡得住」「唯一约束真的在」
只有真库能答——`api/scripts/verify_f4_marks.py`；
迁移原文那一层在 `supabase/verify_rls_local.mjs`（PGlite，含 R17/R18/R19）。
本地绿数与真库观测**分开记，不互相顶替**。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.marks import CONFUSION_NOTE, slice_key, slice_keys_of
from api.settings import Settings
from api.supabase import SupabaseClient

from conftest import AUDIENCE, ISSUER, USER_A, USER_B, StaticJwks  # noqa: F401
from fake_db import row
from fake_rest import RoutedRest, parse_pg_array

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20260913150000_f4_confusion_marks.sql"

SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    supabase_publishable_key="sb_publishable_test",
    anthropic_api_key="sk-ant-fake-for-tests",
)

COURSE_A = "aaaaaaaa-c001-4001-8001-aaaaaaaaaaaa"
COURSE_B = "bbbbbbbb-c002-4002-8002-bbbbbbbbbbbb"
DOC_A1 = "11111111-d001-4001-8001-111111111111"
DOC_B1 = "22222222-d002-4002-8002-222222222222"
SESSION = "99999999-9999-4999-8999-999999999999"
QUESTION = "城市规划的历史传统有哪些？"


# ---------------------------------------------------------------- 夹具


class FakeEmbedder:
    """确定性假嵌入器。**维度取生产选型那个值**，不写字面量——
    写死一个数的话，选型改了这里会以「数据库类型错误」的样子失败。"""

    def __init__(self) -> None:
        from retrieval.selection import SELECTED_DIMENSIONS

        self.dimensions = SELECTED_DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            v[len(t) % self.dimensions] = 1.0
            out.append(v)
        return out


class RecordingModel:
    """假模型 + **把它真正收到的 messages 留下来**。

    「注入触发了」这条断言必须看**发出去的那一份**，不能看服务端自己报的
    `confusion.injected`——那样两端同源，一个只改标志位不改 prompt 的实现
    照样全绿（F16 的同一形态）。
    """

    def __init__(self) -> None:
        from explain.fake import FakeExplainModel

        self._inner = FakeExplainModel()
        self.systems: list[str] = []
        self.messages: list[list[dict[str, Any]]] = []

    def complete(self, system: str, messages: list[dict[str, Any]]):
        self.systems.append(system)
        self.messages.append([dict(m) for m in messages])
        return self._inner.complete(system, messages)

    def request_signature(self, system: str, messages: list[dict[str, Any]]) -> str:
        return self._inner.request_signature(system, messages)

    @property
    def last_user_turn(self) -> str:
        return str(self.messages[-1][-1]["content"])


def _doc(id_: str, filename: str) -> dict[str, Any]:
    return {"id": id_, "filename": filename, "verdict": "usable",
            "unusable_pages": [], "page_count": 3}


def _slice(document_id: str, slice_id: str, text: str) -> dict[str, Any]:
    return {"document_id": document_id, "slice_id": slice_id, "ordinal": 1,
            "page_indices": [1], "title": "标题", "text": text,
            "char_count": len(text)}


@pytest.fixture
def rest() -> RoutedRest:
    """两门课，各一份讲义，**两份讲义的第一片都叫 `s001`**（真库形态）。"""
    return RoutedRest(
        style_rows={USER_A: [row(USER_A)], USER_B: [row(USER_B)]},
        courses={COURSE_A: {"id": COURSE_A, "name": "11.201 城市规划", "owner": USER_A},
                 COURSE_B: {"id": COURSE_B, "name": "22.081 能源", "owner": USER_A}},
        documents={COURSE_A: [_doc(DOC_A1, "规划.pdf")],
                   COURSE_B: [_doc(DOC_B1, "能源.pdf")]},
        slices={DOC_A1: [_slice(DOC_A1, "s001", "规划这一片的正文")],
                DOC_B1: [_slice(DOC_B1, "s001", "能源这一片的正文")]},
        matches={COURSE_A: [{"document_id": DOC_A1, "slice_id": "s001", "similarity": 0.9}],
                 COURSE_B: [{"document_id": DOC_B1, "slice_id": "s001", "similarity": 0.8}]},
    )


def build(jwks, rest, *, model=None, db=None) -> TestClient:
    """⚠️ `model` 缺省也要给一个假的：不给的话 `model_factory` 回落到
    `_default_model()`，那会真去 new 一个 `AnthropicModel`。"""
    from explain.fake import FakeExplainModel

    client_db = db or SupabaseClient(
        SETTINGS.rest_url, SETTINGS.supabase_publishable_key, transport=rest)
    the_model = model if model is not None else FakeExplainModel()
    app = create_app(
        settings=SETTINGS,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=client_db,
        model_factory=lambda: the_model,
        embedder_factory=lambda: FakeEmbedder(),
    )
    return TestClient(app, raise_server_exceptions=False)


def explain(client, keypair, course_id: str, *, sub: str = USER_A,
            question: str = QUESTION):
    r = client.post("/explain",
                    json={"question": question, "course_id": course_id,
                          "session_id": SESSION},
                    headers={"Authorization": f"Bearer {keypair.sign(sub=sub)}"})
    assert r.status_code == 200, r.text
    return r.json()


def mark(client, keypair, out: dict[str, Any], *, sub: str = USER_A,
         course_id: str | None = COURSE_A, session_id: str | None = SESSION):
    """把 `/explain` 的输出**原样带回来**——前端要做的也正是这件事。"""
    payload: dict[str, Any] = {"round_id": out["round_id"],
                               "slice_ids": out["slice_keys"],
                               "question": QUESTION}
    if session_id is not None:
        payload["session_id"] = session_id
    if course_id is not None:
        payload["course_id"] = course_id
    return client.post("/marks", json=payload,
                       headers={"Authorization": f"Bearer {keypair.sign(sub=sub)}"})


# ---------------------------------------------------------------- 键：两段


class TestBothEndsOfTheKey:
    """**这条守卫的两端各自从哪来。**

    交集恒空是本功能最可能的死法，而它的表现是：接口 200、表里有行、
    什么都不注入 —— **没有任何东西会红**。所以「两端从哪来」必须问清楚。

    | | 单测层（本文件） | 真库层（`verify_f4_marks.py` §7） |
    | --- | --- | --- |
    | 写入侧 | `/explain` 响应的 `slice_keys` → 真的 marks API → 假 PostgREST 的行 | 同左，但落的是**真表**，再读回来 |
    | 读取侧 | 下一轮 `slice_keys_of(ctx)` → `marks_overlapping` | `/explain` 响应里的 `retrieval.meta.hit_documents`，即 **`match_slices` 的原始返回** |
    | 交集由谁算 | 我写的 Python | **Postgres 的 `&&`（经 PostgREST 的 `ov.`）** |

    ⚠️ **单测层这一对同源于 `slice_key()`** —— 分隔符一改两端一起改，
    所以「两端拼法一致」这件事在这里**证明不了**（它是构造保证的，无从证伪）。
    这一层能钉住的只有两样，下面两条各管一样：

      1. **格式**：落进表里的那一段，与「两段用冒号连起来」这句话对得上
         —— 这句话在下面**刻意不调 `slice_key()`**，手写一次。
         改了 `KEY_SEPARATOR` 就会红，而那一改会让线上已有的 marks 全部变成孤儿。
      2. **敏感性**：写入侧的键被改了一个字符，下一轮就**必须不注入** ——
         这条把「恒空」那个失败形态本身钉在这里，连同它的三个症状一起。

    DB 往返（JSON → `text[]` → 读回来）与 `ov.` 到底是不是 `&&`，
    **这一层一条都验不了**，那是真库层的活（口径 F34：两种证据不互相顶替）。
    """

    def test_the_stored_key_format_is_pinned_without_calling_slice_key(
            self, keypair, jwks, rest):
        """落进表里的那一段 = 检索返回的两段用**冒号**连起来。

        ⚠️ 右边刻意**不调 `slice_key()`**：调了两端就同源，
        这条断言在分隔符被改成任何东西时都会绿。
        `DOC_A1` / `"s001"` 取自 `matches` 夹具，也就是 `match_slices` 的替身 ——
        它们是**值**的来源，与格式是两件事。
        """
        c = build(jwks, rest)
        first = explain(c, keypair, COURSE_A)
        assert mark(c, keypair, first).status_code == 200
        assert rest.marks[0]["slice_ids"] == [f"{DOC_A1}:s001"], (
            "落库的键与「两段用冒号连起来」对不上 —— 改了 KEY_SEPARATOR 的话，"
            "线上已有的 marks 会全部变成永远不再命中的孤儿")
        # 写入侧与读取侧确实是同一份（这一条是**记录事实**，不是判据：
        # 两端同源于 slice_key()，它无从证伪）。
        assert first["slice_keys"] == rest.marks[0]["slice_ids"]

    def test_reverse_control_one_character_on_the_write_side_kills_it_silently(
            self, keypair, jwks, rest):
        """**反向控制**：写入侧把分隔符换成 `/`，读取侧不变 → 必须不注入。

        这条同时把那个失败形态的**三个症状**一起钉住：
        接口 **200**、表里**有行**、而**什么都不注入**。
        少了它，「交集恒空」在任何一层的断言里都长得像「功能正常，只是这轮没标记过」。
        """
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        first = explain(c, keypair, COURSE_A)

        mangled = [k.replace(":", "/") for k in first["slice_keys"]]
        assert mangled != first["slice_keys"], "夹具里的键压根没有分隔符，这条反向控制是空的"
        r = c.post("/marks",
                   json={"round_id": first["round_id"], "slice_ids": mangled,
                         "question": QUESTION, "session_id": SESSION},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})

        assert r.status_code == 200, "症状一：接口照常 200"
        assert len(rest.marks) == 1, "症状二：表里真的有行"
        second = explain(c, keypair, COURSE_A)
        assert second["confusion"]["injected"] is False, "症状三：什么都不注入"
        assert CONFUSION_NOTE not in model.last_user_turn


class TestSliceKeyIsTwoParts:
    """口径 F66：切片身份是 `(document_id, slice_id)`，`slice_id` 不是 uuid。"""

    def test_same_slice_id_in_two_documents_gives_two_keys(self):
        assert slice_key(DOC_A1, "s001") != slice_key(DOC_B1, "s001")

    def test_separator_in_either_part_raises(self):
        """拼出一个有歧义的键，表现是几个月后「偶尔注入错了地方」。"""
        with pytest.raises(ValueError, match="分隔符"):
            slice_key(DOC_A1, "s0:01")

    def test_empty_part_raises(self):
        with pytest.raises(ValueError):
            slice_key("", "s001")

    def test_stand_in_backend_yields_no_keys(self):
        """替身检索没有 `document_id` → **一个键都不产**，不编一个。

        编出来的键会与某一份真实讲义的键撞上；
        全部留空 `':whole'` 的话所有替身轮次共享一个键，
        于是任何一次替身标记都会污染其余全部替身轮次。
        """
        from explain.context import ContextPack, ContextSlice

        pack = ContextPack(
            question="q",
            slices=(ContextSlice("synthetic_deck.md", "whole", (1,), None, "x", 1),),
            meta={"backend": "in_memory"})
        assert slice_keys_of(pack) == []

    def test_misaligned_hit_documents_yields_no_keys(self):
        """`hit_documents` 与 `slices` 长度对不上 → 一个都不产。

        按序号硬配会把身份配错，而**配错的身份比没有身份更糟**：它看起来是对的。
        """
        from explain.context import ContextPack, ContextSlice

        pack = ContextPack(
            question="q",
            slices=(ContextSlice("a.pdf", "s001", (1,), None, "x", 1),
                    ContextSlice("b.pdf", "s001", (1,), None, "y", 1)),
            meta={"hit_documents": [{"document_id": DOC_A1, "slice_id": "s001"}]})
        assert slice_keys_of(pack) == []


# ---------------------------------------------------------------- 注入会触发


class TestInjectionFires:
    def test_marked_round_injects_the_note_into_the_user_turn(self, keypair, jwks, rest):
        """标记过 → 下次命中同一批切片时，**本轮 user 消息里出现那句限定语**。

        断的是模型真正收到的那一份，不是服务端自报的标志位。
        """
        model = RecordingModel()
        c = build(jwks, rest, model=model)

        first = explain(c, keypair, COURSE_A)
        assert first["confusion"]["injected"] is False, "还没标记就注入了"
        assert CONFUSION_NOTE not in model.last_user_turn

        assert mark(c, keypair, first).status_code == 200

        second = explain(c, keypair, COURSE_A)
        assert second["confusion"]["injected"] is True, second["confusion"]
        assert model.last_user_turn.startswith(CONFUSION_NOTE), model.last_user_turn[:200]
        assert second["confusion"]["matched_slice_keys"] == [slice_key(DOC_A1, "s001")]

    def test_note_goes_into_the_turn_not_the_system(self, keypair, jwks, rest):
        """口径 F57：**system 段一个字都不许变。**

        system 是按「会话内不变」设计的，而 marks 逐轮变化。
        放进去的表现不是报错，是 system 每轮不同——那会把唯一还能缓存的东西毁掉。
        """
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        first = explain(c, keypair, COURSE_A)
        before = model.systems[-1]
        mark(c, keypair, first)
        explain(c, keypair, COURSE_A)
        assert model.systems[-1] == before, "限定语漏进 system 了"
        assert CONFUSION_NOTE not in model.systems[-1]

    def test_reverse_control_removing_the_injection_turns_it_red(
            self, keypair, jwks, rest, monkeypatch):
        """**反向控制**：把注入逻辑换成平凡实现（恒不注入）→ 上面那条必须红。

        少了这一条，一个「`confusion.injected` 恒 True 但 prompt 没变」的实现
        也能让上面全绿。
        """
        import api.main as main_mod

        monkeypatch.setattr(main_mod._marks, "confusion_prefix", lambda rows: "")
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        first = explain(c, keypair, COURSE_A)
        mark(c, keypair, first)
        second = explain(c, keypair, COURSE_A)
        assert second["confusion"]["injected"] is False
        assert CONFUSION_NOTE not in model.last_user_turn, (
            "拿掉注入逻辑之后限定语还在——上一条测的不是这段逻辑")

    def test_the_note_does_not_ask_for_more_detail(self):
        """口径 F58：措辞与详细程度轴**正交**。

        「讲得更详细」与 `paragraph_length` 判定方向相反，在 P2/P3 用户上会
        推高重生成触发率（配置扫描实验实测 P3 触发 8/15）——
        这不是文案偏好，是约束冲突决定的：它决定这一跳会不会顺带涨成本。
        """
        for banned in ("更详细", "详细一点", "展开讲", "多讲"):
            assert banned not in CONFUSION_NOTE, banned
        assert "换一个切入点" in CONFUSION_NOTE

    def test_the_previous_question_is_not_injected(self, keypair, jwks, rest):
        """口径 F59：`question` 存下来，但**一个字都不进 prompt**。"""
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        first = explain(c, keypair, COURSE_A, question="上一轮问的是布隆过滤器")
        mark(c, keypair, first)
        # 存了：库里那一行有它
        assert rest.marks[0]["question"] == QUESTION
        second = explain(c, keypair, COURSE_A, question="这一轮问的是别的")
        assert second["confusion"]["injected"] is True
        assert "布隆过滤器" not in model.last_user_turn


# ---------------------------------------------------------------- 注入不是恒触发


class TestInjectionIsNotAlwaysOn:
    """**本轮最关键的一条。** 恒触发的限定语比不触发更难发现。"""

    def test_no_overlap_no_injection(self, keypair, jwks, rest):
        """在课程 A 标记，去课程 B 提问 → **不注入**。

        两门课的命中片都叫 `s001`，只有两段键能把它们分开（口径 F66）。
        """
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        first = explain(c, keypair, COURSE_A)
        assert mark(c, keypair, first).status_code == 200

        other = explain(c, keypair, COURSE_B)
        assert other["confusion"]["injected"] is False, other["confusion"]
        assert other["confusion"]["marked_rounds"] == 0
        assert CONFUSION_NOTE not in model.last_user_turn

    def test_a_users_marks_do_not_leak_into_another_users_round(
            self, keypair, jwks, rest):
        """B 标记过的切片，不该让 A 的同一轮注入（RLS 那一层的服务端形态）。

        ⚠️ 这里的隔离由假 PostgREST 按 token 的 sub 实现，
        **它不证明策略真的在数据库里**——那条在 `verify_f4_marks.py`（真库两个账号）
        与 `verify_rls_local.mjs`（PGlite 跑迁移原文）。
        """
        rest.courses[COURSE_A]["owner"] = USER_B          # 这门课归 B
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        b_round = explain(c, keypair, COURSE_A, sub=USER_B)
        assert mark(c, keypair, b_round, sub=USER_B).status_code == 200

        rest.courses[COURSE_A]["owner"] = USER_A          # 换成 A 的
        a_round = explain(c, keypair, COURSE_A, sub=USER_A)
        assert a_round["confusion"]["injected"] is False, "读到了别人的 marks"

    def test_reverse_control_always_true_overlap_turns_it_red(
            self, keypair, jwks, rest, monkeypatch):
        """**反向控制**：把交集判定换成恒真 → 上面那条必须红。

        少了它，一个「查询写错了操作符、永远返回 0 行」的实现同样让上面全绿——
        而那正是这个功能最可能的死法。
        """
        model = RecordingModel()
        c = build(jwks, rest, model=model)
        first = explain(c, keypair, COURSE_A)
        mark(c, keypair, first)

        monkeypatch.setattr(
            SupabaseClient, "marks_overlapping",
            lambda self, token, keys: [{"id": "x", "slice_ids": list(keys)}])
        other = explain(c, keypair, COURSE_B)
        assert other["confusion"]["injected"] is True, (
            "交集判定被换成恒真之后仍然不注入——上一条测的不是这段逻辑")
        assert CONFUSION_NOTE in model.last_user_turn

    def test_overlap_query_uses_the_ov_operator_and_both_key_parts(
            self, keypair, jwks, rest):
        """查询形状：`slice_ids=ov.{...}`，且数组里是**两段键**。

        ⚠️ 这条只钉形状不证语义：`ov.` 到底是不是 `&&`，
        只有真库能答（`verify_f4_marks.py` 用有/无交集两组键各查一次）。
        """
        c = build(jwks, rest)
        explain(c, keypair, COURSE_A)
        assert rest.overlap_urls, "一次交集查询都没发出去"
        q = rest.overlap_urls[-1]
        assert "slice_ids=ov." in q.replace("%7B", "{"), q
        from urllib.parse import parse_qs, unquote

        lit = unquote(parse_qs(q)["slice_ids"][0][len("ov."):])
        assert parse_pg_array(lit) == [slice_key(DOC_A1, "s001")]


# ---------------------------------------------------------------- 幂等


class TestIdempotent:
    def test_same_round_twice_keeps_one_row_and_returns_200(self, keypair, jwks, rest):
        """同一 `round_id` 提交两次 → 表里仍一行，两次都 200。

        返回 409 的话前端就得多一个分支去分辨「重复」与「失败」，
        而对用户来说「点了没反应就再点一次」本来就是同一个意图。
        """
        c = build(jwks, rest)
        first = explain(c, keypair, COURSE_A)

        r1 = mark(c, keypair, first)
        r2 = mark(c, keypair, first)
        assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
        assert len(rest.marks) == 1, rest.marks
        assert r1.json()["created"] is True and r2.json()["created"] is False
        # `mark_count_after` 是**之后**的数，重复时不涨。
        assert r1.json()["mark_count_after"] == 1
        assert r2.json()["mark_count_after"] == 1

    def test_two_different_rounds_are_two_rows(self, keypair, jwks, rest):
        """允许控制：不同的轮**必须**各存一行。

        少了它，一个「第二次 insert 一律吞掉」的实现也能让上一条全绿。
        """
        c = build(jwks, rest)
        mark(c, keypair, explain(c, keypair, COURSE_A))
        second = mark(c, keypair, explain(c, keypair, COURSE_A))
        assert len(rest.marks) == 2
        assert second.json()["mark_count_after"] == 2

    def test_round_id_differs_between_two_rounds_of_the_same_question(
            self, keypair, jwks, rest):
        """口径 F65 的分辨力：同一个问题问两遍是**两轮**。

        拿 `retrieval.signature` 当轮标识就会在这里失败——它只认提问与命中切片，
        两轮会得到同一个值，于是第二轮的标记会被幂等成第一轮的。
        """
        c = build(jwks, rest)
        a = explain(c, keypair, COURSE_A)
        b = explain(c, keypair, COURSE_A)
        assert a["round_id"] != b["round_id"]
        assert a["retrieval"]["signature"] == b["retrieval"]["signature"], (
            "前提没成立：两轮的检索签名本该相同，否则这条断言没有分辨力")

    def test_non_uuid_round_id_is_422_not_502(self, keypair, jwks, rest):
        """客户端发了个不是 uuid 的东西 → 422 点名，不是 502「服务端坏了」。"""
        c = build(jwks, rest)
        r = c.post("/marks",
                   json={"round_id": "not-a-uuid", "slice_ids": ["a:b"], "question": "q"},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 422 and "round_id" in r.json()["detail"]


class TestRejectsEmptyMarks:
    def test_empty_slice_ids_is_422(self, keypair, jwks, rest):
        """空 mark 与任何一轮都不会有交集——它是一行死数据，
        而死数据与「标记没生效」在库里长得一模一样。"""
        c = build(jwks, rest)
        r = c.post("/marks",
                   json={"round_id": str(uuid.uuid4()), "slice_ids": [], "question": "q"},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 422
        assert not rest.marks

    def test_unknown_field_is_422_not_silently_dropped(self, keypair, jwks, rest):
        """`extra="forbid"`：多发一个字段要**响亮地失败**（口径 F52）。"""
        c = build(jwks, rest)
        r = c.post("/marks",
                   json={"round_id": str(uuid.uuid4()), "slice_ids": ["a:b"],
                         "question": "q", "concept": "布隆过滤器"},
                   headers={"Authorization": f"Bearer {keypair.sign()}"})
        assert r.status_code == 422 and "concept" in r.text

    def test_marks_needs_auth(self, jwks, rest):
        assert build(jwks, rest).post(
            "/marks",
            json={"round_id": str(uuid.uuid4()), "slice_ids": ["a:b"],
                  "question": "q"}).status_code == 401


# ---------------------------------------------------------------- 埋点


class TestMarkEventLands:
    def test_the_events_table_gets_one_more_row(self, keypair, jwks, rest):
        """断的是**表里多了一行**，不是「函数被调用」——
        后者在写库失败时照样绿。"""
        c = build(jwks, rest)
        first = explain(c, keypair, COURSE_A)
        before = len(rest.events)
        out = mark(c, keypair, first).json()

        added = [e for e in rest.events[before:]
                 if e["event_type"] == "confusion_mark_created"]
        assert len(added) == 1, rest.events[before:]
        p = added[0]["payload"]
        assert p["round_id"] == first["round_id"]
        assert p["slice_ids"] == first["slice_keys"] and p["slice_ids"]
        assert p["mark_count_after"] == 1
        assert added[0]["course_id"] == COURSE_A
        assert out["events"]["emitted"] == 1

    def test_idempotent_repeat_still_emits_with_created_false(self, keypair, jwks, rest):
        """重复点击也发事件，带 `created: false`。

        不发的话「同一轮被点了两次」在数据里与「只点了一次」完全一样，
        而那是一个真实的用户行为信号（点了没反应就再点一次）。
        """
        c = build(jwks, rest)
        first = explain(c, keypair, COURSE_A)
        mark(c, keypair, first)
        mark(c, keypair, first)
        created = [e["payload"]["created"] for e in rest.events
                   if e["event_type"] == "confusion_mark_created"]
        assert created == [True, False]

    def test_no_session_id_means_no_event_but_the_mark_still_lands(
            self, keypair, jwks, rest):
        """没有 `session_id` → **一条事件都不发，服务端不现编**；
        但标记本身照常成立——一条埋点不该让用户标记不了。"""
        c = build(jwks, rest)
        first = explain(c, keypair, COURSE_A)
        out = mark(c, keypair, first, session_id=None).json()
        assert len(rest.marks) == 1
        assert out["events"]["emitted"] == 0
        assert "session_id" in out["events"]["skipped_reason"]

    def test_event_failure_does_not_break_the_mark(self, keypair, jwks, rest,
                                                   monkeypatch):
        """口径 F42：埋点写不进去不打断主链路，**但也不静默**。"""
        def boom(self, *, token, rows):
            raise RuntimeError("模拟写库失败")

        monkeypatch.setattr(SupabaseClient, "insert_events", boom)
        c = build(jwks, rest)
        first = explain(c, keypair, COURSE_A)
        r = mark(c, keypair, first)
        assert r.status_code == 200
        assert len(rest.marks) == 1
        assert r.json()["events"]["failed"] == 1
        assert "模拟写库失败" in str(r.json()["events"]["failures"])


# ---------------------------------------------------------------- slice_ids 非空


class TestSliceIdsAreRecorded:
    """护栏指标「重复提问率」的**唯一**数据来源（口径 F62）。漏了补不回来。"""

    def test_retrieval_event_carries_non_empty_slice_ids_with_document_id(
            self, keypair, jwks, rest):
        c = build(jwks, rest)
        explain(c, keypair, COURSE_A)
        ev = [e for e in rest.events if e["event_type"] == "retrieval_executed"]
        assert len(ev) == 1
        ids = ev[0]["payload"]["slice_ids"]
        assert ids, "讲解轮事件里的 slice_ids 是空的"
        assert ids[0]["document_id"] == DOC_A1 and ids[0]["slice_id"] == "s001"

    def test_reverse_control_emptying_slice_ids_is_rejected_at_construction(self):
        """**反向控制**：置空 → 构造时就抛，事件根本发不出去。

        置空之前它一路通过 `REQUIRED_KEYS`（`[]` 不是 None、不是空串），
        这正是这条护栏补的洞。
        """
        from api.events import complete_event

        base = {"top_k_scores": [0.9], "slice_ids": [{"document_id": DOC_A1,
                                                      "slice_id": "s001"}],
                "pages": [1], "latency_ms": 1.0, "embed_model": "m",
                "dimensions": 1024, "ruleset_versions": {}, "backend": "supabase",
                "slice_count": 1}
        complete_event("retrieval_executed", base)          # 允许控制：原样通过
        with pytest.raises(ValueError, match="slice_ids 是空的"):
            complete_event("retrieval_executed", {**base, "slice_ids": []})

    def test_empty_retrieval_is_still_recorded(self):
        """⚠️ **空检索是合法状态，不许被一并拒掉**（口径 F50 的同一条）。

        一片都没检索到是显式失败，比答非所问安全（PRD §6.2），
        它**更该**被采集到。拒掉它就是「最该被采集的那一类进不了库」的复发。
        """
        from api.events import complete_event

        ev = complete_event("retrieval_executed", {
            "top_k_scores": [], "slice_ids": [], "pages": [], "latency_ms": 1.0,
            "embed_model": "m", "dimensions": 1024, "ruleset_versions": {},
            "backend": "supabase", "slice_count": 0})
        assert ev["payload"]["slice_ids"] == []

    def test_mark_event_has_no_empty_exception(self):
        from api.events import complete_event

        with pytest.raises(ValueError, match="slice_ids 是空的"):
            complete_event("confusion_mark_created",
                           {"round_id": "r", "slice_ids": [], "mark_count_after": 1,
                            "slice_count": 0})


# ---------------------------------------------------------------- 与 SQL 的镜像


def _effective_required_keys() -> dict[str, list[str]]:
    """`f1_event_required_keys` 的**生效定义**——按文件名排序后的**最后一个**。

    ⚠️ 取第一个会读到阶段 4 那份（只有 5 个类型），而它会一直绿：
    绿的理由是它在跟一份已经不生效的定义比。
    `web/scripts/verify_style_logic.mjs` 2026-09-12 踩过这个坑，原样搬过来。
    """
    mig = REPO / "supabase" / "migrations"
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(mig.glob("*.sql")))
    blocks = re.findall(
        r"create or replace function public\.f1_event_required_keys.*?\$\$;",
        src, re.DOTALL)
    if not blocks:
        raise AssertionError("一个 f1_event_required_keys 定义都没解析出来")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"when\s+'([a-z_]+)'\s*\n?\s*then\s+array\[(.*?)\]",
                         blocks[-1], re.DOTALL):
        out[m.group(1)] = re.findall(r"'([a-z_]+)'", m.group(2))
    return out


class TestMirrorsTheSql:
    def test_effective_definition_has_fourteen_types(self):
        """分辨力先立：十四 = F1 五个 + 讲解四个 + 上传两个 + F4 一个
        + 开场标签两个（`style_taste_shown` / `style_taste_chosen`，2026-09-19）。

        得到 12 说明解析器读的是内测前那一版；得到 11 说明漏了 `confusion_mark_created`；
        得到 5 说明读的是阶段 4 那份旧定义。
        """
        parsed = _effective_required_keys()
        assert len(parsed) == 14, sorted(parsed)

    def test_confusion_mark_created_mirrors_the_sql(self):
        from api.events import REQUIRED_KEYS

        parsed = _effective_required_keys()
        assert set(REQUIRED_KEYS["confusion_mark_created"]) == \
            set(parsed["confusion_mark_created"])

    def test_the_other_eleven_survive_the_replace(self):
        """漏一个 `when` 分支，那一类事件会当场变成「未知类型」全部被拒，
        而被拒的事件是静默消失的。"""
        from api.events import REQUIRED_KEYS

        parsed = _effective_required_keys()
        for t, keys in REQUIRED_KEYS.items():
            assert t in parsed, f"{t} 的 when 分支在本轮 replace 之后没了"
            assert set(keys) == set(parsed[t]), f"{t}：Python {sorted(keys)} ≠ SQL {sorted(parsed[t])}"

    def test_python_and_sql_agree_on_the_non_empty_rule(self):
        """`_slice_ids_problem`（Python）与 `f4_event_slice_ids_ok`（SQL）
        是同一条判据的两份。**两份存在 ≠ 两份一致**（口径 F33）。

        这里比的是**它们对同一组输入的结论**，不是文本——
        比文本的话换个写法就红，而换个语义反倒不红。
        SQL 那一份由 `verify_rls_local.mjs` 真的执行；这里只保证 Python 这份
        与它在同一组样本上给出相同答案，样本逐条对应那边的断言。
        """
        from api.events import _slice_ids_problem

        cases = [
            # (event_type, payload, 应当放行吗)
            ("retrieval_executed", {"slice_ids": [{"x": 1}], "slice_count": 1}, True),
            ("retrieval_executed", {"slice_ids": [], "slice_count": 0}, True),
            ("retrieval_executed", {"slice_ids": [], "slice_count": 3}, False),
            # ⚠️ 下面两条是那个例外的边界，SQL 侧曾经在这里错过一次：
            # `payload->'slice_count' = '0'` 在键缺失时返回 NULL，
            # 而**结果为 NULL 的 CHECK 是通过的**。现在两边都判假。
            ("retrieval_executed", {"slice_ids": []}, False),          # slice_count 缺
            ("retrieval_executed", {"slice_ids": [], "slice_count": "0"}, False),  # 字符串不算
            ("retrieval_executed", {}, True),                          # 缺键：另一条 CHECK 的活
            ("confusion_mark_created", {"slice_ids": ["a:b"]}, True),
            ("confusion_mark_created", {"slice_ids": [], "slice_count": 0}, False),
            ("explain_turn", {"slice_ids": []}, True),                 # 不涉及的类型放行
        ]
        for t, payload, allowed in cases:
            assert (_slice_ids_problem(t, payload) == "") is allowed, (t, payload)

    def test_migration_declares_the_same_table_and_policies(self):
        """迁移原文里那几样东西确实在。**这不是测数据库**（那是 PGlite 那边的活），
        是防止「口径写在方案里而迁移里没有」。"""
        src = MIGRATION.read_text(encoding="utf-8")
        assert "create table if not exists public.confusion_marks" in src
        assert "slice_ids  text[] not null" in src
        assert "unique (user_id, round_id)" in src
        assert "on delete cascade" in src
        assert "using gin (slice_ids)" in src
        assert "grant select, insert on public.confusion_marks to authenticated" in src
        # **不授 delete / update**（口径 F60）：v1 不做取消标记。
        assert " delete " not in src.split("grant select, insert on public.confusion_marks")[1].split("\n")[0]
        assert "for delete" not in src and "for update" not in src


# ---------------------------------------------------------------- 23505 的翻译


class TestDuplicateIsIdempotentNotAnError:
    """真库上第二次提交是 **23505**。`_post` 把它翻成 `DuplicateMark`。

    ⚠️ **下面这份假正文的 `message` 是编的**（本表还没在真库上撞过一次）。
    它无害的理由是：匹配条件**只认错误码 + 调用点**，不认正文里的任何字符串
    （`insert_document` 那次教训——手写的假正文与匹配条件同源，必然相等）。
    最后一条断言把这一点钉死：正文里连约束名都没有时**仍然**要翻译成功。
    **待办**：`verify_f4_marks.py` 第一次真跑撞上时，把真正文抄回这里。
    """

    def _patched(self, status: int, body: str):
        import httpx

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = status
                text = body
                content = b"x"
            return R()

        return httpx, fake_post

    def _run(self, status: int, body: str):
        httpx, fake_post = self._patched(status, body)
        client = SupabaseClient("https://x/rest/v1", "sb_publishable_x")
        orig, httpx.post = httpx.post, fake_post
        try:
            return client.insert_mark(token="t", row={"round_id": "r"})
        finally:
            httpx.post = orig

    def test_23505_becomes_created_false(self):
        assert self._run(409, '{"code":"23505","details":null,"hint":null,'
                              '"message":"duplicate key value violates unique '
                              'constraint \\"confusion_marks_user_round_uniq\\""}') is False

    def test_translation_does_not_depend_on_the_constraint_name(self):
        """正文里没有约束名时仍然要翻译成功——判的是**错误码 + 调用点**。"""
        assert self._run(409, '{"code":"23505"}') is False

    def test_other_errors_are_not_swallowed(self):
        """反向控制：**不是 23505 的失败不许被当成幂等重复。**

        吞掉的话，RLS 拦下（42501）会返回一个 200 与一个「已经标记过」，
        而库里其实什么都没有——用户以为标记成功了。
        """
        from api.supabase import SupabaseError

        with pytest.raises(SupabaseError) as e:
            self._run(403, '{"code":"42501","message":"row-level security"}')
        assert "42501" in e.value.message


# ---------------------------------------------------------------- 跨课继承（F4 第一件事）


class TestStyleIsInheritedAcrossCourses:
    """①「风格配置跨课程、跨会话继承」。

    接线时已拍板「配置按人不按课」，`style_config` 主键就是 `user_id`——
    这件事在结构上已经成立。但按口径 F53（验收要确认的东西，系统必须能自己
    答出来），「已经成立」不能是一句话，得有一条**会红**的断言。
    """

    def test_style_fragment_is_byte_identical_across_two_courses(
            self, keypair, jwks, rest):
        """同一用户在两门不同课下各跑一轮 → 风格片段**逐字节相同**。"""
        c = build(jwks, rest)
        a = explain(c, keypair, COURSE_A)
        b = explain(c, keypair, COURSE_B)
        assert a["style_rules"] == b["style_rules"]
        assert a["config_fingerprint"] == b["config_fingerprint"]
        # 允许控制：两门课**确实是两门课**（材料清单不同）。
        # 少了它，两次请求打到同一门课上时这条也会全绿。
        assert a["retrieval"]["slices"][0]["source_file"] != \
            b["retrieval"]["slices"][0]["source_file"]

    def test_reverse_control_course_scoped_config_turns_it_red(
            self, keypair, jwks, rest):
        """**反向控制**：把配置读取换成「按 `course_id` 分行」→ 上一条必须红。

        平凡实现就是「每门课读到不同的一行」——这里用一个按调用次序换行的
        包装来实现它，它走的仍然是 `/explain` 那条真实路径。
        """
        real = SupabaseClient(SETTINGS.rest_url, SETTINGS.supabase_publishable_key,
                              transport=rest)

        class CourseScopedStyle:
            """配置按课分行的平凡实现：第 n 次读配置给第 n 个档位。"""

            def __init__(self) -> None:
                self.tiers = ["P0", "P3"]

            def style_row(self, user_id: str, token: str):
                from api.supabase import StyleRow

                base = real.style_row(user_id, token)
                tier = self.tiers.pop(0) if self.tiers else "P0"
                return StyleRow(**{**base.to_dict(), "detail_level": tier})

            def __getattr__(self, name: str):
                return getattr(real, name)

        c = build(jwks, rest, db=CourseScopedStyle())
        a = explain(c, keypair, COURSE_A)
        b = explain(c, keypair, COURSE_B)
        assert a["style_rules"] != b["style_rules"], (
            "配置按课分行之后风格片段仍然相同——上一条测的不是配置那条路径")
        assert a["config_fingerprint"] != b["config_fingerprint"]
