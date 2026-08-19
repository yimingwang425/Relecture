"""开跑前的 sanity check：两组第 1 轮输入必须完全相同。

若第 1 轮就有系统性差异，说明实现有 bug 或随机性未控制，后面的曲线不可信。

分两层：

1. **输入同一性（硬检查）**——两组第 1 轮的 system / messages / 模型参数
   逐字节相同。这是确定性检查，能抓出实现 bug，必须过。
2. **输出同分布（观察）**——重复采样第 1 轮各 N 次，报告四个维度的均值与
   极差。n 小的时候这不是统计检验，只能看有没有量级上的异常，报告里据此
   写「未见系统性差异」而非「已验证同分布」。

**注意**：Claude Opus 5 / Sonnet 5 已移除 temperature/top_p/top_k，
无法通过固定温度来控制随机性。可固定的是 effort 与 thinking，两者都记进
实验元数据；剩余随机性来自模型采样本身，只能靠重复采样观察。

决策日期：2026-08-19
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any

from .models import Model
from .reviewer import RuleReviewer

__all__ = ["SanityResult", "check_inputs_identical", "sample_first_round"]


@dataclass(frozen=True, slots=True)
class SanityResult:
    inputs_identical: bool
    signature_a: str
    signature_b: str
    samples: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.inputs_identical

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs_identical": self.inputs_identical,
            "signature_with_review": self.signature_a,
            "signature_without_review": self.signature_b,
            "samples": self.samples,
        }


def check_inputs_identical(model: Model, system: str, first_question: str) -> tuple[bool, str, str]:
    """两组第 1 轮的请求指纹。历史为空，所以只差 system 与首个提问。"""
    messages = [{"role": "user", "content": first_question}]
    a = model.request_signature(system, messages)
    b = model.request_signature(system, messages)
    return a == b, a, b


def sample_first_round(
    *, model: Model, system: str, question: str, reviewer: RuleReviewer, repeats: int
) -> dict[str, Any]:
    """重复采样第 1 轮，报告各维度的均值与极差。

    这是观察不是检验：repeats 通常只有 3–5，说不了分布，只能看量级异常。
    """
    rows: list[dict[str, float | None]] = []
    for _ in range(max(0, repeats)):
        completion = model.complete(system, [{"role": "user", "content": question}])
        ev = reviewer.review(completion.text).evaluation
        rows.append({d.name: d.raw for d in ev.dimensions})

    if not rows:
        return {"repeats": 0, "note": "未采样"}

    summary: dict[str, Any] = {"repeats": len(rows)}
    for name in rows[0]:
        values = [r[name] for r in rows if r[name] is not None]
        summary[name] = (
            {
                "mean": round(mean(values), 4),
                "min": min(values),
                "max": max(values),
                "n": len(values),
            }
            if values
            else {"n": 0}
        )
    summary["note"] = (
        "重复采样为观察性质，n 过小不构成分布检验；"
        "temperature 在当前模型上不可用，剩余随机性来自模型采样本身"
    )
    return summary
