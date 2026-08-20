"""维度 4 · 单段最长引用字数。

对应 PRD §8.1 的「引用长度」行，达标线 ≤ 100 字。

空文本与「有正文但无引用」必须分开：前者是无效样本，后者是 0 字、确实达标。
混同会让一次生成失败在聚合层显示为「全部达标」。

**这是合规约束，不是风格偏好**——它来自 PRD §12.1「溯源引用单段上限 100 字」，
所以在任何配置下都激活，且违规片段必须留证据：光有一个数字无法追责。

识别三类引用：markdown 引用块（连续 `>` 行合并为一段）、中文引号、英文双引号。
不做「与课件原文求最长公共子串」——那需要课件文本作为额外输入，超出本脚本的
输入范围，已记入 TODO。

决策日期：2026-08-19
"""

from __future__ import annotations

import re
from typing import Any

from ..config import StyleConfig
from ..glossary import Glossary
from ..result import DimensionResult, Reason
from ..text import BlockKind, Document, count_chars, strip_quote_markers

__all__ = ["NAME", "max_quote_length"]

NAME = "quote_length"

#: 成对引号。均要求闭合——未闭合的引号更可能是强调或笔误，不是引用。
_QUOTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"“([^”]{1,3000})”"),
    re.compile(r"「([^」]{1,3000})」"),
    re.compile(r"『([^』]{1,3000})』"),
    re.compile(r"\"([^\"\n]{1,3000})\""),
)


def max_quote_length(
    text: str, config: StyleConfig, glossary: Glossary | None = None
) -> DimensionResult:
    """找出最长的单段引用。常驻维度。"""
    limit = config.max_quote_chars
    target = f"≤ {limit} 字"

    doc = Document.parse(text)
    if doc.is_blank:
        # 「有正文但没有引用」= 0 字，确实达标（口径 D40）。
        # 「压根没有正文」不一样：那是无效样本。若在此返回 0/达标，一次生成
        # 失败会让本维度成为唯一计分项，聚合结果显示「全部达标」——空输出在
        # 漂移曲线上变成满分点。
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_TEXT, target)

    quotes: list[dict[str, Any]] = []

    for block in doc.blocks_of(BlockKind.QUOTE):
        n = count_chars(strip_quote_markers(block.masked)).total
        if n:
            quotes.append(
                {"kind": "blockquote", "line": block.line_no, "chars": n,
                 "preview": doc.preview(block.start, block.end, width=40)}
            )

    for pattern in _QUOTE_PATTERNS:
        for m in pattern.finditer(doc.masked):
            n = count_chars(m.group(1)).total
            if n:
                quotes.append(
                    {"kind": "inline_quote", "line": doc.line_of(m.start(1)), "chars": n,
                     "preview": doc.preview(m.start(1), m.end(1), width=40)}
                )

    quotes.sort(key=lambda q: -q["chars"])
    longest = quotes[0]["chars"] if quotes else 0

    return DimensionResult(
        name=NAME,
        activated=True,
        raw=longest,
        passed=longest <= limit,
        target=target,
        details={
            "longest": longest,
            "quote_count": len(quotes),
            "limit": limit,
            # 违规片段的取证记录，服务 PRD §12.1 的版权约束
            "violations": [q for q in quotes if q["chars"] > limit],
            "quotes": quotes[:20],
        },
    )
