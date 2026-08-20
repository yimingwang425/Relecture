"""旋转页回归测试。

**为什么单独一个文件**：旋转页在真实用户的扫描件与手机翻拍课件里大概率比测试集常见，
这不是一次性修补。合成用例覆盖四个角度，不依赖测试集是否恰好含旋转页。

背景：``page.get_text("dict", sort=True)`` 的排序发生在**未旋转**坐标空间。
对 ``/Rotate != 0`` 的页，该顺序是错的，且 ``page.rect``（显示尺寸）与 dict 的
坐标空间不是同一个矩形，用前者裁剪后者会削掉 bbox。
修法见 ``parse_service.pdf._display_textpage``。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from parse_service.pdf import parse_pdf

CORPUS = Path(__file__).resolve().parents[2] / "实验脚本" / "测试集"
ROTATED = "MIT11_201F07_slides2.pdf"

#: 合成页上自上而下的三行文字（文本，字号）。
LINES = [("TITLE OF THE SLIDE", 24), ("first body line", 12), ("second body line", 12)]

_W, _H = 612.0, 792.0

#: 每个 ``/Rotate`` 角度对应的绘制参数，使**显示出来**是横排、自上而下。
#:
#: 真实的旋转页（如 11.201）是**文字本身带旋转绘制**，再配 ``/Rotate`` 抵消，
#: 显示时读起来是正的。若只是把横排文字画上去再旋转页面，显示出来就是竖排——
#: 那种页面没有「横向阅读顺序」可言，拿它做断言是构造错了测试。
#:
#: ``text_rotate``：使文字方向在显示空间里指向 +x。
#: ``start``：沿文字方向的起点（要留够长度，否则会跑出页面被截）。
#: ``step``：换行方向，使显示空间里的行自上而下。
_LAYOUT = {
    0:   dict(text_rotate=0,   start=(72.0, 100.0),  step=(0.0, 100.0)),
    90:  dict(text_rotate=90,  start=(100.0, 700.0), step=(100.0, 0.0)),
    180: dict(text_rotate=180, start=(540.0, 692.0), step=(0.0, -100.0)),
    270: dict(text_rotate=270, start=(512.0, 92.0),  step=(-100.0, 0.0)),
}


def make_pdf(rotation: int, with_image: bool = False) -> bytes:
    """造一份单页 PDF，**显示出来**是自上而下的三行横排文字，页面 ``/Rotate`` 为给定角度。"""
    layout = _LAYOUT[rotation]
    doc = pymupdf.open()
    page = doc.new_page(width=_W, height=_H)
    (sx, sy), (dx, dy) = layout["start"], layout["step"]
    for i, (text, size) in enumerate(LINES):
        page.insert_text(
            (sx + dx * i, sy + dy * i), text, fontsize=size, rotate=layout["text_rotate"]
        )
    if with_image:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20))
        pix.set_rect(pix.irect, (200, 30, 30))
        page.insert_image(pymupdf.Rect(72, 400, 472, 600), pixmap=pix)
    if rotation:
        page.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    return data


class TestSyntheticRotation:
    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_reading_order_preserved(self, rotation):
        """四个角度下，块顺序都必须是版面上的自上而下顺序。"""
        result = parse_pdf(make_pdf(rotation), f"rot{rotation}.pdf")
        page = result.pages[0]
        texts = [b.text.strip() for b in page.blocks]
        assert texts == [t for t, _ in LINES], f"rotation={rotation} 顺序错：{texts}"

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_rotation_recorded(self, rotation):
        page = parse_pdf(make_pdf(rotation), "x.pdf").pages[0]
        assert page.rotation == rotation

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_bbox_within_page_and_not_clipped(self, rotation):
        """bbox 必须落在页内，且不能恰好贴着页边——贴边是被裁的特征。"""
        page = parse_pdf(make_pdf(rotation), "x.pdf").pages[0]
        w, h = page.size_pt
        for block in page.blocks:
            x0, y0, x1, y1 = block.bbox_pt
            assert 0 <= x0 < x1 <= w + 0.01
            assert 0 <= y0 < y1 <= h + 0.01
            assert y1 < h - 1, f"rotation={rotation} 的块 y1 贴着页高，疑似被裁：{block.bbox_pt}"

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_text_lines_are_horizontal(self, rotation):
        """显示空间里，一行文字应当是宽而扁的。竖条说明没做旋转变换。"""
        page = parse_pdf(make_pdf(rotation), "x.pdf").pages[0]
        for block in page.blocks:
            x0, y0, x1, y1 = block.bbox_pt
            assert (x1 - x0) > (y1 - y0), f"rotation={rotation} 的块是竖条：{block.bbox_pt}"

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_page_size_matches_display_orientation(self, rotation):
        """90/270 度旋转后，页面的宽高应当互换。"""
        page = parse_pdf(make_pdf(rotation), "x.pdf").pages[0]
        w, h = page.size_pt
        if rotation in (90, 270):
            assert (round(w), round(h)) == (792, 612)
        else:
            assert (round(w), round(h)) == (612, 792)

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_no_text_lost(self, rotation):
        """旋转不得吞字。默认裁剪框在坐标变换后会切掉右侧文本，必须用 INFINITE_RECT。"""
        page = parse_pdf(make_pdf(rotation), "x.pdf").pages[0]
        joined = " ".join(b.text for b in page.blocks)
        for text, _ in LINES:
            assert text in joined, f"rotation={rotation} 丢了 {text!r}"

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_image_rect_transformed(self, rotation):
        """``get_image_rects`` 返回未旋转坐标，必须一并变换，否则覆盖率算错。"""
        page = parse_pdf(make_pdf(rotation, with_image=True), "x.pdf").pages[0]
        assert page.images, "合成图未被识别"
        w, h = page.size_pt
        for img in page.images:
            x0, y0, x1, y1 = img.bbox_pt
            assert 0 <= x0 < x1 <= w + 0.01 and 0 <= y0 < y1 <= h + 0.01
            assert img.coverage > 0.0
        # 图占 400x200 pt，页面 612x792 → 覆盖率与旋转无关
        assert page.evidence.image_coverage_union == pytest.approx(
            (400 * 200) / (612 * 792), rel=0.05
        )

    def test_font_size_unaffected_by_rotation(self):
        """字号是标量，不该随旋转变化——size_rank 依赖它。"""
        sizes = {}
        for rotation in (0, 90, 180, 270):
            page = parse_pdf(make_pdf(rotation), "x.pdf").pages[0]
            sizes[rotation] = sorted(round(b.font.size_pt, 1) for b in page.blocks)
        assert len(set(map(tuple, sizes.values()))) == 1, sizes


@pytest.mark.skipif(not CORPUS.exists(), reason="语料不存在")
class TestCorpusRotatedDocument:
    """11.201 全部 15 页 /Rotate=90，是语料里唯一的真实旋转样本。"""

    @pytest.fixture(scope="class")
    def parsed(self):
        return parse_pdf((CORPUS / ROTATED).read_bytes(), ROTATED)

    def test_all_pages_rotated(self, parsed):
        assert [p.rotation for p in parsed.pages] == [90] * 15

    def test_title_is_first_block_on_most_pages(self, parsed):
        """修复前 13/15 页的最大字号块排在第 4 块之后；修复后应回到最前。"""
        first = 0
        for page in parsed.pages:
            ranked = [b for b in page.blocks if b.font.size_rank is not None]
            if not ranked:
                continue
            top = max(ranked, key=lambda b: b.font.size_rank)
            if page.blocks[0].block_id == top.block_id:
                first += 1
        assert first >= 13, f"最大字号块排在首位的页只有 {first}/15"

    def test_no_bbox_clipped_to_page_height(self, parsed):
        """修复前该文件每个块的 y1 都被裁到 538（页高）。"""
        clipped = [
            b.block_id
            for p in parsed.pages
            for b in p.blocks
            if abs(b.bbox_pt[3] - p.size_pt[1]) < 0.01
        ]
        assert clipped == [], f"仍有块贴着页高：{clipped[:5]}"

    def test_blocks_are_horizontal(self, parsed):
        vertical = [
            b.block_id
            for p in parsed.pages
            for b in p.blocks
            if (b.bbox_pt[2] - b.bbox_pt[0]) < (b.bbox_pt[3] - b.bbox_pt[1])
        ]
        assert vertical == [], f"仍有竖条块：{vertical[:5]}"

    def test_no_text_lost_versus_raw_extraction(self, parsed):
        """与不做任何变换的原始抽取比，字符数不得减少。"""
        doc = pymupdf.open(CORPUS / ROTATED)
        try:
            for page in parsed.pages:
                raw = doc[page.index - 1].get_text("text")
                raw_chars = len("".join(raw.split()))
                got_chars = len("".join(" ".join(b.text for b in page.blocks).split()))
                assert got_chars >= raw_chars, f"p{page.index} 丢字：{got_chars} < {raw_chars}"
        finally:
            doc.close()

    def test_reading_order_reconstructs_sentences(self, parsed):
        """p2 的三条学习目标在修复前被打散成 10 块乱序，修复后应能读通。"""
        text = " ".join(" ".join(b.text.split()) for b in parsed.pages[1].blocks)
        assert text.index("Today's learning objectives".replace("'", "’")) == 0
        assert "Move from theory to practice and back again." in text


@pytest.mark.skipif(not CORPUS.exists(), reason="语料不存在")
class TestUnrotatedDocumentsUnchanged:
    """未旋转文档必须逐块不变——修法的等价性前提。

    ``rotation_matrix`` 对 ``/Rotate=0`` 的页是恒等矩阵，故 ``_display_textpage``
    的结果应与原来的 ``get_text("dict", sort=True)`` 完全一致。
    这条一旦破，说明 clip 或 flags 没对齐，会悄悄改动另外三份课件的切片文本。
    """

    @pytest.mark.parametrize(
        "name",
        ["MIT22_081JF10_lec01b.pdf", "MIT14_129S25_lec02.pdf", "MIT15_316SU05_leader_dev.pdf"],
    )
    def test_identical_to_plain_sorted_extraction(self, name):
        parsed = parse_pdf((CORPUS / name).read_bytes(), name)
        doc = pymupdf.open(CORPUS / name)
        try:
            for page in parsed.pages:
                raw = doc[page.index - 1].get_text("dict", sort=True)
                w, h = page.size_pt
                # 解析侧会把 bbox 裁剪到页面矩形内（15.316 的图右下溢出页面），
                # 故比较前对原始 bbox 做同样的裁剪，否则比的是两个口径
                def clip(bbox):
                    x0, y0, x1, y1 = bbox
                    return (
                        round(max(0.0, min(x0, w)), 3),
                        round(max(0.0, min(y0, h)), 3),
                        round(max(0.0, min(x1, w)), 3),
                        round(max(0.0, min(y1, h)), 3),
                    )

                expected = [
                    (
                        clip(b["bbox"]),
                        "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip(),
                    )
                    for b in raw["blocks"]
                    if b["type"] == 0
                    and "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
                ]
                got = [
                    (tuple(round(v, 3) for v in b.bbox_pt), b.text.strip().replace("\n", ""))
                    for b in page.blocks
                ]
                assert len(got) == len(expected), f"{name} p{page.index} 块数变了"
                for (gb, _), (eb, _) in zip(got, expected):
                    assert gb == pytest.approx(eb, abs=0.01), f"{name} p{page.index} bbox 变了"
        finally:
            doc.close()
