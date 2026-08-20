"""报告格式化。

**按 tag 分组，不给单一总分**（PRD §8.1 设计要点、a_rules D17）。

三条限制固定附在每份报告页脚，不随结果变化——它们描述的是**评测集本身**的性质，
好看的数字不会让它们失效：

1. ``synonym`` 那 18 条主要由 22.081J 一门课决定（评测集 README §6）
2. n=30、单人标注、语料 3 门 → 只做矩阵内相对比较，不是绝对召回率（§2.2）
3. ``figure_heavy`` / ``unreadable_page`` 预期失败；后者上限恒为 0（§3.1）

决策日期：2026-08-21
"""

from __future__ import annotations

from .metrics import Separability, TagSummary

__all__ = [
    "ZERO_CEILING_TAGS",
    "TAG_ORDER",
    "VARIANT_LEGEND",
    "format_run",
    "format_separability",
    "variant_note",
    "FOOTER",
]

#: gold 页不产生切片、上限恒为 0 的 tag。
ZERO_CEILING_TAGS = frozenset({"unreadable_page"})

TAG_ORDER = ("synonym", "multipage", "figure_heavy", "unreadable_page", "no_answer")

#: 表内两种空值的区分。**必须紧跟表格输出**——
#: 只截表格发人看时，行尾的箭头注释很容易掉，含义就没了。
_LEGEND = (
    "  图例：0/n 与 0.000 是**真实测得的零**；"
    "「—」= 无命中故无位次；「n/a」= 该指标对本组不适用。\n"
    "        unreadable_page 的零是**结构性的**：gold 页不产生切片、不在索引里，上限即 0。"
)

#: 四个文本变体各自在测什么。真实与样张两种模式都要输出，
#: 只有「差异是否为真实测量」这一句不同。
VARIANT_LEGEND = (
    "文本变体：T0 基线（仅清理控制/私用区字符）· T1 = T0 − 重复页眉页脚 · "
    "T2 = T1 + title 前置 · T3 = T2 + 文档级前缀"
)


def variant_note(kind: str) -> str:
    """交代四个变体在测什么，以及本次运行下它们的差异意味着什么。"""
    if kind == "planted":
        return (
            VARIANT_LEGEND
            + "\n构造向量按**切片身份**种信号、不读文本内容，"
            "故 T0–T3 的数字必然相同——**文本变体的差异只有真实模型能测**。"
        )
    if kind == "fake":
        return (
            VARIANT_LEGEND
            + "\n词袋哈希只对字面重合有反应，而本语料是英文、问题是中文，"
            "两侧零重合 → 各变体指标恒为 0，差异不可解读。"
        )
    return (
        VARIANT_LEGEND
        + "\n**T0–T3 之间的差异是真实测量**：它回答「去掉样板 / 前置标题 / 加文档前缀"
        "各自对召回有没有用」。差异小于 1 题按噪声处理，不作结论。"
    )


FOOTER = """限制（描述评测集本身，与本次结果无关）
  1. synonym 的 18 条主要由 22.081J 一门课决定（20/39 的 gold 页引用集中在该门）。
     该组结论只能读作「在这门能源课件上」，不能外推为「synonym 表现好」。
  2. n=30、单人标注、语料仅 3 门 → 只用于本矩阵内的相对比较。
     不可作绝对召回率，不可写进 PRD §3.2 的溯源率指标或 §8.4 的上线达标线。
  3. figure_heavy 与 unreadable_page 预期失败，失败是数据不是 bug；
     unreadable_page 的 gold 页不产生切片，page-recall 上限恒为 0。"""


