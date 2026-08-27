"""聚合入口：一次跑完全部 A 类维度。

**v1 只有三个维度计分**（PRD §3.2「风格遵循度的 v1 定义」）：详细程度 /
引用长度 / 讲解语言一致性。`term_annotation` 仍然被计算，但 `activated=False`，
不进 `scored`、不影响 `all_activated_passed`——保留计算是为了让它的 `details`
仍能作为观察项进日志，代价接近零（术语表为 None 时它直接返回）。

**不做混合分**（PRD §8.1 设计要点）。这里只把四个结果收在一起，外加一个
`all_activated_passed` 布尔量——「§3.2 的 ≥85% 应理解为各激活维度均需达标，
而非平均值达标」。

每次输出都带配置指纹与术语表版本：§8.2 要求 20 轮固定同一份配置与术语表，
带上这两个标识事后才能验证，而不是依赖「我记得没改过」。

决策日期：2026-08-19
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import CONFIG_VERSION, StyleConfig
from .glossary import Glossary
from .metrics.chinese_ratio import chinese_ratio
from .metrics.paragraph_length import avg_paragraph_length
from .metrics.quote_length import max_quote_length
from .metrics.term_annotation import term_annotation_rate
from .result import DimensionResult

__all__ = ["Evaluation", "evaluate", "DIMENSIONS"]

#: 四个维度的执行顺序。签名一致，可整体迭代。
DIMENSIONS = (chinese_ratio, term_annotation_rate, avg_paragraph_length, max_quote_length)


@dataclass(frozen=True, slots=True)
class Evaluation:
    dimensions: tuple[DimensionResult, ...]
    config_fingerprint: str
    #: 指标集标识。与指纹分工见 `StyleConfig.metric_set_id`——
    #: 指纹管「阈值变没变」，它管「指标集是不是同一个」。
    #: 跨批次聚合前必须比对它，否则四维口径的历史数据会被画进三维的图里。
    metric_set_id: str
    config_version: str
    glossary_version: str | None
    glossary_frozen: bool

    def by_name(self, name: str) -> DimensionResult:
        for d in self.dimensions:
            if d.name == name:
                return d
        raise KeyError(name)

    @property
    def scored(self) -> tuple[DimensionResult, ...]:
        """真正计入遵循度的维度：已激活且算得出数。"""
        return tuple(d for d in self.dimensions if d.counts_toward_score)

    @property
    def all_activated_passed(self) -> bool | None:
        """各激活维度是否**全部**达标。

        无维度计分时返回 None 而非 True——「没有维度可判」和「全部通过」
        是两回事，混同会让空样本在曲线上表现为满分。
        """
        s = self.scored
        return all(d.passed for d in s) if s else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_fingerprint": self.config_fingerprint,
            "metric_set_id": self.metric_set_id,
            "config_version": self.config_version,
            "counted_dimensions": [d.name for d in self.scored],
            "glossary_version": self.glossary_version,
            "glossary_frozen": self.glossary_frozen,
            "scored_count": len(self.scored),
            "all_activated_passed": self.all_activated_passed,
            "dimensions": {d.name: d.to_dict() for d in self.dimensions},
        }


def evaluate(
    text: str,
    config: StyleConfig | None = None,
    glossary: Glossary | None = None,
    *,
    annotated_terms: frozenset[str] = frozenset(),
) -> Evaluation:
    """跑完四个维度。

    `annotated_terms` 只对术语维度有意义（口径 D44：首现的作用域是整段对话）。
    多轮场景下由调用方跨轮累积；单轮评测传空集即可。
    """
    cfg = config or StyleConfig()
    return Evaluation(
        dimensions=tuple(
            f(text, cfg, glossary, annotated_terms=annotated_terms)
            if f is term_annotation_rate
            else f(text, cfg, glossary)
            for f in DIMENSIONS
        ),
        config_fingerprint=cfg.fingerprint(),
        metric_set_id=cfg.metric_set_id(),
        config_version=CONFIG_VERSION,
        glossary_version=glossary.version if glossary else None,
        glossary_frozen=bool(glossary and glossary.frozen),
    )
