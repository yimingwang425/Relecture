"""PDF 解析路径（PyMuPDF）。

产出 ``schema.ParseResult``。本模块只负责「从 PDF 里把数字取出来」，
判据逻辑在 ``scanned.py``、几何口径在 ``geometry.py``，三者不互相渗透。

几个关键取法（口径理由见 README）：

- **不着墨文本占比**用 ``page.get_texttrace()`` 的 ``type``（3 = 不可见、
  7 = 仅加入裁剪路径），逐字符计。不用 dict 的 ``alpha``：实测两者高度相关但有反例。
- **图像覆盖率**用 ``page.get_image_rects(xref)`` 的**落版矩形**，不是图像像素尺寸——
  一张图可以被缩放到任意大小，像素数说明不了它占多大版面。多张图取**精确并集**
  （扫描件可能被切成多条带，15.316 p8 就是 2 张图）。矩形先裁剪到页面内：
  15.316 的扫描图右下溢出页面，不裁会算出 > 1.0 的覆盖率。
- **块顺序**用 ``sort=True`` 的几何排序，schema 里明确声明它不是阅读顺序。

决策日期：2026-08-20
"""

from __future__ import annotations

import hashlib
from collections import Counter

import pymupdf

from .geometry import ADJACENCY_RULESET_VERSION, Rect, nearest_image
from .schema import (
    SCHEMA_VERSION,
    SERVICE_VERSION,
    DocumentMeta,
    FontInfo,
    ImageAdjacency,
    ImageObject,
    Page,
    PageEvidence,
    ParseResult,
    ParserInfo,
    PdfImageOrigin,
    PdfSpan,
    PdfTextBlockOrigin,
    TextBlock,
    UnusablePage,
)
from .scanned import SCAN_RULESET_VERSION, PageSignals, classify_page, document_verdict

__all__ = ["parse_pdf", "parse_pdf_bytes"]

#: PyMuPDF span flags 位。
_FLAG_SUPERSCRIPT = 1
_FLAG_ITALIC = 2
_FLAG_SERIF = 4
_FLAG_MONO = 8
_FLAG_BOLD = 16

#: 不着墨的文本渲染模式：3 = invisible，7 = 仅加入裁剪路径。两者都不在页面上留墨。
_INVISIBLE_RENDER_MODES = frozenset({3, 7})

#: 纯白判定的容差。PDF 颜色值是浮点，1.0 常写成 0.99998。
_WHITE_EPS = 0.01


def _to_rgb(colorspace: int | None, color: list[float] | tuple[float, ...] | None):
    """把 texttrace 的颜色分量换算成 0~1 的 RGB 三元组。无法识别时返回 None。"""
    if not color:
        return None
    try:
        if colorspace == 1:  # GRAY
            return (float(color[0]),) * 3
        if colorspace == 3:  # RGB
            return tuple(float(v) for v in color[:3])
        if colorspace == 4:  # CMYK
            c, m, y, k = (float(v) for v in color[:4])
            return (1 - min(1.0, c + k), 1 - min(1.0, m + k), 1 - min(1.0, y + k))
    except (IndexError, TypeError, ValueError):
        return None
    return None


def _hex_color(value: int | None) -> str | None:
    """PyMuPDF dict 的 color 是 sRGB 整数。"""
    if value is None:
        return None
    return f"#{value & 0xFFFFFF:06x}"


def _text_signals(page: pymupdf.Page) -> tuple[float, float, int]:
    """返回 ``(不着墨占比, 纯白填充占比, texttrace 字符总数)``。

    两个比值共用同一个分母（texttrace 的字符总数），这样它们之间可以直接相加比较；
    若各用各的分母，「0.9 不着墨 + 0.2 白字」就没法解读了。
    """
    total = invisible = white = 0
    for span in page.get_texttrace():
        n = len(span.get("chars", ()))
        if not n:
            continue
        total += n
        if span.get("type") in _INVISIBLE_RENDER_MODES:
            invisible += n
        rgb = _to_rgb(span.get("colorspace"), span.get("color"))
        if rgb is not None and all(v >= 1.0 - _WHITE_EPS for v in rgb):
            white += n
    if not total:
        return 0.0, 0.0, 0
    return invisible / total, white / total, total


