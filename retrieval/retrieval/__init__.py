"""AI 课件讲解助手 · F2 第二步 2b：向量索引与检索。

范围：索引 + 检索，产出能跑 30 条评测集并按 tag 分组报告。**不做 F3 的讲解生成。**

分层（单向）::

    parse_service.schema → slicer → retrieval
"""

from .compose import EMBED_TEXT_RULESET_VERSION, compose
from .embed import EmbeddingCache, FakeEmbedder, OpenAIEmbedder
from .index import IndexedSlice, VectorIndex
from .metrics import summarize, separability
from .search import EXPANSION_RULESET_VERSION, retrieve

__all__ = [
    "EMBED_TEXT_RULESET_VERSION",
    "EXPANSION_RULESET_VERSION",
    "compose",
    "EmbeddingCache",
    "FakeEmbedder",
    "OpenAIEmbedder",
    "IndexedSlice",
    "VectorIndex",
    "retrieve",
    "summarize",
    "separability",
]
