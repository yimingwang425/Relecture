"""链路与口径测试。

**这些测试刻意不使用 ``FakeEmbedder`` 做召回断言。** 原因见第七条通则：
本语料是英文、评测集问题是中文（评测集刻意用学生的中文说法提问），
词袋哈希两侧 token 零重合，**所有召回指标恒为 0**——
那样的断言在检索逻辑被整个删掉时也会通过，等于没测。

故召回相关的断言一律用 ``PlantedEmbedder``：它把已知答案按可控强度种进向量，
命中与落空都能被造出来，断言才有区分力。
"""

from __future__ import annotations

import numpy as np
import pytest

from retrieval.compose import EMBED_TEXT_RULESET_VERSION, VARIANTS, boilerplate_keys, compose
from retrieval.corpus import EXCLUDED_FILES, RESTRICTED_PAGES, build_items, load_eval, load_slicesets
from retrieval.embed import EmbeddingCache, FakeEmbedder, PlantedEmbedder
from retrieval.index import IndexedSlice, VectorIndex
from retrieval.metrics import Separability, score_question, separability, summarize
from retrieval.search import retrieve, retrieved_pages


@pytest.fixture(scope="module")
def slicesets():
    return load_slicesets()


@pytest.fixture(scope="module")
def items(slicesets):
    return build_items(slicesets)


@pytest.fixture(scope="module")
def questions():
    return load_eval()


class TestCorpusExclusions:
    def test_scanned_document_excluded(self, slicesets):
        assert EXCLUDED_FILES.isdisjoint(slicesets)

    def test_restricted_pages_not_indexed(self, items):
        """受限页在索引侧也要排除，不能只靠评测集不引用它们。"""
        for it in items:
            banned = RESTRICTED_PAGES.get(it.source_file, frozenset())
            assert not (set(it.page_indices) & banned), f"{it.source_file} {it.page_indices}"

    def test_image_only_pages_absent(self, items):
        """image_only 页不产生切片，故不在索引里——unreadable_page 上限恒为 0 的根据。"""
        pages = {(it.source_file, p) for it in items for p in it.page_indices}
        for p in (33, 34, 35, 36):
            assert ("MIT14_129S25_lec02.pdf", p) not in pages

    def test_index_size(self, items):
        # 100 个可用切片 − 15.316 的 0 个 − 14.129 的 8 个受限页
        assert len(items) == 92


class TestCompose:
    def test_all_variants_produce_text(self, slicesets):
        for name, ss in slicesets.items():
            for v in VARIANTS:
                out = compose(ss, v)
                assert len(out) == len(ss.slices)
                assert all(t.strip() for t in out.values()), f"{name}/{v} 有空文本"

    def test_unknown_variant_rejected(self, slicesets):
        with pytest.raises(ValueError):
            compose(next(iter(slicesets.values())), "T9")

    def test_control_and_pua_chars_stripped(self, slicesets):
        """缺陷清理：四个变体一律执行，不参与 A/B。乱码不是一种口径。"""
        import unicodedata

        for ss in slicesets.values():
            for v in VARIANTS:
                for text in compose(ss, v).values():
                    bad = [c for c in text if unicodedata.category(c) in ("Co", "Cc", "Cf", "Cs", "Cn")
                           and c not in "\n\t"]
                    assert not bad, f"残留控制/私用区字符 {[hex(ord(c)) for c in bad[:3]]}"

    def test_pua_actually_present_in_source(self, slicesets):
        """反向确认：源文本里确实有 U+F076，否则上一条测的是空集。"""
        raw = "".join(u.text for ss in slicesets.values() for s in ss.slices for u in s.units)
        assert "" in raw, "源文本无私用区字符，清理测试等于没测"

    def test_t1_drops_boilerplate(self, slicesets):
        ss = slicesets["MIT22_081JF10_lec01b.pdf"]
        keys = boilerplate_keys(ss)
        assert keys, "未识别出样板块"
        t0, t1 = compose(ss, "T0"), compose(ss, "T1")
        assert sum(len(t) for t in t1.values()) < sum(len(t) for t in t0.values())

    def test_t2_prepends_title(self, slicesets):
        ss = slicesets["MIT22_081JF10_lec01b.pdf"]
        t2 = compose(ss, "T2")
        s = next(x for x in ss.slices if x.title)
        assert t2[s.slice_id].startswith(s.title.strip()[:10])

    def test_ruleset_version_declared(self):
        assert EMBED_TEXT_RULESET_VERSION


