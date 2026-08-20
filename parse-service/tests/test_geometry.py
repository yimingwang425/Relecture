"""几何口径测试。重点在**精确并集**与**邻接关系**两处，它们直接决定判据的数字。"""

from __future__ import annotations

import math

import pytest

from parse_service.geometry import (
    ADJACENCY_MAX_GAP_LINES,
    Rect,
    Relation,
    nearest_image,
    relate,
    union_area,
)


class TestRect:
    def test_of_normalizes_flipped_axes(self):
        assert Rect.of(10, 20, 0, 5).as_tuple() == (0, 5, 10, 20)

    def test_clipped_to_handles_overflow(self):
        """15.316 的扫描图右下溢出页面；不裁剪会算出 > 1.0 的覆盖率。"""
        page = Rect(0, 0, 805, 621)
        img = Rect(53.2, 75.2, 805.94, 621.01)
        clipped = img.clipped_to(page)
        assert clipped.x1 == 805 and clipped.y1 == 621
        assert clipped.area / page.area < 1.0

    def test_clipped_to_disjoint_is_empty(self):
        assert Rect(100, 100, 200, 200).clipped_to(Rect(0, 0, 50, 50)).is_empty


class TestUnionArea:
    def test_empty(self):
        assert union_area([]) == 0.0

    def test_single(self):
        assert union_area([Rect(0, 0, 10, 20)]) == pytest.approx(200.0)

    def test_partial_overlap_counted_once(self):
        # 100 + 100 - 25 = 175
        assert union_area([Rect(0, 0, 10, 10), Rect(5, 5, 15, 15)]) == pytest.approx(175.0)

    def test_identical_rects(self):
        assert union_area([Rect(0, 0, 10, 10)] * 3) == pytest.approx(100.0)

    def test_fully_contained(self):
        assert union_area([Rect(0, 0, 10, 10), Rect(2, 2, 4, 4)]) == pytest.approx(100.0)

    def test_disjoint_sums(self):
        assert union_area([Rect(0, 0, 10, 10), Rect(20, 20, 30, 30)]) == pytest.approx(200.0)

    def test_adjacent_edges_no_double_count(self):
        """共边不重叠：面积应恰好相加，不能因边界重合而多算。"""
        assert union_area([Rect(0, 0, 10, 10), Rect(10, 0, 20, 10)]) == pytest.approx(200.0)

    def test_degenerate_zero_area_ignored(self):
        assert union_area([Rect(0, 0, 10, 10), Rect(3, 3, 3, 8)]) == pytest.approx(100.0)

    def test_scanned_strips(self):
        """扫描件可能被切成多条带（15.316 p8 就是 2 张图），并集必须把它们合起来。"""
        strips = [Rect(0, 0, 100, 50), Rect(0, 50, 100, 100), Rect(0, 100, 100, 150)]
        assert union_area(strips) == pytest.approx(15000.0)

    def test_matches_bruteforce_on_random_rects(self):
        import random

        rng = random.Random(20260820)
        for _ in range(30):
            rects = []
            for _ in range(rng.randint(1, 6)):
                x0, y0 = rng.uniform(0, 90), rng.uniform(0, 90)
                rects.append(Rect(x0, y0, x0 + rng.uniform(1, 40), y0 + rng.uniform(1, 40)))
            # 细网格暴力解作参照；网格越细越逼近精确值
            n = 400
            hit = sum(
                1
                for i in range(n)
                for j in range(n)
                if any(
                    r.x0 <= (i + 0.5) * 130 / n <= r.x1 and r.y0 <= (j + 0.5) * 130 / n <= r.y1
                    for r in rects
                )
            )
            approx = hit / (n * n) * 130 * 130
            assert union_area(rects) == pytest.approx(approx, rel=0.02, abs=20)


