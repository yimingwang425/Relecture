"""**再导出**：模型抽象与成本核算已提升到 `explain/llm.py`（2026-08-22）。

提升的原因是定价表带 8/31 到期日：复制一份就等于**两个到期日**，
一个按时回落、另一个不会，而且两边都不报错。定价必须只有一处出处。

本文件保留的只有 `FakeModel`——它模拟的是**术语标注率随轮次衰减**，
专为 D02/D44 那次实验而写，与 v1 的三维指标集无关，不该进生产包。

**本文件的数值行为逐字节不变**，由 `tests/test_pricing_unchanged.py` 钉住。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from . import _paths  # noqa: F401  — 注入 explain 路径
from explain.llm import (  # noqa: F401  — 再导出，保持 drift 既有 import 不变
    FAKE_PREFIX,
    PRICING,
    AnthropicModel,
    Completion,
    Model,
    Usage,
    cost_usd,
    rates_for,
    signature as _signature,
)

__all__ = [
    "Usage", "Completion", "Model", "FakeModel", "AnthropicModel",
    "cost_usd", "rates_for", "PRICING", "FAKE_PREFIX",
]


@dataclass
class FakeModel:
    """确定性假模型，用于在没有 API key 的情况下端到端跑通全链路。

    刻意让输出质量随轮次下降：这样能验证漂移曲线的绘制、审查触发、重生成计数
    确实工作。**它不是模拟真实漂移**——曲线形状是写死的，不能当结论看。

    衰减挂在**对话轮次**上而非调用次数上。若挂调用次数，重生成也会推进衰减，
    有审查组每轮调用最多 3 次、衰减快 3 倍，假跑输出会呈现「审查让结果更差」
    的形态——那是假模型的缺陷，却长得像一个结论，很容易被误读。
    """

    name: str = FAKE_PREFIX + "claude-sonnet-5"
    seed: int = 0
    replicate: int = 0
    decay: float = 0.06
    params: dict[str, Any] = field(default_factory=lambda: {"effort": "medium"})

    _TERMS = [
        ("热机", "heat engine"), ("功率密度", "power density"), ("核裂变", "nuclear fission"),
        ("燃料棒", "fuel rod"), ("碳封存", "carbon sequestration"), ("生物质", "biomass"),
        ("太阳常数", "solar constant"), ("冷凝器", "condenser"), ("堆芯", "reactor core"),
        ("废热", "waste heat"),
    ]

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion:
        started = time.perf_counter()
        # 对话轮次 = 历史里的 user 消息数；重生成产生的追加轮不计入衰减
        conversation_round = sum(1 for m in messages if m["role"] == "user")
        rng = random.Random(f"{self.seed}:{self.replicate}:{len(messages)}:{messages[-1]['content'][:40]}")
        # 标注率随轮次线性衰减，用于验证曲线绘制链路
        jitter = rng.uniform(-0.12, 0.12)  # 让各 replicate 有离散度，验证聚合链路
        annotate_p = min(1.0, max(0.05, 0.95 - self.decay * (conversation_round - 1) + jitter))
        paras = []
        for i in range(rng.randint(3, 5)):
            picked = rng.sample(self._TERMS, k=rng.randint(1, 3))
            body = "，".join(
                f"{zh}（{en}）" if rng.random() < annotate_p else zh for zh, en in picked
            )
            paras.append(f"关于{body}，这里给出一段用于验证链路的占位讲解文字。" * rng.randint(1, 3))
        text = "# 占位讲解\n\n" + "\n\n".join(paras)
        usage = Usage(
            input_tokens=len(system) // 4 if conversation_round == 1 else 40,
            output_tokens=len(text) // 2,
            cache_creation_input_tokens=len(system) // 4 if conversation_round == 1 else 0,
            cache_read_input_tokens=0 if conversation_round == 1 else len(system) // 4,
        )
        return Completion(
            text=text,
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
            model=self.name,
            stop_reason="end_turn",
        )

    def request_signature(self, system: str, messages: list[dict[str, Any]]) -> str:
        return _signature(self.name, self.params, system, messages)
