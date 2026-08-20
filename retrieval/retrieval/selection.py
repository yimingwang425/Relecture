"""选型结论。**这是维度这个数在代码里的唯一出处。**

理由见 ``README.md`` §七，分两层：

- **large 优于 small 是召回证据**：multipage full-hit@5 上多 2–4 题，
  四个文本变体与三种扩展模式全部同向（满足 R17 的重复要求）。
- **1024 而非 large 默认的 3072 是工程约束，不是召回**：两者在本次评测上
  无法区分（差异只出现在单格，按 R17 不构成证据）；3072 超过 pgvector 的
  ANN 索引上限 2000 维，故在无法区分的选项间选保留 ANN 能力的那个。

维度与 ``sql/001_slices.sql`` 强耦合：改选型必须同步改 SQL 的三处
（列定义、检索函数参数、ANN 索引），否则接线时才会报维度不匹配。
``tests/test_selection.py`` 会在两者不一致时变红。

决策日期：2026-08-21
"""

from __future__ import annotations

__all__ = ["SELECTED_MODEL", "SELECTED_DIMENSIONS", "PGVECTOR_ANN_MAX_DIM", "SELECTED_VARIANT", "SELECTED_MODE"]

SELECTED_MODEL = "text-embedding-3-large"

#: 显式截断（Matryoshka），不是模型默认值——large 默认 3072。
SELECTED_DIMENSIONS = 1024

#: pgvector 的 hnsw / ivfflat 索引维度上限。vector 列本身可到 16000。
PGVECTOR_ANN_MAX_DIM = 2000

#: 组文本变体。实测无任何变体差异构成证据（R17），故取假设最少的基线。
SELECTED_VARIANT = "T0"

#: 扩展模式。实测扩展无收益，最强模型上三种模式基本持平，弱模型上有害。
SELECTED_MODE = "none"
