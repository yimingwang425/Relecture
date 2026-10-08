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

from .llm import FAKE_PREFIX, Completion, ToolCall, Usage, signature

__all__ = ["FakeExplainModel", "ScriptedToolModel"]

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


#: 一段能过默认配置审查的讲解（四段、每段六十字上下，落在段长默认区间里）。
_ANSWER_PARA = ("第二十页讲的是热机效率的定义：热机把热能转成机械功，"
                "卡诺效率等于一减去低温与高温的绝对温度之比。")


@dataclass
class ScriptedToolModel:
    """**照生产 `AnthropicModel.stream` 的签名写的**、会调工具的假模型（口径 F111）。

    `script` 逐次调用取一项：`("tool", [(name, input), ...])` 发起工具调用、
    `("text", "...")` 直接作答、`("preamble+tool", text, [(name, input), ...])`
    先吐几个字再调工具（system 不许这样，但模型不一定照做——测的是那条兜底）、
    `("raise", "原因")` 抛异常。用完了就回默认讲解。

    **`tool_choice={"type": "none"}` 时不发工具调用**，与真 API 一致——
    否则轮数上限那条判据测的是假模型，不是链路。
    """

    script: list[tuple[Any, ...]] = field(default_factory=list)
    name: str = FAKE_PREFIX + "claude-sonnet-5"
    answer: str = "\n\n".join([_ANSWER_PARA] * 4)
    params: dict[str, Any] = field(default_factory=lambda: {"effort": "medium"})
    calls: list[dict[str, Any]] = field(default_factory=list)

    def stream(self, system, messages, *, on_delta=None, cancel=None, tools=None,
               tool_choice=None, on_tool=None) -> Completion:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages],
                           "tools": [t["name"] for t in (tools or [])], "tool_choice": tool_choice})
        n = len(self.calls)
        step = self.script[n - 1] if n <= len(self.script) else ("text", self.answer)
        kind = step[0]
        if kind == "raise":
            raise RuntimeError(step[1])
        forbid = tool_choice is not None and tool_choice.get("type") == "none"
        if kind in ("tool", "preamble+tool") and (forbid or not tools):
            kind, step = "text", ("text", self.answer)

        text, calls = "", ()
        if kind == "text":
            text = step[1]
        elif kind == "preamble+tool":
            text, calls = step[1], step[2]
        else:
            calls = step[1]

        sent = ""
        for piece in (text[i:i + 20] for i in range(0, len(text), 20)):
            if cancel is not None and cancel.is_set():
                return Completion(text=sent, usage=Usage(input_tokens=100, output_tokens=len(sent)),
                                  latency_ms=2.0, model=self.name, stop_reason="cancelled")
            sent += piece
            if on_delta is not None:
                on_delta(piece)
        tool_calls = tuple(ToolCall(id=f"toolu_{n}_{i}", name=nm, input=dict(inp))
                           for i, (nm, inp) in enumerate(calls))
        for tc in tool_calls:
            if on_tool is not None:
                on_tool(tc.name)
        blocks: tuple[dict[str, Any], ...] = ()
        if tools:
            blocks = tuple(([{"type": "text", "text": text}] if text else [])
                           + [{"type": "tool_use", **tc.to_dict()} for tc in tool_calls])
        return Completion(text=text, usage=Usage(input_tokens=100, output_tokens=len(text) + 10),
                          latency_ms=2.0, model=self.name,
                          stop_reason="tool_use" if tool_calls else "end_turn",
                          tool_calls=tool_calls, content=blocks)

    def complete(self, system, messages) -> Completion:
        return self.stream(system, messages)

    def request_signature(self, system, messages) -> str:
        return signature(self.name, self.params, system, messages)
