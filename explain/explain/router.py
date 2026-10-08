"""路由 agent（2026-10-08，口径 F110）。

多智能体链路的第一跳：**这一句要不要去翻课件**。

| 取值 | 是什么 | 之后 |
| --- | --- | --- |
| `course` | 问课件内容、追问上一轮、要例子 / 推导 / 换个说法 | 讲解 agent（Sonnet，带工具） |
| `chat` | 寒暄、致谢、问助手自己能做什么——**不需要任何课件内容就能答** | 路由这一跳顺手给出的简短回复，**不调 Sonnet、不做兜底判断** |

## 拿不准一律 `course`

两类错误代价不对称：把课件问题错分成 `chat`，学生拿到一句没有出处的闲聊，
而且**看起来是正常回复**（PRD §6.2「它看起来成功了」那一类）；反向只是多花一次讲解。
所以 prompt 写明「拿不准选 course」，并且**调用失败、超时、输出不合法都按 `course` 走**——
回落到「没有路由时的行为」，与 `rag.py` 四个助手同一条（F42）。

## 与检索并行，不在关键路径上

路由只看提问与最近两轮，不依赖检索结果，所以调用方（`api/main.py`）让它与检索同时跑。
检索本身就要 1–3 s（嵌入 + 改写 + 重排），Haiku 的一次短输出通常更早回来。
被判成 `chat` 的那一轮，检索结果作废（钱照记，见 `RouteResult`）。

## 模型 id 单独写

`ROUTER_MODEL_NAME` 与 `RAG_MODEL_NAME`、`GAP_MODEL_NAME` 数值相同、**分开写**——
三个独立的选择（`rag.py` 模块说明第 1 条的同一个理由）。

决策日期：2026-10-08
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .llm import Completion, Model
from .rag import RAG_TIMEOUT_S, _guarded, _json_obj

__all__ = [
    "ROUTER_MODEL_NAME",
    "ROUTER_SYSTEM",
    "ROUTES",
    "Router",
    "RouteResult",
    "build_router",
]

#: 路由用的模型。与检索助手、兜底判断**数值相同、分开写**（见模块说明）。
ROUTER_MODEL_NAME = "claude-haiku-4-5-20251001"

#: 合法取值。**取值不在域内即判无效**，不做就近解释（G01 同一条），按 `course` 走。
ROUTES: tuple[str, ...] = ("course", "chat")

ROUTER_SYSTEM = """你是课件讲解助手的分诊员。学生上传了课程课件，正在向助手提问。
判断学生这一句**需不需要查课件**才能回答。只输出一个 JSON 对象，不要有其他文字：

{"route": "course" | "chat", "reply": "……"}

- `course`：问课件里的概念、公式、例子、推导；追问上一轮的讲解（「为什么」「再讲细一点」
  「那第二步呢」）；让助手换个说法、举个例子。这时 reply 写空字符串。
- `chat`：寒暄、道谢、道别、问助手是谁或能做什么——**完全不需要课件内容**就能回答的话。
  这时 reply 写一两句简短、自然的中文回复，不要编造任何课程内容。

**拿不准就选 course。** 把课件问题错判成 chat 的代价，远大于反过来。
不要回答课件问题本身，你只做分诊。"""


@dataclass(frozen=True, slots=True)
class RouteResult:
    route: str
    reply: str | None = None
    #: `"model"` 判了 · `"error"` 调用失败或超时 · `"invalid"` 输出不合法 · `"none"` 没有路由器。
    #: 后三种 `route` 恒为 `course`——**回落不等于判了**，分开记（同 `RewriteResult`）。
    implementation: str = "model"
    #: `None` = 未知（超时：请求可能已计费，F73 同一条）。
    cost_usd: float | None = 0.0
    latency_ms: float = 0.0
    model: str | None = None
    error: str | None = None
    #: 判成 `chat` 时，这次调用就是这一轮的「讲解」——调用方拿它当第 1 版。
    completion: Completion | None = None

    @property
    def answers_directly(self) -> bool:
        return self.route == "chat" and bool(self.reply)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "implementation": self.implementation,
            "cost_usd": self.cost_usd,
            "latency_ms": round(self.latency_ms, 1),
            "model": self.model,
            "error": self.error,
        }


@dataclass
class Router:
    model: Model
    timeout_s: float = RAG_TIMEOUT_S
    #: 只带最近一轮：分诊要的是「这句是不是在接着上一轮问」，不是全文。
    max_history_messages: int = 2
    max_message_chars: int = 300

    def _render(self, question: str, history: list[dict[str, Any]]) -> str:
        lines = []
        for m in history[-self.max_history_messages:]:
            who = "学生" if m.get("role") == "user" else "助手"
            lines.append(f"{who}：{str(m.get('content', ''))[: self.max_message_chars]}")
        head = "【上一轮】\n" + "\n".join(lines) + "\n\n" if lines else ""
        return f"{head}【这一句】\n{question}"

    def route(self, question: str, history: list[dict[str, Any]] | None = None) -> RouteResult:
        msgs = [{"role": "user", "content": self._render(question, history or [])}]
        c, err, ms = _guarded(lambda: self.model.complete(ROUTER_SYSTEM, msgs), self.timeout_s)
        name = getattr(self.model, "name", None)
        if c is None:
            return RouteResult(route="course", implementation="error", cost_usd=None,
                               latency_ms=ms, model=name, error=err)
        data = _json_obj(c.text)
        route = data.get("route") if data else None
        reply = data.get("reply") if data else None
        if route not in ROUTES or (route == "chat" and not (isinstance(reply, str) and reply.strip())):
            # chat 却没给回复也算不合法：没有回复就只能去讲解，那就是 course。
            return RouteResult(route="course", implementation="invalid", cost_usd=c.cost,
                               latency_ms=ms, model=name, error=f"输出不合法：{c.text[:200]}")
        if route == "chat":
            text = reply.strip()[:400]
            return RouteResult(
                route="chat", reply=text, cost_usd=c.cost, latency_ms=ms, model=name,
                # 第 1 版讲解的文本是回复本身，不是那段 JSON——审查与落库看的是学生看到的字。
                completion=Completion(text=text, usage=c.usage, latency_ms=c.latency_ms,
                                      model=c.model, stop_reason=c.stop_reason),
            )
        return RouteResult(route="course", cost_usd=c.cost, latency_ms=ms, model=name)


def build_router(model: Model | None = None) -> Router:
    """路由器的**唯一构造处**。`model` 只给测试注入假模型用。"""
    if model is None:
        from .llm import AnthropicModel

        model = AnthropicModel(
            name=ROUTER_MODEL_NAME,
            effort=None,            # Haiku 4.5 不支持 effort（gap.py 同一条，实测 400）
            thinking="disabled",
            max_tokens=200,
            cache_system=False,     # 系统提示远低于最小可缓存长度
            cache_conversation=False,
        )
    return Router(model)
