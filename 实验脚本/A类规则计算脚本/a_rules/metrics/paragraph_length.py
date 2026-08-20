"""维度 3 · 平均段落字数。

对应 PRD §8.1 的「详细程度」行，达标线「按配置区间」。

段落口径见 README D09：空行分割；**连续列表项合并为一段**（逐条 bullet 计段
会把均值人为拉低）；标题、表格、代码块不计入——标题是结构不是散文，表格是
数据不是散文。

`raw` 只报均值，但 `details` 给出完整分布。§8.2 画漂移曲线时需要的是分布而
不只是均值：均值不变而方差扩大，同样是风格失控。

决策日期：2026-08-19
"""

from __future__ import annotations

from statistics import median
from typing import Any

from ..config import StyleConfig
from ..glossary import Glossary
from ..result import DimensionResult, Reason
from ..text import Document

__all__ = ["NAME", "avg_paragraph_length"]

NAME = "paragraph_length"


def avg_paragraph_length(
    text: str, config: StyleConfig, glossary: Glossary | None = None
) -> DimensionResult:
    """计算平均段落字数。常驻维度，任何配置下都激活。"""
    rng = config.paragraph_length
    target = rng.describe()

    doc = Document.parse(text)
    blocks = [b for b in doc.paragraph_blocks() if b.count().total > 0]
    if not blocks:
        # 只有标题/代码/表格的输出算不出段落均值，标为无效而非 0
        return DimensionResult.uncomputable(NAME, Reason.NO_PARAGRAPH, target)

    lengths = [b.count().total for b in blocks]
    mean = sum(lengths) / len(lengths)

    return DimensionResult(
        name=NAME,
        activated=True,
        raw=mean,
        passed=rng.contains(mean),
        target=target,
        details={
            "mean": mean,
            "median": median(lengths),
            "min": min(lengths),
            "max": max(lengths),
            "count": len(lengths),
            "total_chars": sum(lengths),
            "lengths": lengths,
            "out_of_range": [
                {"line": b.line_no, "chars": n, "preview": doc.preview(b.start, b.end)}
                for b, n in zip(blocks, lengths)
                if not rng.contains(n)
            ],
        },
    )
