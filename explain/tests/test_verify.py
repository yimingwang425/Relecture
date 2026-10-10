"""出处核对 agent（口径 F121–F123）。

替身钉的是**代码那一半**：分类规则、摘录必须真的在读到过的那一页、not_found 必须查过、
标过的每一页都要核到、归一化不误退回、只核课件那一半、退回后能改对、限步与强制提交、永不抛。
「摘录支不支持说法」是模型的判断，替身证明不了——那是注入错误评测（`scripts/verify_eval.py`）的事。
"""

from __future__ import annotations

from explain.agent import WEB_HEADING as AGENT_WEB_HEADING
from explain.context import ContextPack, ContextSlice
from explain.fake import ScriptedToolModel
from explain.verify import (
    MAX_STEPS,
    TOOL_READ,
    TOOL_SEARCH,
    TOOL_SUBMIT,
    WEB_HEADING,
    VerifyAgent,
    _Seen,
    check_verdicts,
    cited_pages,
    course_part,
    normalize,
)

P3 = ContextSlice(source_file="deck.pdf", slice_id="s3", page_indices=(3,), title="Examples",
                  text="Reflection across a line: vectors on the line have eigenvalue 1, "
                       "vectors perpendicular to it have eigenvalue -1.", char_count=100)
P2 = ContextSlice(source_file="deck.pdf", slice_id="s2", page_indices=(2,), title="Definition",
                  text="An eigenvector v satisfies Av = λv; λ is its eigenvalue. A vector it leaves alone has eigenvalue 1.",
                  char_count=100)
P5 = ContextSlice(source_file="deck.pdf", slice_id="s5", page_indices=(5,), title="Symmetric",
                  text="For a symmetric matrix, eigenvectors of distinct eigenvalues are orthogonal.", char_count=80)

ANSWER = ("反射时，镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）。\n\n"
          "就像照镜子：镜面上的东西不动，垂直的被翻过去。")


class Deck:
    slices = [P2, P3, P5]

    def fetch(self, q):
        hits = [s for s in self.slices if any(w.lower() in s.text.lower() for w in q.split())]
        return ContextPack(question=q, slices=tuple(hits[:5]), meta={})

    def page(self, f, p):
        return ContextPack(question="", slices=tuple(s for s in self.slices if p in s.page_indices), meta={})


def fact(claim, page, verdict, quote="", ev_page=0):
    return {"claim": claim, "kind": "fact", "cited_page": page, "verdict": verdict,
            "evidence_quote": quote, "evidence_file": "deck.pdf" if quote else "", "evidence_page": ev_page}


SUPP = {"claim": "就像照镜子：镜面上的东西不动，垂直的被翻过去。", "kind": "supplement", "cited_page": 0,
        "verdict": "supplement", "evidence_quote": "", "evidence_file": "", "evidence_page": 0}
GOOD = {"claims": [fact("镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）", 3, "supported",
                        "vectors on the line have eigenvalue 1", 3), SUPP]}


def ctx(*slices):
    return ContextPack(question="q", slices=tuple(slices), meta={})


def agent(script):
    m = ScriptedToolModel(script=script, name="fake:claude-haiku-4-5")
    return VerifyAgent(m, Deck()), m


class TestNormalize:
    def test_pdf_and_width_artifacts_do_not_cause_false_rejects(self):
        slice_text = "eigen-\nvalue  is  the  stretch\nfactor"
        quote = "eigenvalue is the stretch factor"
        assert normalize(quote) in normalize(slice_text)
        assert normalize("ＡＶ＝λｖ") == normalize("AV=λv")
        assert normalize("“特征值”—1") == normalize('"特征值"-1')
        assert normalize("`Av=λv` 与 **特征值**") == normalize("Av=λv与特征值")

    def test_cited_pages(self):
        assert cited_pages("见第3页，也见第 4、5 页和第7页") == {3, 4, 5, 7}
        assert cited_pages("没有页码") == set()

    def test_only_course_part_is_checked(self):
        t = "课件部分（第3页）。\n\n" + WEB_HEADING + "：网上的内容。"
        assert course_part(t) == "课件部分（第3页）。"
        assert WEB_HEADING == AGENT_WEB_HEADING      # 两处同值（核对模块不反向依赖 agent）


