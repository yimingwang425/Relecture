"""真实语料回归：实验脚本/测试集/ 的 4 份 OCW 课件。

golden file **只对 kind 做回归**，不含 coverage —— coverage 是浮点几何量，
换算法就会在末位变动（见 README「网格近似 vs 精确并集」），
写进 golden 会让每次几何改动都红一片，掩盖真正的判定漂移。

刷新 golden：``python -m scripts.probe_corpus --write-golden``
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parse_service.pdf import parse_pdf
from parse_service.schema import DocumentMeta
from parse_service.scanned import INVISIBLE_TEXT_RATIO_MIN, SCAN_RULESET_VERSION

CORPUS = Path(__file__).resolve().parents[2] / "实验脚本" / "测试集"
GOLDEN = Path(__file__).resolve().parent / "golden"

pytestmark = pytest.mark.skipif(
    not CORPUS.exists(), reason=f"语料不存在：{CORPUS}"
)


def corpus_files() -> list[Path]:
    return sorted(CORPUS.glob("*.pdf"))


@pytest.fixture(scope="module")
def parsed() -> dict[str, object]:
    return {p.name: parse_pdf(p.read_bytes(), p.name) for p in corpus_files()}


def test_corpus_present():
    assert len(corpus_files()) == 4


@pytest.mark.parametrize("path", corpus_files(), ids=lambda p: p.stem)
def test_matches_golden(path: Path):
    golden_path = GOLDEN / f"{path.stem}.json"
    assert golden_path.exists(), f"缺 golden：{golden_path}"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    assert golden["scan_ruleset_version"] == SCAN_RULESET_VERSION, (
        "判据口径版本变了，golden 必须重新生成并复核差异，不能直接覆盖"
    )
    result = parse_pdf(path.read_bytes(), path.name)
    assert [p.kind for p in result.pages] == golden["kinds"]
    assert result.document.verdict == golden["verdict"]
    assert result.document.usable_page_count == golden["usable_page_count"]


class TestScannedDetection:
    """PRD §6.2 / §10 的核心断言：15.316 必须被拦下，其余三份不能被误伤。"""

    SCAN = "MIT15_316SU05_leader_dev.pdf"

    def test_15316_is_rejected(self, parsed):
        doc = parsed[self.SCAN]
        assert doc.document.verdict == "unusable"
        assert doc.document.usable_page_count == 0
        kinds = [p.kind for p in doc.pages]
        assert kinds.count("scanned_ocr") == 17
        assert kinds.count("image_only") == 2

    def test_15316_ocr_pages_are_fully_invisible(self, parsed):
        """OCR 层的定义性特征：文字压根不着墨。"""
        for page in parsed[self.SCAN].pages:
            if page.kind == "scanned_ocr":
                assert page.evidence.invisible_text_ratio == pytest.approx(1.0)

    def test_no_false_positive_in_digital_courseware(self, parsed):
        """正常课件里不能有任何一页被判为扫描件。"""
        for name, doc in parsed.items():
            if name == self.SCAN:
                continue
            offenders = [p.index for p in doc.pages if p.kind == "scanned_ocr"]
            assert offenders == [], f"{name} 误判为扫描件的页：{offenders}"

    def test_invisible_ratio_margin_is_wide(self, parsed):
        """判据的余量：非扫描页的不着墨占比应远低于阈值。

        实测非扫描侧最大 0.066，阈值 0.80——余量 12 倍。
        这条断言在余量被压缩时会红，提醒重新评估判据。
        """
        worst = 0.0
        for name, doc in parsed.items():
            for page in doc.pages:
                if page.kind != "scanned_ocr":
                    worst = max(worst, page.evidence.invisible_text_ratio)
        assert worst < 0.10
        assert worst < INVISIBLE_TEXT_RATIO_MIN / 2

    def test_coverage_alone_cannot_separate(self, parsed):
        """把「铺满版面不可判别」钉成回归。

        非扫描页的最大覆盖率高于扫描页的最大覆盖率——两类区间完全重叠。
        任何把 coverage 当判别器的改动都会让这条断言红。
        """
        scan_cov = [
            p.evidence.image_coverage_union
            for p in parsed[self.SCAN].pages
            if p.kind == "scanned_ocr"
        ]
        other_cov = [
            p.evidence.image_coverage_union
            for name, doc in parsed.items()
            if name != self.SCAN
            for p in doc.pages
        ]
        assert max(other_cov) > max(scan_cov)
        assert min(other_cov) < min(scan_cov)

    def test_embedded_font_count_alone_cannot_separate(self, parsed):
        """把「内嵌字体数不可判别」钉成回归：非扫描件里也大量存在 0 内嵌字体的页。"""
        zero_in_digital = sum(
            1
            for name, doc in parsed.items()
            if name != self.SCAN
            for p in doc.pages
            if p.evidence.embedded_font_count == 0
        )
        assert zero_in_digital == 26


class TestLayoutPreserved:
    """硬要求：版面信息不能丢。丢了会导致第三步（图注通道）整个返工。"""

    FIG = "MIT22_081JF10_lec01b.pdf"

    def test_every_block_carries_layout(self, parsed):
        for doc in parsed.values():
            for page in doc.pages:
                for block in page.blocks:
                    assert len(block.bbox_pt) == 4
                    assert len(block.bbox_norm) == 4
                    assert block.page_index == page.index
                    assert block.origin.kind == "pdf_textblock"
                    assert block.origin.spans, "span 级字体信息不能为空"
                    assert block.font.resolved is True

    def test_bbox_norm_within_page(self, parsed):
        for doc in parsed.values():
            for page in doc.pages:
                for block in page.blocks:
                    x0, y0, x1, y1 = block.bbox_norm
                    assert -0.01 <= x0 <= 1.01 and -0.01 <= y0 <= 1.01
                    assert x0 <= x1 and y0 <= y1

    def test_font_signals_present(self, parsed):
        """字号、加粗、字体名——图注识别要靠它们。"""
        doc = parsed[self.FIG]
        sizes = {b.font.size_pt for p in doc.pages for b in p.blocks}
        assert len(sizes) > 3, "字号应当有分布，全同说明汇总把信号压平了"
        assert any(b.font.bold for p in doc.pages for b in p.blocks)
        assert all(
            b.font.size_rank is not None and 0.0 <= b.font.size_rank <= 1.0
            for p in doc.pages
            for b in p.blocks
        )

    def test_image_adjacency_measured(self, parsed):
        """带图的页上应当能找到与图相邻的文本块，且原始测量量齐备。"""
        doc = parsed[self.FIG]
        adjacent = [
            b
            for p in doc.pages
            if p.images
            for b in p.blocks
            if b.image_adjacency.is_adjacent
        ]
        assert adjacent, "图密集课件里一个相邻块都没有，邻接口径有问题"
        for block in adjacent:
            adj = block.image_adjacency
            assert adj.nearest_image_id is not None
            assert adj.relation is not None
            assert adj.gap_pt is not None and adj.gap_lines is not None
            assert adj.overlap_ratio is not None

    def test_adjacency_references_existing_image(self, parsed):
        for doc in parsed.values():
            for page in doc.pages:
                ids = {i.image_id for i in page.images}
                for block in page.blocks:
                    ref = block.image_adjacency.nearest_image_id
                    assert ref is None or ref in ids

    def test_image_coverage_never_exceeds_one(self, parsed):
        """15.316 的扫描图右下溢出页面；不裁剪会算出 > 1.0。"""
        for doc in parsed.values():
            for page in doc.pages:
                assert 0.0 <= page.evidence.image_coverage_union <= 1.0
                assert 0.0 <= page.evidence.image_coverage_max <= 1.0
                for image in page.images:
                    assert 0.0 <= image.coverage <= 1.0

    def test_block_and_image_ids_unique(self, parsed):
        for doc in parsed.values():
            for page in doc.pages:
                block_ids = [b.block_id for b in page.blocks]
                image_ids = [i.image_id for i in page.images]
                assert len(set(block_ids)) == len(block_ids)
                assert len(set(image_ids)) == len(image_ids)


class TestDocumentMeta:
    def test_pdf_has_no_notes(self, parsed):
        """演讲者备注是 PPTX 专有；PDF 路径恒为 null。"""
        for doc in parsed.values():
            assert all(p.notes_text is None for p in doc.pages)

    def test_ruleset_versions_recorded(self, parsed):
        for doc in parsed.values():
            assert doc.document.parser.scan_ruleset_version == SCAN_RULESET_VERSION
            assert doc.document.parser.adjacency_ruleset_version
            assert doc.document.parser.python_pptx is None

    def test_unusable_pages_listed(self, parsed):
        """调用方靠这张表生成 UI 文案，必须与 kind 一致。"""
        for doc in parsed.values():
            listed = {u.index: u.kind for u in doc.document.unusable_pages}
            actual = {p.index: p.kind for p in doc.pages if not p.usable}
            assert listed == actual
            assert doc.document.unusable_page_count == len(actual)

    def test_content_sha256_is_content_hash(self, parsed):
        import hashlib

        path = CORPUS / "MIT11_201F07_slides2.pdf"
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        assert parsed[path.name].document.content_sha256 == expected

    def test_no_document_primary_key_emitted(self, parsed):
        """服务无状态、不认识 user_id，不得产出文档主键。

        PRD §12.1：按内容哈希去重等于跨用户共享存储。主键归持久化层。
        """
        fields = set(DocumentMeta.model_fields)
        assert "doc_id" not in fields
        assert "document_id" not in fields
        assert "content_sha256" in fields
