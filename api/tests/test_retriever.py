"""阶段 5 · `SupabaseRetriever` —— 检索从替身换成真库。

## ⚠️ 这一组**不出任何召回结论**

假嵌入器不是语义模型，假 PostgREST 的排序是我写死的。
「A 课提问召回的切片 course_id 全是 A」「同课两份讲义能同时召回」
这两条**只在真库上成立才算数**，在 `supabase/verify_retrieval_prod.py`。

这里验的是**接线**：键配得对不对、原文补得对不对、口径版本传得对不对、
清单带不带得下来、扩展模式变了会不会吵。这些在本地验得了，
而它们错了的话真库上会以「召回不准」的样子出现——两种原因长得一样，
所以分开验（同 F34：两种证据不互相顶替）。
"""

from __future__ import annotations

from typing import Any

import pytest

from api.retriever import SupabaseRetriever, label_documents

COURSE = "cccccccc-3333-4333-8333-cccccccccccc"
DOC_A = "dddddddd-1111-4111-8111-dddddddddddd"
DOC_B = "dddddddd-2222-4222-8222-dddddddddddd"


class FakeEmbedder:
    """确定性假嵌入器。**它不假装自己是语义模型**——见文件头。"""

    name = "fake"
    dimensions = 1024

    def __init__(self, dims: int = 1024) -> None:
        self.dimensions = dims
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            v = [0.0] * self.dimensions
            v[len(t) % self.dimensions] = 1.0
            out.append(v)
        return out


class FakeStore:
    """假 `SupabaseClient`。**记下每次调用的参数**，供「传对了没有」那些断言。"""

    def __init__(self, documents: list[dict[str, Any]], slices: list[dict[str, Any]],
                 matches: list[dict[str, Any]]) -> None:
        self._documents = documents
        self._slices = slices
        self._matches = matches
        self.match_calls: list[dict[str, Any]] = []
        self.slice_calls: list[list[str]] = []

    def documents_in_course(self, token: str, course_id: str) -> list[dict[str, Any]]:
        return [d for d in self._documents if d.get("_course", COURSE) == course_id]

    def match_slices(self, **kw: Any) -> list[dict[str, Any]]:
        self.match_calls.append(kw)
        return self._matches[: kw["limit"]]

    def slices_in_documents(self, token: str, document_ids: list[str]) -> list[dict[str, Any]]:
        self.slice_calls.append(list(document_ids))
        return [s for s in self._slices if s["document_id"] in set(document_ids)]


def doc(id_: str, filename: str, **over: Any) -> dict[str, Any]:
    base = {"id": id_, "filename": filename, "verdict": "usable",
            "unusable_pages": [], "page_count": 3}
    base.update(over)
    return base


def sl(document_id: str, slice_id: str, text: str, pages=(1,), title=None) -> dict[str, Any]:
    return {"document_id": document_id, "slice_id": slice_id, "ordinal": 1,
            "page_indices": list(pages), "title": title, "text": text,
            "char_count": len(text)}


def build(**over: Any) -> tuple[SupabaseRetriever, FakeStore]:
    documents = over.pop("documents", [doc(DOC_A, "能源.pdf"), doc(DOC_B, "规划.pdf")])
    slices = over.pop("slices", [
        sl(DOC_A, "s001", "能源第一片", (1, 2), "能源概览"),
        sl(DOC_B, "s001", "规划第一片", (5,), "规划传统"),
    ])
    matches = over.pop("matches", [
        {"document_id": DOC_A, "slice_id": "s001", "similarity": 0.91},
        {"document_id": DOC_B, "slice_id": "s001", "similarity": 0.42},
    ])
    store = FakeStore(documents, slices, matches)
    r = SupabaseRetriever(store=store, embedder=over.pop("embedder", None) or FakeEmbedder(),
                          token="tok", course_id=COURSE, **over)
    return r, store


