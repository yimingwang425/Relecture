"""维度结果的统一返回结构。

四个维度函数签名一致、返回同一类型，聚合层才能不做特判。

两条来自 PRD 的约束：

- **不做混合分**（§8.1 设计要点）。混合分会掩盖结构性问题——「长度控制良好
  但从不给类比」仍能得到 88% 的表面达标分。本结构按维度分别报告，聚合层只
  额外给一个 `all_activated_passed` 布尔量。
- **未激活与无法计算必须与「得 0 分」区分**（§8.1 + 口径 D03）。三者的
  `raw` 都不是数字，但含义完全不同：未激活的维度不进分母，无法计算的样本
  应被标为无效，只有真正测出来的 0 才是 0。用 `activated` + `reason` 区分。

决策日期：2026-08-19
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Reason", "DimensionResult"]


class Reason:
    """`raw is None` 时的原因常量。用字符串而非枚举，便于直接写入埋点日志。"""

    NOT_ACTIVATED = "not_activated"  # 配置未激活该维度
    EMPTY_TEXT = "empty_text"  # 没有任何可计量内容（空串/纯空白/只有代码块）
    EMPTY_GLOSSARY = "empty_glossary"  # 术语表为空
    NO_TERM_OCCURRENCE = "no_term_occurrence"  # 术语表非空但文本中一条都没命中
    NO_PARAGRAPH = "no_paragraph"  # 没有可计入段落统计的块


@dataclass(frozen=True, slots=True)
class DimensionResult:
    """单个维度的度量结果。"""

    name: str
    activated: bool
    raw: float | int | None = None
    passed: bool | None = None
    target: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @property
    def counts_toward_score(self) -> bool:
        """是否计入遵循度分母。

        未激活不计（PRD §8.1），无法计算也不计——后者若计入，一个空样本
        会被当成不达标，把整轮实验的曲线往下拽。
        """
        return self.activated and self.raw is not None

    @classmethod
    def not_activated(cls, name: str, target: str = "") -> "DimensionResult":
        return cls(name=name, activated=False, target=target, reason=Reason.NOT_ACTIVATED)

    @classmethod
    def uncomputable(
        cls, name: str, reason: str, target: str = "", details: dict[str, Any] | None = None
    ) -> "DimensionResult":
        """维度已激活，但这份样本算不出数——标为无效，不要返回 0。"""
        return cls(
            name=name,
            activated=True,
            target=target,
            details=details or {},
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """可直接 json.dumps，供 PRD §13.1 的每轮埋点。"""
        return {
            "name": self.name,
            "activated": self.activated,
            "raw": self.raw,
            "passed": self.passed,
            "target": self.target,
            "reason": self.reason,
            "counts_toward_score": self.counts_toward_score,
            "details": self.details,
        }
