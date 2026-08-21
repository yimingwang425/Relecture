"""G1–G5 判据的自动近似。

判据全文见 `实验脚本/A类规则计算脚本/examples/glossary_criteria.md`（已冻结，不重造）。

| 判据 | 能否自动近似 | 本模块做法 |
| --- | --- | --- |
| G1 领域限定性 | **粗略** | 常见词黑名单 |
| G2-en/zh 匹配无歧义 | **不能** | 需语义判断，一律不给 high 置信 |
| G3 讲解必要性 | **不能** | 同上 |
| G4 翻译有障碍 | 能 | 单位 / 化学式 / 纯数字正则 |
| G5 非专有名词 | **粗略** | 机构 / 地名 / 人名片段黑名单 + 全大写缩写 |

**G2 与 G3 是自动提取的能力边界。** 它们决定一个词「匹配时会不会误伤」和
「学生要不要解释」，两者都需要语义判断。故本模块只做拒绝（reject），
通过者一律不宣称已满足 G2/G3——这体现在 `confidence` 上限为 `medium`。

决策日期：2026-08-21
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import COMMON_WORDS, PROPER_NOUN_HINTS, STOPWORDS, is_unit_or_number, tokenize

__all__ = ["CriteriaResult", "check"]

_ACRONYM = re.compile(r"^[A-Z]{2,}$")


@dataclass(frozen=True, slots=True)
class CriteriaResult:
    passed: bool
    rejected_by: str | None = None
    reason: str = ""


def check(phrase: str) -> CriteriaResult:
    """按 G1/G4/G5 的近似拒绝明显不该收的词。**通过不等于满足 G2/G3。**"""
    toks = tokenize(phrase)
    if not toks:
        return CriteriaResult(False, "format", "无字母 token")

    # 连字符复合词要拆开查专名表：EIA-DoE 作为单 token 查不到，拆成 eia / doe 才行
    lowered = [part for t in toks for part in t.lower().split("-") if part]

    # G4 · 单位 / 化学式 / 纯数字
    if any(is_unit_or_number(t) for t in phrase.split()):
        return CriteriaResult(False, "G4", "含单位、化学式或数字")

    # 单字母、过短
    if all(len(t) <= 2 for t in toks):
        return CriteriaResult(False, "format", "全部为过短 token")

    # 停用词残留
    if all(t in STOPWORDS for t in lowered):
        return CriteriaResult(False, "format", "全部为停用词")

    # G5 · 专有名词
    if any(t in PROPER_NOUN_HINTS for t in lowered):
        return CriteriaResult(False, "G5", "含机构 / 地名 / 人名片段")
    if any(_ACRONYM.match(t) and len(t) >= 2 for t in toks) and len(toks) == 1:
        return CriteriaResult(False, "G5", "疑似缩写（全大写）")

    # G1 · 领域限定性。单词术语要求不在常见词表；多词组合放宽——
    # `energy density` 是术语而 `energy` 不是，组合本身提供了限定
    if len(toks) == 1 and lowered[0] in COMMON_WORDS:
        return CriteriaResult(False, "G1", "日常常见词")
    if len(toks) > 1 and all(t in COMMON_WORDS for t in lowered):
        return CriteriaResult(False, "G1", "全部由日常常见词组成")

    return CriteriaResult(True, None, "通过 G1/G4/G5 近似；G2/G3 未经验证")
