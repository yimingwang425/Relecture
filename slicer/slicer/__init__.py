"""AI 课件讲解助手 · F2 第二步 2a：课件切片。

**库，不是服务。** 纯函数 ``ParseResult → SliceSet``，无 I/O、无模型调用、
不写盘、不缓存——所有持久化走 Supabase（架构决定，理由见 README）。

分层（单向，无环）::

    parse_service.schema   契约，无依赖
            ↑
         slicer            消费契约
            ↑
    api.ingest             编排：parse → slice → 组文本 → 嵌入 → 落库
                           （独立的 parse_service.app 已于 2026-10-07 删除，见 parse-service/README）
"""

from .core import slice_document
from .schema import SLICE_RULESET_VERSION, SLICE_SCHEMA_VERSION, SLICER_VERSION, SliceSet

__all__ = [
    "slice_document",
    "SliceSet",
    "SLICE_SCHEMA_VERSION",
    "SLICER_VERSION",
    "SLICE_RULESET_VERSION",
]