class TestEmbedDeterminism:
    """可复现性：这条有 bug 会表现为「A/B 结论不稳定」，而人会先怀疑模型。"""

    def test_fake_is_order_independent(self):
        e = FakeEmbedder(dimensions=64)
        a = e.embed(["alpha", "beta", "gamma"])
        b = e.embed(["gamma", "alpha", "beta"])
        assert np.allclose(a[0], b[1]) and np.allclose(a[2], b[0])

    def test_fake_is_run_independent(self):
        v1 = FakeEmbedder(dimensions=64).embed(["heat engine"])
        v2 = FakeEmbedder(dimensions=64).embed(["heat engine"])
        assert np.array_equal(v1, v2)

    def test_vectors_are_normalized(self):
        v = FakeEmbedder(dimensions=64).embed(["a b c", "d e"])
        assert np.allclose(np.linalg.norm(v, axis=1), 1.0)

    def test_cache_roundtrip(self, tmp_path):
        cache = EmbeddingCache(tmp_path)
        e = FakeEmbedder(dimensions=64)
        a = cache.get_or_compute(e, ["x", "y"])
        assert cache.misses == 2 and cache.hits == 0
        b = cache.get_or_compute(e, ["x", "y"])
        assert cache.hits == 2
        assert np.allclose(a, b)

    def test_cache_key_includes_dimensions(self, tmp_path):
        cache = EmbeddingCache(tmp_path)
        cache.get_or_compute(FakeEmbedder(dimensions=64), ["x"])
        cache.get_or_compute(FakeEmbedder(dimensions=128), ["x"])
        assert cache.misses == 2, "不同维度必须各自取向量，不能复用"

    def test_search_ties_are_stable(self):
        """并列时按下标升序，保证同一份报告重跑得到同一组数字。"""
        items = [
            IndexedSlice("f", f"s{i}", i, (i,), 100, None, None) for i in range(1, 4)
        ]
        vecs = np.tile(np.array([1.0, 0.0]), (3, 1))
        idx = VectorIndex(items, vecs)
        q = np.array([1.0, 0.0])
        assert [i for i, _ in idx.search(q, 3)] == [0, 1, 2]
        assert idx.search(q, 3) == idx.search(q, 3)


