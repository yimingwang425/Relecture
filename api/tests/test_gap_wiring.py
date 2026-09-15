"""内测前-P1 · GapChecker 接进生产（口径 F70–F72）。

## 四条守卫，每条配一次「改回去就红」

| 守卫 | 反向控制 |
| --- | --- |
| 生产链路真的调了模型判断 | 换回 `NullGapChecker` → `gap_verdict` 恒 null（常驻，见 `TestProductionCallsTheModelJudge`） |
| **限定语不是恒触发** | 把 G03a 的条件换成恒真 → covered 那一轮也带跳过页（常驻） |
| 成本与延迟真的被记账 | `gap_cost_usd` 置 0 → 红（执行记录里演示过） |
| 判断失败不打断主链路 | 去掉 `GuardedGapChecker` → 500（执行记录里演示过） |

**第二条最关键**，形态与 F4 的注入守卫完全一样：一个恒触发的限定语，
输出看起来完全正常，只是每轮都多说一行——最难发现的那种。

## 断的是什么

- 埋点：**`rest.events` 里真的多了那一行、那一行里的值**（纪律 3），不是函数被调用。
- 限定语：**讲解模型真正收到的那一份 user turn**，不是服务端自报的标志位
  （`test_marks.py::RecordingModel` 的同一条理由：两端同源的话，只改标志位不改 prompt
  的实现照样全绿）。

## 这里证明不了什么（口径 F34）

判断模型是假的。「Haiku 在生产上真的返回合法 JSON」「真实成本与延迟是多少」
只有部署后真跑一轮能答——执行记录「交付后」第 4 步。本地绿数与那次观测分开记。
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.auth import TokenVerifier
from api.main import create_app
from api.settings import Settings
from api.supabase import SupabaseClient
from explain.context import ContextSlice, SkippedPageRef
from explain.gap import (
    GAP_MODEL_NAME,
    GAP_TIMEOUT_S,
    VERDICTS,
    GapResult,
    ModelGapChecker,
    NullGapChecker,
)
from explain.llm import Completion, Usage
from explain.retrieval_adapter import InMemoryRetriever

from conftest import AUDIENCE, ISSUER, USER_A
from fake_db import row
from fake_rest import RoutedRest
from test_marks import FakeEmbedder, RecordingModel, _slice

REPO = Path(__file__).resolve().parents[2]
SESSION = "99999999-9999-4999-8999-999999999999"
QUESTION = "账本方案之间的具体对比是什么？"
COURSE = "cccccccc-c003-4003-8003-cccccccccccc"
DOC = "33333333-d003-4003-8003-333333333333"

SETTINGS = Settings(
    supabase_url="https://example.supabase.co",
    supabase_publishable_key="sb_publishable_test",
    anthropic_api_key="sk-ant-fake-for-tests",
)

SLICE = ContextSlice(source_file="MIT14_129S25_lec02.pdf", slice_id="s010",
                     page_indices=(32,), title="账本", text="片段提到了账本方案这个主题。",
                     char_count=14, score=0.7)
SKIPPED = (SkippedPageRef(source_file="MIT14_129S25_lec02.pdf", index=33,
                          kind="image_only", reason="内容全在图里"),)

#: G03a 那句限定语里**稳定的一段**。取自 `explain/prompt.py::render_gap_note`。
#: ⚠️ 只用来**检测**限定语在不在，不是它的第二份出处——措辞改了这里会红，那是想要的。
HINT = "页的内容全在图里"

#: 判断模型的假用量。**成本手算一次**，不调 `cost_usd()` 反推：
#: Haiku 4.5 价目 $1 / $5 每百万 token → 900 × 1e-6 + 40 × 5e-6 = 0.0011。
GAP_USAGE = Usage(input_tokens=900, output_tokens=40)
GAP_COST_BY_HAND = 0.0011
GAP_LATENCY = 1406.0


class FakeGapModel:
    """判断模型的替身。**照生产 `Completion` 的形状写**（口径 F49）：
    成本由 `Completion.cost` 按真价目表算，不是替身自报。"""

    name = GAP_MODEL_NAME

    def __init__(self, verdict: str = "partial") -> None:
        self.verdict = verdict
        self.calls = 0
        self.seen_user: list[str] = []

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion:
        self.calls += 1
        self.seen_user.append(str(messages[-1]["content"]))
        return Completion(
            text=f'{{"verdict": "{self.verdict}", "reason": "测试用理由"}}',
            usage=GAP_USAGE, latency_ms=GAP_LATENCY, model=GAP_MODEL_NAME,
            stop_reason="end_turn")

    def request_signature(self, system: str, messages: list[dict[str, Any]]) -> str:
        return "fake-gap"


def run(jwks, keypair, *, verdict: str | None = "partial",
        skipped: tuple[SkippedPageRef, ...] = SKIPPED,
        gap_factory: Any = "model", retriever: Any = None,
        rest: RoutedRest | None = None, course_id: str | None = None):
    """跑一次 `/explain`。`gap_factory="model"` = 注入一个真的 `ModelGapChecker`
    （只有它里面的模型是假的）；`None` = **不注入**，走生产默认。"""
    rest = rest or RoutedRest(style_rows={USER_A: [row(USER_A)]})
    model = RecordingModel()
    gap_model = FakeGapModel(verdict or "partial")
    factory = (lambda: ModelGapChecker(gap_model)) if gap_factory == "model" else gap_factory
    app = create_app(
        settings=SETTINGS,
        verifier=TokenVerifier(jwks, issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(SETTINGS.rest_url, SETTINGS.supabase_publishable_key,
                                transport=rest),
        # ⚠️ 替身的 `meta` 要带真检索后端会给的 `embed_model` / `embed_dimensions`：
        # 缺了的话 `retrieval_executed` 构造时就抛，**整批讲解事件一条都不发**
        # （响应里是 `events.build_error`），下面每一条「表里那一行」都无从断起。
        # 第一版就是这么红的——不传 `course_id` 的替身路径本来就不发讲解事件。
        retriever=(retriever if retriever is not None or course_id
                   else InMemoryRetriever(
                       default_slices=(SLICE,), skipped_pages=skipped,
                       meta={"backend": "in_memory", "embed_model": "text-embedding-3-large",
                             "embed_dimensions": 1024})),
        model_factory=lambda: model,
        embedder_factory=lambda: FakeEmbedder(),
        gap_checker_factory=factory,
    )
    body: dict[str, Any] = {"question": QUESTION, "session_id": SESSION}
    if course_id:
        body["course_id"] = course_id
    r = TestClient(app, raise_server_exceptions=False).post(
        "/explain", json=body, headers={"Authorization": f"Bearer {keypair.sign()}"})
    return r, rest, model, gap_model


def turn_row(rest: RoutedRest) -> dict[str, Any]:
    """**表里那一行**的 payload。恰好一行，多一行少一行都是错。"""
    rows = [e for e in rest.events if e["event_type"] == "explain_turn"]
    assert len(rows) == 1, f"explain_turn 行数 {len(rows)}"
    return rows[0]["payload"]


def first_user_turn(model: RecordingModel) -> str:
    """讲解模型**首次**收到的 user turn。重生成之后最后一条是审查反馈，不是它。"""
    return str(model.messages[0][-1]["content"])


# ---------------------------------------------------------------- 守卫 1


class TestProductionCallsTheModelJudge:
    def test_the_row_carries_a_real_verdict(self, jwks, keypair):
        r, rest, _, gap_model = run(jwks, keypair, verdict="partial")
        assert r.status_code == 200, r.text
        assert gap_model.calls == 1, "判断模型没被调用（或调了不止一次）"
        p = turn_row(rest)
        assert p["gap_verdict"] == "partial"
        assert p["gap_implementation"] == "model"
        assert p["gap_model"] == GAP_MODEL_NAME
        # 响应里也看得见——部署后验收第 4 步读的就是它。
        assert r.json()["gap"]["gap_verdict"] == "partial"

    def test_route_uses_the_default_when_nothing_is_injected(self, jwks, keypair, monkeypatch):
        """**不注入时走的是 `_default_gap_checker`**——生产就是这条路。

        上一条注入了 factory，它证明不了「不注入时接没接」。这一条把默认构造换成
        一个带假模型的真 `ModelGapChecker`，不注入 factory，verdict 必须穿透到表里。
        """
        import api.main as main

        gm = FakeGapModel("insufficient")
        monkeypatch.setattr(main, "_default_gap_checker", lambda: ModelGapChecker(gm))
        r, rest, _, _ = run(jwks, keypair, gap_factory=None)
        assert r.status_code == 200, r.text
        assert gm.calls == 1
        assert turn_row(rest)["gap_verdict"] == "insufficient"

    def test_default_is_the_shared_haiku_judge_not_null(self):
        """默认构造出来的是 `ModelGapChecker` + F3b 校准用的那份 Haiku 配置。

        只构造、不调用，不碰网络。参数逐项钉住：换任何一项，F3b 那组数就不再对应线上。
        """
        from api.main import _default_gap_checker

        chk = _default_gap_checker()
        assert isinstance(chk, ModelGapChecker), type(chk)
        m = chk.model
        assert m.name == GAP_MODEL_NAME == "claude-haiku-4-5-20251001"
        assert m.thinking == "disabled"          # G01：关 thinking
        assert m.effort is None                  # Haiku 4.5 传 effort 返回 400
        assert m.max_tokens == 300
        assert m.cache_system is False and m.cache_conversation is False

    def test_reverse_control_null_checker_turns_it_red(self, jwks, keypair):
        """**常驻反向控制**：换回 `NullGapChecker`，第一条的判据必须不成立。"""
        r, rest, _, _ = run(jwks, keypair, gap_factory=lambda: NullGapChecker())
        assert r.status_code == 200
        p = turn_row(rest)
        assert p["gap_verdict"] not in VERDICTS, "Null 也给出了合法 verdict——第一条没有分辨力"
        assert p["gap_implementation"] == "null"

    def test_there_is_no_switch(self):
        """**不加开关**（口径 F70）。设置里不许出现任何 gap 相关项，
        `/explain` 里 `gap_checker` 的来源只有「注入」与「默认」两条。"""
        settings_src = (REPO / "api" / "api" / "settings.py").read_text(encoding="utf-8")
        assert not re.search(r"gap", settings_src, re.IGNORECASE), "settings.py 里出现了 gap 相关项"
        main_src = (REPO / "api" / "api" / "main.py").read_text(encoding="utf-8")
        assert "make_gap = gap_checker_factory or _default_gap_checker" in main_src
        assert not re.search(r"(?i)(GAP_ENABLED|ENABLE_GAP|gap_enabled)", main_src)


# ---------------------------------------------------------------- 守卫 2


class TestHintIsNotAlwaysOn:
    """G03a：**非 `covered` 且该课件跳过页非空** → 跳过页清单进讲解 prompt。

    两条「必须不注入」+ 两条允许控制 + 一条常驻反向控制。
    ⚠️ 跳过页清单**同时**在 system 的课件清单里（口径 X07，常驻、与判断无关）——
    所以这里只看 **user turn**，那是 G03a 限定语唯一会出现的地方。
    """

    def test_covered_with_skipped_pages_injects_nothing(self, jwks, keypair):
        r, rest, model, _ = run(jwks, keypair, verdict="covered", skipped=SKIPPED)
        assert r.status_code == 200, r.text
        assert HINT not in first_user_turn(model)
        assert turn_row(rest)["gap_skipped_hint_pages"] == []

    def test_non_covered_without_skipped_pages_injects_nothing(self, jwks, keypair):
        r, rest, model, _ = run(jwks, keypair, verdict="partial", skipped=())
        assert r.status_code == 200, r.text
        assert HINT not in first_user_turn(model)
        assert turn_row(rest)["gap_skipped_hint_pages"] == []

    @pytest.mark.parametrize("verdict", ["partial", "insufficient"])
    def test_non_covered_with_skipped_pages_injects(self, jwks, keypair, verdict):
        """允许控制。**`partial` 也必须触发**——门槛是「非 covered」不是 `insufficient`
        （F3b §2.2：建在 insufficient 上 2 题漏 1 题）。少了这两条，一个「永不注入」的
        实现会让上面两条全绿。"""
        r, rest, model, _ = run(jwks, keypair, verdict=verdict, skipped=SKIPPED)
        assert r.status_code == 200, r.text
        assert HINT in first_user_turn(model)
        assert "第 33 页" in first_user_turn(model)
        assert turn_row(rest)["gap_skipped_hint_pages"] == [33]

    def test_reverse_control_always_true_condition_turns_it_red(self, jwks, keypair, monkeypatch):
        """**常驻反向控制**：把 G03a 的条件换成恒真，covered 那一条必须不成立。"""
        import explain.gap as gap

        monkeypatch.setattr(gap, "skipped_pages_hint",
                            lambda context, verdict: tuple(context.skipped_pages))
        r, rest, _, _ = run(jwks, keypair, verdict="covered", skipped=SKIPPED)
        assert r.status_code == 200
        assert turn_row(rest)["gap_skipped_hint_pages"] == [33], (
            "条件恒真之后 covered 仍不带跳过页——第一条没有分辨力")

    def test_skipped_pages_reach_the_hint_from_the_database(self, jwks, keypair):
        """预查第 2 条的落地：**生产路径上跳过页从 `documents.unusable_pages` 来**
        （`SupabaseRetriever._skipped_and_unusable`），不是从替身来。

        走真检索后端那条路（给 `course_id`），跳过页只写在假库的 documents 行里。
        """
        doc = {"id": DOC, "filename": "MIT14_129S25_lec02.pdf", "verdict": "usable",
               "unusable_pages": [{"index": 33, "kind": "image_only"}], "page_count": 40}
        rest = RoutedRest(
            style_rows={USER_A: [row(USER_A)]},
            courses={COURSE: {"id": COURSE, "name": "14.129", "owner": USER_A}},
            documents={COURSE: [doc]},
            slices={DOC: [_slice(DOC, "s010", "片段提到了账本方案这个主题。")]},
            matches={COURSE: [{"document_id": DOC, "slice_id": "s010", "similarity": 0.7}]},
        )
        r, rest, model, gm = run(jwks, keypair, verdict="partial", rest=rest, course_id=COURSE)
        assert r.status_code == 200, r.text
        assert r.json()["retrieval"]["meta"]["backend"] == "supabase"
        assert HINT in first_user_turn(model) and "第 33 页" in first_user_turn(model)
        # G01 末条：**跳过页不给判断模型**。给了它会开始猜「是不是在图里」。
        assert all("33" not in u for u in gm.seen_user), gm.seen_user


# ---------------------------------------------------------------- 守卫 3


class TestCostAndLatencyAreBooked:
    def test_gap_cost_and_latency_land_in_the_row(self, jwks, keypair):
        r, rest, _, _ = run(jwks, keypair)
        assert r.status_code == 200, r.text
        p = turn_row(rest)
        assert p["gap_cost_usd"] == pytest.approx(GAP_COST_BY_HAND), p["gap_cost_usd"]
        assert p["gap_cost_usd"] > 0
        assert p["gap_latency_ms"] == pytest.approx(GAP_LATENCY)

    def test_it_is_counted_in_the_round_total(self, jwks, keypair):
        """**计入单轮**：同一问题、同一假讲解模型，有判断那一轮的 `cost_usd`
        必须恰好多出判断那一笔。两次跑的生成部分逐字节相同（假模型确定性）。"""
        _, rest_null, _, _ = run(jwks, keypair, gap_factory=lambda: NullGapChecker())
        _, rest_model, _, _ = run(jwks, keypair, verdict="covered")
        base, with_gap = turn_row(rest_null), turn_row(rest_model)
        assert with_gap["cost_usd"] == pytest.approx(base["cost_usd"] + GAP_COST_BY_HAND)
        assert with_gap["latency_ms"] >= GAP_LATENCY


# ---------------------------------------------------------------- 守卫 4


class Exploding:
    """判断调用本身抛异常（网络、529、SDK 错误都是这个形态）。"""

    model = FakeGapModel()

    def check(self, question, context):
        raise RuntimeError("Haiku 返回 529 overloaded")


class TestJudgeFailureDoesNotBreakTheRound:
    def test_explain_still_returns_and_says_why(self, jwks, keypair):
        r, rest, _, _ = run(jwks, keypair, gap_factory=lambda: Exploding())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["text"], "讲解丢了"
        assert body["gap"]["gap_verdict"] is None
        assert body["gap"]["gap_implementation"] == "error"
        assert "529" in body["gap"]["gap_error"]
        # **响应与事件同一格同一个值**：此前响应是 0.0、事件是 null（生产观测 2026-09-14）。
        assert body["gap"]["gap_cost_usd"] is None, "响应侧写 0.0 会被读成「这一跳没花钱」"
        # 事件照发，**失败时成本写 null 不写 0**（请求可能已计费，金额未知）。
        p = turn_row(rest)
        assert p["gap_verdict"] is None and p["gap_implementation"] == "error"
        assert p["gap_cost_usd"] is None
        assert "529" in p["gap_error"]
        # 失败不伪装成 covered：限定语也不出现（没有判断就没有依据）。
        assert p["gap_skipped_hint_pages"] == []

    def test_default_path_without_a_key_is_an_error_not_a_crash(self, jwks, keypair):
        """不注入、环境里没有 key（conftest 拿掉了）→ 真的 `AnthropicModel` 当场抛，
        讲解仍 200。这也证明**测试从不真调 Haiku**。"""
        r, rest, _, _ = run(jwks, keypair, gap_factory=None)
        assert r.status_code == 200, r.text
        p = turn_row(rest)
        assert p["gap_implementation"] == "error"
        assert "ANTHROPIC_API_KEY" in p["gap_error"]


# ---------------------------------------------------------------- 守卫 4b · 超时


#: 替身挂住的时长。**必须明显长于超时**，且短到「去掉超时」那次反向验证不至于跑太久。
HANG_S = 12.0


class Hanging:
    """必然超时的判断：挂住 `HANG_S` 秒，之后才返回一个 covered。

    挂住**不是异常**——`GuardedGapChecker` 只接异常的话，这个替身会把整轮讲解一起挂住。
    `release` 让测试结束时放它走，不留一个挂着的线程。
    """

    model = FakeGapModel()

    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def check(self, question, context):
        self.release.wait(HANG_S)
        return GapResult(verdict="covered", implementation="model")


class TestJudgeTimeout:
    def test_timeout_is_anchored_on_the_measured_p90(self):
        """5 s ≈ 3× F3b 实测 P90 1,595 ms。**是实测锚点，不是占位阈值**。"""
        assert GAP_TIMEOUT_S == 5.0
        assert GAP_TIMEOUT_S >= 3 * 1.595 - 0.1

    def test_hanging_judge_does_not_hang_the_round(self, jwks, keypair):
        """**讲解在预期时间内返回**，`gap_verdict` 为 null 并带失败原因。

        判据两端都要：下界 `>= GAP_TIMEOUT_S` 证明它**真的等到了超时**才放弃
        （而不是一上来就当失败处理——那样上界照样满足）；上界证明它**没有等替身挂完**。
        """
        release = threading.Event()
        started = time.monotonic()
        try:
            r, rest, _, _ = run(jwks, keypair, gap_factory=lambda: Hanging(release))
        finally:
            elapsed = time.monotonic() - started
            release.set()
        assert r.status_code == 200, r.text
        assert GAP_TIMEOUT_S <= elapsed < GAP_TIMEOUT_S + 2.5, (
            f"整轮用了 {elapsed:.1f} s——超时没生效（替身挂 {HANG_S:g} s）")
        body = r.json()
        assert body["text"], "讲解丢了"
        assert body["gap"]["gap_verdict"] is None
        assert body["gap"]["gap_implementation"] == "error"
        assert "TimeoutError" in body["gap"]["gap_error"]
        assert body["gap"]["gap_cost_usd"] is None, "超时那轮响应侧也是金额未知"
        p = turn_row(rest)
        assert p["gap_verdict"] is None and p["gap_implementation"] == "error"
        assert "TimeoutError" in p["gap_error"]
        assert p["gap_cost_usd"] is None, "超时的那次调用可能已计费，金额未知——写 null 不写 0"
        assert p["gap_latency_ms"] >= GAP_TIMEOUT_S * 1000 * 0.99
        # 替身挂完之后返回的 covered **不许**回流进这一轮。
        assert p["gap_skipped_hint_pages"] == []


# ---------------------------------------------------------------- 一份实现（纪律 6）


class TestOneImplementation:
    """**不重抄一份实现**（G06 那条教训）。Haiku 配置只住在 `explain/explain/gap.py`。"""

    def _py(self, *parts: str) -> list[Path]:
        return sorted((REPO.joinpath(*parts)).glob("*.py"))

    def test_the_model_id_is_written_in_one_place(self):
        hits = [p for p in (self._py("api", "api")
                            + self._py("实验脚本", "配置扫描实验", "scan"))
                if "claude-haiku-4-5" in p.read_text(encoding="utf-8")]
        assert hits == [], f"判断模型 id 在 explain/gap.py 之外又写了一份：{hits}"

    def test_model_gap_checker_is_constructed_in_one_place(self):
        files = (self._py("api", "api") + self._py("实验脚本", "配置扫描实验", "scan")
                 + [p for p in self._py("explain", "explain") if p.name != "gap.py"])
        hits = [p for p in files if "ModelGapChecker(" in p.read_text(encoding="utf-8")]
        assert hits == [], f"ModelGapChecker 在 build_model_gap_checker 之外被构造：{hits}"

    def test_both_sides_call_the_shared_factory(self):
        live = (REPO / "实验脚本" / "配置扫描实验" / "scan" / "live.py").read_text(encoding="utf-8")
        main = (REPO / "api" / "api" / "main.py").read_text(encoding="utf-8")
        assert "build_model_gap_checker" in live
        assert "build_model_gap_checker" in main