def format_run(
    title: str,
    summaries: dict[str, TagSummary],
    k: int,
    note: str | None = None,
) -> str:
    lines = [title, ""]
    lines.append(
        f"  {'tag':<16} {'n':>3}  {'full-hit@'+str(k):>11}  "
        f"{'page-recall@'+str(k):>14}  {'首命中位次':>10}  {'未命中':>6}"
    )
    lines.append("  " + "-" * 72)
    for tag in TAG_ORDER:
        s = summaries.get(tag)
        if s is None:
            continue
        if tag == "no_answer":
            # 「n/a」而非「—」：这组没有 gold 页，三个召回指标在定义上就不适用，
            # 与「有指标但值为空」是两回事
            lines.append(
                f"  {tag:<16} {s.n:>3}  {'n/a':>11}  {'n/a':>14}  {'n/a':>10}  {'n/a':>6}"
                "   ← 另见可分性表"
            )
            continue
        rank = f"{s.mean_rank:.1f}" if s.mean_rank is not None else "—"
        row = (
            f"  {tag:<16} {s.n:>3}  {s.full_hits:>5}/{s.n:<5}  "
            f"{s.page_recall:>14.3f}  {rank:>10}  {s.missed:>6}"
        )
        if s.ceiling_zero:
            row += "   ← 上限 0（gold 页不产生切片）"
        lines.append(row)
    lines.append("")
    lines.append(_LEGEND)
    lines.append("  注：一题可带多个 tag，各组之和大于题数；各组不可相加当总数。")
    if note:
        lines.append(f"  {note}")
    return "\n".join(lines)


def _hist(values: tuple[float, ...], lo: float, hi: float, width: int = 40) -> list[str]:
    """把一组分数画成一行一个点的横向分布，便于目视两组是否重叠。"""
    if not values:
        return ["    (无样本)"]
    span = max(hi - lo, 1e-9)
    out = []
    for v in sorted(values, reverse=True):
        pos = int((v - lo) / span * (width - 1))
        out.append("    " + " " * pos + "●" + " " * (width - pos - 1) + f"  {v:.4f}")
    return out


def format_separability(sep: Separability, config: str = "") -> str:
    """``no_answer`` 的可分性。**无间隔是结论，不是待办。**

    ``config`` 必须标出——可分性是逐配置的量，不标配置的图无法解读。
    """
    all_v = list(sep.with_gold) + list(sep.no_answer)
    if not all_v:
        return f"no_answer 可分性 [{config or '?'}]：无数据"
    lo, hi = min(all_v), max(all_v)

    head = "no_answer 可分性 · top-1 相似度分布"
    lines = [f"{head}   【配置：{config}】" if config else head, ""]
    lines.append(f"  有 gold 的题（n={len(sep.with_gold)}）")
    lines += _hist(sep.with_gold, lo, hi)
    lines.append("")
    lines.append(f"  no_answer（n={len(sep.no_answer)}）")
    lines += _hist(sep.no_answer, lo, hi)
    lines.append("")

    gap = sep.gap
    if sep.separable and gap is not None:
        a, b = sep.interval  # type: ignore[misc]
        lines.append(f"  ▸ 两组存在间隔，宽 {gap:.4f}，区间 [{a:.4f}, {b:.4f}]。")
        lines.append("")
        lines.append("  **本次仍不定 τ。** n=5 上选出的阈值必然过拟合，且会显得有依据。")
        lines.append("  τ 来源：占位待定。确定路径：样本量做大后在上述区间内取值。")
    else:
        overlap = f"{-gap:.4f}" if gap is not None else "?"
        lines.append(f"  ▸ **两组重叠，无间隔**（重叠宽 {overlap}）。")
        lines.append("")
        lines.append("  **这本身就是结论，不是「待后续验证」。** 它说明：")
        lines.append("  纯向量相似度无法支撑 PRD §10 的「这个内容课件里没有提到」兜底——")
        lines.append("  按任何单一阈值切分，都会把有答案的题误判为无答案，或反之。")
        lines.append("  产品含义：§10 的兜底不能只靠检索分数，必须引入另一个信号")
        lines.append("  （例如把候选片交给模型判断「够不够回答这个问题」）。")
        lines.append("  该结论应与正面结果同等看待，不得压缩为一句「待验证」。")
    return "\n".join(lines)