class TestSearchExpansion:
    def _index(self, n=6):
        items = [
            IndexedSlice(
                "f", f"s{i:03d}", i, (i,), 50 if i % 2 else 500,
                f"s{i-1:03d}" if i > 1 else None,
                f"s{i+1:03d}" if i < n else None,
            )
            for i in range(1, n + 1)
        ]
        vecs = np.zeros((n, 2))
        for i in range(n):
            vecs[i] = [1.0 - i * 0.1, np.sqrt(max(0.0, 1 - (1.0 - i * 0.1) ** 2))]
        return VectorIndex(items, vecs)

    def test_none_respects_budget(self):
        got = retrieve(self._index(), np.array([1.0, 0.0]), budget=3, mode="none")
        assert [r.item.slice_id for r in got] == ["s001", "s002", "s003"]
        assert all(r.via == "hit" for r in got)

    def test_always_orders_host_then_prev_then_next(self):
        """§4.3 的取舍顺序：命中片在前，邻居紧随宿主，prev 先于 next。"""
        got = retrieve(self._index(), np.array([1.0, 0.0]), budget=5, mode="always")
        assert [r.item.slice_id for r in got] == ["s001", "s002", "s003", "s004", "s005"]
        assert got[0].via == "hit"
        # s001 无 prev，故其邻居只有 next=s002；s002 已作为邻居出现，不再重复
        assert [r.via for r in got][:2] == ["hit", "next"]

    def test_budget_is_filled_by_backfill(self):
        """规则 5：相邻切片去重后会填不满预算，须用后续命中片回填。

        不回填的话扩展组拿到的片数少于 none 组，比较偏向 none——
        与「扩展必然赢」方向相反，但同样是不公平的比较。
        """
        for mode, tau in (("none", None), ("always", None), ("short_only", 100)):
            got = retrieve(self._index(6), np.array([1.0, 0.0]), budget=5,
                           mode=mode, tau_short=tau)
            assert len(got) == 5, f"{mode} 只拿到 {len(got)} 片，未填满预算"

    def test_dedup_keeps_earlier_position(self):
        got = retrieve(self._index(), np.array([1.0, 0.0]), budget=6, mode="always")
        ids = [r.item.slice_id for r in got]
        assert len(ids) == len(set(ids))

    def test_short_only_requires_explicit_tau(self):
        with pytest.raises(ValueError):
            retrieve(self._index(), np.array([1.0, 0.0]), budget=3, mode="short_only")

    def test_short_only_expands_only_short_slices(self):
        idx = self._index()
        got = retrieve(np.array and idx, np.array([1.0, 0.0]), budget=6,
                       mode="short_only", tau_short=100)
        for r in got:
            if r.via != "hit":
                host = next(x for x in idx.items if x.slice_id == r.host_slice_id)
                assert host.char_count < 100

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            retrieve(self._index(), np.array([1.0, 0.0]), budget=3, mode="magic")

    def test_budget_must_be_positive(self):
        with pytest.raises(ValueError):
            retrieve(self._index(), np.array([1.0, 0.0]), budget=0, mode="none")


class TestMetrics:
    def test_full_hit_vs_page_recall_diverge(self):
        """multipage 上两个指标必须给出不同信号——这是三个指标并列的理由。"""
        r = score_question("Q", ["multipage"], [("f", 1), ("f", 2)], [("f", 1), ("f", 9)], 0.5)
        assert r.full_hit is False
        assert r.page_recall == 0.5
        assert r.first_hit_rank == 1

    def test_full_hit_true_when_all_present(self):
        r = score_question("Q", ["multipage"], [("f", 1), ("f", 2)], [("f", 2), ("f", 1)], 0.5)
        assert r.full_hit and r.page_recall == 1.0

    def test_miss_has_no_rank(self):
        r = score_question("Q", ["synonym"], [("f", 1)], [("f", 7)], 0.3)
        assert r.first_hit_rank is None and r.page_recall == 0.0

    def test_no_answer_flagged(self):
        r = score_question("Q", ["no_answer"], [], [("f", 1)], 0.2)
        assert r.is_no_answer and r.full_hit is False

    def test_multi_tag_counted_in_each_group(self):
        rs = [score_question("Q", ["synonym", "multipage"], [("f", 1)], [("f", 1)], 0.9)]
        s = summarize(rs)
        assert s["synonym"].n == 1 and s["multipage"].n == 1

    def test_zero_ceiling_marked(self):
        rs = [score_question("Q", ["unreadable_page"], [("f", 1)], [], 0.1)]
        s = summarize(rs, frozenset({"unreadable_page"}))
        assert s["unreadable_page"].ceiling_zero
        assert s["unreadable_page"].page_recall == 0.0

    def test_separability_gap(self):
        sep = Separability(with_gold=(0.5, 0.6), no_answer=(0.2, 0.3))
        assert sep.separable and sep.gap == pytest.approx(0.2)
        assert sep.interval == (0.3, 0.5)

    def test_separability_overlap_is_a_result(self):
        sep = Separability(with_gold=(0.3, 0.6), no_answer=(0.2, 0.4))
        assert not sep.separable and sep.gap < 0 and sep.interval is None


