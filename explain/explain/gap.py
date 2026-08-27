"""F3b 的插入点。**本轮不实现三分类判断。**

PRD §10.1 实测：纯向量相似度无法区分「课件里有答案」与「课件里没有」——
四个 embedding 模型上两组 top-1 相似度全部重叠。因此兜底判断必须改为模型调用，
且它**每轮都发生**，不是异常路径。

那件事是 F3b。本轮留的是**插入点**，不是注释：

- `GapChecker` 协议已定，`pipeline` 已经在调用它
- `NullGapChecker` 恒返回 `covered`，成本恒 0
- 日志字段 `gap_verdict` / `gap_cost_usd` 已经在写，本轮恒为 `null` / 0

接 F3b 时只需换一个实现，**调用方一行都不用改**。

三分类的取值直接对应 PRD §10.1 那张表的三行，不多不少：

===========================  =================================================
``covered``                  片够回答
``not_mentioned``            课件确实没提
``in_figure``                内容在图里读不了（判据需要 ``skipped_pages``）
``mentioned_not_elaborated`` 提了但没展开
===========================  =================================================

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .context import ContextPack

__all__ = ["GapVerdict", "GapResult", "GapChecker", "NullGapChecker"]

GapVerdict = Literal["covered", "not_mentioned", "in_figure", "mentioned_not_elaborated"]


@dataclass(frozen=True, slots=True)
class GapResult:
    verdict: GapVerdict | None
    reason: str | None = None
    cost_usd: float = 0.0
    #: 实现方式，进日志。`null` 表示本轮没有做这个判断，
    #: 与「做了判断且结论是 covered」是两回事——不区分的话，
    #: F3b 上线后无法回答「兜底判断从哪个批次开始真的在跑」。
    implementation: str = "null"

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_verdict": self.verdict,
            "gap_reason": self.reason,
            "gap_cost_usd": self.cost_usd,
            "gap_implementation": self.implementation,
        }


class GapChecker(Protocol):
    def check(self, question: str, context: ContextPack) -> GapResult: ...


@dataclass(frozen=True, slots=True)
class NullGapChecker:
    """本轮的实现：什么都不判，恒返回 `verdict=None`。

    **刻意不返回 `covered`。** 返回 `covered` 等于断言「片够回答」，
    而我们根本没做这个判断——那是一条假断言（通则七 #1：
    凡是写成「已验证」的句子，必须能指出是哪次测量得出的）。
    返回 `None` 如实表示「本轮未判断」。
    """

    def check(self, question: str, context: ContextPack) -> GapResult:
        return GapResult(verdict=None, reason=None, cost_usd=0.0, implementation="null")
