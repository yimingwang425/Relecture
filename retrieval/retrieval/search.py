"""检索与邻接扩展。

## 三种扩展模式

============  ==========================================================
``none``      只取 top-k
``always``    每个命中切片各取 prev / next 各一片
``short_only``**仅当**命中切片 ``char_count < τ_short`` 时才扩展
============  ==========================================================

``short_only`` 是 slicer S04「过短页要不要合并」的**替代验证**：
它只在查询时扩展，不改切片、不重建索引。
**能推迟到查询时的决策，不该在索引时定死。**

## 必须在同等预算下比较

扩展本身就是「多取几片」，直接比 ``recall@5`` 对 ``none`` 不公平。
故一律以**预算 B**（最终交给下游的切片数）为口径：``none`` 取 top-B，
``always`` 取 top-⌈B/3⌉ 再各补邻居，两者最终都截到 B 片。

## 超预算时的取舍顺序（口径，改动须递增 ``EXPANSION_RULESET_VERSION``）

邻居没有相似度分数，无法与命中片混排，故顺序显式规定：

1. **命中片在前**，按相似度降序
2. **邻居紧随其宿主片**，宿主排名越靠前其邻居越靠前
3. 同一宿主内 **prev 先于 next**
4. 去重时保留更靠前的位置（命中片位置优先于它作为别人邻居的位置）
5. **不足预算时用后续命中片回填**，按相似度继续往下取

第 5 条不是补丁：相邻切片被扩展进来后会与后续命中片重复，去重后可能**填不满预算**
（实测 top-2 + 邻居在切片连号时只剩 3 片）。若不回填，扩展组拿到的片数少于
``none`` 组，比较就偏向了 ``none``——与「扩展必然赢」方向相反，但同样是不公平的比较。
**同预算比较要求两边都真的用满预算。**

这几条决定同预算下哪些片被挤掉，**直接影响对照结论**，所以先写死再跑。

决策日期：2026-08-21
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .index import IndexedSlice, VectorIndex

__all__ = [
    "EXPANSION_RULESET_VERSION",
    "MODES",
    "Retrieved",
    "retrieve",
]

#: 扩展口径版本。三种模式的行为、取舍顺序、预算换算，任意一处改动都必须递增。
EXPANSION_RULESET_VERSION = "expand-1.0"

MODES = ("none", "always", "short_only")


@dataclass(frozen=True, slots=True)
class Retrieved:
    """一条检索结果。``score`` 为 ``None`` 表示它是被扩展进来的邻居。"""

    item: IndexedSlice
    score: float | None
    via: str  # "hit" | "prev" | "next"
    host_slice_id: str | None = None


def _neighbours(index: VectorIndex, item: IndexedSlice) -> list[tuple[str, IndexedSlice]]:
    out: list[tuple[str, IndexedSlice]] = []
    for via, sid in (("prev", item.prev_slice_id), ("next", item.next_slice_id)):
        if not sid:
            continue
        pos = index.lookup(item.source_file, sid)
        if pos is not None:
            out.append((via, index.items[pos]))
    return out


def retrieve(
    index: VectorIndex,
    query: np.ndarray,
    budget: int,
    mode: str = "none",
    tau_short: int | None = None,
) -> list[Retrieved]:
    """在预算 ``budget`` 下返回最终的切片列表。

    ``tau_short`` 仅 ``short_only`` 模式使用；该模式下未提供则报错，
    不设默认值——默认值会变成一个没人记得来源的阈值。
    """
    if mode not in MODES:
        raise ValueError(f"未知模式 {mode!r}，可选 {MODES}")
    if mode == "short_only" and tau_short is None:
        raise ValueError("short_only 模式必须显式提供 tau_short，本层不设默认值")
    if budget <= 0:
        raise ValueError("budget 必须为正")

    # 预算换算：扩展模式每个命中片最多带 2 个邻居，故命中片数取 ⌈B/3⌉。
    # 多取一批候选备着回填（规则 5），取到 budget 足够覆盖最坏情况。
    n_hits = budget if mode == "none" else max(1, -(-budget // 3))
    pool = index.search(query, budget)

    ordered: list[Retrieved] = []
    for pos, score in pool[:n_hits]:
        item = index.items[pos]
        ordered.append(Retrieved(item=item, score=score, via="hit"))
        if mode == "none":
            continue
        if mode == "short_only" and item.char_count >= (tau_short or 0):
            continue
        for via, nb in _neighbours(index, item):
            ordered.append(Retrieved(item=nb, score=None, via=via, host_slice_id=item.slice_id))

    # 规则 5：不足预算时按相似度继续取后续命中片回填
    for pos, score in pool[n_hits:]:
        ordered.append(Retrieved(item=index.items[pos], score=score, via="hit"))

    # 去重：保留首次出现的位置（命中片必然先于它作为邻居出现）
    seen: set[tuple[str, str]] = set()
    out: list[Retrieved] = []
    for r in ordered:
        key = (r.item.source_file, r.item.slice_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= budget:
            break
    return out


def retrieved_pages(results: list[Retrieved]) -> list[tuple[str, int]]:
    """展开成 ``(source_file, page_index)`` 列表，保持顺序、去重。

    切片与页当前是一一对应（slicer S01），但 ``page_indices`` 是列表——
    日后若启用过短页合并，一个切片会带多页，这里不必改。
    """
    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for r in results:
        for p in r.item.page_indices:
            key = (r.item.source_file, p)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out
