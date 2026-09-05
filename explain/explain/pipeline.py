"""一轮讲解：检索 → 生成 → 三维审查 → 不合格重生成（上限 2）。

## 四条口径继承自 `漂移实验/drift/runner.py`（逐条列出，来源可查）

1. **修正往返不进对话历史**——只留提问与最终输出。否则有审查组的上下文更长，
   输入长度会变成审查之外的第二个变量。
2. **无审查组照常计算指标，只记录不干预**——不算就没有对照。
3. **重生成是反馈驱动，不是重试**——当前模型已移除 temperature，
   纯重试大概率复现同样的问题。
4. **重复是整条轨迹的重复**——轨迹状态（历史、已见检索片）由 `Trajectory` 持有，
   调用方每个 replicate 新建一个。

## 本轮新增的两条

5. **ContextPack 每轮只算一次，两组共用**（口径 X02）。检索只取决于提问
   （口径 X01），所以两组拿到的输入在检索侧**逐字节相同**，签名可核验。
   第 0 周为保住对照干脆不含检索，本轮不需要那样做。
6. **GapChecker 每轮都调用**（口径 X08）。本轮是 `NullGapChecker`，
   恒返回 `verdict=None`、成本 0；F3b 换实现时调用方一行都不用改。

## 降级输出

重生成触顶仍不达标时**照常输出**（PRD §10：降级输出 + 说明），
`accepted=False` 记进日志。不无限重试——那既是成本问题也是体验问题。

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .context import ContextPack, Retriever
from .gap import GapChecker, GapResult, NullGapChecker
from .llm import Completion, Model, Usage
from .observe import Observations, observe
from .prompt import build_system, build_user_turn
from .review import Review, RuleReviewer

__all__ = ["Attempt", "RoundResult", "Trajectory", "run_round"]


@dataclass(frozen=True, slots=True)
class Attempt:
    index: int
    completion: Completion
    review: Review

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.index,
            "completion": self.completion.to_dict(),
            "review": self.review.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RoundResult:
    round_index: int
    question: str
    context: ContextPack
    gap: GapResult
    attempts: tuple[Attempt, ...]
    final_text: str
    accepted: bool
    request_signature: str
    observations: Observations
    system_text: str

    @property
    def regenerations(self) -> int:
        return len(self.attempts) - 1

    @property
    def triggered_regeneration(self) -> bool:
        return self.regenerations > 0

    @property
    def usage(self) -> Usage:
        total = Usage()
        for a in self.attempts:
            total = total + a.completion.usage
        return total

    @property
    def cost_usd(self) -> float:
        return sum(a.completion.cost for a in self.attempts) + self.gap.cost_usd

    @property
    def latency_ms(self) -> float:
        """**含兜底判断那一跳。** 它是串行的，用户等待的就是这个总和。"""
        return sum(a.completion.latency_ms for a in self.attempts) + self.gap.latency_ms

    @property
    def generation_latency_ms(self) -> float:
        """只算生成，用于分离出兜底判断的延迟增量。"""
        return sum(a.completion.latency_ms for a in self.attempts)

    @property
    def final_review(self) -> Review:
        return self.attempts[-1].review


@dataclass
class Trajectory:
    """一条独立对话轨迹的可变状态。

    **每个 replicate 一个实例。** 在同一条轨迹里对某轮重采样再挑一条继续，
    轨迹仍然只有一次实现——那是给单个点加误差棒，不是重复。
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    #: 本轨迹已见过的全部检索片，供 verbatim 探针比对（含历史轮）。
    seen_sources: list[tuple[str, str]] = field(default_factory=list)

    def remember(self, context: ContextPack) -> None:
        known = {label for label, _ in self.seen_sources}
        for s in context.slices:
            label = f"{s.source_file}#{s.slice_id}"
            if label not in known:
                known.add(label)
                self.seen_sources.append((label, s.text))

    def commit(self, question: str, final_text: str) -> None:
        """只把提问与**最终**输出写进历史——修正往返不入历史（口径 1）。"""
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": final_text})


def run_round(
    *,
    round_index: int,
    question: str,
    trajectory: Trajectory,
    retriever: Retriever,
    model: Model,
    reviewer: RuleReviewer,
    style_rules: str,
    course_label: str,
    review_enabled: bool,
    max_regenerations: int = 2,
    gap_checker: GapChecker | None = None,
    context: ContextPack | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> RoundResult:
    """跑一轮。

    `context` 可由调用方预先取好并在两组间共用（口径 X02）；不传则本函数自取。
    `review_enabled=False` 时**仍然完整计算审查结果**，只是不据此重生成——
    不算就没有对照（口径 2）。
    """
    ctx = context if context is not None else retriever.fetch(question)
    trajectory.remember(ctx)

    gap = (gap_checker or NullGapChecker()).check(question, ctx)

    system = build_system(style_rules, ctx, course_label)
    # 判断结果进本轮 user turn（每轮不同，不进 system——那会让缓存每轮失效）
    turn = trajectory.history + [
        {"role": "user", "content": build_user_turn(ctx, question, gap)}
    ]
    request_signature = model.request_signature(system, turn)

    attempts: list[Attempt] = []
    working = list(turn)
    accepted = False
    final_text = ""

    for attempt_index in range(max_regenerations + 1):
        completion = model.complete(system, working)
        review = reviewer.review(completion.text)
        attempt = Attempt(attempt_index, completion, review)
        attempts.append(attempt)
        if on_attempt is not None:
            on_attempt(attempt)
        final_text = completion.text

        if not review_enabled or review.passed:
            # 无审查组也如实记录「这一版达没达标」，只是不据此重生成
            accepted = bool(review.passed)
            break
        if attempt_index == max_regenerations:
            accepted = False  # 触顶降级输出（PRD §10）
            break
        working = working + [
            {"role": "assistant", "content": completion.text},
            {"role": "user", "content": review.feedback},
        ]

    trajectory.commit(build_user_turn(ctx, question, gap), final_text)

    return RoundResult(
        round_index=round_index,
        question=question,
        context=ctx,
        gap=gap,
        attempts=tuple(attempts),
        final_text=final_text,
        accepted=accepted,
        request_signature=request_signature,
        observations=observe(final_text, trajectory.seen_sources),
        system_text=system,
    )
