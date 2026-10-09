"""上下文压缩 agent（口径 F116–F118）。

替身是 `ScriptedToolModel`——照生产 `AnthropicModel.stream` 的签名写的、会调工具的假模型。
这里钉的是**循环本身**：模型决定读什么、交什么；代码校验、退回、限步、强制提交；
失败不抛。真模型会不会这样用工具，替身证明不了——那是 `scripts/memory_live_check.py` 的事。
"""

from __future__ import annotations

from explain.fake import ScriptedToolModel
from explain.memory import (
    KEEP_LIVE_ROUNDS,
    MAX_STEPS,
    MEMORY_CHAR_BUDGET,
    MEMORY_CLOSE,
    MEMORY_OPEN,
    READ_TURNS_MAX,
    TOOL_READ,
    TOOL_SEARCH,
    TOOL_WRITE,
    TRIGGER_LIVE_ROUNDS,
    Concept,
    MemoryAgent,
    MemoryNote,
    Turn,
    apply_memory,
    check_note,
    plan_compaction,
)
from explain.memory import _render_brief


def turns(n: int, start: int = 1) -> list[Turn]:
    out = []
    for i in range(start, start + n):
        out.append(Turn(index=i, question=f"第{i}问：特征值{i}是什么",
                        answer=f"第{i}答。" + "讲解正文。" * 80 + f"例子{i}号",
                        detail_level="P2" if i < 5 else "P0", confusion=(i == 3)))
    return out


def good_note(through: int = 7, **over):
    base = {
        "fold_through_turn": through,
        "summary": "在学特征值与特征向量，讲到了对角化。",
        "concepts": [{"name": "特征值", "turns": [1, 3], "status": "struggling",
                      "note": "用拉伸的例子讲过，第 3 轮仍没懂"}],
        "open_questions": ["为什么对称矩阵一定能对角化"],
        "preferences": ["多举例"],
        "dropped_concepts": [],
    }
    base.update(over)
    return base


def agent(script, **kw):
    model = ScriptedToolModel(script=script, name="fake:claude-haiku-4-5")
    return MemoryAgent(model, **kw), model


PLAN = plan_compaction(list(range(1, 12)))     # 11 轮原文 → 触发


# ---------------------------------------------------------------- 触发：纯计数


class TestPlan:
    def test_triggers_only_above_the_line(self):
        assert plan_compaction(list(range(1, TRIGGER_LIVE_ROUNDS + 1))) is None
        p = plan_compaction(list(range(1, TRIGGER_LIVE_ROUNDS + 2)))
        assert p is not None
        # 最近 KEEP 轮永远留原文
        assert p.tail == tuple(range(TRIGGER_LIVE_ROUNDS + 2 - KEEP_LIVE_ROUNDS, TRIGGER_LIVE_ROUNDS + 2))
        assert (p.lo, p.hi) == (1, TRIGGER_LIVE_ROUNDS + 1 - KEEP_LIVE_ROUNDS)
        # 模型最多把最后两轮留作原文
        assert p.min_through == p.hi - 2

    def test_old_long_conversation_is_folded_in_bounded_chunks(self):
        p = plan_compaction(list(range(1, 31)))
        assert (p.lo, p.hi) == (1, 12)
        assert 13 in p.tail and 30 in p.tail

    def test_after_a_note_only_live_turns_count(self):
        # 笔记已覆盖 1–7，原文 8–15（8 轮）：不触发
        assert plan_compaction(list(range(8, 16))) is None
        p = plan_compaction(list(range(8, 19)))
        assert p.lo == 8


# ---------------------------------------------------------------- 循环


