"""审查 agent · v1 三维，零模型成本。

PRD §7.2 的论证是「生成方与审查方的目标相反」。v1 把这一点做到最直白：
审查方完全不调模型，只跑规则，没有自我合理化的空间。

## 与 `漂移实验/drift/reviewer.py` 的关系

四条口径**逐条继承**（来源：漂移实验 README「实验设计与其边界」）：

1. 审查只查规则，零模型成本
2. 重生成**反馈驱动，不是重试**——当前模型已移除 temperature，纯重试大概率
   复现同样的问题；把反馈作为一轮追加指令，模型才有依据修改
3. 反馈必须**指名道姓**：哪一段太长、哪一处引用超限。泛泛的「不符合风格要求」
   无法指导重生成
4. 无审查组**照常计算指标，只记录不干预**——不算就没有对照

变化的只有维度集合：**v1 三维**（PRD §3.2）。术语反馈整条删除——
维度不计分了还去反馈它，等于逼模型花输出 token 做一件审查不看的事。

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from a_rules.config import StyleConfig
from a_rules.evaluator import Evaluation, evaluate
from a_rules.glossary import Glossary

from .llm import Usage

__all__ = ["Review", "Reviewer", "RuleReviewer"]


@dataclass(frozen=True, slots=True)
class Review:
    passed: bool
    evaluation: Evaluation
    failed_dimensions: tuple[str, ...]
    feedback: str
    #: 风格审查员的这一次判断（`style_review.StyleJudgment`）。规则审查、或这一版没问它时为 None。
    style_run: Any = None

    @property
    def usage(self) -> Usage:
        return self.style_run.usage if self.style_run is not None else Usage()

    @property
    def cost_usd(self) -> float:
        """计入单轮总成本。超时时金额未知（`style_run.cost_usd is None`），这里按 0 加，原值在 `style_run` 里。"""
        return (self.style_run.cost_usd or 0.0) if self.style_run is not None else 0.0

    @property
    def latency_ms(self) -> float:
        return self.style_run.latency_ms if self.style_run is not None else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_dimensions": list(self.failed_dimensions),
            "feedback": self.feedback,
            "evaluation": self.evaluation.to_dict(),
            "style_run": self.style_run.to_dict() if self.style_run is not None else None,
        }


def _feedback_for(name: str, d: Any) -> str:
    det = d.details
    if name == "paragraph_length":
        bad = det.get("out_of_range", [])[:5]
        where = "、".join(f"第 {b['line']} 行（{b['chars']} 字）" for b in bad)
        return (
            f"平均段落 {d.raw:.0f} 字，超出要求区间 {d.target}。"
            f"以下段落需要调整长度：{where}。"
            "**不要靠拆行或合并空行来凑**——请真正改写内容的详略。"
        )
    if name == "chinese_ratio":
        return (
            f"中文占比 {d.raw:.0%}，低于要求的 {d.target}。"
            "请减少未标注的英文表述，用中文讲解，英文只出现在专业名词的括注里。"
        )
    if name == "quote_length":
        bad = det.get("violations", [])[:3]
        where = "、".join(f"第 {b['line']} 行（{b['chars']} 字）" for b in bad)
        return (
            f"单段引用最长 {d.raw} 字，超过上限 {det['limit']} 字（版权约束，不可协商）。"
            f"请压缩或改写以下引用：{where}。"
        )
    return f"{name} 不达标（当前 {d.raw}，要求 {d.target}）。"


class Reviewer(Protocol):
    """讲解链路要的审查器接口。`RuleReviewer`（实验侧）与 `style_review.StyleReviewer`（生产）都满足它。"""

    def review(self, text: str, *, question: str = "", use_judge: bool = True) -> Review: ...


@dataclass(frozen=True, slots=True)
class RuleReviewer:
    """三维规则审查。

    `glossary` 仍可传入，但**只用于 `chinese_ratio` 的括注排除口径 D15**，
    不作达标分母——术语维度在 v1 已下线（PRD §8.1）。
    多课程下没有术语表时，括注排除走形态规则（口径 D48），本类不需要知道差别。
    """

    config: StyleConfig
    glossary: Glossary | None = None

    def review(self, text: str, *, question: str = "", use_judge: bool = True) -> Review:
        """`question` / `use_judge` 与 `style_review.StyleReviewer` 同一个接口，这里用不到。"""
        ev = evaluate(text, self.config, self.glossary)
        failed = tuple(d.name for d in ev.scored if not d.passed)
        if not failed:
            return Review(True, ev, (), "")
        lines = [
            "上一版讲解未通过风格审查，请按以下几点重写（保持内容不变，只改表达）：",
            *(f"{i}. {_feedback_for(n, ev.by_name(n))}" for i, n in enumerate(failed, 1)),
            "直接给出修改后的完整讲解，不要解释你做了什么改动。",
        ]
        return Review(False, ev, failed, "\n".join(lines))
