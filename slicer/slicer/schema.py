"""切片输出契约。

**切片是纯派生视图**：由 ``parse_service.schema.ParseResult`` 完全决定，
不引入任何新信息、不产生任何新判定。

三条设计原则：

1. **不做判定、不生成新标签。** 切片层不判断「这是标题还是图注」——
   那是第三步（术语提取的图注通道）的事。切片只把信号（``size_rank``、
   ``image_adjacency``、``bbox``、页级 evidence）原样送到下游手上。
   在这里贴一个无依据的标签，第三步真做时还得先把它清掉。
2. **不产出文档主键。** 见 ``SliceManifest.content_sha256`` 的说明——
   按内容哈希做主键等于跨用户共享存储，违反 PRD §12.1。
3. **页号一律用原始页号。** 跳过的页在 ``page_indices`` 里表现为缺号，
   缺号本身即信号。绝不重新编号。

决策日期：2026-08-20
"""

from __future__ import annotations

from typing import Literal

from parse_service.schema import FontInfo, ImageAdjacency, PageKind
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SLICE_SCHEMA_VERSION",
    "SLICER_VERSION",
    "SLICE_RULESET_VERSION",
    "SkippedPage",
    "SliceUnit",
    "SliceImage",
    "PageSignals",
    "Slice",
    "SliceManifest",
    "SliceSet",
]

SLICE_SCHEMA_VERSION = "1.0"
SLICER_VERSION = "0.1.0"

#: 切片口径版本。切片单位、边界策略、``title_block_id`` 的选取规则，
#: 任意一处改动都必须同步递增——否则两批切片的召回统计不可比。
SLICE_RULESET_VERSION = "slice-1.0"

BBox = list[float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkippedPage(_Model):
    """被跳过的页。**这张表要一路传到检索层。**

    ``image_only`` 的页**不是空页**，是「内容全在图里」的页。
    用户问到这类内容时检索会一无所获，而 PRD §10 现有的兜底文案是
    「这个内容课件里没有提到」——**那句话是错的**：课件提到了，只是我们读不了图。
    正确文案应为「这部分在第 N 页的图里，我读不了图」。本字段就是让 F3 能写出这句话的数据。
    """

    index: int = Field(description="原始页号（1-based）")
    kind: PageKind
    reason: str
    image_count: int = 0
    image_coverage_union: float = 0.0


class PageSignals(_Model):
    """页级 evidence 的**原样透传**。不做任何判定，不生成任何标签。

    为什么要带这两个量：``digital_text`` 里存在「标题 + 满版图 + 图注」的页
    （如 22.081J p21 的 ``REAL HEAT ENGINES``），文本只有几十字符，
    但**实质内容在图里**。PRD §8.1 实测图注术语在图密集章节占分母的 **83–87%**。

    这两个量是 2b 之后按召回率分组统计的依据，也是「过短页要不要合并」的判据来源。
    **现在没有数据，故不拍任何阈值**——只把量带下去。
    """

    page_index: int
    image_coverage_union: float
    text_char_count: int


class SliceUnit(_Model):
    """切片内的一个块。**溯源锚点**，粒度选择的依据见 方案.md §2。

    版面信息在这一层完整保留：第三步的图注通道要用 ``bbox_*`` 与 ``image_adjacency``，
    切片若只吐文本，第三步就得回头重做。
    """

    block_id: str
    page_index: int
    order: int
    text: str
    text_span: list[int] = Field(
        min_length=2,
        max_length=2,
        description="本块文本在 ``Slice.text`` 中的 [起, 止) 偏移。"
        "PRD §12.1 的「单段引用 ≤ 100 字」检查要与课件原文求最长公共子串，"
        "靠它把命中片段回指到具体的块",
    )
    bbox_pt: BBox
    bbox_norm: BBox
    font: FontInfo
    image_adjacency: ImageAdjacency


class SliceImage(_Model):
    """切片所属页上的图片。位置信息透传，**不含二进制**。"""

    image_id: str
    page_index: int
    bbox_norm: BBox
    coverage: float


class Slice(_Model):
    slice_id: str = Field(
        description="**文档内局部 id**（形如 's003'）。"
        "全局主键由持久化层赋予，形如 (document_id, slice_id)——"
        "本库无状态、不认识 user_id，不产出全局 id"
    )
    ordinal: int = Field(description="切片序号，1-based，连续无缺号")
    page_indices: list[int] = Field(
        description="**原始页号**。跳过的页在此表现为缺号，缺号本身即信号。"
        "绝不重新编号——重编会让「第 N 页」对不上用户手里的课件。"
        "定成列表是给日后可能的过短页合并留形状，v1 恒为单元素"
    )
    prev_slice_id: str | None = None
    next_slice_id: str | None = None
    title: str | None = Field(
        default=None, description="``title_block_id`` 对应块的文本。便利字段"
    )
    title_block_id: str | None = Field(
        default=None,
        description="``size_rank`` 最高的块，并列时取最靠上者。**无阈值**——"
        "只需相对比较（哪个最大），不需要「够不够大才算标题」的绝对判断",
    )
    text: str = Field(description="供 2b 嵌入与讲解 agent 使用的拼接文本")
    char_count: int
    page_signals: list[PageSignals] = Field(
        default_factory=list, description="与 ``page_indices`` 对齐。原样透传，不做判定"
    )
    units: list[SliceUnit] = Field(default_factory=list)
    images: list[SliceImage] = Field(default_factory=list)


class SliceManifest(_Model):
    content_sha256: str = Field(
        description="与 ``ParseResult.document.content_sha256`` 同值，用于确认两者同源。"
        "\\n\\n**不是文档主键，严禁用作跨用户去重或跨用户缓存的键。** "
        "PRD §12.1 承诺「不跨用户共享、不建公共库、不做跨用户检索」，"
        "按内容哈希去重等于两个用户上传同一份课件时共用一份存储。"
        "**这是产品承诺，不是性能取舍。**"
    )
    source_schema_version: str = Field(description="消费的 parse schema 版本；对不上应拒绝")
    slicer_version: str
    slice_ruleset_version: str
    page_count: int = Field(description="原始总页数，**含不可用页**")
    sliced_page_count: int
    slice_count: int
    skipped_pages: list[SkippedPage] = Field(default_factory=list)
    split_threshold_chars: int | None = Field(
        default=None,
        description="超长页拆分阈值。**默认 None = 不拆**，值由 2b 注入——"
        "它取决于 2b 选定的 embedding 供应商的上下文窗口，本层不写死。"
        "当前语料 0 触发（可用页 max 1911 字符）",
    )


class SliceSet(_Model):
    schema_version: str = SLICE_SCHEMA_VERSION
    manifest: SliceManifest
    slices: list[Slice] = Field(default_factory=list)