class TestRelate:
    PAGE_IMG = Rect(100, 100, 300, 200)

    def test_caption_below_figure(self):
        block = Rect(120, 204, 280, 214)  # 紧贴图下方
        adj = relate(block, self.PAGE_IMG, line_height=10.0)
        assert adj.relation is Relation.BELOW
        assert adj.gap_pt == pytest.approx(4.0)
        assert adj.gap_lines == pytest.approx(0.4)
        assert adj.overlap_ratio == pytest.approx(1.0)
        assert adj.is_adjacent

    def test_caption_above_figure(self):
        adj = relate(Rect(120, 86, 280, 96), self.PAGE_IMG, 10.0)
        assert adj.relation is Relation.ABOVE
        assert adj.gap_pt == pytest.approx(4.0)
        assert adj.is_adjacent

    def test_left_and_right(self):
        assert relate(Rect(310, 120, 380, 130), self.PAGE_IMG, 10.0).relation is Relation.RIGHT
        assert relate(Rect(20, 120, 90, 130), self.PAGE_IMG, 10.0).relation is Relation.LEFT

    def test_label_inside_figure(self):
        adj = relate(Rect(150, 130, 200, 145), self.PAGE_IMG, 10.0)
        assert adj.relation is Relation.INSIDE
        assert adj.gap_pt == 0.0 and adj.is_adjacent

    def test_partial_overlap(self):
        adj = relate(Rect(280, 180, 400, 260), self.PAGE_IMG, 10.0)
        assert adj.relation is Relation.OVERLAP
        assert adj.is_adjacent

    def test_diagonal_is_not_adjacent(self):
        """两轴投影都不重叠——斜向错开，不构成图注关系。"""
        adj = relate(Rect(400, 400, 450, 410), self.PAGE_IMG, 10.0)
        assert adj.relation is None
        assert not adj.is_adjacent
        assert math.isinf(adj.gap_pt)

    def test_far_below_not_adjacent(self):
        gap = (ADJACENCY_MAX_GAP_LINES + 1.0) * 10.0
        adj = relate(Rect(120, 200 + gap, 280, 210 + gap), self.PAGE_IMG, 10.0)
        assert adj.relation is Relation.BELOW
        assert not adj.is_adjacent

    def test_low_projection_overlap_not_adjacent(self):
        """只有一角对着图的块不是图注。"""
        adj = relate(Rect(280, 204, 480, 214), self.PAGE_IMG, 10.0)
        assert adj.relation is Relation.BELOW
        assert adj.overlap_ratio == pytest.approx(0.1)
        assert not adj.is_adjacent

    def test_gap_lines_is_scale_free(self):
        """同样「一行的间距」，字号不同 gap_pt 不同，但 gap_lines 应相同。"""
        small = relate(Rect(120, 209, 280, 217), self.PAGE_IMG, 9.0)
        big = relate(Rect(120, 218, 280, 240), self.PAGE_IMG, 18.0)
        assert small.gap_lines == pytest.approx(1.0)
        assert big.gap_lines == pytest.approx(1.0)

    def test_overlap_ratio_denominator_is_the_block(self):
        """宽图配短图注：分母若用图会得到极小比值，把真图注判掉。"""
        wide = Rect(0, 100, 500, 200)
        adj = relate(Rect(230, 204, 270, 214), wide, 10.0)
        assert adj.overlap_ratio == pytest.approx(1.0)
        assert adj.is_adjacent


class TestNearestImage:
    def test_picks_closest(self):
        images = [("i0", Rect(100, 100, 300, 200)), ("i1", Rect(100, 300, 300, 400))]
        image_id, adj = nearest_image(Rect(120, 210, 280, 220), images, 10.0)
        assert image_id == "i0" and adj.relation is Relation.BELOW

    def test_ties_break_on_overlap(self):
        """两图与块间距相同时，取正对面积更大的那张。"""
        block = Rect(100, 200, 300, 210)  # 宽 200
        images = [
            ("offset", Rect(0, 100, 150, 190)),   # 间距 10，x 投影重叠 50 → 0.25
            ("facing", Rect(120, 100, 320, 190)),  # 间距 10，x 投影重叠 180 → 0.90
        ]
        image_id, adj = nearest_image(block, images, 10.0)
        assert image_id == "facing"
        assert adj.overlap_ratio == pytest.approx(0.9)

    def test_no_images(self):
        image_id, adj = nearest_image(Rect(0, 0, 10, 10), [], 10.0)
        assert image_id is None and adj.relation is None and not adj.is_adjacent

    def test_only_diagonal_images_yields_none(self):
        image_id, adj = nearest_image(
            Rect(0, 0, 10, 10), [("i0", Rect(500, 500, 600, 600))], 10.0
        )
        assert image_id is None and not adj.is_adjacent
