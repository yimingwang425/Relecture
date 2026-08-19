"""模型抽象层。

PRD §7.3 要求「不硬编码模型，做抽象层，配置可切换」。本模块是那层抽象，
外加成本核算——§9.1 要求「从第一天起将每次调用的 token 数与成本写入日志」。

**没有 temperature。** Claude Opus 5 / Sonnet 5 已移除 `temperature`、`top_p`、
`top_k`，传入返回 400。可固定并记录的随机性相关参数是 `effort` 与 `thinking`，
实验元数据记的是这两个。这一点必须显式写下来，否则「固定温度以控制随机性」
会被当成已完成的事项。

决策日期：2026-08-19
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = ["Usage", "Completion", "Model", "FakeModel", "AnthropicModel", "cost_usd", "PRICING"]

#: 每百万 token 的美元单价 (输入, 输出)。取自 claude-api 技能的模型表。
#: Sonnet 5 在 2026-08-31 前有 $2/$10 的introductory价，此处按正价计，
#: 成本估计偏保守——宁可高估也不要在 §8.4 的 <$0.05 判定上乐观。
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

#: 缓存写入 1.25×、读取 0.1×（5 分钟 TTL）。课件全量进 system 且会话内不变，
#: 缓存命中率直接决定单会话成本能否压到 §9.1 的 $0.05 以下。
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


#: 假模型的名字前缀。定价时剥掉，这样 --fake 也能走通成本核算链路——
#: 否则假跑时成本恒为 0，等到真实调用才发现成本日志从没被验证过。
FAKE_PREFIX = "fake:"


def cost_usd(model: str, usage: Usage) -> float:
    """按 PRICING 折算美元。未知模型返回 0 并不报错——成本缺失不该中断实验。"""
    rates = PRICING.get(model.removeprefix(FAKE_PREFIX))
    if rates is None:
        return 0.0
    inp, out = rates
    return (
        usage.input_tokens * inp
        + usage.cache_creation_input_tokens * inp * _CACHE_WRITE_MULTIPLIER
        + usage.cache_read_input_tokens * inp * _CACHE_READ_MULTIPLIER
        + usage.output_tokens * out
    ) / 1_000_000


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    usage: Usage
    latency_ms: float
    model: str
    stop_reason: str | None = None

    @property
    def cost(self) -> float:
        return cost_usd(self.model, self.usage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stop_reason": self.stop_reason,
            "latency_ms": round(self.latency_ms, 1),
            "usage": self.usage.to_dict(),
            "cost_usd": self.cost,
            "chars": len(self.text),
        }


class Model(Protocol):
    """一次调用一个回合。实现方负责重试与错误处理。"""

    name: str

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion: ...

    def request_signature(self, system: str, messages: list[dict[str, Any]]) -> str:
        """请求内容的指纹。sanity check 用它证明两组的第 1 轮输入完全相同。"""
        ...


def _signature(model: str, params: dict[str, Any], system: str, messages: list[dict[str, Any]]) -> str:
    import json

    payload = json.dumps(
        {"model": model, "params": params, "system": system, "messages": messages},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


@dataclass
class AnthropicModel:
    """真实调用。`anthropic` SDK 与 API key 都在运行时才要求。"""

    name: str = "claude-sonnet-5"
    effort: str = "medium"
    max_tokens: int = 8000
    thinking: str = "adaptive"  # adaptive | disabled
    cache_system: bool = True
    #: 在最近一轮消息末尾再放一个缓存断点。对话历史逐轮增长，20 轮下来
    #: 它远比课件本身贵——只缓存 system 会让历史每轮按全价重算一遍。
    cache_conversation: bool = True
    _client: Any = None

    @property
    def params(self) -> dict[str, Any]:
        """写进实验元数据的可复现参数。**没有 temperature**——见模块 docstring。"""
        return {
            "effort": self.effort,
            "thinking": self.thinking,
            "max_tokens": self.max_tokens,
            "cache_system": self.cache_system,
            "cache_conversation": self.cache_conversation,
            "temperature": None,
            "temperature_note": "Opus 5 / Sonnet 5 已移除 temperature/top_p/top_k，传入返回 400",
        }

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - 环境相关
                raise RuntimeError(
                    "需要 anthropic SDK：pip install anthropic（a_rules 本身仍是零依赖）"
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, messages: list[dict[str, Any]]) -> Completion:
        client = self._ensure_client()
        # 课件全量在 system 且会话内不变 → 必须开 prompt caching（PRD §7.3）。
        # 断点放在 system 末块：渲染顺序是 tools → system → messages，
        # 放这里能把整段课件缓存住，而每轮变化的提问在断点之后。
        system_block: Any = (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if self.cache_system
            else system
        )
        sent = messages
        if self.cache_conversation and messages:
            # 断点打在最近一轮的最后一个内容块上：下一轮请求即可复用整段历史前缀
            sent = list(messages[:-1])
            last = messages[-1]
            content = last["content"]
            blocks = (
                [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
            )
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            sent.append({**last, "content": blocks})

        kwargs: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self.max_tokens,
            "system": system_block,
            "messages": sent,
            "output_config": {"effort": self.effort},
        }
        if self.thinking == "disabled":
            kwargs["thinking"] = {"type": "disabled"}
        else:
            kwargs["thinking"] = {"type": "adaptive"}

        started = time.perf_counter()
        with client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
        latency = (time.perf_counter() - started) * 1000

        text = "".join(b.text for b in message.content if b.type == "text")
        u = message.usage
        return Completion(
            text=text,
            usage=Usage(
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            ),
            latency_ms=latency,
            model=message.model,
            stop_reason=message.stop_reason,
        )

    def request_signature(self, system: str, messages: list[dict[str, Any]]) -> str:
        return _signature(self.name, self.params, system, messages)