class TestTheLoop:
    def test_reads_then_writes_and_stops(self):
        a, model = agent([
            ("tool", [(TOOL_READ, {"from_turn": 1, "to_turn": 4})]),
            ("tool", [(TOOL_SEARCH, {"query": "例子3号"})]),
            ("tool", [(TOOL_WRITE, good_note())]),
            ("tool", [(TOOL_READ, {"from_turn": 5, "to_turn": 7})]),   # 不该被走到
        ])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        assert run.status == "written", run.error
        assert run.note.through_turn == 7
        assert [t.name for t in run.tools] == [TOOL_READ, TOOL_SEARCH, TOOL_WRITE]
        # **交出通过检查的笔记即停**：第 4 项脚本没有被调用
        assert len(model.calls) == 3
        # 读到的全文真的交回了模型（下一步的 messages 里有第 2 轮的完整回答结尾）
        second = model.calls[1]["messages"]
        assert any("例子2号" in str(m["content"]) for m in second)
        assert "第 3 轮" in run.tools[1].summary or run.tools[1].summary.endswith("处")

    def test_rejected_note_goes_back_with_reasons_and_is_fixed(self):
        a, model = agent([
            ("tool", [(TOOL_WRITE, good_note(through=99, summary=""))]),
            ("tool", [(TOOL_WRITE, good_note())]),
        ])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        assert run.status == "written" and run.rejected == 1
        # 退回理由作为 is_error 的工具结果交回了模型
        back = model.calls[1]["messages"][-1]["content"]
        assert back[0]["is_error"] is True
        assert "fold_through_turn" in back[0]["content"] and "summary" in back[0]["content"]

    def test_stopping_without_a_note_is_not_done(self):
        a, model = agent([("text", "我整理完了。"), ("tool", [(TOOL_WRITE, good_note())])])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        assert run.status == "written"
        nudge = model.calls[1]["messages"][-1]["content"]
        assert TOOL_WRITE in nudge

    def test_step_cap_and_forced_submit_on_the_last_step(self):
        a, model = agent([("tool", [(TOOL_SEARCH, {"query": "特征值"})])] * 20)
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        assert run.status == "failed" and str(MAX_STEPS) in run.error
        assert len(model.calls) == MAX_STEPS
        assert model.calls[-1]["tool_choice"] == {"type": "tool", "name": TOOL_WRITE}
        # 反向：前面几步**不**强制——否则「每一步都逼它交」也会让上一条全绿
        assert all(c["tool_choice"] is None for c in model.calls[:-1])

    def test_model_failure_never_raises(self):
        a, _ = agent([("raise", "上游 529")])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        assert run.status == "failed" and "529" in run.error

    def test_cost_is_summed_over_steps(self):
        a, _ = agent([("tool", [(TOOL_READ, {"from_turn": 1, "to_turn": 2})]),
                      ("tool", [(TOOL_WRITE, good_note())])])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        assert run.cost_usd > 0 and len(run.steps) == 2
        d = run.to_dict()
        assert d["status"] == "written" and d["through_turn"] == 7 and d["steps"] == 2


# ---------------------------------------------------------------- 工具


class TestTools:
    def test_read_is_capped_and_says_so(self):
        a, model = agent([("tool", [(TOOL_READ, {"from_turn": 1, "to_turn": 7})]),
                          ("tool", [(TOOL_WRITE, good_note())])])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        text = model.calls[1]["messages"][-1]["content"][0]["content"]
        assert f"第 1–{READ_TURNS_MAX} 轮" in text and "再读一次" in text
        assert "例子4号" in text and "例子5号" not in text
        assert run.tools[0].summary == f"第 1–{READ_TURNS_MAX} 轮"

    def test_cannot_read_outside_the_range(self):
        a, model = agent([("tool", [(TOOL_READ, {"from_turn": 9, "to_turn": 11})]),
                          ("tool", [(TOOL_WRITE, good_note())])])
        run = a.compact(prev=None, turns=turns(11), plan=PLAN)
        res = model.calls[1]["messages"][-1]["content"][0]
        assert res["is_error"] is True and "例子10号" not in res["content"]
        assert run.tools[0].ok is False

    def test_brief_is_an_index_not_the_full_text(self):
        """目录只给回答的开头——要全文就得自己去读。少了这一条，「读」这个决定就是假的。"""
        brief = _render_brief(None, {t.index: t for t in turns(11)}, PLAN)
        assert "例子2号" not in brief
        assert "第2问" in brief
        assert "调整了讲法" in brief and "没看懂" in brief
        assert "之后仍保留原文" in brief and "第11问" in brief


# ---------------------------------------------------------------- 校验


