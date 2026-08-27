"""P4 的三口径并列重算。**用本批次自己的 transcripts，不引用旧样本。**

括注排除有三种口径，在 0.95 阈值附近可能给出不同答案：

==============  ==================================================
无排除          `exclude_annotated_terms=False`
仅术语表（A）    D15 原实现，需要人工术语表
形态规则（B）    D48，不依赖术语表 —— **路径上采用的就是这个**
==============  ==================================================

B 是三者中最宽松的，所以 P4 的触发率会偏低。**这不是退回 A 的理由**
（A 靠 22.081J 恰好有人工术语表，而 v1 多课程没有，是观测/应用范围错配），
而是**报告必须三口径并列**的理由。

## 为什么必须用本批次重算

第 0 周那六份样本测自 `term_annotation` 激活、prompt 明确要求写括注的配置。
**v1 的 prompt 里没有任何一句让模型写括注。** 若模型因此几乎不写，
三种口径就没有分歧，P4 根本不口径敏感——这个话题自己关掉。

拿旧配置下的敏感性分析解释新配置下的结果，是同一类错误的又一种形态。
`annotation_count` 就是用来先回答「模型到底还写不写括注」的。

决策日期：2026-08-22
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import _paths  # noqa: F401
from a_rules.config import StyleConfig
from a_rules.glossary import Glossary

__all__ = ["CaliperRow", "recompute_calipers", "format_calipers"]


@dataclass(frozen=True, slots=True)
class CaliperRow:
    caliper: str
    n: int
    mean_ratio: float | None
    min_ratio: float | None
    below_threshold: int
    threshold: float


def _chinese_ratio_module():
    # a_rules.metrics 的 __init__ 导出了同名函数，遮蔽了模块名；取 sys.modules
    import a_rules.metrics.chinese_ratio  # noqa: F401

    return sys.modules["a_rules.metrics.chinese_ratio"]


def recompute_calipers(
    texts: Iterable[str], threshold: float, glossary_path: Path | None = None
) -> list[CaliperRow]:
    cr = _chinese_ratio_module()
    texts = [t for t in texts if t.strip()]
    cfg = StyleConfig.from_dict(
        {"explain_language": "zh", "source_language": "en", "chinese_ratio": {"min": threshold}}
    )
    no_exclude = StyleConfig.from_dict(
        {
            "explain_language": "zh",
            "source_language": "en",
            "chinese_ratio": {"min": threshold},
            "exclude_annotated_terms": False,
        }
    )
    gl = Glossary.from_file(glossary_path) if glossary_path and glossary_path.is_file() else None
    original = cr.parenthetical_english_spans

    def collect(compute) -> tuple[list[float], int]:
        vals, below = [], 0
        for t in texts:
            r = compute(t)
            if r.raw is None:
                continue
            vals.append(r.raw)
            below += int(r.passed is False)
        return vals, below

    rows: list[CaliperRow] = []
    try:
        # 无排除
        vals, below = collect(lambda t: cr.chinese_ratio(t, no_exclude, None))
        rows.append(_row("无排除", vals, below, threshold))

        # 仅术语表（A）——把形态匹配器换成平凡实现
        cr.parenthetical_english_spans = lambda doc: ()
        vals, below = collect(lambda t: cr.chinese_ratio(t, cfg, gl))
        rows.append(_row("仅术语表 A" + ("" if gl else "（无术语表，退化为无排除）"),
                         vals, below, threshold))
    finally:
        cr.parenthetical_english_spans = original

    # 形态规则（B）—— 路径上实际采用的
    vals, below = collect(lambda t: cr.chinese_ratio(t, cfg, None))
    rows.append(_row("形态规则 B（采用）", vals, below, threshold))
    return rows


def _row(name: str, vals: list[float], below: int, threshold: float) -> CaliperRow:
    return CaliperRow(
        caliper=name,
        n=len(vals),
        mean_ratio=(sum(vals) / len(vals)) if vals else None,
        min_ratio=min(vals) if vals else None,
        below_threshold=below,
        threshold=threshold,
    )


def format_calipers(rows: list[CaliperRow], annotation_stats: dict[str, Any]) -> str:
    lines = [
        "# P4 的三口径并列（用本批次 transcripts 重算，不引用第 0 周样本）",
        "",
        f"括注密度：每轮平均 {annotation_stats.get('mean', 0):.2f} 处、"
        f"合计 {annotation_stats.get('total', 0)} 处、"
        f"有括注的轮次 {annotation_stats.get('rounds_with', 0)}/{annotation_stats.get('rounds', 0)}",
        "",
        f"{'口径':<28}{'n':>5}{'均值':>10}{'最低':>10}{'不达标':>8}",
    ]
    for r in rows:
        if r.mean_ratio is None:
            lines.append(f"{r.caliper:<28}{r.n:>5}{'—':>10}{'—':>10}{'—':>8}")
            continue
        lines.append(
            f"{r.caliper:<28}{r.n:>5}{r.mean_ratio:>10.4f}{r.min_ratio:>10.4f}"
            f"{r.below_threshold:>8}"
        )
    distinct = len({r.below_threshold for r in rows if r.mean_ratio is not None})
    lines += [
        "",
        (
            "三种口径给出**相同**的不达标数 → **P4 不口径敏感**，这个话题关闭。"
            if distinct == 1
            else "三种口径给出**不同**的不达标数 → P4 口径敏感，报告须三口径并列。"
        ),
    ]
    if annotation_stats.get("total", 0) == 0:
        lines.append(
            "括注密度为 0：v1 的 prompt 不再要求写括注，模型也确实不写。"
            "第 0 周那张表测的是 D02 期 prompt 下的括注密度，**与本批次无关**。"
        )
    return "\n".join(lines)