def _image_objects(page: pymupdf.Page, page_rect: Rect) -> list[ImageObject]:
    """收集本页所有图片的落版矩形。同一 xref 多处落版则各发一条。"""
    out: list[ImageObject] = []
    page_area = page_rect.area
    for info in page.get_images(full=True):
        xref = info[0]
        px_w, px_h = info[2], info[3]
        colorspace = info[5] or None
        bpc = info[4] or None
        try:
            rects = list(page.get_image_rects(xref))
        except Exception:
            # 损坏或不可解析的图对象：跳过而不是让整份解析失败
            continue
        for placement, r in enumerate(rects):
            rect = Rect.of(r.x0, r.y0, r.x1, r.y1).clipped_to(page_rect)
            if rect.is_empty:
                continue
            image_id = f"p{page.number + 1}-i{len(out)}"
            out.append(
                ImageObject(
                    image_id=image_id,
                    page_index=page.number + 1,
                    bbox_pt=list(rect.as_tuple()),
                    bbox_norm=_norm(rect, page_rect),
                    coverage=rect.area / page_area if page_area else 0.0,
                    pixel_size=[px_w, px_h] if px_w and px_h else None,
                    origin=PdfImageOrigin(
                        xref=xref,
                        colorspace=colorspace,
                        bpc=bpc,
                        placement_index=placement,
                    ),
                )
            )
    return out


def _norm(rect: Rect, page_rect: Rect) -> list[float]:
    w = page_rect.width or 1.0
    h = page_rect.height or 1.0
    return [
        (rect.x0 - page_rect.x0) / w,
        (rect.y0 - page_rect.y0) / h,
        (rect.x1 - page_rect.x0) / w,
        (rect.y1 - page_rect.y0) / h,
    ]


def _size_rank_table(raw_blocks: list[dict]) -> dict[float, float]:
    """本页字号 → 字符加权分位（0~1）。

    分位而非绝对字号：图注偏小、标题偏大这件事在不同页面尺寸、不同课件之间才可比。
    取区间中点（累计到该字号之前 + 一半本字号），避免最小字号恒为 0、最大恒为 1。
    """
    weight: Counter[float] = Counter()
    for block in raw_blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                weight[round(float(span["size"]), 2)] += len(span.get("text", ""))
    total = sum(weight.values())
    if not total:
        return {}
    table: dict[float, float] = {}
    cumulative = 0
    for size in sorted(weight):
        n = weight[size]
        table[size] = (cumulative + n / 2) / total
        cumulative += n
    return table


def _dominant_font(spans: list[PdfSpan], rank: dict[float, float]) -> FontInfo:
    """块级字体汇总：按字符数加权取主导 span 的属性。"""
    if not spans:
        return FontInfo(resolved=True)
    lead = max(spans, key=lambda s: len(s.text))
    size = round(lead.size_pt, 2)
    return FontInfo(
        size_pt=lead.size_pt,
        size_rank=rank.get(size),
        bold=lead.bold,
        italic=lead.italic,
        name=lead.font,
        color=lead.color,
        resolved=True,  # PDF 侧字体信息永远是显式的，没有 PPTX 那样的继承链
    )


def _build_blocks(
    page: pymupdf.Page,
    raw_blocks: list[dict],
    page_rect: Rect,
    images: list[ImageObject],
) -> list[TextBlock]:
    rank = _size_rank_table(raw_blocks)
    image_rects = [
        (img.image_id, Rect(*img.bbox_pt))  # type: ignore[arg-type]
        for img in images
    ]
    out: list[TextBlock] = []
    order = 0
    for block in raw_blocks:
        if block.get("type") != 0:
            continue
        spans: list[PdfSpan] = []
        parts: list[str] = []
        lines = block.get("lines", ())
        for line in lines:
            for span in line.get("spans", ()):
                text = span.get("text", "")
                flags = int(span.get("flags", 0))
                spans.append(
                    PdfSpan(
                        text=text,
                        size_pt=float(span["size"]),
                        font=span.get("font", ""),
                        flags=flags,
                        bold=bool(flags & _FLAG_BOLD),
                        italic=bool(flags & _FLAG_ITALIC),
                        serif=bool(flags & _FLAG_SERIF),
                        mono=bool(flags & _FLAG_MONO),
                        color=_hex_color(span.get("color")) or "#000000",
                        alpha=int(span.get("alpha", 255)),
                        bbox_pt=[float(v) for v in span["bbox"]],
                    )
                )
                parts.append(text)
            parts.append("\n")
        text = "".join(parts).strip()
        if not text:
            continue

        rect = Rect.of(*(float(v) for v in block["bbox"])).clipped_to(page_rect)
        font = _dominant_font(spans, rank)
        line_height = font.size_pt or 1.0
        image_id, adj = nearest_image(rect, image_rects, line_height)

        order += 1
        out.append(
            TextBlock(
                block_id=f"p{page.number + 1}-b{order}",
                page_index=page.number + 1,
                order=order,
                text=text,
                bbox_pt=list(rect.as_tuple()),
                bbox_norm=_norm(rect, page_rect),
                font=font,
                image_adjacency=ImageAdjacency(
                    nearest_image_id=image_id,
                    relation=adj.relation.value if adj.relation else None,
                    gap_pt=adj.gap_pt if adj.relation else None,
                    gap_lines=adj.gap_lines if adj.relation else None,
                    overlap_ratio=adj.overlap_ratio if adj.relation else None,
                    is_adjacent=adj.is_adjacent,
                ),
                origin=PdfTextBlockOrigin(
                    block_number=int(block.get("number", order - 1)),
                    line_count=len(lines),
                    spans=spans,
                ),
            )
        )
    return out


