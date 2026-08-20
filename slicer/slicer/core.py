"""切片：``ParseResult`` → ``SliceSet``。

**纯函数，无 I/O、无模型调用、不写盘、不缓存。** 服务侧的持久化一律走 Supabase，
本库不碰存储（架构决定，理由是合规：一键删除与注销即清空跨两个存储系统实现会有两处漏点）。

## 切片单位：页

一个可用页 = 一个切片。实测依据（2026-08-20，4 份 OCW 共 100 个可用页）：
每页字符 p50 = 260、p95 = 1125、**max = 1911，无一页超过 2000**。
幻灯片版面本身限制了单页信息量，页因此天然有界——
「切片多长」是**测量结果**而非需要现在拍板的**输入参数**。

不用固定 token 窗口：幻灯片文本非线性（标题/项目符号/图注在版面上并列而非连续），
拼成字符串再切会在任意位置断开，实测 37.9% 的块 ≤ 20 字符，切碎概率高；
且它要求现在就定窗口大小与重叠比例两个无依据的数，而这两个数取决于 2b 还没选的
embedding 供应商。不用块：块 p50 仅 30 字符，单个 bullet 脱离标题后无法讲解，
且向量库规模膨胀 7.4×（741 vs 100）。

## 本层不做的事

- **不做判定、不生成新标签**（`role` / `is_caption` 之类）。图注判定属第三步。
- **不产出文档主键**。见 ``schema.SliceManifest.content_sha256``。
- **不重新编号页**。跳过的页表现为 ``page_indices`` 的缺号。

决策日期：2026-08-20
"""

from __future__ import annotations

from parse_service.schema import Page, ParseResult, TextBlock

from .schema import (
    SLICE_RULESET_VERSION,
    SLICE_SCHEMA_VERSION,
    SLICER_VERSION,
    PageSignals,
    Slice,
    SliceImage,
    SliceManifest,
    SliceSet,
    SliceUnit,
    SkippedPage,
)

__all__ = ["slice_document", "SKIP_REASONS"]

#: 不可用页的说明文案。**给 F3 生成兜底话术用**，不是给日志看的。
#: 特别是 ``image_only``：它不是空页，是「内容全在图里」的页——
#: 兜底文案必须是「这部分在第 N 页的图里，我读不了图」，
#: 而不是 PRD §10 现有的「课件里没提到」（那句话是错的）。
SKIP_REASONS: dict[str, str] = {
    "scanned_ocr": "扫描件页，文字层是 OCR 噪声，不可用于讲解",
    "image_only": "内容全在图里，解析服务读不了图",
    "empty": "空白页，无可提取内容",
}

#: 块之间的拼接分隔符。用换行而非空格：幻灯片的块是版面上并列的独立条目，
#: 用空格拼会把「标题」和第一个 bullet 连成一句话。
_JOIN = "\n"


def _title_block(blocks: list[TextBlock]) -> TextBlock | None:
    """``size_rank`` 最高的块，并列时取最靠上者。

    **无阈值**：只需相对比较（哪个最大），不需要「够不够大才算标题」的绝对判断。
    实测在 22.081J 上给出的正是幻灯片标题（rank 0.94–0.97）。
    """
    ranked = [b for b in blocks if b.font.size_rank is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda b: (b.font.size_rank, -b.bbox_pt[1]))


def _build_slice(page: Page, ordinal: int) -> Slice:
    parts: list[str] = []
    units: list[SliceUnit] = []
    cursor = 0
    for block in page.blocks:
        start = cursor
        end = start + len(block.text)
        units.append(
            SliceUnit(
                block_id=block.block_id,
                page_index=block.page_index,
                order=block.order,
                text=block.text,
                text_span=[start, end],
                bbox_pt=list(block.bbox_pt),
                bbox_norm=list(block.bbox_norm),
                font=block.font,
                image_adjacency=block.image_adjacency,
            )
        )
        parts.append(block.text)
        cursor = end + len(_JOIN)

    text = _JOIN.join(parts)
    title = _title_block(page.blocks)

    return Slice(
        slice_id=f"s{ordinal:03d}",
        ordinal=ordinal,
        page_indices=[page.index],
        title=title.text if title else None,
        title_block_id=title.block_id if title else None,
        text=text,
        char_count=len(text),
        page_signals=[
            PageSignals(
                page_index=page.index,
                image_coverage_union=page.evidence.image_coverage_union,
                text_char_count=page.evidence.text_char_count,
            )
        ],
        units=units,
        images=[
            SliceImage(
                image_id=img.image_id,
                page_index=page.index,
                bbox_norm=list(img.bbox_norm),
                coverage=img.coverage,
            )
            for img in page.images
        ],
    )


def slice_document(parsed: ParseResult, split_threshold_chars: int | None = None) -> SliceSet:
    """把解析结果切成切片集合。

    ``split_threshold_chars`` 为超长页拆分阈值，**默认 None = 不拆**。
    它取决于 2b 选定的 embedding 供应商的上下文窗口，故作为注入参数而非写死的常量。
    当前语料 0 触发（可用页 max 1911 字符）。
    """
    if split_threshold_chars is not None and split_threshold_chars <= 0:
        raise ValueError("split_threshold_chars 必须为正整数或 None")

    slices: list[Slice] = []
    skipped: list[SkippedPage] = []

    for page in parsed.pages:
        if not page.usable:
            skipped.append(
                SkippedPage(
                    index=page.index,
                    kind=page.kind,
                    reason=SKIP_REASONS.get(page.kind, "不可用页"),
                    image_count=len(page.images),
                    image_coverage_union=page.evidence.image_coverage_union,
                )
            )
            continue
        if not page.blocks:
            # 可用页但无文本块：理论上不该出现（R3 会把它判成 empty），
            # 真出现了也不能产出空切片——空切片会污染检索
            skipped.append(
                SkippedPage(
                    index=page.index,
                    kind=page.kind,
                    reason="可用页但无文本块（异常，应回查解析）",
                    image_count=len(page.images),
                    image_coverage_union=page.evidence.image_coverage_union,
                )
            )
            continue
        slices.append(_build_slice(page, len(slices) + 1))

    # 邻接链：过短页不合并，检索层需要上下文时沿链自行扩展
    linked: list[Slice] = []
    for i, s in enumerate(slices):
        linked.append(
            s.model_copy(
                update={
                    "prev_slice_id": slices[i - 1].slice_id if i > 0 else None,
                    "next_slice_id": slices[i + 1].slice_id if i + 1 < len(slices) else None,
                }
            )
        )

    return SliceSet(
        schema_version=SLICE_SCHEMA_VERSION,
        manifest=SliceManifest(
            content_sha256=parsed.document.content_sha256,
            source_schema_version=parsed.schema_version,
            slicer_version=SLICER_VERSION,
            slice_ruleset_version=SLICE_RULESET_VERSION,
            page_count=parsed.document.page_count,
            sliced_page_count=len(linked),
            slice_count=len(linked),
            skipped_pages=skipped,
            split_threshold_chars=split_threshold_chars,
        ),
        slices=linked,
    )
