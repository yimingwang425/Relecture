"""输出契约。**这是 Python 与 TS 之间唯一的 schema 定义处。**

用 pydantic 定义而非 dataclass，是为了能直接导出 JSON Schema 给 TS 侧生成类型
（``python -m parse_service.schema`` 打印 JSON Schema）。pydantic 是 FastAPI 自带依赖，
不额外引入。

三条设计原则：

1. **版面信息一律保留。** 格式特有的信号全部收进 ``origin``（按 ``kind`` 区分的
   可辨识联合），共有信息升到顶层。不为了「统一 PDF 与 PPTX」而丢任一侧的字段。
2. **判据发出的是测量值，不只是结论。** 每个结论旁边并排放它依据的原始数字
   （``PageEvidence``、``ImageAdjacency`` 的几何量），下游改阈值可直接重算，
   不必重新解析文件。
3. **口径版本随结论走。** ``scan_ruleset_version`` 与 ``adjacency_ruleset_version``
   分别标记两套阈值的版本，改口径必须改版本号，否则两批解析结果不可比。

本次交付只实现 PDF 路径；PPTX 的 origin 模型已定义并纳入联合类型，解析器下次交付。

决策日期：2026-08-20
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SCHEMA_VERSION",
    "SERVICE_VERSION",
    "PageKind",
    "ParserInfo",
    "DocumentMeta",
    "PageEvidence",
    "FontInfo",
    "ImageAdjacency",
    "PdfSpan",
    "PdfTextBlockOrigin",
    "PptxShapeOrigin",
    "PdfImageOrigin",
    "PptxPictureOrigin",
    "TextBlock",
    "ImageObject",
    "Page",
    "UnusablePage",
    "ParseResult",
]

SCHEMA_VERSION = "1.0"
SERVICE_VERSION = "0.1.0"

PageKind = Literal["digital_text", "scanned_ocr", "image_only", "empty"]

BBox = Annotated[list[float], Field(min_length=4, max_length=4)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------
# 文档级
# --------------------------------------------------------------------------


class ParserInfo(_Model):
    service_version: str = Field(description="parse-service 自身版本")
    pymupdf: str | None = Field(default=None, description="PDF 路径的解析库版本")
    python_pptx: str | None = Field(default=None, description="PPTX 路径；本次交付恒为 null")
    scan_ruleset_version: str = Field(
        description="扫描件判据口径版本（parse_service.scanned.SCAN_RULESET_VERSION）"
    )
    adjacency_ruleset_version: str = Field(
        description="图文邻接口径版本（parse_service.geometry.ADJACENCY_RULESET_VERSION）"
    )


class UnusablePage(_Model):
    """被判为不可用的页，供调用方生成 UI 文案。"""

    index: int
    kind: PageKind


class DocumentMeta(_Model):
    content_sha256: str = Field(
        description="文件内容 sha256（裸十六进制）。用途仅两项：**完整性校验**，"
        "以及**同一用户内**的重复上传提示。"
        "\n\n**它不是文档主键，且严禁用作跨用户去重或跨用户缓存的键。**"
        "PRD §12.1 承诺「课件仅存于用户个人空间，不跨用户共享、不建公共库、"
        "不做跨用户检索」——按内容哈希去重等于两个用户上传同一份课件时共用一份存储，"
        "直接违反该承诺。**这是产品承诺，不是性能取舍**，不因存储成本而放宽。"
        "\n\n文档主键由持久化层赋予：随机 id 或 (user_id, content_sha256)。"
        "本服务无状态、不认识 user_id，故不产出主键",
    )
    source_format: Literal["pdf", "pptx"]
    filename: str
    byte_size: int
    page_count: int
    producer: str | None = Field(
        default=None,
        description="PDF /Producer 或 PPTX docProps/app.xml。扫描件归因用："
        "当前判据的实测基础只有 Acrobat Capture 3.0 一个 OCR 工具，"
        "留下这个字段是为了日后能按产出工具复盘漏判",
    )
    parser: ParserInfo
    usable_page_count: int
    unusable_page_count: int
    unusable_pages: list[UnusablePage] = Field(
        default_factory=list,
        description="不可用页的页号与类型。**服务不替用户拒绝上传**："
        "verdict 只在一页都不可用时为 unusable，其余一律 usable，"
        "UI 文案由调用方按 unusable_page_count 自行决定",
    )
    verdict: Literal["usable", "unusable"]
    verdict_reason: str


# --------------------------------------------------------------------------
# 页级
# --------------------------------------------------------------------------


class PageEvidence(_Model):
    """扫描件判据的全部输入，逐页留存以便事后改阈值重算。"""

    image_coverage_union: float = Field(
        description="所有图片落版矩形**并集**（精确，非网格近似）裁剪到页面后 ÷ 页面积"
    )
    image_coverage_max: float = Field(description="单张图片落版矩形裁剪到页面后的最大占比")
    invisible_text_ratio: float = Field(
        description="不着墨文本字符占比（PDF 渲染模式 Tr 3 / Tr 7）。"
        "**这是扫描件判据的唯一判别项**，其余字段为佐证或观测"
    )
    text_char_count: int = Field(description="去空白后的可提取字符数")
    embedded_font_count: int = Field(
        description="内嵌字体数。**不是判别项**——base-14 标准字体按 PDF 规范本就不内嵌，"
        "实测 123 页中 45 页为 0 而其中仅 19 页属扫描件（精确率 42%）。仅作归因参考"
    )
    font_count: int = Field(description="本页引用的字体总数（含未内嵌）")
    white_fill_text_ratio: float = Field(
        description="**观测项，不参与任何判定。** 填充色为纯白的文本字符占比。"
        "用于日后排查「白字型 OCR 层」这一已知漏判方向；"
        "深色底幻灯片的正常白字会让它有噪声（实测 14.129 有页高达 0.919），"
        "故只记录不接规则"
    )
    rule_fired: str = Field(description="命中的规则编号，见 parse_service.scanned")


class FontInfo(_Model):
    """块级字体汇总：按字符数加权的主导值。span 级原始值在 ``origin`` 里。"""

    size_pt: float | None = None
    size_rank: float | None = Field(
        default=None,
        description="该字号在本页**字符加权**字号分布中的分位（0~1）。"
        "跨页面尺寸与跨文件可比——图注偏小、标题偏大，绝对字号做不到这点",
    )
    bold: bool | None = None
    italic: bool | None = None
    name: str | None = None
    color: str | None = Field(default=None, description="十六进制，如 '#000000'")
    resolved: bool = Field(
        description="字体信息是否解析成功。PDF 路径恒为 true；"
        "PPTX 的 run 常继承自 layout/master，回溯不到时为 false —— "
        "下游必须区分「没加粗」与「不知道有没有加粗」",
    )


class ImageAdjacency(_Model):
    """文本块与最近一张图片的位置关系。**给测量，不给定论。**

    ``is_adjacent`` 是按默认阈值导出的便利字段；阈值本身是占位待定，
    确定路径见 README。下游（第三步图注通道）应当用 ``gap_lines`` /
    ``overlap_ratio`` 自行重算，不要把 ``is_adjacent`` 当成事实。
    """

    nearest_image_id: str | None = None
    relation: Literal["inside", "overlap", "above", "below", "left", "right"] | None = None
    gap_pt: float | None = Field(default=None, description="到图边的间距，pt")
    gap_lines: float | None = Field(
        default=None, description="gap_pt ÷ 主导字号。尺度无关，跨字号与跨页面尺寸可比"
    )
    overlap_ratio: float | None = Field(
        default=None, description="与图在正交轴上的投影重叠比，分母是**文本块**的长度"
    )
    is_adjacent: bool = False


# --------------------------------------------------------------------------
# origin：两种来源各自的原始信号，不做统一
# --------------------------------------------------------------------------


class PdfSpan(_Model):
    text: str
    size_pt: float
    font: str
    flags: int = Field(description="PyMuPDF 原始 flags 位")
    bold: bool
    italic: bool
    serif: bool
    mono: bool
    color: str
    alpha: int = Field(
        description="PyMuPDF dict 的原始 alpha（0~255）。"
        "**不等同于渲染模式**：实测 alpha=0 与 Tr 3 高度相关但存在反例，"
        "且 texttrace 与 dict 的 span 切分不一致（实测仅约 50% 可按 origin 对齐），"
        "故本服务不做 span 级渲染模式回填。页级不着墨占比以 "
        "``PageEvidence.invisible_text_ratio`` 为准",
    )
    bbox_pt: BBox


class PdfTextBlockOrigin(_Model):
    kind: Literal["pdf_textblock"] = "pdf_textblock"
    block_number: int
    line_count: int
    spans: list[PdfSpan] = Field(
        description="保留 span 级是必要的：块是 PyMuPDF 按几何聚类出来的，"
        "一个块里混着不同字号很常见（22.081J p3 同一块里 13.06pt 与 31.33pt 并存），"
        "只留块级汇总会把标题与副标题压成一个平均字号，字号信号就废了"
    )


class PptxRun(_Model):
    text: str
    size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    name: str | None = None
    resolved_from: Literal["run", "placeholder", "layout", "master", "theme"] | None = Field(
        default=None,
        description="字体属性是从继承链哪一级解析到的。null 表示没解析出来",
    )


class PptxShapeOrigin(_Model):
    """PPTX 侧的 origin。**本次交付未实现解析器**，模型先定以固定契约形状。"""

    kind: Literal["pptx_shape"] = "pptx_shape"
    shape_id: int
    shape_name: str
    shape_type: str
    is_placeholder: bool
    placeholder_type: str | None = None
    placeholder_idx: int | None = None
    from_layout: bool = Field(
        default=False,
        description="true = 来自 layout/master 的背景装饰而非本页内容。"
        "来自母版的图片不参与邻接计算——每页都有的 logo 会把每个文本块都判成图注",
    )
    z_order: int
    rotation: float = 0.0
    paragraph_index: int
    list_level: int = Field(default=0, description="项目符号层级。PDF 侧没有的结构信号")
    runs: list[PptxRun] = Field(default_factory=list)


TextBlockOrigin = Annotated[
    Union[PdfTextBlockOrigin, PptxShapeOrigin], Field(discriminator="kind")
]


class PdfImageOrigin(_Model):
    kind: Literal["pdf_image"] = "pdf_image"
    xref: int
    colorspace: str | None = None
    bpc: int | None = None
    placement_index: int = Field(
        default=0,
        description="同一 xref 可在一页内多处落版，每处各发一条 ImageObject",
    )


class PptxPictureOrigin(_Model):
    """**本次交付未实现。**"""

    kind: Literal["pptx_picture"] = "pptx_picture"
    shape_id: int
    shape_name: str
    from_layout: bool = False
    z_order: int = 0
    rotation: float = 0.0


ImageOrigin = Annotated[Union[PdfImageOrigin, PptxPictureOrigin], Field(discriminator="kind")]


# --------------------------------------------------------------------------
# 块与图
# --------------------------------------------------------------------------


class TextBlock(_Model):
    block_id: str = Field(description="形如 'p3-b7'，文档内唯一")
    page_index: int
    order: int = Field(
        description="解析器给出的顺序。**不是阅读顺序**——"
        "PDF 侧为几何排序（先 y 后 x），多栏版面下与人的阅读顺序不一致"
    )
    text: str
    bbox_pt: BBox
    bbox_norm: BBox = Field(description="bbox_pt 除以页宽高。跨页面尺寸可比")
    font: FontInfo
    image_adjacency: ImageAdjacency
    origin: TextBlockOrigin


class ImageObject(_Model):
    image_id: str = Field(description="形如 'p3-i1'，文档内唯一")
    page_index: int
    bbox_pt: BBox = Field(description="落版矩形，已裁剪到页面内")
    bbox_norm: BBox
    coverage: float = Field(description="本图落版面积 ÷ 页面积")
    pixel_size: list[int] | None = Field(
        default=None, description="源图像素尺寸。**不导出二进制**，需要像素时按 image_id 回取"
    )
    origin: ImageOrigin


class Page(_Model):
    index: int = Field(description="1-based")
    size_pt: list[float] = Field(min_length=2, max_length=2)
    rotation: int = 0
    kind: PageKind
    usable: bool = Field(description="仅 digital_text 为 true")
    evidence: PageEvidence
    notes_text: str | None = Field(
        default=None,
        description="仅 PPTX：演讲者备注。不进 blocks——它没有 bbox，"
        "混进去会污染邻接计算。**备注同样受 PRD §12.1 的单段引用 100 字上限约束**，"
        "下游引用备注原文时须与正文同等对待。"
        "术语提取是否使用备注由第三步决定，本服务只存不用。PDF 路径恒为 null",
    )
    blocks: list[TextBlock] = Field(default_factory=list)
    images: list[ImageObject] = Field(default_factory=list)


class ParseResult(_Model):
    schema_version: str = SCHEMA_VERSION
    document: DocumentMeta
    pages: list[Page] = Field(default_factory=list)


if __name__ == "__main__":  # pragma: no cover
    import json

    print(json.dumps(ParseResult.model_json_schema(), ensure_ascii=False, indent=2))
