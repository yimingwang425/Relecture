"""模型抽象层 + 成本核算。**定价的唯一出处。**

PRD §7.3 要求「不硬编码模型，做抽象层，配置可切换」。本模块是那层抽象，
外加成本核算——§9.1 要求「从第一天起将每次调用的 token 数与成本写入日志」。

## 为什么它从 `漂移实验/drift/models.py` 提升到这里（2026-08-22）

本模块原本住在漂移实验里。F3a 的配置扫描实验同样要报成本，
若各自持有一份定价表，就会有**两个 8/31 到期日**——一个按时回落到正价、
另一个继续用 introductory 价，而且**两边都不会报错**。
§9.1 承诺的是报准单位经济，那种偏差正是它最怕的形态。

故：本模块是定价与用量核算的唯一出处，`drift/models.py` 退化为再导出，
其数值行为逐字节不变（有测试钉住）。

**没有 temperature。** Claude Opus 5 / Sonnet 5 已移除 `temperature`、`top_p`、
`top_k`，传入返回 400。可固定并记录的随机性相关参数是 `effort` 与 `thinking`，
实验元数据记的是这两个。这一点必须显式写下来，否则「固定温度以控制随机性」
会被当成已完成的事项。

决策日期：2026-08-19
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

__all__ = [
    "Usage", "Completion", "Model", "AnthropicModel",
    "cost_usd", "rates_for", "PRICING", "FAKE_PREFIX", "signature",
]

#: 假模型的名字前缀。定价时剥掉，这样 --fake 也能走通成本核算链路——
#: 否则假跑时成本恒为 0，等到真实调用才发现成本日志从没被验证过。
FAKE_PREFIX = "fake:"

#: 每百万 token 的美元单价。含 introductory 优惠价及其到期日。
#:
#: **不要为了「保守」用正价。** §9.1 承诺的是报准单位经济，不是报保守值——
#: 高估不是安全，是错。首次单轨实测：脚本按正价报 $1.0971，按 introductory
#: 价重算为 $0.7314，与后台数字对得上。
PRICING: dict[str, dict[str, Any]] = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {
        "input": 3.00,
        "output": 15.00,
        # introductory 价，到期后自动回落到上面的正价
        "intro_input": 2.00,
        "intro_output": 10.00,
        "intro_until": "2026-08-31",
    },
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}


def rates_for(model: str, on: date | None = None) -> tuple[float, float] | None:
    """取该日期适用的 (输入, 输出) 单价。未知模型返回 None。

    **带日期后缀的模型 id 走前缀匹配**（`claude-haiku-4-5-20251001` →
    `claude-haiku-4-5`）。不为每个日期后缀单列一行：那会让同一个模型有两份单价，
    而定价必须只有一处出处——正是本模块从漂移实验提升上来的理由。
    """
    name = model.removeprefix(FAKE_PREFIX)
    entry = PRICING.get(name)
    if entry is None:
        # 最长前缀匹配，避免 haiku-4-5 意外匹配到更短的键
        candidates = [k for k in PRICING if name.startswith(k)]
        if candidates:
            entry = PRICING[max(candidates, key=len)]
    if entry is None:
        return None
    until = entry.get("intro_until")
    if until and (on or date.today()) <= date.fromisoformat(until):
        return entry["intro_input"], entry["intro_output"]
    return entry["input"], entry["output"]


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


def cost_usd(model: str, usage: Usage, on: date | None = None) -> float:
    """按适用价目折算美元。未知模型返回 0 并不报错——成本缺失不该中断实验。

    三类输入 token 互不重叠：`input_tokens` 是**未命中缓存的余量**，
    与 `cache_read_input_tokens` / `cache_creation_input_tokens` 不相加重复。
    """
    rates = rates_for(model, on)
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


def signature(model: str, params: dict[str, Any], system: str, messages: list[dict[str, Any]]) -> str:
    import json

    payload = json.dumps(
        {"model": model, "params": params, "system": system, "messages": messages},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class AnthropicModel:
    """真实调用。`anthropic` SDK 与 API key 都在运行时才要求。"""

    name: str = "claude-sonnet-5"
    #: `None` = **不发 `output_config`**。Haiku 4.5 不支持 effort 参数，
    #: 传了返回 400（实测 2026-09-05）。分层调用（PRD §7.3）要求两档模型
    #: 走同一个抽象层，所以差异在这里吸收，而不是再写一个类。
    effort: str | None = "medium"
    max_tokens: int = 8000
    thinking: str = "adaptive"  # adaptive | disabled
    cache_system: bool = True
    #: 在最近一轮消息末尾再放一个缓存断点。对话历史逐轮增长，20 轮下来
    #: 它远比课件本身贵——只缓存 system 会让历史每轮按全价重算一遍。
    cache_conversation: bool = True
    #: 客户端持有凭据，排除出 repr 与比较——避免任何 print(model) / 断言失败
    #: 的输出里出现它。
    _client: Any = field(default=None, repr=False, compare=False)

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
        """只从环境变量 ANTHROPIC_API_KEY 读凭据。

        刻意不走 SDK 的零参构造：零参构造会依次回退到 ANTHROPIC_AUTH_TOKEN、
        `ant auth login` 的 OAuth profile、WIF 等来源——实验跑起来了，却说不清
        这笔钱是从哪个凭据出的。显式读取 + 缺失即报错，比静默回退可控。

        **凭据只在进程内存里传给 SDK，不写入任何文件、不进日志、不进 repr。**
        """
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - 环境相关
                raise RuntimeError(
                    "需要 anthropic SDK：pip install anthropic（a_rules 本身仍是零依赖）"
                ) from exc
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError(
                    "未设置 ANTHROPIC_API_KEY。请用 .env 局部注入，不要全局 export"
                    "——全局设置会让 Claude Code 从订阅计费切到 API 按量计费。"
                    "用法见 漂移实验/README.md「运行前：凭据注入」。"
                )
            self._client = anthropic.Anthropic(api_key=api_key)
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
        }
        if self.effort is not None:
            kwargs["output_config"] = {"effort": self.effort}
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
        return signature(self.name, self.params, system, messages)
