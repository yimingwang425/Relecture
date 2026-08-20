"""FastAPI 服务：``POST /parse``。

解析 + 切片一次返回。向量化（2b）与术语提取（第三步）不在本服务范围内。

**服务无状态**：不写盘、不缓存、不做跨用户去重。所有持久化走 Supabase——
PRD §12.1 的「一键删除」与「注销即清空」若跨两个存储系统实现会有两处漏点。
故切片不单开端点：一次调用同时产出两者，由调用方一次性写入。

同步接口，不排队：实测最慢一份（14.129，50 页 228 张图）全量解析 3.5 s，
最快的 11.201 是 5.1 ms/页。上传环节这个量级不需要任务队列。

决策日期：2026-08-20
"""

from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from slicer import slice_document

from .pdf import parse_pdf
from .response import ParseResponse
from .schema import SCHEMA_VERSION, SERVICE_VERSION

__all__ = ["app", "MAX_UPLOAD_BYTES", "SUPPORTED_SUFFIXES"]

#: 上传体积上限。来源：**占位待定**。
#: 确定路径：PRD §13.1 的课件上传埋点回流真实文件大小分布后取 P99。
#: 现值 50 MB 是测试集最大文件（15.316，约 1.8 MB）的近 30 倍，不会误伤。
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

SUPPORTED_SUFFIXES = (".pdf",)

app = FastAPI(
    title="AI 课件讲解助手 · 解析服务",
    version=SERVICE_VERSION,
    description="F2：把 PDF 课件解析为保留版面信息的结构化 JSON，并切成检索用的切片。",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service_version": SERVICE_VERSION, "schema_version": SCHEMA_VERSION}


@app.post("/parse", response_model=ParseResponse)
async def parse(file: UploadFile = File(...)) -> ParseResponse:
    filename = file.filename or "upload"
    lower = filename.lower()

    if not lower.endswith(SUPPORTED_SUFFIXES):
        # PPTX 的 schema 已定，解析器下次交付——这里明确区分「不支持」与「还没做」，
        # 避免调用方把它当成永久不支持而去做别的兜底
        if lower.endswith(".pptx"):
            raise HTTPException(
                status_code=501,
                detail="PPTX 解析尚未实现（schema 已定义，解析器为下一次交付）",
            )
        raise HTTPException(
            status_code=415,
            detail=f"仅支持 {'/'.join(SUPPORTED_SUFFIXES)}，收到 {filename}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 上限",
        )

    try:
        parsed = parse_pdf(data, filename)
    except Exception as exc:  # PyMuPDF 对损坏文件抛的异常类型不统一
        raise HTTPException(status_code=422, detail=f"无法解析该 PDF：{exc}") from exc

    return ParseResponse(
        schema_version=parsed.schema_version,
        document=parsed.document,
        pages=parsed.pages,
        slices=slice_document(parsed),
    )