def _page_evidence(
    page: pymupdf.Page, page_rect: Rect, images: list[ImageObject], text: str
) -> PageEvidence:
    from .geometry import union_area

    page_area = page_rect.area or 1.0
    rects = [Rect(*img.bbox_pt) for img in images]  # type: ignore[arg-type]
    union = union_area(rects) / page_area
    max_cov = max((img.coverage for img in images), default=0.0)

    invisible_ratio, white_ratio, _ = _text_signals(page)

    fonts = page.get_fonts(full=True)
    embedded = sum(1 for f in fonts if f[0] and f[1] not in ("n/a", ""))

    return PageEvidence(
        image_coverage_union=min(1.0, union),
        image_coverage_max=min(1.0, max_cov),
        invisible_text_ratio=invisible_ratio,
        text_char_count=len("".join(text.split())),
        embedded_font_count=embedded,
        font_count=len(fonts),
        white_fill_text_ratio=white_ratio,
        rule_fired="",  # 由 parse 阶段填入
    )


def parse_pdf(data: bytes, filename: str) -> ParseResult:
    """解析 PDF 字节流为结构化结果。"""
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        pages: list[Page] = []
        for page in doc:
            rect = Rect.of(page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1)
            raw = page.get_text("dict", sort=True)
            raw_blocks = raw.get("blocks", [])
            images = _image_objects(page, rect)
            evidence = _page_evidence(page, rect, images, page.get_text("text"))

            kind, rule = classify_page(
                PageSignals(
                    image_coverage_union=evidence.image_coverage_union,
                    image_coverage_max=evidence.image_coverage_max,
                    invisible_text_ratio=evidence.invisible_text_ratio,
                    text_char_count=evidence.text_char_count,
                    embedded_font_count=evidence.embedded_font_count,
                    font_count=evidence.font_count,
                    white_fill_text_ratio=evidence.white_fill_text_ratio,
                )
            )
            evidence = evidence.model_copy(update={"rule_fired": rule})

            pages.append(
                Page(
                    index=page.number + 1,
                    size_pt=[rect.width, rect.height],
                    rotation=int(page.rotation or 0),
                    kind=kind,  # type: ignore[arg-type]
                    usable=(kind == "digital_text"),
                    evidence=evidence,
                    notes_text=None,  # PDF 无演讲者备注
                    blocks=_build_blocks(page, raw_blocks, rect, images),
                    images=images,
                )
            )

        kinds = [p.kind for p in pages]
        verdict, reason, usable, unusable = document_verdict(kinds)
        meta = DocumentMeta(
            doc_id="sha256:" + hashlib.sha256(data).hexdigest(),
            source_format="pdf",
            filename=filename,
            byte_size=len(data),
            page_count=len(pages),
            producer=((doc.metadata or {}).get("producer") or "").strip() or None,
            parser=ParserInfo(
                service_version=SERVICE_VERSION,
                pymupdf=pymupdf.__version__,
                python_pptx=None,
                scan_ruleset_version=SCAN_RULESET_VERSION,
                adjacency_ruleset_version=ADJACENCY_RULESET_VERSION,
            ),
            usable_page_count=usable,
            unusable_page_count=unusable,
            unusable_pages=[
                UnusablePage(index=p.index, kind=p.kind) for p in pages if not p.usable
            ],
            verdict=verdict,  # type: ignore[arg-type]
            verdict_reason=reason,
        )
        return ParseResult(schema_version=SCHEMA_VERSION, document=meta, pages=pages)
    finally:
        doc.close()


def parse_pdf_bytes(data: bytes, filename: str) -> ParseResult:
    """``parse_pdf`` 的别名，保持与 PPTX 路径的命名对称。"""
    return parse_pdf(data, filename)
