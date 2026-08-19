"""维度 1 · 中文字符占比。

对应 PRD §8.1 的「讲解语言一致性」行，达标线「按配置，±5%」。

分母是这个维度唯一有争议的地方，两条决策：

- **英文按词计，不按字母计**（口径 D01）。按字母计会让 `gradient descent`
  顶 16 个汉字的量，占比随中英混合比例剧烈抖动，与 §8.2 要观察的漂移信号混淆。
- **排除术语括注里的英文原文**（口径 D15）。`translate_with_source` 下用户
  **要求**模型写「热机（heat engine）」；若把括注英文计入分母，满足术语维度
  就必然拉低本维度——两个维度直接打架。未排除口径同时输出，供对照。

决策日期：2026-08-19
"""

from __future__ import annotations

from typing import Any

from ..config import StyleConfig
from ..glossary import Glossary
from ..result import DimensionResult, Reason
from ..text import MASK, Document, count_chars
from .term_annotation import annotated_english_spans

__all__ = ["NAME", "chinese_ratio"]

NAME = "chinese_ratio"


def _mask_spans(masked: str, spans: tuple[tuple[int, int], ...]) -> str:
    if not spans:
        return masked
    buf = list(masked)
    for start, end in spans:
        for k in range(start, end):
            if buf[k] != "\n":
                buf[k] = MASK
    return "".join(buf)


def _countable(doc: Document, masked: str) -> str:
    return "\n".join(masked[b.start : b.end] for b in doc.countable_blocks())


def chinese_ratio(
    text: str, config: StyleConfig, glossary: Glossary | None = None
) -> DimensionResult:
    """计算汉字在可计量正文中的占比。"""
    band = config.chinese_ratio
    target = f"{band.describe()}（{band.low:.2f}–{band.high:.2f}）"

    if not config.activates_chinese_ratio:
        return DimensionResult.not_activated(NAME, target)

    doc = Document.parse(text)
    if doc.is_blank:
        # 空文本返回 0.0 会被读成「纯英文，严重不达标」——误差方向危险（口径 D03）
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_TEXT, target)

    including = count_chars(_countable(doc, doc.masked))
    spans = annotated_english_spans(doc, glossary) if config.exclude_annotated_terms else ()
    excluding = count_chars(_countable(doc, _mask_spans(doc.masked, spans)))

    def ratio(c) -> float | None:
        base = c.han + c.latin_words
        return (c.han / base) if base else None

    raw = ratio(excluding)
    if raw is None:
        # 只有数字，没有任何汉字或英文词——占比无定义
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_TEXT, target)

    return DimensionResult(
        name=NAME,
        activated=True,
        raw=raw,
        passed=band.contains(raw),
        target=target,
        details={
            "han": excluding.han,
            "latin_words": excluding.latin_words,
            "digit_groups": excluding.digit_groups,
            "excluded_annotation_words": including.latin_words - excluding.latin_words,
            "ratio_excluding_annotations": raw,
            "ratio_including_annotations": ratio(including),
            "exclude_annotated_terms": config.exclude_annotated_terms,
            "target_low": band.low,
            "target_high": band.high,
        },
    )
