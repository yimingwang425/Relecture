"""摄入的三个新步骤（2026-10-07）：图片页转写（F100）、定位语（F99）、词条（F97）。

**真解析、真切片、真渲染**——喂的是仓库里的真 PDF；只有模型调用是假的
（它们要出境、要钱）。`MIT15_316SU05_leader_dev.pdf` 是测试集里那份整份扫描件，
`MIT14_129S25_lec02.pdf` 有一段 `image_only` 页（p33–36）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api.indexing import INDEX_RULESET, LEGACY_RULESET, embedding_text
from api.ingest import UnusableDocument, ingest_document

from conftest import USER_A
from test_ingest import COURSE, DECK, FakeEmbedder, StoreSpy

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "实验脚本" / "测试集"
SCANNED = CORPUS / "MIT15_316SU05_leader_dev.pdf"
FIGURES = CORPUS / "MIT14_129S25_lec02.pdf"


class FakeVision:
    """照 `explain.rag.VisionTranscriber.transcribe_many` 的签名写。记下收到了哪几页、是不是 PNG。"""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.pages: list[int] = []
        self.png_ok: list[bool] = []

    def transcribe_many(self, filename, pages):
        from explain.rag import VisionResult

        out = []
        for idx, png in pages:
            self.pages.append(idx)
            self.png_ok.append(png[:8] == b"\x89PNG\r\n\x1a\n")
            if self.fail:
                out.append(VisionResult(idx, "", "error", None, 1.0, "down"))
            else:
                out.append(VisionResult(idx, f"第 {idx} 页转写：leadership development 领导力", "model",
                                        0.004, 1.0))
        return out


class FakeContextualizer:
    def __init__(self) -> None:
        self.seen: list[tuple[str, list[int], str | None, str]] = []

    def contextualize(self, filename, slices):
        from explain.rag import ContextResult

        self.seen = list(slices)
        return ContextResult({sid: f"定位：{sid} 讲热机（heat engine）" for sid, *_ in slices},
                             0.01, 1, 0)


def ingest(path: Path, **kw):
    store = StoreSpy()
    emb = FakeEmbedder()
    res = ingest_document(data=path.read_bytes(), filename=path.name, owner=USER_A,
                          course_id=COURSE, token="tok", store=store, embedder=emb, **kw)
    return res, store, emb


@pytest.fixture(scope="module")
def scanned_run():
    """同一个场景（扫描件 + 转写）只跑一次：解析 + 渲染一份 19 页扫描件约 4 秒，
    几条断言各跑一遍就是半分钟。**共享的是输入与产出，不是断言**——每条仍各断各的。"""
    vis = FakeVision()
    res, store, emb = ingest(SCANNED, vision=vis)
    return vis, res, store, emb


@pytest.fixture(scope="module")
def figures_run():
    vis = FakeVision()
    res, store, _ = ingest(FIGURES, vision=vis)
    return vis, res, store


class TestVisionRescuesScannedDecks:
    def test_reverse_control_without_vision_it_is_still_refused(self):
        """**反向验证**：不转写时这份扫描件照旧被拒——下面「救回来了」是转写带来的。"""
        with pytest.raises(UnusableDocument):
            ingest(SCANNED)

    def test_with_vision_it_is_ingested(self, scanned_run):
        vis, res, store, _ = scanned_run
        assert res.vision_page_count > 0
        assert all(vis.png_ok), "送去转写的不是 PNG"
        assert [s["slice_id"] for s in store.slices] == [f"v{p:03d}" for p in vis.pages]
        assert all("图像转写" in s["title"] for s in store.slices)
        assert len(store.embeddings) == len(store.slices) == len(store.lexicon)

    def test_parser_verdict_is_not_rewritten(self, scanned_run):
        """转写救回不等于解析器读出了它——`verdict` 照旧是 unusable（不让字段说谎，F69）。"""
        _, _, store, _ = scanned_run
        assert store.document["verdict"] == "unusable"
        assert store.document["vision_pages"] and all("index" in v for v in store.document["vision_pages"])

    def test_failed_vision_keeps_refusing(self):
        with pytest.raises(UnusableDocument):
            ingest(SCANNED, vision=FakeVision(fail=True))

    def test_cap_limits_pages_and_says_how_many_were_left(self, monkeypatch):
        import api.ingest as I

        monkeypatch.setattr(I, "VISION_MAX_PAGES", 2)
        vis = FakeVision()
        res, _, _ = ingest(SCANNED, vision=vis)
        assert len(vis.pages) == 2
        assert res.vision_skipped_over_cap > 0

    def test_cost_is_summed(self, scanned_run):
        vis, res, _, _ = scanned_run
        assert res.llm_cost_usd == pytest.approx(0.004 * len(vis.pages))


class TestVisionOnFigurePages:
    def test_only_image_pages_are_sent(self, figures_run):
        vis, _, store = figures_run
        assert vis.pages, "这份讲义有 image_only 页，却一页都没送去转写"
        assert set(vis.pages) <= {p["index"] for p in store.document["unusable_pages"]}
        assert any(s["slice_id"].startswith("v") for s in store.slices)
        assert any(s["slice_id"].startswith("s") for s in store.slices), "文字页的切片不见了"

    def test_vision_slice_ids_never_collide_with_slicer_ids(self, figures_run):
        _, _, store = figures_run
        ids = [s["slice_id"] for s in store.slices]
        assert len(ids) == len(set(ids))


class TestContextualRetrieval:
    def test_context_is_prefixed_to_the_embedding_text(self):
        ctx = FakeContextualizer()
        _, store, emb = ingest(DECK, contextualizer=ctx)
        first = emb.calls[0][0]
        assert first.startswith("定位：s001"), first[:60]

    def test_reverse_control_without_contextualizer_no_prefix(self):
        _, _, emb = ingest(DECK)
        assert not emb.calls[0][0].startswith("定位：")

    def test_lexicon_carries_the_context_and_its_chinese_terms(self):
        """定位语里的中文括注要进词条——中文提问在英文课件上唯一能字面命中的东西。"""
        _, store, _ = ingest(DECK, contextualizer=FakeContextualizer())
        row = store.lexicon[0]
        assert row["context"].startswith("定位：")
        assert "热机" in row["lexical_text"].split()

    def test_contextualizer_sees_vision_slices_too(self):
        ctx = FakeContextualizer()
        ingest(SCANNED, vision=FakeVision(), contextualizer=ctx)
        assert ctx.seen and all(sid.startswith("v") for sid, *_ in ctx.seen)


class TestOneRulesetThreePlaces:
    def test_document_embeddings_and_lexicon_share_the_string(self):
        _, store, _ = ingest(DECK, contextualizer=FakeContextualizer())
        assert store.document["index_ruleset"] == INDEX_RULESET
        assert {e["embed_ruleset"] for e in store.embeddings} == {INDEX_RULESET}
        assert {x["ruleset"] for x in store.lexicon} == {INDEX_RULESET}

    def test_legacy_ruleset_is_the_migration_default(self):
        """`LEGACY_RULESET` 与迁移里 `index_ruleset` 的默认值**从两个来源各取一次再比**。

        一边是 Python 常量，另一边从迁移原文里解析——同源的话这条恒真（F16）。
        """
        sql = (REPO / "supabase" / "migrations" / "20261007100000_f2_hybrid_lexicon.sql").read_text()
        m = re.search(r"add column if not exists index_ruleset text not null default '([^']+)'", sql)
        assert m, "迁移里找不到 index_ruleset 的默认值"
        assert m.group(1) == LEGACY_RULESET

    def test_new_ruleset_differs_from_legacy(self):
        assert INDEX_RULESET != LEGACY_RULESET and INDEX_RULESET.startswith(LEGACY_RULESET + "+")

    def test_embedding_text_without_context_is_the_composed_text(self):
        assert embedding_text(None, "abc") == "abc" and embedding_text("", "abc") == "abc"
        assert embedding_text("ctx", "abc") == "ctx\n\nabc"


def test_duplicate_is_caught_before_paying_for_vision():
    """查重排在转写之前：重复上传的那一份不该花转写的钱。"""
    from api.supabase import DuplicateDocument

    vis = FakeVision()
    with pytest.raises(DuplicateDocument):
        ingest(SCANNED, vision=vis, find_existing=lambda sha: {"id": "x"})
    assert vis.pages == []


def test_restricted_pages_are_never_sent_for_vision():
    """受限页**送出去之前**就排除：转写 = 页面图像出境，事后丢掉结果撤不回那次出境。"""
    vis = FakeVision()
    ingest(SCANNED, vision=vis, restricted_pages=frozenset({1, 2, 3}))
    assert vis.pages and not ({1, 2, 3} & set(vis.pages))
