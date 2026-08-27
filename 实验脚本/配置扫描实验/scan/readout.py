"""判读规则。**跑之前定死，写在代码里而不是写在脑子里。**

事后挑一个看起来合适的判据，等于没有判据（通则一：不能让测量结果反向定义标准）。

## 主判据（E05）· 跨点单调性

**触发率沿 P0→P1→P2→P3 单调不减，且该方向在 ≥2 条轨迹上重复。**

为什么不是逐点分类：n=12 时单点 95% CI 宽达 ±25pp，1/12 与 6/12 的区间**重叠**。
逐点分类最可能的结局是 P2/P3 双双落进中间带——**恰好在最关心的两个点上，
实验宣布自己没结论**。

而 P0→P3 是一条单调收紧的轴，独立条件有两组（**轨迹 × 轴上位置**），
这正是 R17「差异必须在多个独立条件下同向重复」的原意。

## 辅助判据（E05a）· 判读带

只用来**定位拐点落在哪两点之间**，不再单独下结论。

## E05b · P4 不在轴上

语言轴的孤点，与详细程度轴无序关系，按判读带单独判。

## E10 · 分段规避

若触发率近零、**总字数基本不变而段数显著上升**，
结论不得写成「模型能遵守极端配置」，须写成
「**本指标在偏短方向可被分段平凡满足**」。

## E10 的分解：一个除法，不是新判据

E10 是互斥三分类，它报不出「部分简洁 + 部分分段」这种混合形态。
但**不需要新判据也能说清楚**，因为被测量本身就是一个除法：

    平均段落字数 = 总字数 ÷ 段数

两边取对数，变化量**恰好可加**：

    ln(字/段 之比) = ln(总字数之比) − ln(段数之比)

于是「达标里有多少来自真的讲短、多少来自多敲回车」是一个**恒等式的分解**，
**没有阈值、没有占位待定项、没有自由参数**。实测（阶段 1，P3 相对 P0）：

===============  ========  ==========================
总字数之比        0.795     贡献 **24%**（真的讲短）
段数之比          2.09      贡献 **76%**（分段）
字/段之比         0.380     = 0.795 ÷ 2.09
===============  ========  ==========================

**报告写法**（评审指定 2026-08-23）：E10 的三分类**保留原样**，
分解数摆在它旁边，并写明——**E10 判为「确实简洁」是阈值技术性的结果，
分解显示主要机制是分段。**

产品结论照旧：PRD §8.1 的「详细程度 = 平均段落字数、**总输出长度**」
只实现了前一半，这就是缺口的来源，补进 **v1.1** 待办。

决策日期：2026-08-22（分解 2026-08-23 追加）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

__all__ = [
    "BANDS", "band_of", "monotone_verdict", "segmentation_verdict",
    "decompose_paragraph_drop", "Readout",
]

#: 判读带（E05a）。分母是单点的有审查轮次数。
NEAR_ZERO_MAX = 1 / 12
HIGH_MIN = 6 / 12

BANDS = (
    ("近零", 0.0, NEAR_ZERO_MAX),
    ("不可分辨", NEAR_ZERO_MAX, HIGH_MIN),
    ("高", HIGH_MIN, 1.0),
)

#: 浮点边界补偿，同 a_rules 的 D21。恰好落在边界上的点必须归入**有结论的那一带**——
#: 判读带写的是「≤1/12 判近零、≥6/12 判高」，边界值掉进「不可分辨」
#: 等于让一个本该有结论的点变成没结论。
_EPS = 1e-9

#: E10 的两条阈值。**它们是观察项的判读门槛，不是达标线**，
#: 不适用「阈值必须标明来源」的规则；但仍写在这里，免得日后被当成有依据的数。
SEGMENTATION_CHARS_TOLERANCE = 0.15  # 总字数变化在 ±15% 内视为「基本不变」
SEGMENTATION_COUNT_RATIO = 1.5  # 段数涨到 1.5 倍以上视为「显著上升」


def band_of(rate: float | None) -> str:
    """把触发率归入判读带。**边界值归入有结论的那一带。**

    刻意不写成「遍历 BANDS 找区间」：相邻带共享端点，那种写法会让
    6/12 落进先匹配到的「不可分辨」——**一个本该有结论的点变成没结论**，
    而且不会报错。BANDS 保留为文档，判定按规则原文写死。
    """
    if rate is None:
        return "无数据"
    if rate <= NEAR_ZERO_MAX + _EPS:
        return "近零"
    if rate >= HIGH_MIN - _EPS:
        return "高"
    return "不可分辨"


@dataclass(frozen=True, slots=True)
class Readout:
    verdict: str
    detail: str
    evidence: dict[str, Any]


def monotone_verdict(
    rates_by_replicate: Sequence[Sequence[float | None]], point_ids: Sequence[str]
) -> Readout:
    """主判据。`rates_by_replicate[i]` 是第 i 条轨迹在各点上的触发率。

    「单调不减」允许持平——真实曲线在近零段本来就会持平，
    要求严格递增等于要求一个不存在的形态。
    """
    #: **数据缺失与方向不一致必须分开。**
    #: 一条只跑了一半的轨迹，其序列里含 None；若把它当成「不满足单调」，
    #: 中断的批次会读出「未见同向单调」——那听起来像一个实质结论，
    #: 实际只是数据没跑完。两者在报告里的含义完全相反：
    #: 前者要补数据，后者要改假设。
    per_traj: list[bool] = []
    incomplete = 0
    for series in rates_by_replicate:
        if any(r is None for r in series):
            incomplete += 1
            continue
        per_traj.append(all(a <= b + 1e-9 for a, b in zip(series, series[1:])))
    agreeing = sum(per_traj)
    total = len(per_traj)
    evidence_base = {
        "complete_trajectories": total,
        "incomplete_trajectories": incomplete,
        "monotone": per_traj,
    }

    if total < 2:
        detail = (
            "主判据要求 ≥2 条**完整**轨迹同向。单轨只是一次实现，"
            "换种子仍是同一格（R17）——**先补轨，不要解读**。"
        )
        if incomplete:
            detail += (
                f"\n另有 **{incomplete} 条轨迹数据不完整**（未跑完或中断）。"
                "**数据缺失不等于方向不一致**——它不构成反面证据，只是还没测。"
            )
        return Readout("证据不足", detail, evidence_base)
    if agreeing >= 2:
        return Readout(
            "单调不减，≥2 轨同向",
            "构成证据：审查的收益随配置偏离程度上升。拐点位置按判读带定位。",
            {**evidence_base, "agreeing": agreeing},
        )
    return Readout(
        "未见同向单调",
        "不构成证据。**这不等于「审查没用」**——它等于「本实验没测出配置依赖」，"
        "两者在报告里必须分开写。",
        {**evidence_base, "agreeing": agreeing},
    )


def segmentation_verdict(
    baseline: dict[str, float], extreme: dict[str, float]
) -> Readout:
    """E10。`baseline` / `extreme` 各含 `mean_total_chars` 与 `mean_paragraph_count`。"""
    b_chars, e_chars = baseline["mean_total_chars"], extreme["mean_total_chars"]
    b_paras, e_paras = baseline["mean_paragraph_count"], extreme["mean_paragraph_count"]
    if not b_chars or not b_paras:
        return Readout("无数据", "基线缺少观察量。", {})

    chars_ratio = e_chars / b_chars
    paras_ratio = e_paras / b_paras
    evidence = {
        "total_chars_ratio": chars_ratio,
        "paragraph_count_ratio": paras_ratio,
        "baseline": baseline,
        "extreme": extreme,
    }
    unchanged = abs(chars_ratio - 1.0) <= SEGMENTATION_CHARS_TOLERANCE
    inflated = paras_ratio >= SEGMENTATION_COUNT_RATIO

    if unchanged and inflated:
        return Readout(
            "达标靠分段实现",
            "总字数基本不变而段数显著上升。结论**不得**写成「模型能遵守极端配置」，"
            "须写成「本指标在偏短方向可被分段平凡满足」，"
            "并把「总输出长度」补进 PRD §8.1 的实现待办。",
            evidence,
        )
    if chars_ratio < 1 - SEGMENTATION_CHARS_TOLERANCE:
        return Readout(
            "确实讲得更简洁",
            "总字数随配置收紧而下降——模型走的是用户真正想要的那条路。",
            evidence,
        )
    return Readout("形态不明确", "总字数与段数的变化都不满足任一判据，如实报原始数。", evidence)


@dataclass(frozen=True, slots=True)
class Decomposition:
    """`平均段落字数 = 总字数 ÷ 段数` 的对数分解。**恒等式，不是判据。**"""

    point: str
    chars_ratio: float
    paras_ratio: float
    per_para_ratio: float
    shrink_share: float | None
    split_share: float | None

    @property
    def sign_mixed(self) -> bool:
        """总字数与段数把「字/段」推向**相反**方向。

        典型形态：总字数反而上升（`chars_ratio > 1`），全靠多分段才把均值压下来。
        这时「真的讲短 N%」这个说法是错的——**什么都没被讲短**。
        """
        return self.chars_ratio > 1.0

    def formula(self) -> str:
        head = (
            f"{self.point}: 字/段 {self.per_para_ratio:.3f}× "
            f"= 总字数 {self.chars_ratio:.3f}× ÷ 段数 {self.paras_ratio:.3f}×"
        )
        if self.shrink_share is None:
            return head + "（基点）"
        if self.sign_mixed:
            # 份额仍按绝对值算得出来，但标签必须换——否则会读成「讲短了 25%」，
            # 而实际是「写得更多了，全靠分段把均值压下去」
            return (
                head + "　→　⚠️ **总字数反而上升**，"
                f"字/段的下降 **全部来自分段**（反向拉高 {self.shrink_share:.0%}）"
            )
        return (
            head + "　→　"
            f"真的讲短 **{self.shrink_share:.0%}** / 分段 **{self.split_share:.0%}**"
        )


def decompose_paragraph_drop(
    baseline: dict[str, float], point_stats: dict[str, dict[str, float]]
) -> list[Decomposition]:
    """把「平均段落字数的下降」分解成「真的讲短」与「多敲回车」两份。

    依据是被测量自身的定义式 `字/段 = 总字数 ÷ 段数`：两边取对数后变化量可加，
    各自的占比就是各自对数变化量的绝对值份额。

    **没有阈值，没有自由参数。** 它不改变 E10 的三分类结论，
    只是在结论旁边说明「那个结论主要由哪一半驱动」。

    同号时（两个方向都在压低字/段）份额落在 [0,1]；异号时（一个在抬高）
    份额仍按绝对值归一，此时应连同原始比值一起读，不要只看百分数。
    """
    import math

    b_chars, b_paras = baseline["mean_total_chars"], baseline["mean_paragraph_count"]
    out: list[Decomposition] = []
    for name, st in point_stats.items():
        c_ratio = st["mean_total_chars"] / b_chars if b_chars else float("nan")
        p_ratio = st["mean_paragraph_count"] / b_paras if b_paras else float("nan")
        per = c_ratio / p_ratio if p_ratio else float("nan")
        if abs(math.log(c_ratio)) + abs(math.log(p_ratio)) < 1e-9:
            shrink = split = None  # 基点自己，无可分解
        else:
            a, b = abs(math.log(c_ratio)), abs(math.log(p_ratio))
            shrink, split = a / (a + b), b / (a + b)
        out.append(Decomposition(name, c_ratio, p_ratio, per, shrink, split))
    return out
