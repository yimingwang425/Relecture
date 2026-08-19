"""风格配置：结构化参数 + 校验 + 维度激活判定。

PRD §5.1 F1 要求「配置以结构化参数存储，非自然语言描述（可程序化检查的前提）」，
本模块就是那个「结构化」的落点。

三件事在这里做，且只在这里做：

1. **校验**。非法配置必须在实验开始前就炸掉，而不是让 §8.2 的 20 轮曲线跑完
   之后才发现某个阈值写错了。
2. **激活判定**。PRD §8.1：「未激活的维度不计入遵循度分母，否则单语用户的得分
   会被虚高拉起。」激活与否只由配置决定，判定逻辑集中在此，维度模块不各判各的。
3. **指纹**。§8.2 的漂移实验前提是「固定一份风格配置」跑 20 轮。`fingerprint()`
   让每轮日志都能记下配置指纹，事后可验证这 20 轮确实用的同一份配置——
   否则曲线的可比性只是口头承诺。

决策日期：2026-08-19
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "ConfigError",
    "TermPolicy",
    "TermCountMode",
    "TargetBand",
    "LengthRange",
    "StyleConfig",
]


class ConfigError(ValueError):
    """配置非法。一律在加载时抛出，不留到度量阶段。"""


class TermPolicy(str, Enum):
    """PRD §8.1：术语处理是三值参数，不是布尔项。"""

    KEEP_ORIGINAL = "keep_original"  # 保留原文术语，不翻译
    TRANSLATE_WITH_SOURCE = "translate_with_source"  # 译文 + 原文括注
    TRANSLATE_ONLY = "translate_only"  # 仅用译文


class TermCountMode(str, Enum):
    """术语计数单位（README 口径 D02）。"""

    FIRST = "first"  # 每个术语只看首次出现——主口径
    ALL = "all"  # 全部出现次数——并行输出，供对照


def _enum_from(value: Any, enum_cls: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ConfigError(f"{field_name} 必须是 {allowed} 之一，得到 {value!r}") from None


def _require_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} 必须是数字，得到 {value!r}")
    return float(value)


def _require_keys(data: dict[str, Any], allowed: set[str], where: str) -> None:
    """未知键一律报错，不静默忽略。

    静默忽略意味着配置里写了 `tolerence`（拼错）也能跑，实验跑完才发现用的是
    默认值。§8.2 的曲线经不起这种错。
    """
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(
            f"{where} 出现未知字段 {sorted(unknown)}；可用字段：{sorted(allowed)}"
        )


@dataclass(frozen=True, slots=True)
class TargetBand:
    """带容差的目标值，对应 PRD §8.1「按配置，±5%」。"""

    target: float
    tolerance: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.target <= 1.0:
            hint = "（比例用 0–1 的小数，不是百分数：85% 写作 0.85）" if self.target > 1 else ""
            raise ConfigError(f"target 必须在 [0, 1] 内，得到 {self.target}{hint}")
        if not 0.0 <= self.tolerance <= 0.5:
            raise ConfigError(f"tolerance 必须在 [0, 0.5] 内，得到 {self.tolerance}")

    #: 浮点边界补偿。abs(0.9 - 0.85) == 0.050000000000000044 > 0.05，
    #: 不补偿的话恰好落在容差边界上的样本会被判不达标——在 20 轮实验里
    #: 就是一个凭空多出来的失败点，且极难从曲线上看出原因。
    _EPS = 1e-9

    def contains(self, value: float) -> bool:
        """闭区间。边界值算达标——达标线是「不低于」，不是「严格高于」。"""
        return abs(value - self.target) <= self.tolerance + self._EPS

    @property
    def low(self) -> float:
        return max(0.0, self.target - self.tolerance)

    @property
    def high(self) -> float:
        return min(1.0, self.target + self.tolerance)

    def describe(self) -> str:
        return f"{self.target:.2f} ± {self.tolerance:.2f}"

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "TargetBand":
        if not isinstance(data, dict):
            raise ConfigError(f"{where} 必须是对象，得到 {data!r}")
        _require_keys(data, {"target", "tolerance"}, where)
        if "target" not in data:
            raise ConfigError(f"{where} 缺少 target")
        return cls(
            target=_require_number(data["target"], f"{where}.target"),
            tolerance=_require_number(data.get("tolerance", 0.05), f"{where}.tolerance"),
        )

    def to_dict(self) -> dict[str, float]:
        return {"target": self.target, "tolerance": self.tolerance}


@dataclass(frozen=True, slots=True)
class LengthRange:
    """字数区间，闭区间。对应 PRD §8.1 详细程度的「按配置区间」。"""

    min: int
    max: int

    def __post_init__(self) -> None:
        if self.min < 1:
            raise ConfigError(f"min 必须 ≥ 1，得到 {self.min}")
        if self.max < self.min:
            raise ConfigError(f"max 必须 ≥ min，得到 min={self.min} max={self.max}")

    def contains(self, value: float) -> bool:
        return self.min <= value <= self.max

    def describe(self) -> str:
        return f"{self.min}–{self.max} 字"

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "LengthRange":
        if not isinstance(data, dict):
            raise ConfigError(f"{where} 必须是对象，得到 {data!r}")
        _require_keys(data, {"min", "max"}, where)
        for key in ("min", "max"):
            if key not in data:
                raise ConfigError(f"{where} 缺少 {key}")
            if isinstance(data[key], bool) or not isinstance(data[key], int):
                raise ConfigError(f"{where}.{key} 必须是整数，得到 {data[key]!r}")
        return cls(min=data["min"], max=data["max"])

    def to_dict(self) -> dict[str, int]:
        return {"min": self.min, "max": self.max}


def _normalize_language(value: Any, field_name: str) -> str:
    """语言标签归一：大小写与地区子标签统一。

    `zh-CN` `zh_Hans` `ZH` 全部归为 `zh`。激活判定只关心「讲解语言是否等于课件
    语言」，地区差异在这个判断上没有意义，留着只会造成 `zh-CN != zh` 这种
    伪不等，把维度错误地激活。
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} 必须是非空字符串，得到 {value!r}")
    return value.strip().lower().replace("_", "-").split("-")[0]


