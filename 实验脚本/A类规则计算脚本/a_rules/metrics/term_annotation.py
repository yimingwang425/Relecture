"""维度 2 · 术语英文标注率。

对应 PRD §8.1 A 类表格的「术语处理」行，达标线 ≥ 90%。

**判定分两步，且第二步刻意不复用第一步的匹配器：**

1. 用术语表在掩码文本上找出术语出现在哪（`glossary.find`）。这一步受
   `match_anchor` 约束——自由文本里 `block` 有歧义，必须靠中文侧锚定。
2. 对每次出现，检查它后面是否紧跟一个括注，且括注内容确为另一侧的形式
   （`Term.is_en_form` / `is_zh_form`）。这一步**不受 anchor 约束**，因为
   括号内没有歧义。

第二步若复用第一步的匹配结果，`match_anchor=zh` 的术语将永远配不成对——
每次正确标注都被判成违规，是方向相反的系统性误判。

决策日期：2026-08-19
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..config import StyleConfig, TermCountMode, TermPolicy
from ..glossary import Glossary, MatchSide, Term, TermSource
from ..result import DimensionResult, Reason
from ..text import Document

__all__ = ["NAME", "term_annotation_rate"]

NAME = "term_annotation"

#: 括注的开闭符号。NFKC 已把全角 `（）［］` 归一为半角，
#: 这里补的是没有兼容分解的中文括号（`【】〔〕`）。
#: 不能写 `^`：`Pattern.match(s, pos)` 已在 pos 处锚定，而 `^` 只在真正的
#: 串首（或换行后）匹配，两者叠加会让 pos>0 的调用全部落空——表现为每一次
#: 正确标注都被判成违规，误判方向与真相相反。
_ANNOTATION_RE = re.compile(r"[ \t]*[(\[【〔]([^)\]】〕\n]{1,120})[)\]】〕]")


class Kind(str, Enum):
    """一次术语出现的形态。"""

    PAIRED = "paired"  # 译文 + 原文括注（任一顺序）
    LONE_ZH = "lone_zh"  # 只出现中文译名
    LONE_EN = "lone_en"  # 只出现英文原文


@dataclass(frozen=True, slots=True)
class Occurrence:
    term: Term
    kind: Kind
    start: int
    end: int
    text: str
    compliant: bool


def _find_annotation(text: str, match_end: int, term: Term, side: MatchSide) -> tuple[int, str] | None:
    """术语出现之后是否紧跟一个合格括注。

    返回 (括注结束位置, 括注内容)，不合格返回 None。
    """
    m = _ANNOTATION_RE.match(text, match_end)
    if not m:
        return None
    content = m.group(1).strip()
    if side is MatchSide.ZH and term.is_en_form(content):
        return m.end(), content
    if side is MatchSide.EN and term.is_zh_form(content):
        return m.end(), content
    return None


def _is_compliant(kind: Kind, policy: TermPolicy) -> bool:
    """三值策略各自认可哪种形态（PRD §8.1）。

    注意 `paired` 只满足 `translate_with_source`：它既翻译了（违反
    keep_original），又带了原文（违反 translate_only）。三者互斥是刻意的，
    否则「合规」的定义会随策略重叠，指标失去区分度。
    """
    if policy is TermPolicy.TRANSLATE_WITH_SOURCE:
        return kind is Kind.PAIRED
    if policy is TermPolicy.TRANSLATE_ONLY:
        return kind is Kind.LONE_ZH
    return kind is Kind.LONE_EN  # KEEP_ORIGINAL


def _collect(doc: Document, glossary: Glossary, policy: TermPolicy) -> list[Occurrence]:
    text = doc.masked
    occurrences: list[Occurrence] = []
    consumed_until = -1

    for m in glossary.find(text):
        if m.start < consumed_until:
            # 落在上一条括注内部——它是那次标注的一部分，不是独立的一次出现
            continue
        annotation = _find_annotation(text, m.end, m.term, m.side)
        if annotation is not None:
            end, _content = annotation
            kind, span_end = Kind.PAIRED, end
        else:
            kind = Kind.LONE_ZH if m.side is MatchSide.ZH else Kind.LONE_EN
            span_end = m.end
        consumed_until = span_end
        occurrences.append(
            Occurrence(
                term=m.term,
                kind=kind,
                start=m.start,
                end=span_end,
                text=doc.preview(m.start, span_end, width=40),
                compliant=_is_compliant(kind, policy),
            )
        )
    return occurrences


def _fold(occurrences: list[Occurrence], mode: TermCountMode) -> list[Occurrence]:
    """按计数口径折叠（口径 D02）。

    `first` 每个术语只看首次出现——标注只需首现一次，全量计数会惩罚正常写作。
    """
    if mode is TermCountMode.ALL:
        return occurrences
    seen: set[str] = set()
    kept: list[Occurrence] = []
    for occ in occurrences:
        if occ.term.en in seen:
            continue
        seen.add(occ.term.en)
        kept.append(occ)
    return kept


def _rate(occurrences: list[Occurrence]) -> float | None:
    if not occurrences:
        return None
    return sum(1 for o in occurrences if o.compliant) / len(occurrences)


def _coverage(glossary: Glossary, occurrences: list[Occurrence]) -> dict[str, Any]:
    """术语表在这段文本上的命中情况。

    这是「讲解文本相对术语表的分母缺口」，与「术语表相对课件的覆盖缺口」
    是两回事（口径 D29），故按 source 分列。命中率过低说明术语表选错了词，
    此时标注率再高也说明不了什么。
    """
    hit = {o.term.en for o in occurrences}
    out: dict[str, Any] = {}
    for label, source in (("body_text", TermSource.BODY_TEXT), ("figure_caption", TermSource.FIGURE_CAPTION)):
        total = len(glossary.by_source(source))
        n = sum(1 for t in glossary.by_source(source) if t.en in hit)
        out[label] = {"total": total, "hit": n, "rate": (n / total) if total else None}
    total = len(glossary)
    out["total"] = {"total": total, "hit": len(hit), "rate": (len(hit) / total) if total else None}
    return out


def term_annotation_rate(
    text: str, config: StyleConfig, glossary: Glossary | None = None
) -> DimensionResult:
    """计算术语英文标注率。

    `glossary` 为空或未提供时该维度不激活——PRD §8.1 的误差方向说明：
    术语表缺失只降低灵敏度，不造成误判。
    """
    target = f"≥ {config.term_annotation_threshold:.2f}（{config.term_policy.value}，{config.term_count_mode.value} 口径）"

    if not config.activates_term_policy:
        return DimensionResult.not_activated(NAME, target)
    if glossary is None or glossary.is_empty:
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_GLOSSARY, target)

    doc = Document.parse(text)
    base = {"glossary": glossary.stats()}
    if doc.is_blank:
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_TEXT, target, base)

    occurrences = _collect(doc, glossary, config.term_policy)
    first = _fold(occurrences, TermCountMode.FIRST)
    every = _fold(occurrences, TermCountMode.ALL)
    selected = first if config.term_count_mode is TermCountMode.FIRST else every

    details: dict[str, Any] = {
        **base,
        "policy": config.term_policy.value,
        "count_mode": config.term_count_mode.value,
        # 两种口径并行输出（口径 D02）：差值反映模型是否反复堆砌括注
        "rate_first": _rate(first),
        "rate_all": _rate(every),
        "occurrences_first": len(first),
        "occurrences_all": len(every),
        "coverage": _coverage(glossary, occurrences),
        "terms_without_zh": glossary.stats()["without_zh"],
        "kinds": {k.value: sum(1 for o in selected if o.kind is k) for k in Kind},
        "violations": [
            {
                "term": o.term.en,
                "preferred_zh": o.term.preferred_zh,
                "source": o.term.source.value,
                "kind": o.kind.value,
                "line": doc.line_of(o.start),
                "preview": o.text,
            }
            for o in selected
            if not o.compliant
        ],
    }

    if not selected:
        # 术语表非空但一条都没命中。0/0 既不是 0% 也不是 100%——
        # 标为无效，并让 coverage 把「为什么没命中」摆出来
        return DimensionResult.uncomputable(NAME, Reason.NO_TERM_OCCURRENCE, target, details)

    raw = _rate(selected)
    assert raw is not None
    return DimensionResult(
        name=NAME,
        activated=True,
        raw=raw,
        passed=raw >= config.term_annotation_threshold,
        target=target,
        details=details,
    )
