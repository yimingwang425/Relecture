"""切片口径测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from parse_service.pdf import parse_pdf
from parse_service.schema import ParseResult

from slicer import slice_document
from slicer.schema import SLICE_RULESET_VERSION, SliceManifest, SliceSet

CORPUS = Path(__file__).resolve().parents[2] / "实验脚本" / "测试集"
SCAN = "MIT15_316SU05_leader_dev.pdf"
GAPPY = "MIT14_129S25_lec02.pdf"
FIGS = "MIT22_081JF10_lec01b.pdf"

pytestmark = pytest.mark.skipif(not CORPUS.exists(), reason=f"语料不存在：{CORPUS}")


@pytest.fixture(scope="module")
def parsed() -> dict[str, ParseResult]:
    return {p.name: parse_pdf(p.read_bytes(), p.name) for p in sorted(CORPUS.glob("*.pdf"))}


@pytest.fixture(scope="module")
def sliced(parsed) -> dict[str, SliceSet]:
    return {name: slice_document(doc) for name, doc in parsed.items()}


class TestSliceUnitIsPage:
    def test_one_slice_per_usable_page(self, parsed, sliced):
        for name, ss in sliced.items():
            usable = [p for p in parsed[name].pages if p.usable]
            assert ss.manifest.slice_count == len(usable)
            assert [s.page_indices for s in ss.slices] == [[p.index] for p in usable]

    def test_ordinals_are_dense(self, sliced):
        for ss in sliced.values():
            assert [s.ordinal for s in ss.slices] == list(range(1, len(ss.slices) + 1))

    def test_slice_ids_unique_and_local(self, sliced):
        for ss in sliced.values():
            ids = [s.slice_id for s in ss.slices]
            assert len(set(ids)) == len(ids)
            assert all(i.startswith("s") and i[1:].isdigit() for i in ids)

    def test_neighbour_chain(self, sliced):
        """过短页不合并，靠邻接链让检索层自行扩展上下文。"""
        for ss in sliced.values():
            if not ss.slices:
                continue
            assert ss.slices[0].prev_slice_id is None
            assert ss.slices[-1].next_slice_id is None
            for a, b in zip(ss.slices, ss.slices[1:]):
                assert a.next_slice_id == b.slice_id
                assert b.prev_slice_id == a.slice_id


class TestPageNumberContinuity:
    """【要求 4】页号序列保持连续，不可用页不能从序列中消失。"""

    def test_skipped_pages_recorded(self, parsed, sliced):
        for name, ss in sliced.items():
            unusable = [p for p in parsed[name].pages if not p.usable]
            assert [s.index for s in ss.manifest.skipped_pages] == [p.index for p in unusable]
            assert [s.kind for s in ss.manifest.skipped_pages] == [p.kind for p in unusable]

    def test_original_page_numbers_never_renumbered(self, sliced):
        """14.129 跳过 p33–p36 后，相邻切片的页号必须是 32 → 37。

        重新编号会让「第 N 页」对不上用户手里的课件，用户按页码翻会翻错页。
        """
        pages = [s.page_indices[0] for s in sliced[GAPPY].slices]
        assert 32 in pages and 37 in pages
        assert 33 not in pages and 36 not in pages
        assert pages[pages.index(32) + 1] == 37

    def test_every_page_accounted_for(self, parsed, sliced):
        """每一页要么产出切片，要么进 skipped_pages，不能凭空消失。"""
        for name, ss in sliced.items():
            covered = {i for s in ss.slices for i in s.page_indices}
            covered |= {s.index for s in ss.manifest.skipped_pages}
            assert covered == {p.index for p in parsed[name].pages}
            assert ss.manifest.page_count == parsed[name].document.page_count

    def test_image_only_carries_actionable_reason(self, sliced):
        """image_only 不是空页，是「内容全在图里」的页。

        F3 要靠这条写出「这部分在第 N 页的图里，我读不了图」，
        而不是 PRD §10 现有的「课件里没提到」——后者是错的。
        """
        skipped = sliced[GAPPY].manifest.skipped_pages
        assert [s.index for s in skipped] == [33, 34, 35, 36]
        for s in skipped:
            assert s.kind == "image_only"
            assert "图" in s.reason
            assert s.image_count > 0
            assert s.image_coverage_union > 0.0

    def test_fully_scanned_document_yields_no_slices(self, sliced):
        ss = sliced[SCAN]
        assert ss.manifest.slice_count == 0
        assert len(ss.manifest.skipped_pages) == 19
        assert {s.kind for s in ss.manifest.skipped_pages} == {"scanned_ocr", "image_only"}


class TestTraceability:
    """【要求 2】溯源到块。"""

    def test_text_span_indexes_into_slice_text(self, sliced):
        for ss in sliced.values():
            for s in ss.slices:
                for unit in s.units:
                    start, end = unit.text_span
                    assert s.text[start:end] == unit.text

    def test_units_cover_slice_text(self, sliced):
        for ss in sliced.values():
            for s in ss.slices:
                assert s.char_count == len(s.text)
                assert sum(len(u.text) for u in s.units) <= s.char_count

    def test_block_ids_traceable_to_parse(self, parsed, sliced):
        for name, ss in sliced.items():
            known = {b.block_id for p in parsed[name].pages for b in p.blocks}
            for s in ss.slices:
                for unit in s.units:
                    assert unit.block_id in known


class TestLayoutPreserved:
    """【要求 3】版面信息不能在切片这一步丢失。"""

    def test_bbox_and_font_carried(self, sliced):
        for ss in sliced.values():
            for s in ss.slices:
                for unit in s.units:
                    assert len(unit.bbox_pt) == 4 and len(unit.bbox_norm) == 4
                    assert unit.font is not None

    def test_image_adjacency_carried(self, sliced):
        """第三步的图注通道要用它；切片若只吐文本，第三步得回头重做。"""
        adjacent = [
            u
            for s in sliced[FIGS].slices
            for u in s.units
            if u.image_adjacency.is_adjacent
        ]
        assert adjacent
        for unit in adjacent:
            assert unit.image_adjacency.gap_lines is not None
            assert unit.image_adjacency.overlap_ratio is not None
            assert unit.image_adjacency.nearest_image_id is not None

    def test_images_carried_without_binary(self, sliced):
        found = False
        for ss in sliced.values():
            for s in ss.slices:
                for img in s.images:
                    found = True
                    assert img.coverage >= 0.0 and len(img.bbox_norm) == 4
                    assert not hasattr(img, "data")
        assert found

    def test_title_picked_structurally(self, sliced):
        """size_rank 最高、并列取最靠上。无阈值。"""
        titles = {s.page_indices[0]: s.title for s in sliced[FIGS].slices}
        assert titles[2] == "WELL KNOWN ISSUES"
        assert titles[7] == "OUTLINE"
        assert titles[21] == "REAL HEAT ENGINES"

    def test_title_block_id_is_a_unit(self, sliced):
        for ss in sliced.values():
            for s in ss.slices:
                if s.title_block_id:
                    assert s.title_block_id in {u.block_id for u in s.units}


class TestPageSignalsPassthrough:
    """【调整 2】页级 evidence 原样透传，不做判定、不生成新标签。"""

    def test_signals_match_parse_evidence(self, parsed, sliced):
        for name, ss in sliced.items():
            evid = {p.index: p.evidence for p in parsed[name].pages}
            for s in ss.slices:
                assert len(s.page_signals) == len(s.page_indices)
                for sig in s.page_signals:
                    e = evid[sig.page_index]
                    assert sig.image_coverage_union == e.image_coverage_union
                    assert sig.text_char_count == e.text_char_count

    def test_no_derived_labels_emitted(self):
        """切片层不得引入 role / is_caption 之类的判定字段。

        图注判定属第三步；在这里贴无依据的标签，第三步真做时还得先清掉。
        """
        from slicer.schema import Slice, SliceUnit

        forbidden = {"role", "is_caption", "is_title", "is_body", "kind", "label"}
        assert not (forbidden & set(SliceUnit.model_fields))
        assert not (forbidden & set(Slice.model_fields))

    def test_figure_heavy_page_is_visible_in_signals(self, sliced):
        """22.081J p21「REAL HEAT ENGINES」：文本少但实质内容在图里。

        切片不为此下任何结论，只把两个量带下去供 2b 之后分组统计。
        """
        s = next(x for x in sliced[FIGS].slices if x.page_indices == [21])
        sig = s.page_signals[0]
        assert sig.image_coverage_union > 0.3
        assert sig.text_char_count < 300


class TestManifestAndCompliance:
    def test_no_document_primary_key(self):
        """PRD §12.1：按内容哈希做主键等于跨用户共享存储。主键归持久化层。"""
        fields = set(SliceManifest.model_fields)
        assert "doc_id" not in fields and "document_id" not in fields
        assert "content_sha256" in fields
        from slicer.schema import Slice

        assert "doc_id" not in set(Slice.model_fields)

    def test_manifest_ties_to_parse_result(self, parsed, sliced):
        for name, ss in sliced.items():
            assert ss.manifest.content_sha256 == parsed[name].document.content_sha256
            assert ss.manifest.source_schema_version == parsed[name].schema_version
            assert ss.manifest.slice_ruleset_version == SLICE_RULESET_VERSION

    def test_split_threshold_defaults_to_none(self, sliced):
        """超长页拆分阈值取决于 2b 选定的 embedding 供应商，本层不写死。"""
        for ss in sliced.values():
            assert ss.manifest.split_threshold_chars is None

    def test_split_threshold_is_recorded_when_injected(self, parsed):
        ss = slice_document(parsed[FIGS], split_threshold_chars=1500)
        assert ss.manifest.split_threshold_chars == 1500

    def test_rejects_invalid_threshold(self, parsed):
        with pytest.raises(ValueError):
            slice_document(parsed[FIGS], split_threshold_chars=0)

    def test_roundtrip(self, sliced):
        import json

        for ss in sliced.values():
            assert SliceSet.model_validate(json.loads(ss.model_dump_json())) == ss

    def test_no_page_exceeds_2000_chars(self, sliced):
        """页做切片单位的实测前提：幻灯片天然有界。

        这条断言在遇到讲义型 PDF 时会红——那正是该启用超长页拆分的信号。
        """
        for ss in sliced.values():
            for s in ss.slices:
                assert s.char_count <= 2000
