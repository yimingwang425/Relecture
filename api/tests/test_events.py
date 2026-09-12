"""阶段 6 · §13.0 的四个讲解事件。

## 这一组里最重要的一条是 `test_required_keys_mirror_the_sql`

`api/api/events.py` 的 `REQUIRED_KEYS` 是**镜像**，唯一出处是迁移里的
`f1_event_required_keys`。两份存在 ≠ 两份一致——所以那一条从 **SQL 原文**
解析出清单再逐项比对（口径 F33，与 `web/scripts/verify_style_logic.mjs`
对 `events.ts` 那份镜像做的是同一件事）。

⚠️ **注意它不能反过来写。** 如果我把 SQL 那份也「从 Python 读」，
两端就同源了——那正是 F16 三次复发的共同形态。这里一端是 `.sql` 文件里的文本，
另一端是 Python 的字典，**它们不会一起变**。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from api.events import EXPLAIN_EVENT_TYPES, REQUIRED_KEYS, build_explain_events, record_events

REPO = Path(__file__).resolve().parents[2]
MIGRATION = REPO / "supabase" / "migrations" / "20260912120000_f3_explain_events.sql"


# ---------------------------------------------------------------- 镜像比对


def parse_required_keys_from_sql() -> dict[str, list[str]]:
    """从迁移原文里解析 `when '<type>' then array[...]`。

    解析失败**抛**，不返回空字典：一个空字典会让下面的比对
    「两边都没有这个类型」地平凡通过（同 F16 反对的那件事）。
    """
    if not MIGRATION.is_file():
        raise AssertionError(f"读不到 {MIGRATION}")
    src = MIGRATION.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"when\s+'([a-z_]+)'\s*\n?\s*then\s+array\[(.*?)\]",
                         src, re.DOTALL):
        out[m.group(1)] = re.findall(r"'([a-z_]+)'", m.group(2))
    if not out:
        raise AssertionError("从迁移里一个 when 分支都没解析出来——解析器与 SQL 写法漂了")
    return out


class TestMirrorsTheSql:
    def test_parser_finds_all_nine_event_types(self):
        """**先立解析器的分辨力。**

        少了这一条，解析器只认出 1 个分支时下面的比对仍然可能绿
        （只比它认出来的那些）。九个 = 阶段 4 的五个 + 本阶段的四个。
        """
        parsed = parse_required_keys_from_sql()
        assert len(parsed) == 9, sorted(parsed)
        assert set(EXPLAIN_EVENT_TYPES) <= set(parsed)

    def test_required_keys_mirror_the_sql(self):
        """Python 侧那份镜像与 SQL 逐项相同（顺序无关，集合相同）。"""
        parsed = parse_required_keys_from_sql()
        for t in EXPLAIN_EVENT_TYPES:
            assert set(REQUIRED_KEYS[t]) == set(parsed[t]), (
                f"{t}：Python {sorted(REQUIRED_KEYS[t])} ≠ SQL {sorted(parsed[t])}")

    def test_nullable_fields_are_in_neither(self):
        """null 有含义的字段**两边都不许出现**。

        进了清单的后果是一条完全正确的事件被 CHECK 拒掉，而**被拒的事件
        是静默消失的**（`f1_event_payload_ok` 把 JSON null 判成「没有」）。
        """
        parsed = parse_required_keys_from_sql()
        for t, k in (("style_review_result", "all_activated_passed"),
                     ("explain_turn", "first_token_latency_ms")):
            assert k not in REQUIRED_KEYS[t], f"{k} 不该在 Python 那份清单里"
            assert k not in parsed[t], f"{k} 不该在 SQL 那份清单里"

    def test_five_stage4_types_survive_the_replace(self):
        """`f1_event_required_keys` 被 `create or replace` 过 ——
        漏一个 `when` 分支，阶段 4 那五个事件会当场变成「未知类型」全部被拒。

        这一条在本地库那边也有一条同形的（`verify_rls_local.mjs`），
        **两条都要**：这一条读的是 SQL 文本，那一条真的往 PGlite 里写一行。
        """
        parsed = parse_required_keys_from_sql()
        for t in ("style_pair_shown", "style_pair_chosen", "style_onboarding_completed",
                  "style_text_mapped", "style_config_changed"):
            assert t in parsed, f"{t} 的 when 分支在 replace 之后没了"


# ---------------------------------------------------------------- 构造


class FakeUsage:
    def to_dict(self):
        return {"input_tokens": 1200, "cache_read": 0, "cache_creation": 0,
                "output_tokens": 430}

    def __add__(self, other):
        return self


class FakeCompletion:
    """只有模型调用这一步是假的 —— 它是唯一要网络与钱的一步。"""

    model = "claude-sonnet-5"
    latency_ms = 9000.0
    cost = 0.004
    usage = FakeUsage()

    def __init__(self, text: str = "一段讲解"):
        self.text = text


# ---------------------------------------------------------------- 审查：用**真的**
#
# ⚠️ **这里刻意没有假的 Evaluation / Review。**
#
# 第一版有。那个假 Evaluation 的 `to_dict()["dimensions"]` 返回**列表**，
# 而生产的 `a_rules.evaluator.Evaluation.to_dict()` 返回的是 `{name: {...}}` 一个**字典**。
# 于是 `events.py` 按列表遍历它，21 条单测全绿，**第一次真跑就
# `AttributeError: 'str' object has no attribute 'get'`**。
#
# 与 numpy 那次（生产返回 ndarray，替身返回 list）、与 23505 那次
# （真错误正文没有列名，手写的假正文有）是**同一个形态**：
# 替身照我的记忆写，而不是照生产实现真的返回什么。
#
# 处置不是「把假货改对」——那还是同一个来源。处置是**不要这个假货**：
# `evaluate()` 与 `RuleReviewer` 都是纯函数，不要网络也不要钱，
# 直接用生产的那两个。这样断言的两端再也不会一起变。


def real_review(text: str, config):
    """用生产的 `RuleReviewer` 真审一段文本。"""
    from explain.review import RuleReviewer

    return RuleReviewer(config).review(text)


class FakeAttempt:
    def __init__(self, index, review):
        self.index = index
        self.completion = FakeCompletion()
        self.review = review


class FakeResult:
    def __init__(self, *, attempts, context, accepted=True):
        self.attempts = tuple(attempts)
        self.context = context
        self.accepted = accepted
        self.round_index = 1

    @property
    def regenerations(self):
        return len(self.attempts) - 1

    @property
    def usage(self):
        return FakeUsage()

    @property
    def cost_usd(self):
        return 0.0071

    @property
    def latency_ms(self):
        return 18234.5

    @property
    def final_review(self):
        return self.attempts[-1].review


def make_context(*, backend="supabase", scores=(0.57, 0.54)):
    from explain.context import ContextPack, ContextSlice, SkippedPageRef

    return ContextPack(
        question="城市规划的历史传统有哪些？",
        slices=tuple(
            ContextSlice(source_file="规划.pdf", slice_id=f"s00{i}", page_indices=(i + 10,),
                         title=None, text="片段" * 20, char_count=40, score=sc, via="hit")
            for i, sc in enumerate(scores, start=1)),
        skipped_pages=(SkippedPageRef(source_file="规划.pdf", index=7,
                                      kind="image_only", reason="内容全在图里"),),
        meta={"backend": backend, "embed_model": "text-embedding-3-large",
              "embed_dimensions": 1024, "embed_text_ruleset": "embed-text-1.0",
              "retrieval_latency_ms": 412.3,
              # 真检索后端会给这一项，与 slices **逐项对齐**。替身不给（见下面
              # test_stand_in_leaves_document_id_null）。
              **({"hit_documents": [{"document_id": f"doc-{i}", "slice_id": f"s00{i}"}
                                    for i in range(1, len(scores) + 1)]}
                 if backend == "supabase" else {})},
    )


#: 一段平均约 80 字/段的文本 —— P0 的区间是 30–120，所以它达标。
#: 长度是真的算出来的，不是我声称的：下面 `test_fixture_is_what_it_claims` 钉着这一点。
PASSING_TEXT = "\n\n".join(["这一段讲的是能源系统的整体结构，先给结论再展开细节，"
                            "让学生知道每一部分在整体里的位置和作用，避免只记住零散名词。"] * 3)
#: 每段约 12 字 —— **低于 P0 的下界 30**，所以在 P0 下不达标。
FAILING_TEXT = "\n\n".join(["能源很重要。", "要省着用。", "就这样。"])


def make_result(*, regenerations=0, accepted=True, context=None, config=None):
    """造一次 `run_round()` 的结果。**审查结果是真算的**（见上面那段说明）。"""
    cfg = config or style_config()
    final = real_review(PASSING_TEXT if accepted else FAILING_TEXT, cfg)
    failed = real_review(FAILING_TEXT, cfg)
    attempts = [FakeAttempt(i, failed) for i in range(regenerations)]
    attempts.append(FakeAttempt(regenerations, final))
    return FakeResult(attempts=attempts, context=context or make_context(),
                      accepted=accepted)


def style_config():
    from explain.style_axes import style_config_for

    return style_config_for("P0")


class TestFixtureHasDiscriminatingPower:
    """**夹具自己先被验一遍。**

    `PASSING_TEXT` 若在 P0 下其实不达标、`FAILING_TEXT` 若其实达标，
    下面那些用例还会绿，但它们测的已经不是自己声称的那件事了
    （同 F11：断错误码不断「有没有报错」）。
    """

    def test_passing_text_really_passes_in_p0(self):
        r = real_review(PASSING_TEXT, style_config())
        assert r.passed, f"PASSING_TEXT 在 P0 下不达标：{r.failed_dimensions}"

    def test_failing_text_really_fails_in_p0(self):
        r = real_review(FAILING_TEXT, style_config())
        assert not r.passed and "paragraph_length" in r.failed_dimensions, r.failed_dimensions

    def test_the_two_are_separated_by_paragraph_length(self):
        """两者的区别就是段落长度 —— 而 P0 的区间从**生产配置**读，不写字面量。"""
        cfg = style_config()
        rng = cfg.paragraph_length
        lo = real_review(FAILING_TEXT, cfg).evaluation.by_name("paragraph_length").raw
        hi = real_review(PASSING_TEXT, cfg).evaluation.by_name("paragraph_length").raw
        assert lo < rng.min <= hi <= rng.max, f"{lo} / {hi} / 区间 {rng.min}–{rng.max}"


class TestBuild:
    def test_emits_three_events_when_no_regeneration(self):
        """**没有重生成时不发 `regeneration_triggered`。**

        发一条 `attempt_index: 0` 的话，「重生成率」的分子会把每一轮都算进去——
        而它是 PRD §3.2 的过程指标之一。
        """
        events = build_explain_events(make_result(), config=style_config(),
                                      question="Q", course_id="c1")
        assert [e["event_type"] for e in events] == [
            "explain_turn", "style_review_result", "retrieval_executed"]

    def test_one_regeneration_event_per_attempt(self):
        """一次重生成一条事件（口径 F37 的同一条：一步一个事件）。

        压成一条「重生成了 2 次」的话，「第 1 次是哪个维度不达标、第 2 次是哪个」
        就再也分不开，而那正是要拿来看审查反馈有没有用的东西。
        """
        events = build_explain_events(make_result(regenerations=2), config=style_config(),
                                      question="Q", course_id="c1")
        regen = [e for e in events if e["event_type"] == "regeneration_triggered"]
        assert [e["payload"]["attempt_index"] for e in regen] == [1, 2]
        # 失败维度由**生产的 RuleReviewer** 算出来，不是我填的
        want = list(real_review(FAILING_TEXT, style_config()).failed_dimensions)
        assert want, "夹具的 FAILING_TEXT 在 P0 下竟然达标了 —— 这一条失去分辨力"
        assert all(e["payload"]["failed_dimensions"] == want for e in regen)

    def test_attempt_index_starts_at_one(self):
        """`attempts[0]` 是**首次生成**，不是重生成。从 0 起的话
        「重生成 1 次」和「重生成 2 次」在数据里错开一格。"""
        events = build_explain_events(make_result(regenerations=1), config=style_config(),
                                      question="Q", course_id="c1")
        regen = [e for e in events if e["event_type"] == "regeneration_triggered"]
        assert len(regen) == 1 and regen[0]["payload"]["attempt_index"] == 1

    def test_usage_is_four_way_not_merged(self):
        """token 四类分列。合并之后就再也算不回成本结构（§13.0）。"""
        ev = _by_type(build_explain_events(make_result(), config=style_config(),
                                          question="Q", course_id="c1"), "explain_turn")
        assert set(ev["payload"]["usage"]) == {
            "input_tokens", "cache_read", "cache_creation", "output_tokens"}

    def test_pricing_carries_expiry(self):
        """价目带到期日，否则事后无法重算（§13.0 原话）。"""
        ev = _by_type(build_explain_events(make_result(), config=style_config(),
                                          question="Q", course_id="c1"), "explain_turn")
        assert "pricing_intro_until" in ev["payload"]
        assert ev["payload"]["pricing_rates"] is not None

    def test_first_token_latency_is_null_not_faked(self):
        """不流式 → 测不了。**写 null，不塞假数**（PRD §9.2 本轮无数据）。"""
        ev = _by_type(build_explain_events(make_result(), config=style_config(),
                                          question="Q", course_id="c1"), "explain_turn")
        assert ev["payload"]["first_token_latency_ms"] is None

    def test_both_config_identifiers_are_recorded(self):
        """F04：`config_fingerprint` 管「阈值变没变」，`metric_set_id` 管
        「指标集是不是同一个」。只记前者挡不住跨指标集的误合并。"""
        cfg = style_config()
        ev = _by_type(build_explain_events(make_result(), config=cfg,
                                          question="Q", course_id="c1"), "explain_turn")
        assert ev["payload"]["style_config_fingerprint"] == cfg.fingerprint()
        assert ev["payload"]["metric_set_id"] == cfg.metric_set_id()
        assert ev["payload"]["style_config_fingerprint"] != ev["payload"]["metric_set_id"]

    def test_dimensions_are_per_dimension_not_a_blended_score(self):
        """§8.1 已定不做单一混合分。只存合并结论的话，§3.2 的
        「各激活维度均需达标」事后就算不出来。"""
        cfg = style_config()
        ev = _by_type(build_explain_events(make_result(config=cfg), config=cfg,
                                          question="Q", course_id="c1"),
                      "style_review_result")
        dims = ev["payload"]["dimensions"]
        # **是列表，每项自带 name** —— 生产的 `Evaluation.to_dict()["dimensions"]`
        # 是个字典，直接搬过来分析时按行展开不了，且 jsonb 的键序不保证。
        assert isinstance(dims, list) and len(dims) > 1
        assert all({"name", "raw", "passed", "target", "activated"} <= set(d) for d in dims)
        # `counted_dimensions` 与生产的 `ev.scored` 同源，不是按 activated 重筛。
        real = real_review(PASSING_TEXT, cfg).evaluation
        assert ev["payload"]["counted_dimensions"] == [d.name for d in real.scored]
        assert "paragraph_length" in ev["payload"]["counted_dimensions"]

    def test_dimensions_is_a_list_because_production_gives_a_dict(self):
        """**钉住那个 bug 本身。**

        `Evaluation.to_dict()["dimensions"]` 是 `{name: {...}}`。
        少了这一条，有人会把 `events.py` 改回 `ev.to_dict()["dimensions"]`
        而上一条仍然可能绿（它只看形状对不对，不看来源）。
        """
        cfg = style_config()
        raw = real_review(PASSING_TEXT, cfg).evaluation.to_dict()["dimensions"]
        assert isinstance(raw, dict), "生产实现变了：现在它返回列表了，events.py 的注释要更新"
        ev = _by_type(build_explain_events(make_result(config=cfg), config=cfg,
                                          question="Q", course_id="c1"),
                      "style_review_result")
        assert isinstance(ev["payload"]["dimensions"], list)

    def test_all_activated_passed_may_be_null(self):
        """无维度可判时为 null，**不是 true**（口径 D42）。

        写 true 的后果是「什么都没判」与「全部达标」在数据里长得一样，
        而风格遵循度是北极星指标的一部分。

        空文本让所有维度都「算不出数」（`counts_toward_score` 为 false），
        于是 `scored` 为空 → `all_activated_passed` 为 None。
        **这是生产的 `evaluate()` 自己算出来的**，不是我把 None 塞进一个假货。
        """
        cfg = style_config()
        empty = FakeResult(attempts=[FakeAttempt(0, real_review("", cfg))],
                           context=make_context())
        ev = _by_type(build_explain_events(empty, config=cfg, question="Q", course_id="c1"),
                      "style_review_result")
        assert ev["payload"]["all_activated_passed"] is None
        assert ev["payload"]["counted_dimensions"] == []

    def test_slice_ids_carry_three_parts(self):
        """`slice_id` 是文档内局部 id，一门课两份讲义都有 's001'。

        只记 slice_id 的话事后分不开是哪一份讲义 ——
        这不是推演，2026-09-12 真库上观测到两行 `slice_id` 确实同名。

        **`document_id` 也必须在**：同一份文件可以挂在不同课程下
        （唯一索引带 course_id，口径 F47），真库上 `MIT11_201F07_slides2.pdf`
        同时在两门课里——按 `source_file` 分组会把两门课的召回混成一份。
        """
        ev = _by_type(build_explain_events(make_result(), config=style_config(),
                                          question="Q", course_id="c1"),
                      "retrieval_executed")
        ids = ev["payload"]["slice_ids"]
        assert all({"source_file", "slice_id", "document_id"} == set(x) for x in ids)
        assert [x["document_id"] for x in ids] == ["doc-1", "doc-2"]

    def test_stand_in_leaves_document_id_null(self):
        """替身后端没有 `document_id` → **None，不编一个**。

        编的话按 deck 分组的分析会把替身那批算成某一份真实讲义。
        """
        ev = _by_type(build_explain_events(
            make_result(context=make_context(backend="in_memory")),
            config=style_config(), question="Q", course_id=None), "retrieval_executed")
        assert all(x["document_id"] is None for x in ev["payload"]["slice_ids"])

    def test_misaligned_hit_documents_are_not_force_matched(self):
        """`hit_documents` 与 `slices` 长度对不上 → **全部 None，不按序号硬配**。

        硬配会把身份配错，而**配错的身份比没有身份更糟**：它看起来是对的。
        """
        ctx = make_context()
        ctx.meta["hit_documents"] = ctx.meta["hit_documents"][:1]   # 人为弄不齐
        ev = _by_type(build_explain_events(make_result(context=ctx),
                                          config=style_config(), question="Q",
                                          course_id="c1"), "retrieval_executed")
        assert all(x["document_id"] is None for x in ev["payload"]["slice_ids"])

    def test_backend_is_recorded_so_stand_in_is_separable(self):
        """替身与真检索必须在事件里分得开：它们的召回数字不可合并。"""
        real = _by_type(build_explain_events(make_result(), config=style_config(),
                                            question="Q", course_id="c1"),
                        "retrieval_executed")
        fake = _by_type(build_explain_events(
            make_result(context=make_context(backend="in_memory")),
            config=style_config(), question="Q", course_id=None), "retrieval_executed")
        assert real["payload"]["backend"] == "supabase"
        assert fake["payload"]["backend"] == "in_memory"

    def test_scores_and_pages_come_through(self):
        ev = _by_type(build_explain_events(make_result(), config=style_config(),
                                          question="Q", course_id="c1"),
                      "retrieval_executed")
        assert ev["payload"]["top_k_scores"] == [0.57, 0.54]
        assert ev["payload"]["pages"] == [11, 12]
        assert ev["payload"]["latency_ms"] == 412.3

    def test_missing_required_field_raises_at_construction(self):
        """缺必需字段在**构造时**就抛，不交给数据库去拒。

        数据库拒掉的表现是「这条事件没了」，而没了的事件在任何报表里都不出现，
        也没有任何东西会红（漏采集是最安静的一类 bug）。
        """
        from api.events import _complete

        with pytest.raises(ValueError, match="turn_index"):
            _complete("explain_turn", {"question": "Q"})


def _by_type(events: list[dict[str, Any]], t: str) -> dict[str, Any]:
    return next(e for e in events if e["event_type"] == t)


# ---------------------------------------------------------------- 写库


class SpyStore:
    def __init__(self, fail_times: int = 0) -> None:
        self.rows: list[dict[str, Any]] = []
        self.attempts = 0
        self.fail_times = fail_times

    def insert_events(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError("数据库返回 503")
        self.rows.extend(rows)


class TestRecord:
    def _events(self):
        return build_explain_events(make_result(), config=style_config(),
                                   question="Q", course_id="c1")

    def test_session_id_missing_means_no_events_and_a_named_reason(self):
        """**不现编 session_id。**

        编一个的后果是每一轮都变成一个独立会话，而「平均会话轮次」是
        北极星指标（PRD §3.1）——那个数会静静地变成 1.0 附近，
        看起来像一个真实的产品结论。
        """
        store = SpyStore()
        out = record_events(store, token="t", user_id="u", arm="pairwise",
                            session_id=None, events=self._events())
        assert store.rows == []
        assert out["emitted"] == 0
        assert "session_id" in out["skipped_reason"]

    def test_rows_carry_course_id_and_arm(self):
        store = SpyStore()
        record_events(store, token="t", user_id="u", arm="pairwise", session_id="s",
                      events=self._events(), course_id="c1")
        assert len(store.rows) == 3
        assert all(r["course_id"] == "c1" for r in store.rows)
        assert all(r["arm"] == "pairwise" for r in store.rows)
        assert all(r["session_id"] == "s" for r in store.rows)

    def test_transient_failure_is_retried_then_succeeds(self):
        """瞬时失败重试（口径 F42）。阶梯比浏览器那份短——用户正在等这一屏。"""
        store = SpyStore(fail_times=1)
        out = record_events(store, token="t", user_id="u", arm="pairwise", session_id="s",
                            events=self._events(), sleep=lambda ms: None)
        assert out["failed"] == 0 and out["emitted"] == 3
        assert store.attempts == 4  # 第一条失败 1 次 + 成功 1 次，另两条各 1 次

    def test_permanent_failure_is_reported_not_swallowed(self):
        """重试用尽仍失败 → **进响应，不静默**。

        静默的表现是「事件少了」，而少掉的事件在任何报表里都不出现。
        """
        store = SpyStore(fail_times=99)
        out = record_events(store, token="t", user_id="u", arm="pairwise", session_id="s",
                            events=self._events(), sleep=lambda ms: None)
        assert out["emitted"] == 0
        assert out["failed"] == 3
        assert len(out["failures"]) == 3
        assert "503" in out["failures"][0]["error"]
