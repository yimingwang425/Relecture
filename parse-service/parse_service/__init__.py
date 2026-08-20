"""AI 课件讲解助手 · F2 课件解析服务。

本次交付只含 PDF 路径；PPTX 的 schema 形状已定义（``schema.PptxShapeOrigin`` 等），
解析器下次交付。
"""

from .schema import SCHEMA_VERSION, SERVICE_VERSION, ParseResult

__all__ = ["SCHEMA_VERSION", "SERVICE_VERSION", "ParseResult"]
