"""检索助手（口径 F96–F100）。**每个助手都要证明：失败时回落到「没有它」的行为，且回落被记下来。**"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest

import explain  # noqa: F401
from explain import rag as R
from explain.llm import Completion, Usage


@dataclass
class Scripted:
    """按脚本返回文本的假模型，并留下它收到的东西。"""

    replies: list[str]
    name: str = "fake:claude-haiku-4-5"
    delay_s: float = 0.0
    raise_exc: Exception | None = None
    seen: list[tuple[str, Any]] = field(default_factory=list)

    def complete(self, system, messages):
        self.seen.append((system, messages))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.raise_exc:
            raise self.raise_exc
        text = self.replies[min(len(self.seen) - 1, len(self.replies) - 1)]
        return Completion(text=text, usage=Usage(input_tokens=1000, output_tokens=50),
                          latency_ms=1.0, model=self.name)

    def request_signature(self, system, messages):
        return "x"


class TestRewriter:
    def test_resolves_and_returns_keywords(self):
        m = Scripted(['{"query": "卡诺效率为什么小于 1？", "keywords": ["Carnot efficiency", "Te/Ti"]}'])
        r = R.QueryRewriter(m).rewrite("那它为什么小于 1？",
                                       [{"role": "user", "content": "卡诺效率是什么"},
                                        {"role": "assistant", "content": "η = 1 − Te/Ti …"}])
        assert r.implementation == "model"
        assert r.query == "卡诺效率为什么小于 1？"
        assert r.keywords == ("Carnot efficiency", "Te/Ti")
        assert r.cost_usd and r.cost_usd > 0

    def test_history_reaches_the_prompt_but_truncated(self):
        m = Scripted(['{"query": "q", "keywords": []}'])
        long_answer = "答" * 1000
        R.QueryRewriter(m, max_answer_chars=300).rewrite(
            "那它呢", [{"role": "user", "content": "问"}, {"role": "assistant", "content": long_answer}])
        sent = m.seen[0][1][0]["content"]
        assert "【最近的对话】" in sent and "答" * 300 in sent and "答" * 301 not in sent

    def test_no_history_no_dialogue_block(self):
        m = Scripted(['{"query": "q", "keywords": []}'])
        R.QueryRewriter(m).rewrite("热机效率是什么", [])
        assert "【最近的对话】" not in m.seen[0][1][0]["content"]

    @pytest.mark.parametrize("bad", ["随便说点什么", '{"query": ""}', '{"query": "x", "keywords": "a"}'])
    def test_invalid_output_falls_back_to_the_raw_question(self, bad):
        r = R.QueryRewriter(Scripted([bad])).rewrite("原问题")
        assert r.implementation == "invalid" and r.query == "原问题" and r.keywords == ()

    def test_exception_falls_back_and_says_why(self):
        r = R.QueryRewriter(Scripted([""], raise_exc=RuntimeError("down"))).rewrite("原问题")
        assert r.implementation == "error" and r.query == "原问题"
        assert "down" in (r.error or "")
        assert r.cost_usd is None, "失败时成本是未知，不是 0（F73 同一条）"

    def test_timeout_does_not_hang(self):
        t0 = time.perf_counter()
        r = R.QueryRewriter(Scripted(["{}"], delay_s=1.0), timeout_s=0.05).rewrite("原问题")
        assert time.perf_counter() - t0 < 0.5
        assert r.implementation == "error" and "Timeout" in (r.error or "")


class TestReranker:
    CANDS = [("A", [1], "aaa"), ("B", [2], "bbb"), ("C", [3], "ccc"), ("D", [4], "ddd")]

    def test_order_is_taken_from_the_model(self):
        r = R.LlmReranker(Scripted(['{"order": [3, 1]}'])).rerank("q", self.CANDS, top_n=2)
        assert r.order == (2, 0) and r.implementation == "model"

    def test_unknown_and_duplicate_ids_are_dropped(self):
        r = R.LlmReranker(Scripted(['{"order": [9, 2, 2, "x", 4]}'])).rerank("q", self.CANDS, top_n=5)
        assert r.order == (1, 3)

    def test_top_n_caps(self):
        r = R.LlmReranker(Scripted(['{"order": [1,2,3,4]}'])).rerank("q", self.CANDS, top_n=2)
        assert r.order == (0, 1)

    def test_invalid_means_none_so_caller_uses_fusion_order(self):
        r = R.LlmReranker(Scripted(["没有 JSON"])).rerank("q", self.CANDS, top_n=2)
        assert r.order is None and r.implementation == "invalid"

    def test_failure_means_none(self):
        r = R.LlmReranker(Scripted([""], raise_exc=RuntimeError("x"))).rerank("q", self.CANDS, top_n=2)
        assert r.order is None and r.implementation == "error" and r.cost_usd is None

    def test_single_candidate_skips_the_call(self):
        m = Scripted(['{"order": [1]}'])
        r = R.LlmReranker(m).rerank("q", self.CANDS[:1], top_n=5)
        assert r.order == (0,) and r.implementation == "skipped" and m.seen == []

    def test_prompt_says_how_many(self):
        m = Scripted(['{"order": [1]}'])
        R.LlmReranker(m).rerank("q", self.CANDS, top_n=3)
        assert "至多 3 个" in m.seen[0][0]


class TestContextualizer:
    SLICES = [(f"s{i:03d}", [i], f"T{i}", f"text {i}") for i in range(1, 30)]

    def test_batches_and_merges(self):
        def reply_for(batch_ids):
            return "{" + ",".join(f'"{s}": "ctx {s}"' for s in batch_ids) + "}"

        class PerBatch(Scripted):
            def complete(self, system, messages):
                ids = [ln[1:5] for ln in messages[0]["content"].splitlines() if ln.startswith("<s")]
                self.replies = [reply_for(ids)]
                return super().complete(system, messages)

        m = PerBatch([""])
        r = R.Contextualizer(m, batch_size=12, workers=2).contextualize("f.pdf", self.SLICES)
        assert r.calls == 3 and r.failed_batches == 0
        assert len(r.contexts) == 29 and r.contexts["s007"] == "ctx s007"

    def test_outline_is_in_the_system_for_every_batch(self):
        m = Scripted(["{}"])
        R.Contextualizer(m, batch_size=10, workers=1).contextualize("f.pdf", self.SLICES)
        assert all("s029｜第 29 页｜T29" in sys for sys, _ in m.seen)

    def test_a_failed_batch_does_not_sink_the_others(self):
        class FailSecond(Scripted):
            def complete(self, system, messages):
                if "<s013>" in messages[0]["content"]:
                    raise RuntimeError("batch 2 down")
                return super().complete(system, messages)

        m = FailSecond(['{"s001": "c1"}'])
        r = R.Contextualizer(m, batch_size=12, workers=1).contextualize("f.pdf", self.SLICES)
        assert r.failed_batches >= 1 and "s001" in r.contexts
        assert r.cost_usd is None, "有一批失败（可能已计费）→ 总成本未知"

    def test_answers_for_slices_not_in_the_batch_are_ignored(self):
        m = Scripted(['{"s001": "c1", "s999": "幻觉出来的编号"}'])
        r = R.Contextualizer(m, batch_size=50, workers=1).contextualize("f.pdf", self.SLICES)
        assert "s999" not in r.contexts


class TestVision:
    def test_transcribes(self):
        m = Scripted(["图里写着：热机效率 η = 1 − Te/Ti"])
        r = R.VisionTranscriber(m).transcribe("f.pdf", 33, b"\x89PNG fake")
        assert r.implementation == "model" and "热机效率" in r.text
        content = m.seen[0][1][0]["content"]
        assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
        assert "第 33 页" in content[1]["text"]

    def test_blank_page_is_empty_not_text(self):
        r = R.VisionTranscriber(Scripted(["[无内容]"])).transcribe("f.pdf", 2, b"x")
        assert r.implementation == "empty" and r.text == ""

    def test_failure_keeps_the_page_skipped(self):
        r = R.VisionTranscriber(Scripted([""], raise_exc=RuntimeError("x"))).transcribe("f.pdf", 2, b"x")
        assert r.implementation == "error" and r.text == ""

    def test_many_keeps_order(self):
        m = Scripted(["页内容"])
        out = R.VisionTranscriber(m, workers=3).transcribe_many("f.pdf", [(5, b"a"), (2, b"b"), (9, b"c")])
        assert [r.page_index for r in out] == [5, 2, 9]


class TestModelChoice:
    def test_rag_model_is_haiku_45(self):
        assert R.RAG_MODEL_NAME.startswith("claude-haiku-4-5")

    def test_rag_and_gap_ids_are_written_separately(self):
        """两个独立的选择，数值相同但各写一处（见模块说明第 1 条）。"""
        import inspect

        from explain import gap

        assert 'RAG_MODEL_NAME = "claude-haiku-4-5' in inspect.getsource(R)
        assert 'GAP_MODEL_NAME = "claude-haiku-4-5' in inspect.getsource(gap)

    def test_built_model_has_no_effort_and_no_thinking(self):
        m = R.build_rag_model(300)
        assert m.name == R.RAG_MODEL_NAME and m.effort is None and m.thinking == "disabled"