class TestEndToEndWithPlantedSignal:
    """用构造向量断言链路真的会命中——恒为 0 的断言测不出链路是否工作。"""

    def _run(self, items, questions, slicesets, mode="none", tau=None):
        from retrieval.compose import compose_all

        texts = compose_all(slicesets, "T0")
        keys = [(it.source_file, it.slice_id) for it in items]
        gold = {}
        for q in questions:
            g = []
            for gp in q["gold_pages"]:
                sid = next(
                    (it.slice_id for it in items
                     if it.source_file == gp["source_file"] and gp["page_index"] in it.page_indices),
                    None,
                )
                if sid:
                    g.append((gp["source_file"], sid))
            gold[q["id"]] = g
        e = PlantedEmbedder(gold, dimensions=128)
        for k in keys:
            e.register_slice(texts[k], k)
        for q in questions:
            e.register_question(q["question"], q["id"])
        index = VectorIndex(items, e.embed([texts[k] for k in keys]))
        qv = e.embed([q["question"] for q in questions])
        out = []
        for q, v in zip(questions, qv):
            got = retrieve(index, v, budget=5, mode=mode, tau_short=tau)
            out.append(
                score_question(
                    q["id"], q["tags"],
                    [(g["source_file"], g["page_index"]) for g in q["gold_pages"]],
                    retrieved_pages(got),
                    next((r.score for r in got if r.score is not None), None),
                )
            )
        return out

    def test_planted_signal_is_actually_retrieved(self, items, questions, slicesets):
        rs = self._run(items, questions, slicesets)
        s = summarize(rs, frozenset({"unreadable_page"}))
        assert s["synonym"].full_hits > 0, "种入的信号没被检索到，链路有问题"
        assert s["synonym"].page_recall > 0.5

    def test_unreadable_page_stays_zero_even_with_planted_signal(self, items, questions, slicesets):
        """结构性的 0：gold 页不在索引里，种什么信号都救不回来。"""
        rs = self._run(items, questions, slicesets)
        s = summarize(rs, frozenset({"unreadable_page"}))
        assert s["unreadable_page"].page_recall == 0.0
        assert s["unreadable_page"].missed == s["unreadable_page"].n

    def test_repeat_runs_identical(self, items, questions, slicesets):
        a = self._run(items, questions, slicesets)
        b = self._run(items, questions, slicesets)
        assert [(r.qid, r.retrieved, r.top_score) for r in a] == [
            (r.qid, r.retrieved, r.top_score) for r in b
        ]

    def test_expansion_changes_results(self, items, questions, slicesets):
        """同预算下扩展会挤掉一些命中片——若毫无变化说明扩展根本没生效。"""
        none = self._run(items, questions, slicesets, "none")
        always = self._run(items, questions, slicesets, "always")
        assert [r.retrieved for r in none] != [r.retrieved for r in always]

    def test_fake_embedder_would_be_a_useless_assertion(self, items, questions, slicesets):
        """把第七条钉成测试：词袋哈希在本语料上恒为 0，故不能用它断言召回。

        这条测试的作用是**防止有人把召回断言改回 FakeEmbedder**——
        那样断言会依然通过，但已经不测任何东西。
        """
        from retrieval.compose import compose_all

        texts = compose_all(slicesets, "T0")
        keys = [(it.source_file, it.slice_id) for it in items]
        e = FakeEmbedder(dimensions=128)
        index = VectorIndex(items, e.embed([texts[k] for k in keys]))
        qv = e.embed([q["question"] for q in questions])
        total = 0.0
        for q, v in zip(questions, qv):
            got = retrieve(index, v, budget=5, mode="none")
            gold = {(g["source_file"], g["page_index"]) for g in q["gold_pages"]}
            if gold:
                total += len(gold & set(retrieved_pages(got))) / len(gold)
        assert total == 0.0, "词袋哈希竟命中了——请复核语料是否变成中英混合"
