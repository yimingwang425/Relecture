"""PRD §8.2 漂移衰减曲线出图。

四条约定，都是为了不让图比数据更好看：

1. **空点显式断开，不插值。** 第 4–7、13 轮术语维度没有观测，连线会凭空
   造出一段并不存在的趋势。用 NaN 断开，并在断口处标注「无观测」。
2. **误差带用全距（min–max）**，与 aggregate.py 一致。n=3 时标准差不稳定。
3. **低分母点标 ⚠**，读图的人须知道哪些点该少信几分。
4. **不做跨维度归一化，四张图各自纵轴**。四个维度量纲不同，合成一张图或
   统一纵轴都会制造虚假的可比性。

决策日期：2026-08-20
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .aggregate import DIMENSIONS, PRODUCTION_TERM_MIN_OCCURRENCES, Point, aggregate

__all__ = ["plot_run"]

_TITLES = {
    "chinese_ratio": ("中文字符占比", "汉字 / (汉字 + 英文词)"),
    "term_annotation": ("术语英文标注率", "合规首现 / 首现总数"),
    "paragraph_length": ("平均段落字数", "字"),
    "quote_length": ("单段最长引用字数", "字"),
}
_STYLE = {
    "with_review": ("有审查", "#1f6feb"),
    "without_review": ("无审查", "#d1242f"),
}


def _series(points: list[Point], condition: str, dim: str, rounds: list[int]) -> dict[str, list]:
    idx = {p.round_index: p for p in points if p.condition == condition and p.dimension == dim}
    nan = float("nan")
    out: dict[str, list] = {"mean": [], "low": [], "high": [], "flag": [], "missing": []}
    for r in rounds:
        p = idx.get(r)
        if p is None or p.mean is None:
            out["mean"].append(nan); out["low"].append(nan); out["high"].append(nan)
            out["flag"].append(False); out["missing"].append(r)
        else:
            out["mean"].append(p.mean); out["low"].append(p.low); out["high"].append(p.high)
            out["flag"].append(p.low_denominator)
    return out


def plot_run(run_dir: Path, target: dict[str, Any] | None = None) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for candidate in ("PingFang SC", "Hiragino Sans GB", "Heiti SC", "Arial Unicode MS"):
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate]
            break
    plt.rcParams["axes.unicode_minus"] = False

    records = [
        json.loads(l)
        for l in (run_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
        if l
    ]
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    points = aggregate(records)
    rounds = sorted({p.round_index for p in points})

    out_dir = run_dir / "figures"
    out_dir.mkdir(exist_ok=True)
    written: list[Path] = []

    for dim in DIMENSIONS:
        title, ylabel = _TITLES[dim]
        fig, ax = plt.subplots(figsize=(9, 4.6))

        for condition, (label, color) in _STYLE.items():
            s = _series(points, condition, dim, rounds)
            ax.plot(rounds, s["mean"], "-o", color=color, label=label, markersize=4, linewidth=1.8)
            ax.fill_between(rounds, s["low"], s["high"], color=color, alpha=0.15, linewidth=0)
            flagged = [(r, m) for r, m, f in zip(rounds, s["mean"], s["flag"]) if f]
            if flagged:
                ax.scatter(
                    [r for r, _ in flagged], [m for _, m in flagged],
                    marker="v", s=46, facecolors="none", edgecolors=color, zorder=5,
                )

        missing = sorted(
            set(_series(points, "with_review", dim, rounds)["missing"])
            & set(_series(points, "without_review", dim, rounds)["missing"])
        )
        for r in missing:
            ax.axvspan(r - 0.42, r + 0.42, color="#999999", alpha=0.13, linewidth=0)
        note = (
            f"灰带 = 无观测（第 {'、'.join(map(str, missing))} 轮），曲线在此断开，未做插值"
            if missing else ""
        )

        if target and dim in target:
            ax.axhline(target[dim], linestyle="--", linewidth=1, color="#888888")
            ax.text(rounds[-1], target[dim], f" 达标线 {target[dim]:g}",
                    va="bottom", ha="right", fontsize=8, color="#666666")

        # 说明文字放标题下方，避免压住曲线
        ax.set_title(
            f"{title} · 漂移衰减曲线（均值 + 全距，{meta.get('repeats', '?')} 条独立轨迹）"
            + (f"\n{note}" if note else ""),
            fontsize=12, linespacing=1.6,
        )
        ax.set_xlabel("对话轮次")
        ax.set_ylabel(ylabel)
        ax.set_xticks(rounds)
        ax.grid(alpha=0.25, linewidth=0.6)
        ax.legend(loc="best", frameon=False)
        fig.text(
            0.01, 0.005,
            f"▽ = 任一轨迹分母低于生产护栏 {PRODUCTION_TERM_MIN_OCCURRENCES}，该点可信度低"
            f"    |    {meta['model']['name']} · effort={meta['model']['params'].get('effort')}"
            f" · 配置 {meta['config_fingerprint']} · 术语表 v{meta.get('glossary_version')}",
            fontsize=7, color="#777777",
        )
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        path = out_dir / f"{dim}.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        written.append(path)

    (out_dir / "plot_command.txt").write_text(
        "本目录的图由以下命令生成（脚本本体：drift/plot.py，随仓库版本化）：\n"
        f"    python -m drift plot --run {run_dir.name}\n\n"
        f"数据源：rounds.jsonl（{len(records)} 条记录）\n"
        f"配置指纹：{meta['config_fingerprint']}   术语表：v{meta.get('glossary_version')}\n",
        encoding="utf-8",
    )
    return written
