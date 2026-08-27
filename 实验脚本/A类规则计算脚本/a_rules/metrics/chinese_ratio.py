"""维度 1 · 中文字符占比。

对应 PRD §8.1 的「讲解语言一致性」行，达标线「按配置，±5%」。

分母是这个维度唯一有争议的地方，两条决策：

- **只设下限，不设上界**（口径 D41）。要检测的是「开始说英文」，不存在
  「中文太多」这种失败模式；设上界还会与术语维度重复计量——`translate_with_source`
  要求模型写括注，括注英文本就会压低本维度，两个维度会给出方向相反的信号。
- **英文按词计，不按字母计**（口径 D01）。按字母计会让 `gradient descent`
  顶 16 个汉字的量，占比随中英混合比例剧烈抖动，与 §8.2 要观察的漂移信号混淆。
- **排除术语括注里的英文原文**（口径 D15）。`translate_with_source` 下用户
  **要求**模型写「热机（heat engine）」；若把括注英文计入分母，满足术语维度
  就必然拉低本维度——两个维度直接打架。未排除口径同时输出，供对照。
- **无术语表时改用形态规则**（口径 X11，2026-08-22）。见下。

## X11 · 括注排除不能依赖术语表

D15 原本靠 `annotated_english_spans(doc, glossary)` 实现，而该函数在
`glossary is None` 时直接返回空集。**v1 走多课程，没有可用术语表**
（自动提取召回 32%，PRD §8.1 已下线术语维度），于是括注英文会重新计入分母，
本维度被系统性压低——**这是术语维度下线的连带影响，不是本维度自己的口径变化**，
且它在高阈值（0.95 以上）附近足以翻转达标判定。

改为**形态规则**：识别「汉字 +（ASCII 英文）」这一形态并排除括注内容，
不需要知道括号里的词是不是术语表条目。

**为什么不用「有术语表就走 D15、没有就走形态规则」**：那样同一个维度在
两种课件上用两套口径，数字不可比。形态规则一律生效，术语表版本只在
`explain/` 的一次性离线比对里用作外部参照——参照物没有参与本规则的设计，
这才是外部验证而不是自洽检查（通则七 #3）。

**已知误收面（口径 X12，登记不修）**：`(Figure 3)`、`(p. 12)` 这类非术语括注
也会被排除。误差**方向单向**——抬高中文占比，即判定更宽松，不会造成误判达标
之外的意外；为消它引入白名单，反而要维护一张没有依据的名单。
括注内含汉字的（如「（见 p12）」）不匹配，因为形态要求括注内是纯 ASCII。

决策日期：2026-08-19（X11 于 2026-08-22 追加）
"""

from __future__ import annotations

import re
from typing import Any

from ..config import StyleConfig
from ..glossary import Glossary
from ..result import DimensionResult, Reason
from ..text import MASK, Document, count_chars
from .term_annotation import annotated_english_spans

__all__ = ["NAME", "chinese_ratio", "parenthetical_english_spans"]

NAME = "chinese_ratio"

#: 「汉字 +（ASCII 英文）」的形态。口径 X11。
#:
#: 三条约束各有理由：
#: - **括号前必须是汉字**：这是「译文 + 原文括注」的形态特征。不加这条，
#:   纯英文句子里的括号也会被排除，本维度会被整段掏空。
#: - **括注内只允许 ASCII**：含汉字的括注（「（见 p12）」）是正文补充说明，
#:   不是原文标注，本就该计入分母。
#: - **至少一个 ASCII 字母**：纯数字括注（「（2010）」）不影响英文词计数，
#:   排不排都一样，不必纳入。
#:
#: 全角括号在 D05b 的 NFKC 归一后已变成半角，故只需处理 `()`；
#: 另收 `[]【】〔〕` 与 term_annotation 的 `_ANNOTATION_RE` 保持一致。
_PAREN_EN_RE = re.compile(
    r"(?<=[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f])"
    r"[ \t]*[(\[【〔]([A-Za-z0-9 \t\-&./,\']{1,120})[)\]】〕]"
)


def parenthetical_english_spans(doc: Document) -> tuple[tuple[int, int], ...]:
    """按形态识别的原文括注区间（内容部分，不含括号本身）。

    与 `annotated_english_spans` 的分工：那个认**术语表条目**，这个认**形态**。
    v1 没有可用术语表，只能认形态。理由见模块头 X11。
    """
    spans: list[tuple[int, int]] = []
    for m in _PAREN_EN_RE.finditer(doc.masked):
        content = m.group(1)
        if any(c.isalpha() for c in content):
            spans.append((m.start(1), m.end(1)))
    return tuple(spans)


def _mask_spans(masked: str, spans: tuple[tuple[int, int], ...]) -> str:
    if not spans:
        return masked
    buf = list(masked)
    for start, end in spans:
        for k in range(start, end):
            if buf[k] != "\n":
                buf[k] = MASK
    return "".join(buf)


def _countable(doc: Document, masked: str) -> str:
    return "\n".join(masked[b.start : b.end] for b in doc.countable_blocks())


def chinese_ratio(
    text: str, config: StyleConfig, glossary: Glossary | None = None
) -> DimensionResult:
    """计算汉字在可计量正文中的占比。"""
    band = config.chinese_ratio
    target = band.describe()

    if not config.activates_chinese_ratio:
        return DimensionResult.not_activated(NAME, target)

    doc = Document.parse(text)
    if doc.is_blank:
        # 空文本返回 0.0 会被读成「纯英文，严重不达标」——误差方向危险（口径 D03）
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_TEXT, target)

    including = count_chars(_countable(doc, doc.masked))
    # 两条通路并集：术语表认得的（D15）+ 形态认得的（X11）。
    # 传了术语表时两者高度重叠，取并集只会让排除更完整，不会互相抵消。
    spans: tuple[tuple[int, int], ...] = ()
    if config.exclude_annotated_terms:
        spans = tuple(
            dict.fromkeys(
                annotated_english_spans(doc, glossary) + parenthetical_english_spans(doc)
            )
        )
    excluding = count_chars(_countable(doc, _mask_spans(doc.masked, spans)))

    def ratio(c) -> float | None:
        base = c.han + c.latin_words
        return (c.han / base) if base else None

    raw = ratio(excluding)
    if raw is None:
        # 只有数字，没有任何汉字或英文词——占比无定义
        return DimensionResult.uncomputable(NAME, Reason.EMPTY_TEXT, target)

    return DimensionResult(
        name=NAME,
        activated=True,
        raw=raw,
        passed=band.contains(raw),
        target=target,
        details={
            "han": excluding.han,
            "latin_words": excluding.latin_words,
            "digit_groups": excluding.digit_groups,
            "excluded_annotation_words": including.latin_words - excluding.latin_words,
            "ratio_excluding_annotations": raw,
            "ratio_including_annotations": ratio(including),
            "exclude_annotated_terms": config.exclude_annotated_terms,
            "target_min": band.min,
        },
    )