class TestCheck:
    def test_good_submission_passes(self):
        claims, p = check_verdicts(GOOD, ANSWER, _Seen([P3]))
        assert p == [] and [c.verdict for c in claims] == ["supported", "supplement"]

    def test_claim_must_be_copied_from_the_answer(self):
        bad = {"claims": [fact("反射的特征值是正负一", 3, "supported", "vectors on the line have eigenvalue 1", 3)]}
        _, p = check_verdicts(bad, ANSWER, _Seen([P3]))
        assert any("原样摘出" in x for x in p)

    def test_quote_must_exist_on_a_seen_page(self):
        bad = {"claims": [fact("镜面方向上的向量特征值是 1", 3, "supported", "eigenvalue 1 for mirrors always", 3), SUPP]}
        _, p = check_verdicts(bad, ANSWER, _Seen([P3]))
        assert any("不在你读到过的任何一页" in x for x in p)

    def test_quote_on_unseen_page_is_rejected(self):
        """摘录是真的，但这次核对没读到那一页——不能算。允许控制见 test_good_submission_passes。"""
        _, p = check_verdicts(GOOD, ANSWER, _Seen([P2]))
        assert p

    def test_supported_on_another_page_must_be_wrong_page(self):
        sub = {"claims": [fact("镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）", 3, "supported",
                               "A vector it leaves alone has eigenvalue 1", 2), SUPP]}
        _, p = check_verdicts(sub, ANSWER, _Seen([P2, P3]))
        assert any("wrong_page" in x for x in p)
        sub["claims"][0]["verdict"] = "wrong_page"
        claims, p = check_verdicts(sub, ANSWER, _Seen([P2, P3]))
        assert p == [] and claims[0].evidence_page == 2

    def test_not_found_requires_a_search(self):
        nf = {"claims": [fact("镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）", 3, "not_found"), SUPP]}
        seen = _Seen([P3])
        _, p = check_verdicts(nf, ANSWER, seen)
        assert any("一次都没查过" in x for x in p)
        seen.searched = 1                                   # 允许控制：查过就可以判 not_found
        assert check_verdicts(nf, ANSWER, seen)[1] == []

    def test_every_cited_page_must_be_checked(self):
        only_supp = {"claims": [SUPP]}
        _, p = check_verdicts(only_supp, ANSWER, _Seen([P3]))
        assert any("第 3 页" in x for x in p)

    def test_supplement_is_not_checked_against_slides(self):
        claims, _ = check_verdicts(GOOD, ANSWER, _Seen([P3]))
        assert claims[1].kind == "supplement" and claims[1].evidence_quote == ""


class TestLoop:
    def test_reads_then_submits(self):
        a, m = agent([("tool", [(TOOL_READ, {"source_file": "deck.pdf", "page": 3})]),
                      ("tool", [(TOOL_SUBMIT, GOOD)])])
        run = a.verify(answer=ANSWER, context=ctx())
        assert run.status == "written", run.error
        assert run.counts == {"supported": 1, "wrong_page": 0, "not_found": 0, "supplement": 1}
        assert len(m.calls) == 2

    def test_reject_then_fix(self):
        bad = {"claims": [fact("镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）", 3, "supported",
                               "made up quote not in deck", 3), SUPP]}
        a, m = agent([("tool", [(TOOL_SUBMIT, bad)]), ("tool", [(TOOL_SUBMIT, GOOD)])])
        run = a.verify(answer=ANSWER, context=ctx(P3))
        assert run.status == "written" and run.rejected == 1
        back = m.calls[1]["messages"][-1]["content"][0]
        assert back["is_error"] is True and "不在你读到过" in back["content"]

    def test_search_then_not_found(self):
        nf = {"claims": [fact("镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）", 3, "not_found"), SUPP]}
        a, _ = agent([("tool", [(TOOL_SEARCH, {"query": "nothing-matches"})]), ("tool", [(TOOL_SUBMIT, nf)])])
        run = a.verify(answer=ANSWER, context=ctx(P3))
        assert run.status == "written" and run.counts["not_found"] == 1

    def test_step_cap_forces_submit(self):
        a, m = agent([("tool", [(TOOL_SEARCH, {"query": "eigenvalue"})])] * 20)
        run = a.verify(answer=ANSWER, context=ctx(P3))
        assert run.status == "failed" and len(m.calls) == MAX_STEPS
        assert m.calls[-1]["tool_choice"] == {"type": "tool", "name": TOOL_SUBMIT}
        assert all(c["tool_choice"] is None for c in m.calls[:-1])

    def test_never_raises(self):
        a, _ = agent([("raise", "上游 529")])
        run = a.verify(answer=ANSWER, context=ctx(P3))
        assert run.status == "failed" and "529" in run.error

    def test_web_part_is_not_sent(self):
        a, m = agent([("tool", [(TOOL_SUBMIT, GOOD)])])
        a.verify(answer=ANSWER + "\n\n" + WEB_HEADING + "：网上说特征值有一百种用途。", context=ctx(P3))
        assert "一百种用途" not in m.calls[0]["messages"][0]["content"]

    def test_nothing_to_check_is_skipped(self):
        a, m = agent([])
        run = a.verify(answer=WEB_HEADING + "：全是网上的。", context=ctx(P3))
        assert run.status == "skipped" and m.calls == []


    def test_rejection_reason_is_recorded(self):
        """只记「退回 N 处」的话，评测里那些失败查不出卡在哪一条（2026-10-10，留出集上 v2 就是靠它查出来的）。"""
        bad = {"claims": [fact("镜面方向上的向量特征值是 1，垂直方向是 -1（第3页）", 3, "supported",
                               "made up quote not in deck", 3), SUPP]}
        a, _ = agent([("tool", [(TOOL_SUBMIT, bad)]), ("tool", [(TOOL_SUBMIT, GOOD)])])
        run = a.verify(answer=ANSWER, context=ctx(P3))
        assert "不在你读到过" in run.tools[0].summary