@dataclass(frozen=True, slots=True)
class StyleConfig:
    """A 类维度的完整配置。所有字段都有默认值，缺省即取默认。"""

    explain_language: str = "zh"
    source_language: str = "en"

    # 维度 1 · 中文字符占比
    chinese_ratio: TargetBand = field(default_factory=lambda: TargetBand(0.85, 0.05))
    exclude_annotated_terms: bool = True

    # 维度 2 · 术语英文标注率
    term_policy: TermPolicy = TermPolicy.TRANSLATE_WITH_SOURCE
    term_annotation_threshold: float = 0.9
    term_count_mode: TermCountMode = TermCountMode.FIRST
    #: 最小分母护栏。首现术语数低于此值时不出数值，标为低置信样本。
    #: 实测第四轮只有 1 个首现术语，指标只能取 0% 或 100%，没有分辨率，
    #: 却会把一个 0% 贡献进漂移曲线。
    term_min_occurrences: int = 5

    # 维度 3 · 平均段落字数
    paragraph_length: LengthRange = field(default_factory=lambda: LengthRange(60, 180))

    # 维度 4 · 单段最长引用字数
    max_quote_chars: int = 100

    _FIELDS = frozenset(
        {
            "explain_language",
            "source_language",
            "chinese_ratio",
            "exclude_annotated_terms",
            "term_policy",
            "term_annotation_threshold",
            "term_count_mode",
            "term_min_occurrences",
            "paragraph_length",
            "max_quote_chars",
        }
    )

    def __post_init__(self) -> None:
        if not 0.0 <= self.term_annotation_threshold <= 1.0:
            hint = "（用 0–1 的小数：90% 写作 0.9）" if self.term_annotation_threshold > 1 else ""
            raise ConfigError(
                f"term_annotation_threshold 必须在 [0, 1] 内，"
                f"得到 {self.term_annotation_threshold}{hint}"
            )
        if isinstance(self.term_min_occurrences, bool) or not isinstance(self.term_min_occurrences, int):
            raise ConfigError(f"term_min_occurrences 必须是整数，得到 {self.term_min_occurrences!r}")
        if self.term_min_occurrences < 1:
            raise ConfigError(f"term_min_occurrences 必须 ≥ 1，得到 {self.term_min_occurrences}")
        if isinstance(self.max_quote_chars, bool) or not isinstance(self.max_quote_chars, int):
            raise ConfigError(f"max_quote_chars 必须是整数，得到 {self.max_quote_chars!r}")
        if self.max_quote_chars < 1:
            raise ConfigError(f"max_quote_chars 必须 ≥ 1，得到 {self.max_quote_chars}")
        if not isinstance(self.exclude_annotated_terms, bool):
            raise ConfigError(
                f"exclude_annotated_terms 必须是布尔值，得到 {self.exclude_annotated_terms!r}"
            )

    # ---------------- 激活判定（PRD §8.1） ----------------

    @property
    def is_bilingual(self) -> bool:
        """讲解语言 ≠ 课件语言。两个跨语言维度的共同前提。"""
        return self.explain_language != self.source_language

    @property
    def activates_chinese_ratio(self) -> bool:
        """讲解语言一致性维度是否激活。

        额外要求讲解语言是中文——本模块度量的是汉字占比，若讲解语言是别的语言，
        这个数字没有意义。generalize 需要同时改 text.py 的汉字正则，属于后续工作。
        """
        return self.is_bilingual and self.explain_language == "zh"

    @property
    def activates_term_policy(self) -> bool:
        """术语处理维度是否激活。

        PRD §8.1：「单语场景（讲解语言 = 课件语言）下该参数为 N/A，整项不激活。」
        术语表为空是另一个不激活的原因，但那取决于运行时传入的术语表，
        不是配置能决定的，故由 term_annotation 模块判定。
        """
        return self.is_bilingual

    @property
    def activates_paragraph_length(self) -> bool:
        """常驻维度。"""
        return True

    @property
    def activates_quote_length(self) -> bool:
        """常驻维度。它是 §12.1 的合规约束，不是风格偏好，任何配置下都激活。"""
        return True

    # ---------------- 序列化 ----------------

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StyleConfig":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError(f"配置必须是对象，得到 {type(data).__name__}")
        _require_keys(data, set(cls._FIELDS), "配置根")

        kwargs: dict[str, Any] = {}
        if "explain_language" in data:
            kwargs["explain_language"] = _normalize_language(
                data["explain_language"], "explain_language"
            )
        if "source_language" in data:
            kwargs["source_language"] = _normalize_language(
                data["source_language"], "source_language"
            )
        if "chinese_ratio" in data:
            kwargs["chinese_ratio"] = TargetBand.from_dict(data["chinese_ratio"], "chinese_ratio")
        if "paragraph_length" in data:
            kwargs["paragraph_length"] = LengthRange.from_dict(
                data["paragraph_length"], "paragraph_length"
            )
        if "term_policy" in data:
            kwargs["term_policy"] = _enum_from(data["term_policy"], TermPolicy, "term_policy")
        if "term_count_mode" in data:
            kwargs["term_count_mode"] = _enum_from(
                data["term_count_mode"], TermCountMode, "term_count_mode"
            )
        if "term_annotation_threshold" in data:
            kwargs["term_annotation_threshold"] = _require_number(
                data["term_annotation_threshold"], "term_annotation_threshold"
            )
        for key in ("exclude_annotated_terms", "max_quote_chars", "term_min_occurrences"):
            if key in data:
                kwargs[key] = data[key]
        return cls(**kwargs)

    @classmethod
    def from_json(cls, text: str) -> "StyleConfig":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置不是合法 JSON：{exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: str | Path) -> "StyleConfig":
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f"配置文件不存在：{p}")
        return cls.from_json(p.read_text(encoding="utf-8"))

    def to_dict(self) -> dict[str, Any]:
        """可直接 json.dumps，供 §13.1 每轮埋点记录当时的配置。"""
        return {
            "explain_language": self.explain_language,
            "source_language": self.source_language,
            "chinese_ratio": self.chinese_ratio.to_dict(),
            "exclude_annotated_terms": self.exclude_annotated_terms,
            "term_policy": self.term_policy.value,
            "term_annotation_threshold": self.term_annotation_threshold,
            "term_count_mode": self.term_count_mode.value,
            "term_min_occurrences": self.term_min_occurrences,
            "paragraph_length": self.paragraph_length.to_dict(),
            "max_quote_chars": self.max_quote_chars,
        }

    def fingerprint(self) -> str:
        """配置指纹，前 12 位十六进制。

        §8.2 的漂移实验要求 20 轮固定同一份配置。每轮日志带上指纹，事后
        `len(set(fingerprints)) == 1` 就能验证，不必依赖「我记得没改过」。
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    def describe_activation(self) -> dict[str, bool]:
        """四个维度的激活状态一览，用于报告与调试。"""
        return {
            "chinese_ratio": self.activates_chinese_ratio,
            "term_annotation": self.activates_term_policy,
            "paragraph_length": self.activates_paragraph_length,
            "quote_length": self.activates_quote_length,
        }
