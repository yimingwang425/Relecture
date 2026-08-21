"""术语表输出契约。

**为将来留结构**（v1 不做用户增删界面）：每条带 `source` 与命中的 `heuristics`，
以便日后既能加人工入口，也能改成「低置信术语不计入审查分母」——
后者不需要用户做任何事，可能更符合产品定位（PRD §2 的 P1：用户说不出但能选）。

决策日期：2026-08-21
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EXTRACTOR_VERSION",
    "RULESET_VERSION",
    "Evidence",
    "Term",
    "Glossary",
]

EXTRACTOR_VERSION = "0.1.0"

#: 提取口径版本。启发式、阈值、G 判据近似，任意一处改动都必须递增。
RULESET_VERSION = "glossary-extract-1.0"

Source = Literal["body_text", "figure_caption"]
Confidence = Literal["high", "medium", "low"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Model):
    page: int
    block_id: str
    bbox_norm: list[float] = Field(min_length=4, max_length=4)
    text: str


class Term(_Model):
    en: str = Field(description="归一化后的术语（小写、复数归一）")
    surface_forms: list[str] = Field(description="课件中出现的原始字面")
    source: Source
    heuristics: list[str] = Field(description="命中的启发式编号，可多条")
    confidence: Confidence
    confidence_reason: str
    pages: list[int]
    occurrences: int
    evidence: list[Evidence]
    zh: list[str] = Field(default_factory=list, description="本次不做翻译，恒为空")
    flags: list[str] = Field(default_factory=list)


class Glossary(_Model):
    course: str
    source_file: str
    extractor_version: str = EXTRACTOR_VERSION
    ruleset_version: str = RULESET_VERSION
    criteria_ref: str = "实验脚本/A类规则计算脚本/examples/glossary_criteria.md"
    terms: list[Term] = Field(default_factory=list)
