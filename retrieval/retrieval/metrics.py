"""召回指标。

## 三个指标并列，不合并

============================  ==============================================
``full-hit@k``                top-k 覆盖**全部** gold 页 → 1，否则 0
``page-recall@k``             |命中 ∩ gold| / |gold|，逐题算再平均
``first-hit-rank``            第一个 gold 页出现的名次，未命中单独计数
============================  ==============================================

**必须三个都报。** 9 条 ``multipage`` 题上，「2 页中命中 1 页」在
``page-recall`` 上是 0.5、在 ``full-hit`` 上是 0——只看任何一个都会漏掉另一半信息。
这与 a_rules 的 D17（不输出混合总分）是同一条纪律。

## ``unreadable_page`` 的上限恒为 0

其 gold 页是 ``image_only``，**不产生切片**、不在索引里，
``page-recall`` 在结构上恒为 0。报告必须显式标注，
否则会被读成「模型不行」，进而有人去换模型试图提升它——
**它测的不是检索质量，是「兜底文案必须存在」这件事**。

## ``no_answer`` 不拟合阈值

n=5 拟合出来的阈值必然过拟合，还会显得有依据。
本模块只输出两组 top-1 相似度的**分布与间隔**，由报告层如实呈现，
包括「无间隔」这个结果——那是结论，不是待办。

决策日期：2026-08-21
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

__all__ = ["QuestionResult", "TagSummary", "Separability", "score_question", "summarize", "separability"]

Page = tuple[str, int]


@dataclass(frozen=True, slots=True)
class QuestionResult:
    qid: str
    tags: tuple[str, ...]
    gold: tuple[Page, ...]
    retrieved: tuple[Page, ...]
    top_score: float | None

    @property
    def is_no_answer(self) -> bool:
        return not self.gold

    @property
    def full_hit(self) -> bool:
        return bool(self.gold) and set(self.gold) <= set(self.retrieved)

    @property
    def page_recall(self) -> float:
        if not self.gold:
            return float("nan")
        return len(set(self.gold) & set(self.retrieved)) / len(self.gold)

    @property
    def first_hit_rank(self) -> int | None:
        """1-based 名次；未命中返回 ``None``。"""
        for i, p in enumerate(self.retrieved, 1):
            if p in set(self.gold):
                return i
        return None


def score_question(qid, tags, gold, retrieved, top_score) -> QuestionResult:
    return QuestionResult(
        qid=qid,
        tags=tuple(tags),
        gold=tuple(gold),
        retrieved=tuple(retrieved),
        top_score=top_score,
    )


@dataclass(slots=True)
class TagSummary:
    tag: str
    n: int = 0
    full_hits: int = 0
    recalls: list[float] = field(default_factory=list)
    ranks: list[int] = field(default_factory=list)
    missed: int = 0
    ceiling_zero: bool = False  # gold 页不产生切片，上限恒为 0

    @property
    def page_recall(self) -> float:
        return mean(self.recalls) if self.recalls else 0.0

    @property
    def mean_rank(self) -> float | None:
        return mean(self.ranks) if self.ranks else None


def summarize(
    results: list[QuestionResult], zero_ceiling_tags: frozenset[str] = frozenset()
) -> dict[str, TagSummary]:
    """按 tag 汇总。一题多 tag 时**在每个 tag 下各计一次**——

    这不是重复计数：tag 分组回答的是「这类问题表现如何」，
    一道既 ``synonym`` 又 ``multipage`` 的题在两个问题下都应当被计入。
    因此各组之和大于题数，报告里不得把各组相加当总数。
    """
    out: dict[str, TagSummary] = {}
    for r in results:
        if r.is_no_answer:
            for t in r.tags:
                out.setdefault(t, TagSummary(t)).n += 1
            continue
        for t in r.tags:
            s = out.setdefault(t, TagSummary(t))
            s.n += 1
            s.full_hits += int(r.full_hit)
            s.recalls.append(r.page_recall)
            rank = r.first_hit_rank
            if rank is None:
                s.missed += 1
            else:
                s.ranks.append(rank)
            if t in zero_ceiling_tags:
                s.ceiling_zero = True
    return out


@dataclass(frozen=True, slots=True)
class Separability:
    """``no_answer`` 与有答案两组 top-1 相似度的可分性。"""

    with_gold: tuple[float, ...]
    no_answer: tuple[float, ...]

    @property
    def gap(self) -> float | None:
        """``no_answer`` 的最高分 与 有答案组的最低分 之差。正数表示存在间隔。"""
        if not self.with_gold or not self.no_answer:
            return None
        return min(self.with_gold) - max(self.no_answer)

    @property
    def separable(self) -> bool:
        g = self.gap
        return g is not None and g > 0

    @property
    def interval(self) -> tuple[float, float] | None:
        """可分时，τ 的候选区间 ``(no_answer 最高分, 有答案最低分)``。"""
        if not self.separable:
            return None
        return (max(self.no_answer), min(self.with_gold))


def separability(results: list[QuestionResult]) -> Separability:
    return Separability(
        with_gold=tuple(
            r.top_score for r in results if not r.is_no_answer and r.top_score is not None
        ),
        no_answer=tuple(
            r.top_score for r in results if r.is_no_answer and r.top_score is not None
        ),
    )
