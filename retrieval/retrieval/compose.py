"""把 ``SliceSet`` 派生成送去 embedding 的文本。

**与 ``slice.text`` 解耦。** 两者职责不同：

===============  =========================  ==============================
                 ``slice.text``             ``embedding_text``
===============  =========================  ==============================
给谁用           人看、溯源、引用比对        只给向量模型
版本号           ``SLICE_RULESET_VERSION``  ``EMBED_TEXT_RULESET_VERSION``
改动代价         切片契约变更                只重建索引
===============  =========================  ==============================

理由：本层要跑多个文本变体做 A/B，每个变体都递增切片契约版本并重切一遍是没必要的
耦合；且 ``slice.text`` 承担 PRD §12.1 的引用比对职责，**不该为了向量效果被改写**。

## 缺陷清理 vs 口径变体

两类改动不可混为一谈：

- **缺陷**（``_clean``，四个变体一律执行）：控制字符与私用区字符。
  14.129 有 **103 个 U+F076**（Wingdings 项目符号字形）、11.201 有 U+0083，
  它们在文本里是纯噪声。**乱码不是一种「口径」，不做 A/B。**
- **口径**（T0–T3）：样板去除、title 前置、文档前缀。这些是有取舍的选择，须 A/B。

决策日期：2026-08-21
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from slicer.schema import Slice, SliceSet

__all__ = [
    "EMBED_TEXT_RULESET_VERSION",
    "BOILERPLATE_PAGE_RATIO",
    "VARIANTS",
    "compose",
    "boilerplate_keys",
]

#: 组文本口径版本。T0–T3 的定义、``_clean`` 的规则、样板判据，任意一处改动都必须递增。
#: 它进 embedding 缓存键，改了会自动导致重新取向量，不会拿旧向量冒充新口径。
EMBED_TEXT_RULESET_VERSION = "embed-text-1.0"

#: 样板块判据：同一文档内出现在 ≥ 该比例的可用页上。
#: 来源：**占位待定**。确定路径：T0/T1 的对比结果——若 T1 明显更好，
#: 再按 30%/50%/70% 扫一遍敏感性确定。
#: 实测 50% 时每份文档各命中 1 个块型（``S.E. Lecture # #`` 等）。
BOILERPLATE_PAGE_RATIO = 0.5

VARIANTS = ("T0", "T1", "T2", "T3")

_JOIN = "\n"

#: 数字归一，用于识别「同一个页脚、只有页码不同」的块。
_DIGITS = re.compile(r"\d+")


def _clean(text: str) -> str:
    """剥掉控制字符与私用区字符。**四个变体一律执行，不参与 A/B。**

    私用区（U+E000–U+F8FF）装的是 Wingdings / Symbol 一类的字形，
    文本层面没有任何意义；C0/C1 控制字符同理。留着它们等于往向量里灌噪声。
    保留 ``\\n`` 与 ``\\t``。
    """
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Co", "Cs", "Cn"):
            out.append(" ")
            continue
        out.append(ch)
    return re.sub(r"[ \t]+", " ", "".join(out))


def _key(text: str) -> str:
    """样板识别用的归一化键：折叠空白 + 数字换成 ``#``。"""
    return _DIGITS.sub("#", " ".join(text.split()))


def boilerplate_keys(sliceset: SliceSet, ratio: float = BOILERPLATE_PAGE_RATIO) -> set[str]:
    """本文档内的样板块（页眉 / 页脚 / 页码）。

    只在**单个文档内**统计——跨文档统计会把「常见措辞」误判成样板。
    """
    n = len(sliceset.slices)
    if not n:
        return set()
    counts = Counter(_key(u.text) for s in sliceset.slices for u in s.units)
    return {k for k, v in counts.items() if k and v >= n * ratio}


def _doc_prefix(sliceset: SliceSet) -> str:
    """文档级前缀：取首个切片的标题作为讲次名。

    没有课程号可用——解析层不产出文件名之外的元数据，而文件名不是内容。
    故只用首页标题，且当它缺失时返回空串而不是编一个。
    """
    if not sliceset.slices:
        return ""
    return (sliceset.slices[0].title or "").strip()


def compose(sliceset: SliceSet, variant: str = "T0") -> dict[str, str]:
    """返回 ``{slice_id: embedding_text}``。

    T0 基线：块按 ``order`` 拼接（已清理缺陷字符）
    T1 = T0 − 样板块
    T2 = T1 + title 前置并重复一次
    T3 = T2 + 文档级前缀
    """
    if variant not in VARIANTS:
        raise ValueError(f"未知变体 {variant!r}，可选 {VARIANTS}")

    drop = boilerplate_keys(sliceset) if variant in ("T1", "T2", "T3") else set()
    prefix = _doc_prefix(sliceset) if variant == "T3" else ""

    out: dict[str, str] = {}
    for s in sliceset.slices:
        parts = [_clean(u.text) for u in s.units if _key(u.text) not in drop]
        body = _JOIN.join(p for p in parts if p.strip())

        head: list[str] = []
        if prefix:
            head.append(prefix)
        if variant in ("T2", "T3") and s.title:
            head.append(_clean(s.title))

        text = _JOIN.join([*head, body]).strip()
        # 全部内容都是样板时会剩空串——退回未去样板的清理文本，
        # 空文本进 embedding 会得到一个无意义但仍参与排序的向量
        out[s.slice_id] = text or _clean(s.text).strip() or s.slice_id
    return out


def compose_all(slicesets: dict[str, SliceSet], variant: str) -> dict[tuple[str, str], str]:
    """多文档版本，键为 ``(source_file, slice_id)``。样板按各自文档统计。"""
    out: dict[tuple[str, str], str] = {}
    for name, ss in slicesets.items():
        for sid, text in compose(ss, variant).items():
            out[(name, sid)] = text
    return out
