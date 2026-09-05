"""F3b · 兜底判断的校准（口径 G09–G12）。

用 `实验脚本/检索评测集` 现成的 30 题，金标准已人工标过，**不新建标注集**。

## ⚠️ 这不是 §8.5 的 judge 校准

===============  ==============================  ==============================
                 §8.5                            本轮
===============  ==============================  ==============================
对象             风格维度的**语义评分**          「片段够不够回答」的**事实判断**
ground truth     需新建双人标注（100–200 条）    **现成 30 题**
用途             当**指标**（进风格遵循度）      当**功能输入**（进讲解 prompt）
达标要求         kappa ≥ 0.6                     召回/误报分别报，达标线占位待定
===============  ==============================  ==============================

**本轮结果不构成启用 B 类的依据。** 判断「片段够不够」准，
说明不了评「讲解深度」准——两者的难度与主观性不在一个量级。

## G10 · 分别报召回与误报，不用单一准确率

两类错误的代价差一个量级：

- 该说「不够」却说「够」 → **模型编造被当作课件内容**，§6.2 的核心风险
- 该说「够」却说「不够」 → 用户多看一段说明，代价小

所以允许在「宁可多说不够」方向偏。**达标线占位待定**，等分布出来再定。

## G11 · 错误必须按 gold 页归因分解

模型只看得到检索片。检索漏了那一页，它看到的就是「片段里没有」——
那是**检索的责任**，不是判断的责任。不分解的话，检索问题会被记成 judge 不准，
然后有人去调 prompt，**修一个不在那里的东西**。

## G12 · 每题 2 次，只报计数

原提 3 次并想据此分「稳定判错 vs 摇摆」，**评审驳回，理由成立**：
n=3 时单题只有 3/0 与 2/1 两种形态，而两者的差别在 n=3 上不构成证据
——R17 在单题尺度上的同一条。撑住那个区分要 n≥5。

改后要的是**一个计数**：两次不一致的题数。计数不需要 n=3 才可信。

决策日期：2026-09-05
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import _paths  # noqa: F401
from explain.gap import GapResult

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent
EVAL = REPO / "实验脚本" / "检索评测集" / "评测集.json"

__all__ = ["GOLD_LABEL", "CalibrationRow", "run_calibration", "format_calibration"]

#: 评测集 tag → 期望判断。
#:
#: **`unreadable_page` 期望 `insufficient`**：那些页不产生切片，检索在结构上
#: 不可能命中，所以「取到的片段里没有」是**对的**——第三类由规则通道补充说明
#: （G03），不是模型的活。
#:
#: **`figure_heavy` 期望 `insufficient`**：命中的是只有标题的片段，答非所问。
#: PRD §6.2：「这类失败更危险——它看起来成功了」，所以判 `insufficient` 才是对的。
#:
#: **`no_answer` 的期望不是单一值**：评测集的注释显示这 5 题里既有「完全没触及」
#: 也有「提了这个角度没给答案」。前者应判 `insufficient`、后者应判 `partial`，
#: 而评测集没有标到这一层。故对 `no_answer` 只要求「**不判 covered**」，
#: 两者都算对——**不能凭 tag 反推一个评测集没标过的更细的标签**。
GOLD_LABEL: dict[str, str] = {
    "no_answer": "not_covered",       # insufficient 或 partial 都算对
    "figure_heavy": "insufficient",
    "unreadable_page": "insufficient",
}
#: 其余 tag（synonym / multipage）的期望是 `covered`。
DEFAULT_LABEL = "covered"


@dataclass(frozen=True, slots=True)
class CalibrationRow:
    qid: str
    question: str
    tags: list[str]
    expected: str
    verdicts: list[str | None]
    gold_pages: list[tuple[str, int]]
    retrieved_pages: list[tuple[str, int]]
    results: list[GapResult] = field(default_factory=list)

    @property
    def gold_retrieved(self) -> bool | None:
        """gold 页在不在检索结果里。无 gold 页（`no_answer`）时为 None。"""
        if not self.gold_pages:
            return None
        return bool(set(self.gold_pages) & set(self.retrieved_pages))

    @property
    def consistent(self) -> bool:
        return len(set(self.verdicts)) == 1

    def correct(self, verdict: str | None) -> bool:
        if verdict is None:
            return False
        if self.expected == "not_covered":
            return verdict != "covered"
        return verdict == self.expected

    @property
    def n_correct(self) -> int:
        return sum(self.correct(v) for v in self.verdicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "tags": self.tags,
            "expected": self.expected,
            "verdicts": self.verdicts,
            "consistent": self.consistent,
            "n_correct": self.n_correct,
            "gold_retrieved": self.gold_retrieved,
            "gap": [r.to_dict() for r in self.results],
        }


def _expected(tags: list[str]) -> str:
    for tag, label in GOLD_LABEL.items():
        if tag in tags:
            return label
    return DEFAULT_LABEL


def run_calibration(checker, retriever, repeats: int = 2) -> list[CalibrationRow]:
    """跑全部 30 题。检索每题只算一次，重复只重复判断那一跳。"""
    items = json.loads(EVAL.read_text(encoding="utf-8"))["items"]
    rows: list[CalibrationRow] = []
    for item in items:
        ctx = retriever.fetch(item["question"])
        results = [checker.check(item["question"], ctx) for _ in range(repeats)]
        rows.append(
            CalibrationRow(
                qid=item["id"],
                question=item["question"],
                tags=item.get("tags", []),
                expected=_expected(item.get("tags", [])),
                verdicts=[r.verdict for r in results],
                gold_pages=[
                    (g["source_file"], g["page_index"]) for g in (item.get("gold_pages") or [])
                ],
                retrieved_pages=[
                    (s.source_file, p) for s in ctx.slices for p in s.page_indices
                ],
                results=results,
            )
        )
    return rows


def format_calibration(rows: list[CalibrationRow]) -> str:
    """报告。**分别报召回与误报，不给单一准确率**（G10）。"""
    # 以「该说不够」为正类：漏报 = 该说不够却说够，是贵的那一类
    pos = [r for r in rows if r.expected != DEFAULT_LABEL]
    neg = [r for r in rows if r.expected == DEFAULT_LABEL]

    def first(r: CalibrationRow) -> str | None:
        return r.verdicts[0]

    tp = sum(1 for r in pos if first(r) not in (None, "covered"))
    fn = sum(1 for r in pos if first(r) == "covered")
    fp = sum(1 for r in neg if first(r) not in (None, "covered"))
    tn = sum(1 for r in neg if first(r) == "covered")
    invalid = sum(1 for r in rows if first(r) is None)

    lines = [
        "# F3b 兜底判断校准（30 题，每题 2 次；第 1 次用于下表）",
        "",
        "**不给单一准确率**（G10）：两类错误的代价差一个量级。",
        "",
        f"{'':22}{'判「不够」':>10}{'判「够」':>10}",
        f"{'实际该说「不够」':<20}{tp:>10}{fn:>10}　← **漏报，贵**",
        f"{'实际该说「够」':<20}{fp:>10}{tn:>10}　← 误报，便宜",
        "",
        f"召回（该说不够而说了）：**{tp}/{len(pos)}**"
        + (f" = {tp / len(pos):.0%}" if pos else ""),
        f"误报（该说够却说不够）：**{fp}/{len(neg)}**"
        + (f" = {fp / len(neg):.0%}" if neg else ""),
        f"无效输出（取值不合法，G01）：**{invalid}/{len(rows)}**",
        "",
        "⚠️ 达标线**占位待定**：确定路径是按两类错误的代价不对称反推，"
        "允许在「宁可多说不够」方向偏。本轮只出分布。",
        "",
        "## G12 · 两次一致性（**计数，不解释成稳定性**）",
    ]
    inconsistent = [r for r in rows if not r.consistent]
    lines.append(f"两次不一致：**{len(inconsistent)}/{len(rows)}** 题"
                 + (f"（{'、'.join(r.qid for r in inconsistent)}）" if inconsistent else ""))
    lines.append("n=2 撑不起「稳定 vs 摇摆」的判定（R17）。这里报的是计数："
                 "不一致题数高本身就是「这个判断不可靠」的直接证据。")

    lines += ["", "## G11 · 错误归因分解（gold 页在不在检索结果里）"]
    wrong = [r for r in rows if not r.correct(first(r))]
    if not wrong:
        lines.append("第 1 次判断全对，无需分解。")
    else:
        judge, retr, nogold = [], [], []
        for r in wrong:
            (nogold if r.gold_retrieved is None else (judge if r.gold_retrieved else retr)).append(r)
        lines += [
            f"判错共 **{len(wrong)}** 题：",
            f"  · gold 页**在**检索结果里 → **判断本身错**：{len(judge)} 题"
            + (f"（{'、'.join(r.qid for r in judge)}）" if judge else ""),
            f"  · gold 页**不在**检索结果里 → **检索漏了**：{len(retr)} 题"
            + (f"（{'、'.join(r.qid for r in retr)}）" if retr else ""),
            f"  · 无 gold 页（`no_answer`），不可分解：{len(nogold)} 题"
            + (f"（{'、'.join(r.qid for r in nogold)}）" if nogold else ""),
            "",
            "**不分解的话，检索问题会被记成 judge 不准，然后有人去调 prompt——"
            "修一个不在那里的东西。**",
        ]

    lines += ["", "## 按 tag 分组（第 1 次）"]
    by_tag: dict[str, Counter] = {}
    for r in rows:
        for t in r.tags:
            by_tag.setdefault(t, Counter())[first(r) or "invalid"] += 1
    for tag in sorted(by_tag):
        dist = "、".join(f"{k} {v}" for k, v in sorted(by_tag[tag].items()))
        lines.append(f"  {tag:<16} {dist}")
    lines.append("")
    lines.append("⚠️ 一题多 tag 时在每个 tag 下各计一次（同 R15），**各组之和大于题数**。")

    cost = sum(g.cost_usd for r in rows for g in r.results)
    lat = [g.latency_ms for r in rows for g in r.results]
    lines += [
        "",
        f"## 成本与延迟（{len(lat)} 次调用）",
        f"合计 ${cost:.4f}　·　单次均值 ${cost / len(lat):.5f}",
        f"延迟 均值 {sum(lat) / len(lat):.0f} ms · "
        f"P50 {sorted(lat)[len(lat) // 2]:.0f} ms · "
        f"P90 {sorted(lat)[int(len(lat) * 0.9)]:.0f} ms",
    ]
    return "\n".join(lines)
