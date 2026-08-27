"""报告。**按臂分开报，不给单一总分。**

两个语义不得混报（口径 E07 / E07a）：

- 扫描臂的触发率是**压力测试上界**（提问序列经过构造）
- **真实使用率与单会话成本只能从成本臂出**（第 0 周原序列，未经构造）

同 a_rules 的 D17 与 retrieval 的 R14：不合并，因为合并会掩盖结构性差异。

决策日期：2026-08-22
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

from .points import AXIS_POINTS, POINTS
from .readout import (
    band_of,
    decompose_paragraph_drop,
    monotone_verdict,
    segmentation_verdict,
)

__all__ = ["format_scan_table", "format_readout", "trigger_rates_by_replicate"]


def _rate(rows: Sequence[dict[str, Any]]) -> float | None:
    return (sum(int(r["triggered_regeneration"]) for r in rows) / len(rows)) if rows else None


def trigger_rates_by_replicate(
    records: Iterable[dict[str, Any]], arm: str = "scan"
) -> tuple[list[list[float | None]], list[str]]:
    """按 (轨迹, 轴上位置) 排出触发率矩阵，供主判据使用。

    只取有审查组——无审查组不重生成，触发率恒为 0，混进来会把单调性冲平。
    """
    by: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r.get("arm") != arm or r["condition"] != "with_review":
            continue
        by[(r["replicate"], r["config_point_id"])].append(r)

    point_ids = [p.id for p in AXIS_POINTS]
    replicates = sorted({rep for rep, _ in by})
    matrix = [[_rate(by.get((rep, pid), [])) for pid in point_ids] for rep in replicates]
    return matrix, point_ids


def format_scan_table(summary: dict[str, Any], arm: str = "scan") -> str:
    lines = [
        f"# {arm} 臂 · 触发率 / 达标率 / 每轮成本",
        f"{'点':<5}{'条件':<16}{'轮次':>5}{'触发率':>10}{'判读带':>10}"
        f"{'达标率':>10}{'每轮$':>10}{'均字数':>9}{'均段数':>8}",
    ]
    for point in POINTS:
        for condition in ("with_review", "without_review"):
            row = summary.get(f"{arm}/{point.id}/{condition}")
            if row is None:
                continue
            band = band_of(row["trigger_rate"]) if condition == "with_review" else "—"
            lines.append(
                f"{point.id:<5}{condition:<16}{row['rounds']:>5}"
                f"{row['trigger_rate']:>10.3f}{band:>10}"
                f"{row['pass_rate']:>10.3f}{row['cost_per_round']:>10.4f}"
                f"{row['mean_total_chars']:>9.0f}{row['mean_paragraph_count']:>8.1f}"
            )
    lines.append("")
    if arm == "cost":
        w = summary.get(f"{arm}/P0/with_review")
        n = summary.get(f"{arm}/P0/without_review")
        if w and n and n["cost_usd"]:
            ratio = w["cost_usd"] / n["cost_usd"]
            lines.append(
                f"单会话（15 轮）：有审查 ${w['cost_usd']:.4f} / 无审查 ${n['cost_usd']:.4f}"
                f"　→　{ratio:.2f}×"
            )
            if (w["trigger_rate"] or 0) == 0:
                lines.append(
                    f"⚠️ **{ratio:.2f}× 不可区分于零。** 本臂 15 轮触发率 0.000，"
                    "没有任何一次重生成，倍数**全部来自两组输出长度的随机差异**，"
                    "且 n=1 轨。说「审查带来 " + f"{ratio:.2f}" + "× 成本」是过度解读——"
                    "第 0 周的 2.55× 有 28 次重生成撑着，这个数什么都没有。"
                )
    else:
        lines.append("⚠️ 扫描臂的提问序列经过构造（诱发引用 / 压长度），"
                     "其触发率是**压力测试上界**，不是真实使用率（口径 E07a）。")
    return "\n".join(lines)


def format_readout(records: Iterable[dict[str, Any]], summary: dict[str, Any]) -> str:
    records = list(records)
    matrix, point_ids = trigger_rates_by_replicate(records)
    main = monotone_verdict(matrix, point_ids)

    lines = [
        "# 判读（规则跑前已定死，见 scan/readout.py）",
        "",
        f"## 主判据 · 跨点单调性（{'→'.join(point_ids)}）",
        f"结论：**{main.verdict}**",
        main.detail,
        f"逐轨触发率：{matrix}",
        "",
        "## 辅助 · 判读带（只用来定位拐点）",
    ]
    for pid in point_ids + ["P4"]:
        row = summary.get(f"scan/{pid}/with_review")
        if row:
            lines.append(f"  {pid}: 触发率 {row['trigger_rate']:.3f} → {band_of(row['trigger_rate'])}")

    base = summary.get("scan/P0/with_review")
    extreme = summary.get("scan/P3/with_review")
    if base and extreme:
        seg = segmentation_verdict(base, extreme)
        lines += [
            "",
            "## E10 · 分段规避",
            f"结论：**{seg.verdict}**",
            seg.detail,
            f"证据：总字数比 {seg.evidence.get('total_chars_ratio'):.2f}、"
            f"段数比 {seg.evidence.get('paragraph_count_ratio'):.2f}",
            "",
            "### 分解（恒等式 `字/段 = 总字数 ÷ 段数`，无阈值、无自由参数）",
        ]
        stats = {
            p.id: summary[f"scan/{p.id}/with_review"]
            for p in AXIS_POINTS
            if f"scan/{p.id}/with_review" in summary
        }
        for d in decompose_paragraph_drop(base, stats):
            lines.append("  " + d.formula())
        lines += [
            "",
            "**E10 判为「确实简洁」是阈值技术性的结果，分解显示主要机制是分段。**",
            "三分类保留原样——分解不是新判据，它只说明那个结论由哪一半驱动。",
            "产品结论不变：PRD §8.1 的「详细程度 = 平均段落字数、**总输出长度**」"
            "只实现了前一半，这就是缺口的来源，补进 v1.1 待办。",
        ]
    return "\n".join(lines)
