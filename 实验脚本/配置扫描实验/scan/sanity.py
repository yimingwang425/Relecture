"""开跑前的 sanity check。

沿用第 0 周的两层结构（`漂移实验/drift/sanity.py`），但第一层的内容变了：
接检索之后，「两组输入相同」不只是 system 与 messages 相同，
还要求**两组拿到的检索结果相同**。

1. **输入同一性（硬检查）**——两组第 1 轮的 system / messages / 模型参数
   逐字节相同（比 sha256），且 ContextPack 签名相同。不过就中止。
2. **配置点可分性（硬检查）**——5 个点必须产出 5 个不同的请求指纹。
   两个点指纹相同 = 配置写重了，扫描退化成重复跑同一个点，**而它不会报错**。

第二层是本轮新增的。第 0 周只有一个配置，不存在这个失效形态。

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import _paths  # noqa: F401
from explain.context import Retriever
from explain.llm import Model
from explain.prompt import build_system, build_user_turn, system_cache_status
from explain.style_prompt import render_style_rules

from .points import POINTS

__all__ = ["SanityResult", "check_inputs_identical", "check_points_distinguishable"]


@dataclass(frozen=True, slots=True)
class SanityResult:
    passed: bool
    lines: list[str]
    details: dict[str, Any]


def _request(model: Model, point, retriever: Retriever, question: str, course: str):
    ctx = retriever.fetch(question)
    system = build_system(render_style_rules(point.config()), ctx, course)
    messages = [{"role": "user", "content": build_user_turn(ctx, question)}]
    return model.request_signature(system, messages), ctx.signature(), system


def check_inputs_identical(
    model: Model, retriever: Retriever, question: str, course: str
) -> SanityResult:
    """两组第 1 轮的输入必须逐字节相同。

    两组的差异**只应该是「审查环节有没有开」**，那发生在生成之后。
    第 1 轮请求若已不同，后面所有曲线都不可信。
    """
    lines: list[str] = []
    sigs = {}
    for condition in ("with_review", "without_review"):
        # 两组走同一条构造路径、同一份 ContextPack——这本身就是口径 X02
        sigs[condition] = _request(model, POINTS[0], retriever, question, course)

    same_req = sigs["with_review"][0] == sigs["without_review"][0]
    same_ctx = sigs["with_review"][1] == sigs["without_review"][1]
    lines.append(
        ("✓" if same_req else "✗") + f" 两组第 1 轮请求指纹 {sigs['with_review'][0]}"
    )
    lines.append(("✓" if same_ctx else "✗") + f" 两组 ContextPack 签名 {sigs['with_review'][1]}")

    status = system_cache_status(sigs["with_review"][2])
    lines.append(
        f"· system 段 {status['system_chars']} 字符 / 约 {status['system_tokens_estimated']} token"
    )
    lines.append(f"· {status['note']}")
    return SanityResult(same_req and same_ctx, lines, {"system_cache": status})


def check_points_distinguishable(
    model: Model, retriever: Retriever, question: str, course: str
) -> SanityResult:
    """5 个配置点必须产出 5 个不同的请求指纹。

    **这条查的是「自变量真的变了」。** 配置点若只改了判定不改 prompt，
    请求指纹会相同——那时测出来的触发率上升是平凡结论（判据变严了），
    而不是「模型能不能遵守偏离常态的指令」（口径 X05）。
    """
    seen: dict[str, str] = {}
    for point in POINTS:
        sig, _ctx, _sys = _request(model, point, retriever, question, course)
        seen[point.id] = sig

    distinct = len(set(seen.values())) == len(seen)
    lines = [("✓" if distinct else "✗") + f" {len(seen)} 个配置点产出 {len(set(seen.values()))} 个不同请求"]
    if not distinct:
        dupes = [pid for pid, s in seen.items() if list(seen.values()).count(s) > 1]
        lines.append(f"    重复的点：{dupes}——配置只改了判定没改 prompt，或者配置写重了")
    return SanityResult(distinct, lines, {"signatures": seen})
