"""版面几何：矩形、并集面积、文本块与图片的邻接度量。

本模块不依赖 PyMuPDF 与 python-pptx，只吃纯数字——两条来源路径共用同一套几何口径，
且可脱离文件单测。

三条约定：

1. 坐标系为 **PDF/PPTX 通用的左上原点**（x 向右、y 向下），单位 pt。
   PPTX 的 EMU 在 ``pptx.py`` 里换算后再进本模块。
2. 面积并集用**坐标压缩 + 精确求和**，不用网格近似。
   探针脚本 ``scripts/probe_corpus.py`` 早期用过 80x80 网格，两者结果不同，
   差异表见 README「网格近似 vs 精确并集」一节。
3. 邻接**只给测量量**（gap、投影重叠比），``is_adjacent`` 是按默认阈值导出的便利字段。
   阈值口径见 ``ADJACENCY_RULESET_VERSION``；改阈值必须改版本号。

决策日期：2026-08-20
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "ADJACENCY_RULESET_VERSION",
    "ADJACENCY_MAX_GAP_LINES",
    "ADJACENCY_MIN_OVERLAP",
    "Rect",
    "Relation",
    "Adjacency",
    "union_area",
    "relate",
    "nearest_image",
]

#: 邻接口径版本。``ADJACENCY_MAX_GAP_LINES`` / ``ADJACENCY_MIN_OVERLAP`` /
#: ``relate()`` 的关系判定逻辑，任意一处改动都必须同步改这个数——
#: 下游（第三步术语提取的图注通道）靠它判断两批解析结果可不可比。
ADJACENCY_RULESET_VERSION = "adj-1.0"

#: 「相邻」的默认间距上限，单位为**行高**（gap_pt ÷ 主导字号）。
#: 来源：占位待定。确定路径见 README 阈值表——第三步的图注标注集建成后，
#: 按「图注被判为相邻」的召回率反推。当前值只用于生成便利字段，
#: 原始量 gap_lines / overlap_ratio 一并输出，下游可自行重算。
ADJACENCY_MAX_GAP_LINES = 1.5

#: 「相邻」要求的正交轴投影重叠比下限。来源：占位待定，同上。
ADJACENCY_MIN_OVERLAP = 0.5

#: 浮点面积比较的容差。PDF 坐标常有 1e-13 量级的尾差。
_EPS = 1e-9


@dataclass(frozen=True, slots=True)
class Rect:
    """左上原点的轴对齐矩形。``x0 <= x1`` 且 ``y0 <= y1`` 由构造时规整保证。"""

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def of(cls, x0: float, y0: float, x1: float, y1: float) -> "Rect":
        """规整方向后构造。PDF 里 y 轴翻转的矩形会给出 y0 > y1，必须先正过来。"""
        lo_x, hi_x = (x0, x1) if x0 <= x1 else (x1, x0)
        lo_y, hi_y = (y0, y1) if y0 <= y1 else (y1, y0)
        return cls(lo_x, lo_y, hi_x, hi_y)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def is_empty(self) -> bool:
        return self.width <= _EPS or self.height <= _EPS

    def clipped_to(self, other: "Rect") -> "Rect":
        """裁剪到 ``other`` 内。无交集时返回零面积矩形。

        必要：15.316 的扫描图右下溢出页面（图 x1=805.94 而页宽 805.0），
        不裁剪会算出 > 1.0 的覆盖率。
        """
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        x1, y1 = min(self.x1, other.x1), min(self.y1, other.y1)
        if x1 <= x0 or y1 <= y0:
            return Rect(x0, y0, x0, y0)
        return Rect(x0, y0, x1, y1)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


def union_area(rects: list[Rect]) -> float:
    """一组矩形的并集面积（精确）。

    做法：把所有 x 边界排序做坐标压缩，得到若干竖条带；在每个条带内，
    与之相交的矩形退化成一维 y 区间，合并区间后乘条带宽度累加。
    重叠区域因此只被计一次。

    复杂度 O(n^2 log n)，n 为矩形数。单页图片数实测最多 10（22.081J p21），
    这个量级下精确解法没有任何性能压力，不必用扫描线堆。
    """
    boxes = [r for r in rects if not r.is_empty]
    if not boxes:
        return 0.0

    xs = sorted({v for r in boxes for v in (r.x0, r.x1)})
    total = 0.0
    for left, right in zip(xs, xs[1:]):
        strip_w = right - left
        if strip_w <= _EPS:
            continue
        # 用条带中点判定归属，避免边界重合时把矩形算进相邻条带
        mid = (left + right) / 2.0
        spans = sorted((r.y0, r.y1) for r in boxes if r.x0 <= mid <= r.x1)
        if not spans:
            continue
        covered = 0.0
        cur_lo, cur_hi = spans[0]
        for lo, hi in spans[1:]:
            if lo > cur_hi:
                covered += cur_hi - cur_lo
                cur_lo, cur_hi = lo, hi
            elif hi > cur_hi:
                cur_hi = hi
        covered += cur_hi - cur_lo
        total += covered * strip_w
    return total


class Relation(str, Enum):
    """文本块相对图片的位置关系。"""

    INSIDE = "inside"      # 文本块完全落在图内（图上标签）
    OVERLAP = "overlap"    # 部分重叠
    ABOVE = "above"
    BELOW = "below"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class Adjacency:
    """一个文本块与一张图片的邻接测量。``relation is None`` 表示斜向错开，不构成邻接。"""

    relation: Relation | None
    gap_pt: float
    gap_lines: float
    overlap_ratio: float

    @property
    def is_adjacent(self) -> bool:
        """按默认阈值导出的便利字段。口径版本见 ``ADJACENCY_RULESET_VERSION``。"""
        if self.relation is None:
            return False
        if self.relation in (Relation.INSIDE, Relation.OVERLAP):
            return True
        return (
            self.gap_lines <= ADJACENCY_MAX_GAP_LINES + _EPS
            and self.overlap_ratio >= ADJACENCY_MIN_OVERLAP - _EPS
        )


def _overlap_len(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def relate(block: Rect, image: Rect, line_height: float) -> Adjacency:
    """测量文本块相对图片的位置关系。

    ``line_height`` 用块的主导字号，不用块高——块高受行数影响，
    同一条图注写成一行还是两行会给出不同的 gap_lines，那不是我们想要的信号。

    ``overlap_ratio`` 的分母是**文本块**在正交轴上的长度，不是图片的：
    要问的是「这个块有多大比例正对着图」，而不是「图有多大比例被块盖住」。
    一张宽图配一条短图注时，用图作分母会得到很小的比值，把真图注判掉。
    """
    lh = line_height if line_height > _EPS else 1.0

    x_ov = _overlap_len(block.x0, block.x1, image.x0, image.x1)
    y_ov = _overlap_len(block.y0, block.y1, image.y0, image.y1)

    if x_ov > _EPS and y_ov > _EPS:
        inside = (
            block.x0 >= image.x0 - _EPS
            and block.x1 <= image.x1 + _EPS
            and block.y0 >= image.y0 - _EPS
            and block.y1 <= image.y1 + _EPS
        )
        rel = Relation.INSIDE if inside else Relation.OVERLAP
        denom = min(block.width, block.height)
        ratio = min(x_ov, y_ov) / denom if denom > _EPS else 1.0
        return Adjacency(rel, 0.0, 0.0, min(1.0, ratio))

    # 竖直相邻：x 投影有重叠
    if x_ov > _EPS:
        ratio = x_ov / block.width if block.width > _EPS else 0.0
        if block.y0 >= image.y1:
            gap = block.y0 - image.y1
            return Adjacency(Relation.BELOW, gap, gap / lh, min(1.0, ratio))
        gap = image.y0 - block.y1
        return Adjacency(Relation.ABOVE, gap, gap / lh, min(1.0, ratio))

    # 水平相邻：y 投影有重叠
    if y_ov > _EPS:
        ratio = y_ov / block.height if block.height > _EPS else 0.0
        if block.x0 >= image.x1:
            gap = block.x0 - image.x1
            return Adjacency(Relation.RIGHT, gap, gap / lh, min(1.0, ratio))
        gap = image.x0 - block.x1
        return Adjacency(Relation.LEFT, gap, gap / lh, min(1.0, ratio))

    # 斜向错开：两轴投影都不重叠，不构成图注关系
    return Adjacency(None, float("inf"), float("inf"), 0.0)


def nearest_image(
    block: Rect, images: list[tuple[str, Rect]], line_height: float
) -> tuple[str | None, Adjacency]:
    """选出与文本块关系最近的一张图。

    排序键：先按 gap_pt 升序，再按 overlap_ratio 降序。
    两张图与块间距相同时（常见于左右并排的两张图），取正对面积更大的那张。
    """
    best_id: str | None = None
    best = Adjacency(None, float("inf"), float("inf"), 0.0)
    for image_id, rect in images:
        adj = relate(block, rect, line_height)
        if adj.relation is None:
            continue
        if best.relation is None or (adj.gap_pt, -adj.overlap_ratio) < (
            best.gap_pt,
            -best.overlap_ratio,
        ):
            best_id, best = image_id, adj
    return best_id, best