class TestKeyIsTwoParts:
    """`slice_id` 是**文档内局部 id**，配对必须用 `(document_id, slice_id)` 两段。"""

    def test_same_slice_id_in_two_documents_is_not_confused(self):
        """两份讲义都有 `s001` —— 这正是真库上观测到的形态（2026-09-12）。

        用一段键的话，两片会取到同一份原文，而**输出看起来完全正常**：
        学生拿到的是一段真实存在的课件文字，只是出处是另一份讲义。
        """
        r, _ = build()
        pack = r.fetch("能源是什么")
        assert [s.slice_id for s in pack.slices] == ["s001", "s001"]
        assert [s.text for s in pack.slices] == ["能源第一片", "规划第一片"]
        assert [s.source_file for s in pack.slices] == ["能源.pdf", "规划.pdf"]

    def test_order_follows_similarity_not_the_slices_query(self):
        """顺序由 RPC 给（已按相似度降序），**不是**按 `slices` 查回来的顺序。

        `slices_in_documents` 是按 `document_id in (...)` 批量取的，
        它的返回顺序由数据库决定。少了这条断言，「顺序错了」只会表现为
        prompt 里片段的排列变了——不报错，而模型最重视最前面那几段。
        """
        r, _ = build(matches=[
            {"document_id": DOC_B, "slice_id": "s001", "similarity": 0.99},
            {"document_id": DOC_A, "slice_id": "s001", "similarity": 0.10},
        ])
        pack = r.fetch("规划是什么")
        assert [s.source_file for s in pack.slices] == ["规划.pdf", "能源.pdf"]
        assert [s.score for s in pack.slices] == [0.99, 0.10]

    def test_one_request_for_all_slices_not_one_per_hit(self):
        """批量取一次，不是每个命中一次往返。

        五次串行往返会直接加到用户等待的延迟上，而这一步在生成之前。
        """
        r, store = build()
        r.fetch("问题")
        assert len(store.slice_calls) == 1
        assert set(store.slice_calls[0]) == {DOC_A, DOC_B}


class TestSourceFileIsAnIdentity:
    """`(source_file, slice_id)` 是 `ContextPack.signature()` 的身份，必须唯一。"""

    def test_same_filename_gets_disambiguated(self):
        """同一门课两份同名文件（改了一版重新上传，文件名往往不变）。

        唯一索引是 `(owner, course_id, content_sha256)`，**同名不同内容可以共存**。
        不加后缀的话两个不同切片撞成同一个身份，签名从此分不开它们——
        而签名的全部用途就是核验「两组拿到的是不是同一份上下文」（口径 X02）。
        """
        r, _ = build(documents=[doc(DOC_A, "讲义.pdf"), doc(DOC_B, "讲义.pdf")])
        pack = r.fetch("问题")
        labels = [s.source_file for s in pack.slices]
        assert len(set(labels)) == 2, labels
        assert all(x.startswith("讲义.pdf") for x in labels)

    def test_distinct_filenames_get_no_suffix(self):
        """允许控制：**不同名时不加后缀。**

        少了它，一个「一律加 id 后缀」的实现也能让上一条全绿，
        而那一截会原样进 prompt 给学生看（`build_user_turn` 拿它当出处标签）。
        """
        r, _ = build()
        assert {s.source_file for s in r.fetch("问题").slices} == {"能源.pdf", "规划.pdf"}

    def test_signature_separates_same_named_documents(self):
        """把上面两条接到它们真正保护的那个东西上。"""
        same, _ = build(documents=[doc(DOC_A, "讲义.pdf"), doc(DOC_B, "讲义.pdf")])
        diff, _ = build()
        assert same.fetch("问题").signature() != diff.fetch("问题").signature()


class TestManifestComesDown:
    """口径 X07：「哪些页读不出来」要一路带到检索层。"""

    def test_unusable_pages_become_skipped_refs_with_reasons(self):
        r, _ = build(documents=[
            doc(DOC_A, "能源.pdf", unusable_pages=[{"index": 7, "kind": "image_only"}]),
            doc(DOC_B, "规划.pdf"),
        ])
        pack = r.fetch("问题")
        assert len(pack.skipped_pages) == 1
        p = pack.skipped_pages[0]
        assert (p.source_file, p.index, p.kind) == ("能源.pdf", 7, "image_only")
        # 文案从 slicer.core.SKIP_REASONS 反查，**不是在这里写第二份**。
        from slicer.core import SKIP_REASONS
        assert p.reason == SKIP_REASONS["image_only"]

    def test_it_reaches_the_system_prompt(self):
        """不止「结构里有」，而是**模型真的看得到**。

        少了这一条，`skipped_pages` 可以完整地躺在 ContextPack 里而
        一个字都没进 prompt —— 那时 PRD §6.2 那个「看起来成功了」的失败照旧。
        """
        from explain.prompt import build_system

        r, _ = build(documents=[
            doc(DOC_A, "能源.pdf", unusable_pages=[{"index": 7, "kind": "image_only"}]),
            doc(DOC_B, "规划.pdf"),
        ])
        system = build_system("风格", r.fetch("问题"), "验收课程")
        assert "第 7" in system
        assert "image_only" in system

    def test_unusable_documents_are_listed(self):
        r, _ = build(documents=[doc(DOC_A, "扫描件.pdf", verdict="unusable"),
                                doc(DOC_B, "规划.pdf")])
        assert r.fetch("问题").unusable_files == ("扫描件.pdf",)

    def test_clean_course_says_so(self):
        """允许控制：没有跳过页时清单里必须是「没有无法读取的页」。"""
        from explain.prompt import build_system

        r, _ = build()
        assert r.fetch("问题").skipped_pages == ()
        assert "没有无法读取的页" in build_system("风格", r.fetch("问题"), "验收课程")


