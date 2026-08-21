"""两条提取通道。

## 正文通道（B1–B4）

见 `方案.md` §2.1 的误收风险表。**B5 定义框不实现**：PDF 里定义框没有稳定信号，
且本语料没有任何一页是这个形态——凭空写检测器等于造无依据的启发式。

## 图注通道（P1–P5 判页 + F1–F5 判块）

**不以 `image_adjacency` 为主判据。** 实测：贡献全部 17 条金标准图注术语的
p20/p27 一张位图都没有（图是矢量绘制），邻接恒为 null。
改用 PRD §8.1 原文写的「无句法结构的孤立名词短语簇」，邻接降为佐证项 P5。

## 三条防误收的构造约束（实测校准，2026-08-21）

1. **P1（块数）是强制项，不参与「几选几」计数。** 降为可选会把项目符号页
   误判成图表页——p7（OUTLINE，8 块）与 p12（9 块）都能靠 P2/P3/P4 凑够三条。
   块数是区分「图内散落标签」与「短列表」的唯一硬信号。
2. **图注候选不做全窗口切分。** 标签本身就是术语；切成所有子窗口会产出
   `condenser pump cooling` 这类垃圾。只取整块与按换行 / 括号切出的段。
3. **碎片只拼 `order` 相邻的两块。** 全对全会产出三段拼接垃圾；
   被版面拆开的术语（`Control`+`rods`、`Cooling`+`tower`）必然是相邻两块。

决策日期：2026-08-21
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

from slicer.schema import SliceSet, SliceUnit

from .text import candidate_phrases, has_sentence_punct, normalize_phrase, tokenize

__all__ = [
    "FIGURE_PAGE_MIN_BLOCKS",
    "FIGURE_PAGE_MAX_MEDIAN_WORDS",
    "FIGURE_PAGE_MAX_PUNCT_RATIO",
    "FIGURE_PAGE_MAX_RANK_LEVELS",
    "FIGURE_PAGE_MIN_CONDITIONS",
    "CAPTION_MAX_WORDS",
    "CROSS_PAGE_MIN",
    "FRAGMENT_MAX_GAP_LINES",
    "FRAGMENT_MAX_MERGED_WORDS",
    "STRUCTURAL_MIN_PAGES",
    "Hit",
    "is_figure_page",
    "body_hits",
    "figure_hits",
]

#: 来源：**占位待定**。实测 p27=31 块、正文页 p22=8 块、p7=8 块、p12=9 块。
#: 刻意不为把 p20（9 块）捞进来而放宽——p20 的图内文字是矢量轮廓、不在文本层，
#: 即使判为图表页也提不出东西，而放宽会把 p7/p12 这类短列表页误判进来。
#: 确定路径：扩到第二门有图表页的课件后重定。
FIGURE_PAGE_MIN_BLOCKS = 12
#: 来源：**实测依据**。p27 每块词数中位 1，正文页 p22 为 7.5。
FIGURE_PAGE_MAX_MEDIAN_WORDS = 2
#: 来源：**实测依据**。p27 为 1/31 = 3%，p22 为 2/8 = 25%。
FIGURE_PAGE_MAX_PUNCT_RATIO = 0.20
#: 来源：**实测依据**。p27 只有 2 档字号（正文 0.49 / 标题 0.98），p22 有 4 档。
FIGURE_PAGE_MAX_RANK_LEVELS = 3
#: **P1 之外**还需满足几条。P1 强制，见模块 docstring 第 1 条。
FIGURE_PAGE_MIN_CONDITIONS = 2

#: 块级图注的词数上限。来源：**实测依据**，p27 块最长 5 词。
CAPTION_MAX_WORDS = 5
#: 跨页高频短语要求出现的最少页数。来源：**实测依据**，判据 G3 要求跨页复现。
CROSS_PAGE_MIN = 2
#: 碎片拼接的间距上限（行高倍数）。**沿用 geometry 的 ADJACENCY_MAX_GAP_LINES**，
#: 不新造一个数——同一个「相邻」概念不该有两个阈值。
FRAGMENT_MAX_GAP_LINES = 1.5
#: 拼接后的词数上限。来源：**实测依据**——金标准 44 条中最长 3 词，35 条为 2 词。
FRAGMENT_MAX_MERGED_WORDS = 3

_QUESTION = re.compile(r"\?|^(how|what|why|when|where|which|who)\b", re.I)
_VERBY = re.compile(r"\b(is|are|was|were|does|do|did|can|will|should|has|have)\b", re.I)
_PAREN = re.compile(r"[（(]([^）)]*)[）)]")


@dataclass(frozen=True, slots=True)
class Hit:
    phrase: str
    key: str
    heuristic: str
    page: int
    block_id: str
    bbox_norm: tuple[float, ...]
    block_text: str


def _words(text: str) -> int:
    return len(tokenize(text))


def _page_units(sliceset: SliceSet) -> dict[int, list[SliceUnit]]:
    out: dict[int, list[SliceUnit]] = defaultdict(list)
    for s in sliceset.slices:
        for u in s.units:
            out[u.page_index].append(u)
    return out


def _adjacent_count(units: list[SliceUnit]) -> int:
    return sum(1 for u in units if u.image_adjacency.is_adjacent)


def is_figure_page(units: list[SliceUnit], has_adjacent: int = 0) -> tuple[bool, list[str]]:
    """P1 强制 + P2–P5 中满足 ≥2 条。返回 (判定, 命中的条件)。"""
    if not units or len(units) < FIGURE_PAGE_MIN_BLOCKS:
        return False, []

    counts = [_words(u.text) for u in units]
    ranks = {round(u.font.size_rank, 2) for u in units if u.font.size_rank is not None}
    punct = sum(1 for u in units if has_sentence_punct(u.text))

    fired = ["P1"]
    if statistics.median(counts) <= FIGURE_PAGE_MAX_MEDIAN_WORDS:
        fired.append("P2")
    if punct / len(units) <= FIGURE_PAGE_MAX_PUNCT_RATIO:
        fired.append("P3")
    if len(ranks) <= FIGURE_PAGE_MAX_RANK_LEVELS:
        fired.append("P4")
    if has_adjacent >= 3:
        fired.append("P5")
    return len(fired) - 1 >= FIGURE_PAGE_MIN_CONDITIONS, fired


#: 整块逐字重复出现在 ≥ 该页数上，即判为结构性文本（面包屑 / 页眉页脚）。
#: 来源：**实测依据**——22.081J 的章节面包屑（`Technologies / Fossil fuels`）
#: 跨 3–8 页逐字重复，而正文散文不会整块重复。
STRUCTURAL_MIN_PAGES = 3


def _structural_blocks(sliceset: SliceSet) -> set[str]:
    """整块逐字跨页重复的文本——面包屑与页眉页脚，不参与 B3 的词频统计。"""
    pages: dict[str, set[int]] = defaultdict(set)
    for s in sliceset.slices:
        for u in s.units:
            key = normalize_phrase(u.text)
            if key:
                pages[key].add(u.page_index)
    return {k for k, v in pages.items() if len(v) >= STRUCTURAL_MIN_PAGES}


def _maximal(phrases: list[str]) -> list[str]:
    """只保留不被其他候选包含的最长短语。"""
    keys = {normalize_phrase(p): p for p in phrases}
    out = []
    for k, p in keys.items():
        if any(k != o and f" {k} " in f" {o} " for o in keys):
            continue
        out.append(p)
    return out


def _hit(phrase: str, heuristic: str, unit: SliceUnit) -> Hit:
    return Hit(
        phrase=phrase.strip(),
        key=normalize_phrase(phrase),
        heuristic=heuristic,
        page=unit.page_index,
        block_id=unit.block_id,
        bbox_norm=tuple(unit.bbox_norm),
        block_text=unit.text.strip(),
    )


# ── 正文通道 ──────────────────────────────────────────────────────────────────


def _bold_spans(unit: SliceUnit) -> list[str]:
    """块级 font 只有主导值，切片未带 span，故退回块级 bold。

    这是已知的信号损失：一个块里只有几个词加粗时块级 bold 为假。
    **方向安全**（漏而非错）。
    """
    return [unit.text] if unit.font.bold else []


def body_hits(sliceset: SliceSet) -> list[Hit]:
    hits: list[Hit] = []
    pages = _page_units(sliceset)
    figure_pages = {
        p for p, us in pages.items() if is_figure_page(us, _adjacent_count(us))[0]
    }

    for s in sliceset.slices:
        for u in s.units:
            if u.page_index in figure_pages:
                continue
            for span in _bold_spans(u):
                for ph in candidate_phrases(span):
                    hits.append(_hit(ph, "B1", u))

    for s in sliceset.slices:
        if not s.title_block_id or s.page_indices[0] in figure_pages:
            continue
        unit = next((u for u in s.units if u.block_id == s.title_block_id), None)
        if unit is None:
            continue
        title = unit.text.strip()
        if _QUESTION.search(title) or _VERBY.search(title):
            continue
        # **只取最长候选**，不吐所有子窗口。吐全窗口会产出
        # basic / basic nuclear / basic nuclear reaction 三条，其中两条是碎片。
        for ph in _maximal(candidate_phrases(title)):
            hits.append(_hit(ph, "B2", unit))

    # B3 · 跨页高频。**高频不等于术语**（energy use / power density 都高频，
    # 前者不是术语），故要求跨页 **且**（有大写信号 或 已被 B1/B2 命中）。
    b12_keys = {h.key for h in hits}
    chrome = _structural_blocks(sliceset)
    seen: dict[str, set[int]] = defaultdict(set)
    capped: set[str] = set()
    first: dict[str, Hit] = {}
    for s in sliceset.slices:
        if s.page_indices[0] in figure_pages:
            continue
        for u in s.units:
            if normalize_phrase(u.text) in chrome:
                continue
            for ph in candidate_phrases(u.text):
                key = normalize_phrase(ph)
                if not key:
                    continue
                seen[key].add(u.page_index)
                # 块首大写是句法而非强调，不算信号
                if ph[:1].isupper() and not u.text.strip().startswith(ph):
                    capped.add(key)
                first.setdefault(key, _hit(ph, "B3", u))
    for key, pgs in seen.items():
        if len(pgs) >= CROSS_PAGE_MIN and key in first and (key in capped or key in b12_keys):
            hits.append(first[key])

    for s in sliceset.slices:
        if s.title and re.search(r"\b(glossary|definitions|terminology)\b", s.title, re.I):
            for u in s.units:
                for ph in candidate_phrases(u.text):
                    hits.append(_hit(ph, "B4", u))

    return hits


# ── 图注通道 ──────────────────────────────────────────────────────────────────


def _label_segments(text: str) -> list[str]:
    """图内标签的候选段：整块、括号内、逐行。**不做全窗口切分。**

    换行有时分隔两个标签（``Condenser\\nPump``）、有时是同一标签折行
    （``Reactor\\ncore``）。两种都保留为候选，由 G1 的常用词表决定谁能留下。
    """
    out: list[str] = []
    stripped = _PAREN.sub(" ", text).strip()
    if stripped:
        out.append(stripped)
    for m in _PAREN.finditer(text):
        inner = m.group(1).strip()
        if inner and inner not in out:
            out.append(inner)
    for line in text.split("\n"):
        line = _PAREN.sub(" ", line).strip()
        if line and line not in out:
            out.append(line)
    return out


def _joined_fragments(units: list[SliceUnit]) -> list[SliceUnit]:
    """把同字号、``order`` 相邻、间距 < 1.5 行高的**两块**拼起来再判一次。

    为的是 ``Control``+``rods``、``Cooling``+``tower`` 这类被版面拆开的术语——
    实测 v1.1 有 2 条只能这样得到。**只拼两块**，三段拼接必然是垃圾。
    """
    out: list[SliceUnit] = []
    by_rank: dict[float, list[SliceUnit]] = defaultdict(list)
    for u in sorted(units, key=lambda x: x.order):
        by_rank[round(u.font.size_rank or 0.0, 2)].append(u)

    for group in by_rank.values():
        for a, b in zip(group, group[1:]):
            lh = a.font.size_pt or 10.0
            ax0, ay0, ax1, ay1 = a.bbox_pt
            bx0, by0, bx1, by1 = b.bbox_pt
            v_gap = max(by0 - ay1, ay0 - by1)
            h_gap = max(bx0 - ax1, ax0 - bx1)
            v_ok = v_gap <= FRAGMENT_MAX_GAP_LINES * lh and not (bx1 < ax0 or ax1 < bx0)
            h_ok = h_gap <= FRAGMENT_MAX_GAP_LINES * lh and not (by1 < ay0 or ay1 < by0)
            if not (v_ok or h_ok):
                continue
            # 收紧到三条硬约束。实测放开时拼接产出的垃圾
            # （pressurizer reactor vessel control、electricity electricity
            # transportation）远多于价值（只换来 containment structure 一条），
            # 违反「宁漏勿错」。
            if "\n" in a.text.strip() or "\n" in b.text.strip():
                continue                      # 只拼单行块——多行块本身已含多个标签
            first, second = (a, b) if (ay0, ax0) <= (by0, bx0) else (b, a)
            merged = f"{first.text.strip()} {second.text.strip()}"
            toks = [t.lower() for t in tokenize(merged)]
            if len(toks) > FRAGMENT_MAX_MERGED_WORDS:
                continue                      # 金标准最长 3 词，35/44 是 2 词
            if len(set(toks)) != len(toks):
                continue                      # 重复词必然是两个独立标签被拼在一起
            out.append(first.model_copy(update={"text": merged}))
    return out


def _try_caption(hits: list[Hit], unit: SliceUnit, top_rank: float, joined: bool) -> None:
    text = unit.text.strip()
    if not text:
        return
    if _words(text) > CAPTION_MAX_WORDS:           # F1
        return
    if has_sentence_punct(text):                    # F2
        return
    if (unit.font.size_rank or 0.0) >= top_rank - 1e-9:   # F3 · 本页最高档 = 标题
        return
    for seg in _label_segments(text):
        hits.append(_hit(seg, "F-JOIN" if joined else "F-CLUSTER", unit))


def figure_hits(sliceset: SliceSet) -> list[Hit]:
    hits: list[Hit] = []
    for units in _page_units(sliceset).values():
        ok, _ = is_figure_page(units, _adjacent_count(units))
        if not ok:
            continue
        top_rank = max((u.font.size_rank or 0.0) for u in units)
        for u in units:
            _try_caption(hits, u, top_rank, False)
        for u in _joined_fragments(units):
            _try_caption(hits, u, top_rank, True)
    return hits
