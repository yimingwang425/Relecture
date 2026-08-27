"""假模型：不调 API 跑通全链路。

**它不是模拟真实模型行为**，只用来验证链路——审查触发、重生成计数、
日志结构、成本核算、缓存字段。曲线形状是写死的，不能当结论看。

与 `漂移实验` 那个假模型的差别：那个模拟的是**术语标注率随轮次衰减**
（D02/D44 那次实验的被测量），本轮术语维度已下线，那个衰减无关紧要。
本轮要能被链路验证的是**段落长度维度会不会触发重生成**，
所以这个假模型的可控量是**段落长度**。

**衰减/违规挂在对话轮次上，不挂调用次数。** 挂调用次数的话，
有审查组每轮最多调 3 次，违规推进速度是对照组的 3 倍，
假跑输出会呈现「审查让结果更差」的形态——那是假模型的缺陷，
却长得像一个结论（第 0 周踩过这个坑，写进了实验 README）。

决策日期：2026-08-22
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from .llm import FAKE_PREFIX, Completion, Usage, signature

__all__ = ["FakeExplainModel"]

_FILLER = (
    "这一段是用于验证链路的占位讲解文字，内容本身没有意义，"
    "但长度可控，便于让段落长度维度稳定地落在或落出配置区间。"
)


@dataclass
class FakeExplainModel:
    """段落长度可控的假模型。

    `target_chars` 决定每段大致字数。**收到审查反馈时会往目标区间靠**——
    否则重生成永远不收敛，链路里「反馈驱动」那一段就没被验证过
    （它会一直走到触顶，而触顶路径与达标路径是两条不同的代码路径）。
    """

    name: str = FAKE_PREFIX + "claude-sonnet-5"
    seed: int = 0
    replicate: int = 0
    target_chars: int = 70
    #: 收到反馈后向该值收敛，模拟「反馈起作用了」
    corrected_chars: int = 55
    paragraphs: int = 4
    params: dict[str, Any] = field(default_factory=lambda: {"effort": "medium"})

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion:
        started = time.perf_counter()
        conversation_round = sum(1 for m in messages if m["role"] == "user")
        # 反馈轮的标志：最后一条 user 消息是审查反馈而非提问
        corrected = "未通过风格审查" in str(messages[-1].get("content", ""))
        rng = random.Random(f"{self.seed}:{self.replicate}:{conversation_round}:{corrected}")

        want = self.corrected_chars if corrected else self.target_chars
        paras = []
        for _ in range(self.paragraphs):
            n = max(8, want + rng.randint(-6, 6))
            body = (_FILLER * (n // len(_FILLER) + 1))[:n]
            paras.append(body)
        text = "\n\n".join(paras)

        # 用量刻意造得有结构：第 1 轮写缓存，之后读缓存。
        # 假跑也要走通成本核算链路，否则成本日志到真实调用才第一次被检验。
        sys_tokens = len(system) // 3
        usage = Usage(
            input_tokens=40,
            output_tokens=len(text) // 2,
            cache_creation_input_tokens=sys_tokens if conversation_round == 1 else 0,
            cache_read_input_tokens=0 if conversation_round == 1 else sys_tokens,
        )
        return Completion(
            text=text,
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            model=self.name,
            stop_reason="end_turn",
        )

    def request_signature(self, system: str, messages: list[dict[str, Any]]) -> str:
        return signature(self.name, self.params, system, messages)
