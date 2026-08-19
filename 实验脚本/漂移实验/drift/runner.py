"""实验编排：两组对照 × N 轮。

设计约束（来自 PRD §8.2 与本次讨论）：

- **两组唯一的差异是审查 agent 的有无。** 同一份课件、同一份风格配置、
  同一组提问、同一顺序、同一模型参数。
- **重复是整条轨迹的重复，不是每轮内部重采样。** 漂移是一条对话轨迹随轮次
  演化出来的性质；在同一条对话里对某轮采样多次再挑一条继续，轨迹仍然只有
  一条，重复毫无统计意义。每个 replicate 是一次独立的 20 轮完整对话。
- **无审查组照常计算指标，只记录不干预**——不算就没有对照。
- **不含检索环节。** 课件全量进 system prompt。若引入检索，两组可能取到不同
  片段，连输入都不一样，对照就废了。结论仅适用于「课件全量在上下文中」。
- **重生成用反馈驱动，不是重试。** 现有模型已移除 temperature，纯重试大概率
  复现同样的问题；把审查反馈作为一轮追加指令，模型才有依据修改。
  修正往返不进对话历史——只留提问与最终输出，两组历史结构保持一致。

决策日期：2026-08-19
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import Completion, Model, Usage
from .reviewer import Review, RuleReviewer

__all__ = ["Attempt", "RoundRecord", "run_condition"]


@dataclass(frozen=True, slots=True)
class Attempt:
    index: int
    completion: Completion
    review: Review | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.index,
            "completion": self.completion.to_dict(),
            "review": self.review.to_dict() if self.review else None,
        }


@dataclass(frozen=True, slots=True)
class RoundRecord:
    condition: str
    replicate: int
    round_index: int
    question: str
    attempts: tuple[Attempt, ...]
    final_text: str
    regenerations: int
    accepted: bool
    request_signature: str

    @property
    def usage(self) -> Usage:
        total = Usage()
        for a in self.attempts:
            total = total + a.completion.usage
        return total

    @property
    def cost(self) -> float:
        return sum(a.completion.cost for a in self.attempts)

    @property
    def final_review(self) -> Review | None:
        return self.attempts[-1].review

    @property
    def term_occurrences_first(self) -> int | None:
        """本轮的首现术语数——术语维度的分母。

        实验配置把护栏降到 2（见 experiment.json 的 style_override_rationale），
        所以低分母的点会照常出数值。必须逐轮记录这个数，报告时才能标出
        哪些点可信度低。
        """
        rv = self.final_review
        if rv is None:
            return None
        d = rv.evaluation.by_name("term_annotation")
        return d.details.get("occurrences_first")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "replicate": self.replicate,
            "round": self.round_index,
            "question": self.question,
            "request_signature": self.request_signature,
            "regenerations": self.regenerations,
            "term_occurrences_first": self.term_occurrences_first,
            "accepted": self.accepted,
            "attempts": [a.to_dict() for a in self.attempts],
            "round_usage": self.usage.to_dict(),
            "round_cost_usd": self.cost,
            "final_evaluation": (
                self.final_review.evaluation.to_dict() if self.final_review else None
            ),
        }


def run_condition(
    *,
    condition: str,
    replicate: int,
    questions: Iterable[str],
    model: Model,
    system: str,
    reviewer: RuleReviewer,
    review_enabled: bool,
    max_regenerations: int = 2,
    on_round: Any = None,
) -> list[RoundRecord]:
    """跑完一组的全部轮次。

    `review_enabled=False` 时仍然计算指标（只记录不干预），这样无审查组才有
    可比的曲线。
    """
    history: list[dict[str, Any]] = []
    records: list[RoundRecord] = []

    for i, question in enumerate(questions, 1):
        turn = history + [{"role": "user", "content": question}]
        signature = model.request_signature(system, turn)

        attempts: list[Attempt] = []
        working = list(turn)
        accepted = False
        final_text = ""

        for attempt_index in range(max_regenerations + 1):
            completion = model.complete(system, working)
            review = reviewer.review(completion.text)
            attempts.append(Attempt(attempt_index, completion, review))
            final_text = completion.text

            if not review_enabled or review.passed:
                accepted = review.passed
                break
            if attempt_index == max_regenerations:
                # PRD §10：连续不通过则降级输出并说明，不无限重试
                accepted = False
                break
            working = working + [
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": review.feedback},
            ]

        # 修正往返不进历史：两组的对话历史结构必须保持一致，否则第 k 轮的
        # 输入长度差异会变成审查之外的第二个变量
        history = history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": final_text},
        ]

        record = RoundRecord(
            condition=condition,
            replicate=replicate,
            round_index=i,
            question=question,
            attempts=tuple(attempts),
            final_text=final_text,
            regenerations=len(attempts) - 1,
            accepted=accepted,
            request_signature=signature,
        )
        records.append(record)
        if on_round is not None:
            on_round(record)

    return records
