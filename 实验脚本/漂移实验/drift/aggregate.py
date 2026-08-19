"""跨 replicate 聚合：均值 + 误差带。

**为什么必须重复**：当前模型已移除 temperature，采样随机性无法消除。
每条件每轮 n=1 时无法区分漂移与噪音——六轮探路数据的 53.6 → 75.0 → 23.1
→ 0.0 → 56.2 → 20.0 就是这个问题的预演。

**误差带取全距（min–max）而非 ±1SD。** n=3 时样本标准差本身极不稳定，
用 ±1SD 作图会暗示一个并不存在的分布假设。全距是 n=3 下诚实的表达。
标准差仍然输出，但只作参考量，不用于作图。

决策日期：2026-08-19
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any, Iterable, Sequence

__all__ = ["Point", "aggregate", "format_table"]

DIMENSIONS = ("chinese_ratio", "term_annotation", "paragraph_length", "quote_length")


@dataclass(frozen=True, slots=True)
class Point:
    condition: str
    round_index: int
    dimension: str
    values: tuple[float, ...]
    n_replicates: int
    n_passed: int
    n_uncomputable: int

    @property
    def mean(self) -> float | None:
        return mean(self.values) if self.values else None

    @property
    def low(self) -> float | None:
        return min(self.values) if self.values else None

    @property
    def high(self) -> float | None:
        return max(self.values) if self.values else None

    @property
    def spread(self) -> float | None:
        """全距。误差带用它作图。"""
        return (self.high - self.low) if self.values else None

    @property
    def stdev(self) -> float | None:
        """总体标准差，仅供参考——n=3 时不稳定，不用于作图。"""
        return pstdev(self.values) if len(self.values) > 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "round": self.round_index,
            "dimension": self.dimension,
            "mean": self.mean,
            "band_low": self.low,
            "band_high": self.high,
            "spread": self.spread,
            "stdev_reference_only": self.stdev,
            "n_replicates": self.n_replicates,
            "n_valid": len(self.values),
            "n_uncomputable": self.n_uncomputable,
            "n_passed": self.n_passed,
            "pass_rate": (self.n_passed / self.n_replicates) if self.n_replicates else None,
        }


def aggregate(records: Iterable[dict[str, Any]]) -> list[Point]:
    """把 rounds.jsonl 的记录按 (condition, round, dimension) 聚合。"""
    buckets: dict[tuple[str, int, str], list[tuple[float | None, bool]]] = {}
    for r in records:
        dims = (r.get("final_evaluation") or {}).get("dimensions", {})
        for name in DIMENSIONS:
            d = dims.get(name)
            if d is None or not d.get("activated"):
                continue
            key = (r["condition"], r["round"], name)
            buckets.setdefault(key, []).append((d.get("raw"), bool(d.get("passed"))))

    points: list[Point] = []
    for (condition, rnd, name), rows in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        values = tuple(v for v, _ in rows if v is not None)
        points.append(
            Point(
                condition=condition,
                round_index=rnd,
                dimension=name,
                values=values,
                n_replicates=len(rows),
                n_passed=sum(1 for _, ok in rows if ok),
                n_uncomputable=sum(1 for v, _ in rows if v is None),
            )
        )
    return points


def format_table(points: Sequence[Point], dimension: str) -> str:
    """一个维度的对照表：每行一轮，两组并列，各带均值与全距。"""
    rounds = sorted({p.round_index for p in points if p.dimension == dimension})
    index = {(p.condition, p.round_index): p for p in points if p.dimension == dimension}
    lines = [
        f"# {dimension}（均值 [全距]，n = 每格的 replicate 数）",
        f"{'轮次':<6}{'有审查':>30}{'无审查':>30}",
        "        均值 [全距] 有效数/重复数 达标数",
    ]
    for rnd in rounds:
        cells = []
        for cond in ("with_review", "without_review"):
            p = index.get((cond, rnd))
            if p is None or p.mean is None:
                cells.append(f"{'—':>30}")
            else:
                # 有效数必须显示：某些 replicate 可能是 low_confidence（raw=None），
                # 只写 n_passed/n_replicates 会让 1 个观测看起来像 3 个
                cells.append(
                    f"{p.mean:.3g} [{p.low:.3g}–{p.high:.3g}] "
                    f"n={len(p.values)}/{p.n_replicates} {p.n_passed}✓".rjust(30)
                )
        lines.append(f"{rnd:<6}" + "".join(cells))
    return "\n".join(lines)
