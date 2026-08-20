"""``POST /parse`` 的组合响应。

**这个模型刻意不放在 ``schema.py``。** 分层是单向的::

    parse_service.schema      契约，无依赖
            ↑
         slicer               消费契约（slicer.schema 导入 parse_service.schema）
            ↑
    parse_service.response    组合两者
            ↑
    parse_service.app         编排

若把 ``ParseResponse`` 放进 ``schema.py``，就会形成
``parse_service.schema → slicer.schema → parse_service.schema`` 的循环导入。

## 为什么切片不单开端点

架构决定：**Python 服务保持无状态，所有持久化在 Supabase。**
理由是合规——PRD §12.1 的「一键删除」与「注销即清空」若跨两个存储系统实现，
会有两处漏点。服务侧不写盘、不缓存，因此也没有「先 parse 存起来、再调 slice」的中间态：
一次调用同时返回解析结果与切片，由调用方一次性写入 Supabase。

决策日期：2026-08-20
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from slicer.schema import SliceSet

from .schema import DocumentMeta, Page


class ParseResponse(BaseModel):
    """解析 + 切片的组合结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    document: DocumentMeta
    pages: list[Page] = Field(default_factory=list)
    slices: SliceSet = Field(
        description="切片集合。由 slicer 库产出，与 pages 同源——"
        "``slices.manifest.content_sha256`` 必等于 ``document.content_sha256``"
    )


__all__ = ["ParseResponse"]
