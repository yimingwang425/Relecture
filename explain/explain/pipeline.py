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

## 兜底判断与首版生成**并行**（2026-10-07，口径 F93）

原先判断是严格串行的一跳（判完才拼 prompt、才开始生成），用户多等一次 Haiku 往返
（实测均值 1.4 s）。现在两件事同时开始：

- 首版**按「没有限定语」投机生成**，输出先进闸门（`_Gate`）缓着，不给用户看；
- 判断返回后：不需要限定语（`covered` / 判断失败 / 无效样本）→ 闸门打开，缓着的
  正文原样放出去，**这一版就是正式的第 1 版**；需要限定语（`insufficient` /
  `partial`）→ 叫停投机那一版（关连接，省掉剩余输出），带上限定语重新生成。

**等价性是这条改动的全部前提**：`render_gap_note(gap) == ""` 当且仅当
`build_user_turn(ctx, q, gap) == build_user_turn(ctx, q, None)`。所以无论判断结果如何，
**最终发给模型、记进历史、算进签名的那条消息与串行时逐字节相同**——
测试 `TestParallelGap` 对三种判断结果逐一钉住。

代价如实登记：判断要求限定语时，投机那一版的输入 token 白花（输出被叫停，
只花到叫停那一刻）。这份钱**计入单轮**（`discarded`，口径 F71 的同一条）。

## 流式（2026-10-07，口径 F94）

`on_event` 给了就逐段往外推：`delta`（正文）、`gap`（判断结果）、
`reset`（审查不达标、要重生成——前端清掉已显示的那一版）。
不给就与原来一样只返回最终结果。**两条路跑的是同一段代码**，不是两份实现。

## 降级输出

重生成触顶仍不达标时**照常输出**（PRD §10：降级输出 + 说明），
`accepted=False` 记进日志。不无限重试——那既是成本问题也是体验问题。

决策日期：2026-08-22
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .context import ContextPack, Retriever
from .gap import GapChecker, GapResult, NullGapChecker
from .llm import Completion, Model, Usage
from .observe import Observations, observe
from .prompt import build_system, build_user_turn, render_gap_note
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
    #: 被作废的投机生成（判断要求加限定语时，口径 F93）。**它们花了钱**，
    #: 所以计入 `usage` / `cost_usd`；但它们不是「一版讲解」，不进 `attempts`、
    #: 不计重生成——用户一个字都没看到。
    discarded: tuple[Completion, ...] = ()
    #: 从本轮开始（调用方给的 `started_at`，没给就是进 `run_round` 那一刻）
    #: 到**第一段正文真正放出闸门**的毫秒数。没有正文时为 None——**不塞假数**。
    first_token_latency_ms: float | None = None
    #: 本轮的墙钟耗时（毫秒）。与 `latency_ms` 分开：那个由各次调用自报的耗时
    #: 按关键路径算出来，可复算；这个是真的等了多久，含线程调度等一切开销。
    wall_ms: float = 0.0

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
        for d in self.discarded:
            total = total + d.usage
        return total

    @property
    def cost_usd(self) -> float:
        return (sum(a.completion.cost for a in self.attempts)
                + sum(d.cost for d in self.discarded) + self.gap.cost_usd
                + self.retrieval_llm_cost_usd)

    @property
    def retrieval_llm_cost_usd(self) -> float:
        """检索时的模型调用（查询改写 + 重排，口径 F96 / F98）。**计入单轮**（F71 同一条）。

        由检索后端写在 `context.meta["llm_cost_usd"]`；替身检索没有这一项，是 0。
        后端写 None（有一次超时，金额未知）时这里按 0 加——与判断失败时的处置相同，
        未知的那一格由 meta 原样带到事件里，不在这里编一个数。
        """
        meta = self.context.meta or {}
        return float(meta.get("llm_cost_usd") or 0.0)

    @property
    def latency_ms(self) -> float:
        """**关键路径**，不再是总和（口径 F93）。

        判断与首版生成并行，用户等的是两者中较慢的那个，再加上之后的各版：

        - 投机那一版留用：`max(判断, 第 1 版) + 其余各版`
        - 投机那一版作废：`max(判断, 投机版) + 全部正式各版`

        由各次调用自报的耗时算出，**可复算**；真实墙钟另记在 `wall_ms`。
        """
        gen = self.generation_latency_ms
        if self.discarded:
            spec = sum(d.latency_ms for d in self.discarded)
            return max(self.gap.latency_ms, spec) + gen
        if not self.attempts:
            return self.gap.latency_ms
        first = self.attempts[0].completion.latency_ms
        return max(self.gap.latency_ms, first) + gen - first

    @property
    def generation_latency_ms(self) -> float:
        """只算正式各版的生成，用于分离出兜底判断的延迟增量。"""
        return sum(a.completion.latency_ms for a in self.attempts)

    @property
    def final_review(self) -> Review:
        return self.attempts[-1].review


