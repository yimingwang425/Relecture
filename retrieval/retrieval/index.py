"""内存精确余弦索引（评测用）。

**为什么评测不走 pgvector**：ANN 索引（hnsw / ivfflat）会引入召回损失，
把「模型好不好」与「近似索引损失多少」混在一起就归因不了。
评测走精确扫描，生产走 pgvector，两者用**同一份向量与同一个余弦函数**。

对 v1 的规模，精确扫描本身也是正确选择：PRD §7.5 判定「单门课数十页」，
本语料每门 15–46 个切片，全表扫描的代价可忽略。

决策日期：2026-08-21
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["IndexedSlice", "VectorIndex"]


@dataclass(frozen=True, slots=True)
class IndexedSlice:
    source_file: str
    slice_id: str
    ordinal: int
    page_indices: tuple[int, ...]
    char_count: int
    prev_slice_id: str | None
    next_slice_id: str | None


class VectorIndex:
    """向量已 L2 归一化，故余弦相似度 = 点积。"""

    def __init__(self, items: list[IndexedSlice], vectors: np.ndarray) -> None:
        if len(items) != len(vectors):
            raise ValueError(f"条目数 {len(items)} 与向量数 {len(vectors)} 不一致")
        self.items = items
        self.vectors = vectors
        self._by_key = {(it.source_file, it.slice_id): i for i, it in enumerate(items)}

    def __len__(self) -> int:
        return len(self.items)

    def lookup(self, source_file: str, slice_id: str) -> int | None:
        return self._by_key.get((source_file, slice_id))

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        """返回 ``[(下标, 相似度)]``，按相似度降序。

        并列时按索引下标升序（即切片的文档内顺序），保证结果**完全确定**——
        不确定的排序会让同一份报告两次重跑得到不同数字。
        """
        scores = self.vectors @ query
        k = min(k, len(scores))
        # 负号让 argsort 变降序；第二键用下标，保证并列时顺序稳定
        order = np.lexsort((np.arange(len(scores)), -scores))[:k]
        return [(int(i), float(scores[i])) for i in order]
