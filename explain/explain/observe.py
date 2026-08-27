"""观察项。**全部不进达标判定。**

沿用第 0 周的纪律：实验可附带观察项，但它们不是维度、不参与达标判定——
混进指标会让「风格遵循度」这个数悄悄变成别的东西。

## 一 · 分段规避探针（口径 E10）

`paragraph_length` 量的是**平均**段落字数。偏短配置（如 15–40 字）下，
模型有两条路满足它：真的讲简洁，或者**多敲回车**——同样 800 字切成 25 段，
每段 32 字，**稳过判定**。而选了「很简洁」的用户想要的是前者。

审查看不见这个失败，所以要有观察量：`total_chars` 与 `paragraph_count`。
判读规则预先登记（E10）：**触发率近零 + 总字数不变 + 段数显著上升
= 达标是靠分段实现的**，报告不得写成「模型能遵守极端配置」。

顺带记一处 PRD 与代码的缺口：§8.1「详细程度」的计算方式写的是
「平均段落字数、**总输出长度**」两项，而 `a_rules` 只实现了前一项。
本轮不补判定（未实现的维度不参与达标判定），但缺口要写清楚。

## 二 · 括注计数（`annotation_count`）

「汉字 +（ASCII 英文）」形态在本轮讲解里出现了几次。**与 D48 的排除规则共用
同一个匹配器**——不共用就会出现「排除了 N 处、却报告了 M 次」这种自相矛盾的日志。

它要回答的是一个**会让整个话题关掉**的问题：

> 括注排除的口径（无排除 / 仅术语表 / 形态规则）在 0.95 阈值下给出三个不同答案，
> 这件事是在**六份第 0 周样本**上测的——而那时 `term_annotation` 激活、
> prompt 明确要求模型写「中文译名（English original）」。
>
> **v1 的 prompt 里 `term_*` 完全不渲染，没有任何一句话让模型写括注。**
> 若模型因此几乎不写括注，那三种口径就没有分歧，P4 根本不口径敏感。

所以口径敏感性**必须用本轮的 transcripts 重算**，不能引用旧表——
否则就是拿旧配置下的敏感性分析去解释新配置下的结果。

## 三 · 句级 verbatim 探针（Q1）

`quote_length` 当前按引号猜。它的盲区是**不加引号地整段照抄英文原文**——
而 v1 正是英文课件 + 中文讲解，这个形态一旦发生，弱口径完全看不见。

探针不做判定，只取证：把讲解切句，逐句在**本轮 + 本轨迹历史轮**的检索片原文里
查子串。**比对范围必须含历史轮**——历史检索片仍在上下文里，模型照抄的来源
不限于本轮；只比本轮会系统性低估，且低估方向不可见。

读法预先登记：探针显示存在长的未加引号照抄 → 升级 `quote_length` 口径有依据；
探针恒小 → 弱口径下的零触发才有解释力。**先取证，再改口径。**

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from a_rules.metrics.chinese_ratio import parenthetical_english_spans
from a_rules.text import Document, count_chars, normalize, split_sentences

__all__ = ["Observations", "observe", "MIN_VERBATIM_CHARS"]

#: 短于此长度的重合不算「照抄」。术语、常用短语天然会重合。
#: **这是观察项的门槛，不是达标线**——它不参与任何判定，
#: 故不适用「阈值必须标明来源」的规则；但仍记在这里，免得日后被当成有依据的数。
MIN_VERBATIM_CHARS = 12


@dataclass(frozen=True, slots=True)
class Observations:
    total_chars: int
    paragraph_count: int
    #: 「汉字 +（ASCII 英文）」括注的出现次数。与 D48 的排除规则共用匹配器。
    annotation_count: int
    max_verbatim_chars: int
    verbatim_preview: str | None
    verbatim_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_chars": self.total_chars,
            "paragraph_count": self.paragraph_count,
            "annotation_count": self.annotation_count,
            "max_verbatim_sentence_chars": self.max_verbatim_chars,
            "verbatim_preview": self.verbatim_preview,
            "verbatim_source": self.verbatim_source,
        }


def observe(text: str, source_texts: Iterable[tuple[str, str]] = ()) -> Observations:
    """算观察量。

    `source_texts` 是 `(来源标识, 原文)` 的序列，应包含**本轮与历史轮**的全部检索片。
    """
    doc = Document.parse(text)
    blocks = [b for b in doc.paragraph_blocks() if b.count().total > 0]
    total = sum(b.count().total for b in blocks)

    haystacks = [(label, normalize(src)) for label, src in source_texts]
    best, preview, source = 0, None, None
    if haystacks:
        for span in split_sentences(doc.masked):
            # split_sentences 返回 Span，取 .text；
            # 切的是 masked 串，代码块/公式/URL 已被掩掉，不会被误判成照抄
            probe = normalize(span.text).strip().strip("\x00").strip()
            if len(probe) < MIN_VERBATIM_CHARS:
                continue
            for label, hay in haystacks:
                if probe in hay:
                    n = count_chars(probe).total
                    if n > best:
                        best, preview, source = n, probe[:80], label
                    break
    return Observations(
        total_chars=total,
        paragraph_count=len(blocks),
        annotation_count=len(parenthetical_english_spans(doc)),
        max_verbatim_chars=best,
        verbatim_preview=preview,
        verbatim_source=source,
    )