class _Gate:
    """投机生成的输出闸门（口径 F93）。

    判断返回之前，投机那一版的正文**只缓着不放**：如果判断要求加限定语，
    这一版会被作废，而用户不该看到一段随后被收回的讲解。

    三个状态，单向：`waiting` →（`open` | `dropped`）。
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._lock = threading.Lock()
        self._buf: list[str] = []
        self._state = "waiting"
        self._emit = emit
        #: 置位即叫停投机生成（`AnthropicModel.stream` 在下一个事件处跳出、关连接）。
        self.cancel = threading.Event()

    def feed(self, text: str) -> None:
        with self._lock:
            if self._state == "open":
                self._emit(text)
            elif self._state == "waiting":
                self._buf.append(text)
            # dropped：丢弃

    def open(self) -> None:
        with self._lock:
            self._state = "open"
            for t in self._buf:
                self._emit(t)
            self._buf.clear()

    def drop(self) -> None:
        with self._lock:
            self._state = "dropped"
            self._buf.clear()
            self.cancel.set()


def _generate(model: Any, system: str, messages: list[dict[str, Any]],
              on_delta: Callable[[str], None] | None,
              cancel: threading.Event | None = None) -> Completion:
    """有 `stream()` 就流式，没有就整段调用后一次性交出去。

    假模型与实验侧的替身大多只实现了 `complete()`——它们照样走得通这条链，
    只是「流」只有一段。**协议不因流式而加必选方法**。
    """
    stream = getattr(model, "stream", None)
    if callable(stream):
        return stream(system, messages, on_delta=on_delta, cancel=cancel)
    completion = model.complete(system, messages)
    if on_delta is not None and completion.text and not (cancel is not None and cancel.is_set()):
        on_delta(completion.text)
    return completion


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
    turn_prefix: str = "",
    on_attempt: Callable[[Attempt], None] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    started_at: float | None = None,
) -> RoundResult:
    """跑一轮。

    `context` 可由调用方预先取好并在两组间共用（口径 X02）；不传则本函数自取。
    `review_enabled=False` 时**仍然完整计算审查结果**，只是不据此重生成——
    不算就没有对照（口径 2）。

    `turn_prefix` 原样塞在**本轮用户消息**最前面（口径 F57）。F4 的「没看懂」
    限定语走这里；措辞与触发条件都由调用方决定，本函数不认识它。

    `on_event` 收流式事件（口径 F94）；`started_at` 是 `time.perf_counter()` 的读数，
    给了的话首字延迟从它算起（`/explain` 传请求开始的那一刻，含检索）。
    """
    t0 = started_at if started_at is not None else time.perf_counter()
    emit = on_event or (lambda _e: None)
    first_token: list[float] = []

    def emit_delta(text: str) -> None:
        if not text:
            return
        if not first_token:
            first_token.append(time.perf_counter())
        emit({"type": "delta", "text": text})

    ctx = context if context is not None else retriever.fetch(question)
    trajectory.remember(ctx)
    checker = gap_checker or NullGapChecker()
    system = build_system(style_rules, ctx, course_label)

    # ---- 判断与投机生成同时开始（口径 F93）。
    #
    # 投机那一版按「没有限定语」拼。判断回来之后若 `render_gap_note` 为空，
    # 这条消息与串行时 `build_user_turn(ctx, q, gap)` **逐字节相同**——那一版直接留用。
    speculative_turn = build_user_turn(ctx, question, None, prefix=turn_prefix)
    gate = _Gate(emit_delta)
    box: dict[str, Any] = {}

    def judge() -> None:
        try:
            box["gap"] = checker.check(question, ctx)
        except BaseException as exc:  # noqa: BLE001 —— 原样交回主线程再抛
            box["error"] = exc
            gate.drop()
            return
        if render_gap_note(box["gap"]):
            gate.drop()
        else:
            gate.open()
        emit({"type": "gap", **box["gap"].to_dict()})

    judge_thread = threading.Thread(target=judge, name="gap-judge", daemon=True)
    judge_thread.start()
    speculative = _generate(model, system, trajectory.history + [
        {"role": "user", "content": speculative_turn}], gate.feed, gate.cancel)
    judge_thread.join()

    if "error" in box:
        # 实验侧不套 GuardedGapChecker：判断失败就该响亮地停（同改动前）。
        raise box["error"]
    gap: GapResult = box["gap"]

    discarded: tuple[Completion, ...] = ()
    if render_gap_note(gap):
        discarded = (speculative,)
        # 判断结果进本轮 user turn（每轮不同，不进 system——那会让缓存每轮失效）。
        user_turn = build_user_turn(ctx, question, gap, prefix=turn_prefix)
    else:
        user_turn = speculative_turn

    # ⚠️ **只拼一次，多处共用。** 下面 `trajectory.commit()` 记进历史的必须与
    # 真正发出去的**是同一个字符串**（`turn_prefix` 进来之后两次调用就不再等值）。
    turn = trajectory.history + [{"role": "user", "content": user_turn}]
    request_signature = model.request_signature(system, turn)

    attempts: list[Attempt] = []
    working = list(turn)
    accepted = False
    final_text = ""

    for attempt_index in range(max_regenerations + 1):
        if attempt_index == 0 and not discarded:
            completion = speculative          # 已经从闸门放出去了
        else:
            if attempt_index > 0:
                # 上一版已经给用户看过了，告诉前端清掉（口径 F94）。
                emit({"type": "reset", "reason": "review", "attempt": attempt_index})
            completion = _generate(model, system, working, emit_delta)
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

    trajectory.commit(user_turn, final_text)

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
        discarded=discarded,
        first_token_latency_ms=(round((first_token[0] - t0) * 1000, 1)
                                if first_token else None),
        wall_ms=round((time.perf_counter() - t0) * 1000, 1),
    )