class TestCheck:
    def test_good_note_passes(self):
        note, problems = check_note(good_note(), PLAN, None)
        assert problems == [] and note.through_turn == 7

    def test_hold_back_is_bounded(self):
        _, p = check_note(good_note(through=PLAN.min_through - 1), PLAN, None)
        assert p and "fold_through_turn" in p[0]
        note, p = check_note(good_note(through=PLAN.min_through), PLAN, None)
        assert p == [] and note.through_turn == PLAN.min_through

    def test_cited_turn_beyond_fold_is_rejected(self):
        bad = good_note(through=5, concepts=[{"name": "x", "turns": [6], "status": "understood", "note": "n"}])
        _, p = check_note(bad, PLAN, None)
        assert any("第 [6] 轮" in s for s in p)

    def test_struggling_needs_turns(self):
        bad = good_note(concepts=[{"name": "x", "turns": [], "status": "struggling", "note": "n"}])
        _, p = check_note(bad, PLAN, None)
        assert any("struggling" in s for s in p)

    def test_budget(self):
        many = [{"name": f"概念{i}", "turns": [1], "status": "understood", "note": "长" * 110}
                for i in range(12)]
        _, p = check_note(good_note(concepts=many), PLAN, None)
        assert any(str(MEMORY_CHAR_BUDGET) in s for s in p)

    def test_prior_concepts_cannot_vanish_silently(self):
        prev = MemoryNote(through_turn=3, summary="s",
                          concepts=(Concept("行列式", (1,), "understood", "n"),))
        _, p = check_note(good_note(), PLAN, prev)
        assert any("行列式" in s for s in p)
        # 允许控制：明确列出不要 → 通过
        note, p = check_note(good_note(dropped_concepts=["行列式"]), PLAN, prev)
        assert p == [] and note is not None
        # 允许控制：保留 → 通过
        kept = good_note(concepts=good_note()["concepts"] + [
            {"name": "行列式", "turns": [1], "status": "understood", "note": "n"}])
        assert check_note(kept, PLAN, prev)[1] == []


# ---------------------------------------------------------------- 注入讲解请求


def rows(lo: int, hi: int) -> list[dict]:
    out = []
    for i in range(lo, hi + 1):
        out.append({"role": "user", "content": f"问{i}", "turn_index": i})
        out.append({"role": "assistant", "content": f"答{i}", "turn_index": i})
    return out


NOTE = MemoryNote(through_turn=7, summary="在学特征值。",
                  concepts=(Concept("特征值", (1, 3), "struggling", "拉伸的例子讲过"),),
                  preferences=("多举例",))


class TestApply:
    def test_folded_turns_are_replaced_by_the_note(self):
        msgs = apply_memory(NOTE, rows(1, 11))
        assert [m["role"] for m in msgs] == ["user", "assistant"] * 4
        assert msgs[0]["content"].startswith(MEMORY_OPEN)
        assert MEMORY_CLOSE in msgs[0]["content"] and msgs[0]["content"].endswith("问8")
        contents = {m["content"] for m in msgs}
        folded = {f"问{i}" for i in range(1, 8)} | {f"答{i}" for i in range(1, 8)}
        assert not contents & folded
        assert {"问9", "答8", "答11"} <= contents
        assert "拉伸的例子" in msgs[0]["content"] and "多举例" in msgs[0]["content"]

    def test_no_note_no_change(self):
        """允许控制：没有笔记时历史原样——否则「一律截掉前几轮」也会让上一条全绿。"""
        assert apply_memory(None, rows(1, 3)) == [
            {"role": r["role"], "content": r["content"]} for r in rows(1, 3)]

    def test_prefix_is_stable_between_compactions(self):
        """两次整理之间**前缀一字不变**——prompt caching 靠它（口径 F118）。"""
        a = apply_memory(NOTE, rows(1, 9))
        b = apply_memory(NOTE, rows(1, 10))
        assert b[:len(a)] == a

    def test_round_trip(self):
        assert MemoryNote.from_dict(NOTE.to_dict()) == NOTE
        assert MemoryNote.from_dict({"through_turn": "7"}) is None
        assert MemoryNote.from_dict(None) is None