class TestRulesetsAreNotRewritten:
    """model / ruleset 传错的表现是**什么都召不回**，和「这门课没内容」一模一样。"""

    def test_model_id_is_the_same_string_ingest_writes(self):
        """检索按 `model` 过滤，摄入按 `model` 写入。**两边必须是同一个值。**

        不同源的话：摄入写 `text-embedding-3-large@1024`，检索找别的，
        `match_slices` 返回 0 行 —— 而 0 行看起来就是「这门课没有相关内容」。
        所以这里断的是「检索用的那个字符串 == 摄入用的那个函数的返回值」。
        """
        from api.ingest import EMBED_MODEL_ID as ingest_model_id

        r, store = build()
        r.fetch("问题")
        assert store.match_calls[0]["model"] == ingest_model_id()

    def test_ruleset_is_the_compose_version(self):
        from retrieval.compose import EMBED_TEXT_RULESET_VERSION

        r, store = build()
        r.fetch("问题")
        assert store.match_calls[0]["ruleset"] == EMBED_TEXT_RULESET_VERSION

    def test_budget_is_passed_as_limit(self):
        r, store = build(budget=3)
        r.fetch("问题")
        assert store.match_calls[0]["limit"] == 3

    def test_course_id_and_token_are_forwarded(self):
        """RLS 由数据库执行，前提是**用户自己的 JWT 真的被转发了**。"""
        r, store = build()
        r.fetch("问题")
        assert store.match_calls[0]["course_id"] == COURSE
        assert store.match_calls[0]["token"] == "tok"

    def test_meta_carries_what_the_event_needs(self):
        """§13.0 的 `retrieval_executed` 要的字段必须在 meta 里。"""
        from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL

        r, _ = build()
        meta = r.fetch("问题").meta
        assert meta["backend"] == "supabase"
        assert meta["embed_model"] == SELECTED_MODEL
        assert meta["embed_dimensions"] == SELECTED_DIMENSIONS
        assert meta["course_id"] == COURSE
        assert meta["retrieval_latency_ms"] >= 0


class TestDimensionIsCheckedAtBothEnds:
    def test_actual_vector_length_is_asserted(self):
        """断**实际返回**的长度，不是 `embedder.dimensions`（口径 F16）。

        这个嵌入器**声称 1024 而返回 512**——拿它跟自己比是不会失败的。
        """
        class Liar(FakeEmbedder):
            dimensions = 1024

            def embed(self, texts):
                return [[0.0] * 512 for _ in texts]

        r, _ = build(embedder=Liar())
        with pytest.raises(ValueError, match="512"):
            r.fetch("问题")

    def test_right_dimension_passes(self):
        """允许控制：1024 维必须通过。少了它，「什么都拒」也能让上一条全绿。"""
        r, _ = build()
        assert len(r.fetch("问题").slices) == 2


class TestExpansionModeMustShout:
    def test_non_none_mode_raises_at_construction(self, monkeypatch):
        """`SELECTED_MODE` 改成别的 → **当场抛**，不静默按 none 跑。

        静默继续的表现是「换了扩展模式但召回没变」，
        最容易被读成「扩展模式没用」——一个会改变结论的静默失败。
        """
        import retrieval.selection as selection

        monkeypatch.setattr(selection, "SELECTED_MODE", "neighbor")
        with pytest.raises(RuntimeError, match="SELECTED_MODE"):
            build()

    def test_none_mode_constructs(self):
        """允许控制：当前选型（none）必须能构造。"""
        from retrieval.selection import SELECTED_MODE

        assert SELECTED_MODE == "none"
        build()


class TestInconsistentRowsAreVisible:
    def test_vector_without_slice_is_recorded_not_swallowed(self):
        """向量在、切片不在 → 记进 meta，**不静默跳过**。

        外键本应挡住这种状态。真发生了的话它意味着两张表不一致，
        而静默跳过的表现是「召回少了一片」——没有任何东西会红。
        """
        r, _ = build(slices=[sl(DOC_A, "s001", "能源第一片")])
        pack = r.fetch("问题")
        assert len(pack.slices) == 1
        assert pack.meta["slices_missing_text"] == [f"{DOC_B[:8]}#s001"]

    def test_consistent_rows_report_nothing(self):
        """允许控制：一致时那个列表是空的。"""
        r, _ = build()
        assert r.fetch("问题").meta["slices_missing_text"] == []


