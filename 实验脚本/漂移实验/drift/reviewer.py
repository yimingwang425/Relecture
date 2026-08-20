"""审查 agent v0 —— 只查 A 类四维。

PRD §7.2 的论证是「生成方与审查方的目标相反」。v0 把这一点做到最直白：
审查方完全不调模型，只跑规则，没有任何自我合理化的空间。

B 类维度与 judge 校准不在 v0 范围内（等人工标注就绪）。因此本实验的结论
只覆盖「A 类维度的漂移」，报告里必须写明这一点。

决策日期：2026-08-19
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import _paths  # noqa: F401  — 注入 a_rules 路径
from a_rules.config import StyleConfig
from a_rules.evaluator import Evaluation, evaluate
from a_rules.glossary import Glossary

__all__ = ["Review", "RuleReviewer"]


@dataclass(frozen=True, slots=True)
class Review:
    passed: bool
    evaluation: Evaluation
    failed_dimensions: tuple[str, ...]
    feedback: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_dimensions": list(self.failed_dimensions),
            "feedback": self.feedback,
            "evaluation": self.evaluation.to_dict(),
        }


def _feedback_for(name: str, d: Any) -> str:
    """把维度结果翻译成模型能据以修改的具体指令。

    泛泛的「不符合风格要求」无法指导重生成——反馈必须指名道姓地说出
    哪个术语没标注、哪一段太长、哪一处引用超限。
    """
    det = d.details
    if name == "term_annotation":
        missing = det.get("violations", [])[:8]
        items = "、".join(f"{v['preferred_zh']}（{v['term']}）" for v in missing)
        return (
            f"术语标注率 {d.raw:.0%}，低于要求的 {det['policy']} 策略。"
            f"以下术语首次出现时未按「译文（原文）」标注：{items}。请补上。"
        )
    if name == "chinese_ratio":
        return (
            f"中文占比 {d.raw:.0%}，低于要求的 {d.target}。"
            "请减少未标注的英文表述，用中文讲解，英文只出现在术语括注里。"
        )
    if name == "paragraph_length":
        bad = det.get("out_of_range", [])[:5]
        where = "、".join(f"第 {b['line']} 行（{b['chars']} 字）" for b in bad)
        return (
            f"平均段落 {d.raw:.0f} 字，超出要求区间 {d.target}。"
            f"以下段落需要调整长度：{where}。"
        )
    if name == "quote_length":
        bad = det.get("violations", [])[:3]
        where = "、".join(f"第 {b['line']} 行（{b['chars']} 字）" for b in bad)
        return (
            f"单段引用最长 {d.raw} 字，超过上限 {det['limit']} 字（版权约束，不可协商）。"
            f"请压缩或改写以下引用：{where}。"
        )
    return f"{name} 不达标（当前 {d.raw}，要求 {d.target}）。"


@dataclass(frozen=True, slots=True)
class RuleReviewer:
    config: StyleConfig
    glossary: Glossary | None = None

    def review(self, text: str, annotated_terms: frozenset[str] = frozenset()) -> Review:
        """`annotated_terms` 见口径 D44：首现的作用域是整段对话，由 runner 跨轮累积。"""
        ev = evaluate(text, self.config, self.glossary, annotated_terms=annotated_terms)
        failed = tuple(d.name for d in ev.scored if not d.passed)
        if not failed:
            return Review(True, ev, (), "")
        lines = [
            "上一版讲解未通过风格审查，请按以下几点重写（保持内容不变，只改表达）：",
            *(f"{i}. {_feedback_for(n, ev.by_name(n))}" for i, n in enumerate(failed, 1)),
            "直接给出修改后的完整讲解，不要解释你做了什么改动。",
        ]
        return Review(False, ev, failed, "\n".join(lines))