class TestLabelDocuments:
    def test_pure_function_behaviour(self):
        assert label_documents([doc(DOC_A, "a.pdf"), doc(DOC_B, "b.pdf")]) == {
            DOC_A: "a.pdf", DOC_B: "b.pdf"}
        out = label_documents([doc(DOC_A, "x.pdf"), doc(DOC_B, "x.pdf")])
        assert out[DOC_A] != out[DOC_B]
        assert all(v.startswith("x.pdf#") for v in out.values())

    def test_suffix_grows_until_it_actually_separates(self):
        """**前缀不是写死 8 位。**

        `DOC_A` / `DOC_B` 的前 8 位都是 `dddddddd` —— 写死 8 位时两个标签相撞，
        而撞了不会报错。这一条钉着「加长到真的分得开」这个行为本身。
        """
        assert DOC_A[:8] == DOC_B[:8], "测试数据前提变了，这一条失去分辨力"
        out = label_documents([doc(DOC_A, "x.pdf"), doc(DOC_B, "x.pdf")])
        assert len(set(out.values())) == 2

    def test_three_way_collision(self):
        ids = ["aaaa-1", "aaaa-2", "aaaa-3"]
        out = label_documents([doc(i, "y.pdf") for i in ids])
        assert len(set(out.values())) == 3


class TestHitDocumentsAreTheIdentity:
    """**`source_file` 不能当身份用于「这一片属于哪门课」的判据。**

    同一份文件可以挂在不同课程下（唯一索引是 `(owner, course_id, content_sha256)`，
    口径 F47 明确要支持「同一份导论讲义用于两个学期」）。真库上就是这个形态：
    `MIT11_201F07_slides2.pdf` 同时在课程 A 与课程 B 里。

    于是「召回的切片来自课程 A 的文件」这条判据**在真的召回了课程 B 时同样成立**
    ——两边文件名一模一样。那不是判据（同 F48）。
    2026-09-12 的验收判据就是按文件名写的，这个洞是真的。
    """

    def test_meta_carries_document_ids_aligned_with_slices(self):
        r, _ = build()
        pack = r.fetch("问题")
        hits = pack.meta["hit_documents"]
        assert len(hits) == len(pack.slices)
        assert [h["document_id"] for h in hits] == [DOC_A, DOC_B]
        assert [h["slice_id"] for h in hits] == [s.slice_id for s in pack.slices]

    def test_filename_alone_cannot_separate_two_courses(self):
        """**钉住那个洞本身。**

        两门课各有一份同名文件时，按 `source_file` 断言分不开；
        按 `document_id` 分得开。少了这一条，有人会把验收判据改回文件名
        而上一条仍然绿（它只说 meta 里有这一项，没说为什么需要它）。
        """
        SAME = "MIT11_201F07_slides2.pdf"
        course_a, _ = build(documents=[doc(DOC_A, SAME)],
                            slices=[sl(DOC_A, "s001", "A 的内容")],
                            matches=[{"document_id": DOC_A, "slice_id": "s001",
                                      "similarity": 0.9}])
        course_b, _ = build(documents=[doc(DOC_B, SAME)],
                            slices=[sl(DOC_B, "s001", "B 的内容")],
                            matches=[{"document_id": DOC_B, "slice_id": "s001",
                                      "similarity": 0.9}])
        pa, pb = course_a.fetch("问题"), course_b.fetch("问题")

        # 文件名这一端：**完全相同** —— 所以它区分不了两门课。
        assert {s.source_file for s in pa.slices} == {s.source_file for s in pb.slices}
        # document_id 这一端：分得开。
        assert (pa.meta["hit_documents"][0]["document_id"]
                != pb.meta["hit_documents"][0]["document_id"])

    def test_course_document_ids_is_the_other_end(self):
        """`course_document_ids` 是这门课有哪些文档 —— 判据的另一端。"""
        r, _ = build()
        assert set(r.fetch("问题").meta["course_document_ids"]) == {DOC_A, DOC_B}

    def test_missing_text_slices_do_not_desync_the_alignment(self):
        """向量在、切片不在时，`hit_documents` **只记真的进了 pack 的那些**。

        少了这一条，被跳过的那一片会让两个列表错开一格，而
        `retrieval_executed` 的 `document_id` 从此逐项配错——
        **配错的身份比没有身份更糟，它看起来是对的。**
        """
        r, _ = build(slices=[sl(DOC_B, "s001", "只有 B 的原文")])
        pack = r.fetch("问题")
        assert len(pack.slices) == 1
        assert len(pack.meta["hit_documents"]) == 1
        assert pack.meta["hit_documents"][0]["document_id"] == DOC_B
        assert pack.meta["slices_missing_text"] == [f"{DOC_A[:8]}#s001"]
