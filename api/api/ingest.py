"""摄入：上传 → 解析 → 切片 → 组文本 → 嵌入 → 落库。

**接线前这条链一行都没有。** `parse-service` 有 `/parse` 但无鉴权、不落盘；
`retrieval/corpus.py` 文件头写着「不写盘、不碰数据库」——它读的是仓库里的测试集。

## 一次事务性的动作，拆不开

`compose()` 的 boilerplate 检测是**整份文档范围**的（要看所有页才知道哪些块是页眉页脚），
所以嵌入必须**整份一起算**，不能增量。这决定了摄入的边界就是「一份文档」。

## 受限页在**索引侧**也排除，不只在评测集侧

`retrieval/corpus.py` 的原话：「只在评测集侧排除属于『靠约定保证』，靠不住。」
同一条在生产上更硬——第三方保留权利的页**不入库**，不是「入库但不检索」。
⚠️ 当前 `RESTRICTED_PAGES` 是按测试集文件名写死的清单（`SOURCES.md` 为准）。
真实用户上传的课件**不在那张清单上**，所以这一层对他们恒不触发：
**如实登记，不写成「受限页已排除」**——那是对测试语料成立的句子。

## 嵌入是唯一要出境的一步

切片原文 → OpenAI（PRD §13.3 的**第二次出境**，且处理地不在新加坡）。
所以 `embedder` 是注入的：断言里喂一个确定性的假嵌入器，
**真解析、真切片、假嵌入**——省掉的只有那次网络调用与那笔钱，
解析与切片走的是生产的同一份代码。

决策日期：2026-09-12
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

__all__ = ["Embedder", "IngestResult", "DuplicateDocument", "ingest_document", "EMBED_MODEL_ID"]


class Embedder(Protocol):
    """把一批文本变成一批向量。维度必须等于 `slice_embeddings.embedding` 的 1024。"""

    name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class DuplicateDocument(Exception):
    """同一用户、同一课程、同一份文件。

    ⚠️ 数据库那条唯一索引是**硬失败（23505）不是提示**。摄入路径必须把它翻译成人话，
    否则用户看到的是一个数据库错误码——这一条登记在
    `f1_samples/README.md` §二，阶段 6 做「先查后提示」。
    """


@dataclass(frozen=True, slots=True)
class IngestResult:
    document_id: str
    slice_count: int
    embedded_count: int
    skipped_pages: int
    restricted_pages_excluded: int
    verdict: str


#: 写进 `slice_embeddings.model` 的字符串。**从 `retrieval.selection` 读，不写字面量**——
#: 那是维度与模型这两个数在代码里的唯一出处（口径 F13 的同一条）。
def EMBED_MODEL_ID() -> str:  # noqa: N802 —— 它是个取值，不是类
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL

    return f"{SELECTED_MODEL}@{SELECTED_DIMENSIONS}"


def ingest_document(
    *,
    data: bytes,
    filename: str,
    owner: str,
    course_id: str,
    token: str,
    store: Any,
    embedder: Embedder,
    restricted_pages: frozenset[int] = frozenset(),
    split_threshold_chars: int | None = None,
) -> IngestResult:
    """跑完整条链，返回落库结果。

    `store` 是 `SupabaseClient`——所有写入带**用户自己的 JWT**，RLS 由数据库执行
    （与 `/me/style` 同一条路径，服务端不持有 service_role key，口径 F40）。
    """
    from parse_service.pdf import parse_pdf
    from retrieval.compose import EMBED_TEXT_RULESET_VERSION, compose
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_VARIANT
    from slicer import slice_document
    from slicer.schema import SLICE_SCHEMA_VERSION

    parsed = parse_pdf(data, filename)
    sliceset = slice_document(parsed, split_threshold_chars)
    manifest = sliceset.manifest

    # 受限页不入索引（见模块 docstring 的登记）。
    kept = [s for s in sliceset.slices if not any(p in restricted_pages for p in s.page_indices)]
    excluded = len(sliceset.slices) - len(kept)

    doc = store.insert_document(
        token=token,
        row={
            "owner": owner,
            "course_id": course_id,
            "filename": filename,
            "source_format": parsed.document.source_format,
            "content_sha256": parsed.document.content_sha256,
            "page_count": parsed.document.page_count,
            "usable_page_count": parsed.document.usable_page_count,
            "unusable_pages": [p.model_dump(mode="json") for p in parsed.document.unusable_pages],
            "verdict": parsed.document.verdict,
            "parser": parsed.document.parser.model_dump(mode="json"),
            # ---- 五个口径版本列。漏一个数据库就拒（not null），这是刻意的：
            # 口径一改新旧行再也分不开，而合并会得到一个看起来正常的错数。
            "parse_schema_version": parsed.schema_version,
            "slice_schema_version": SLICE_SCHEMA_VERSION,
            "slicer_version": manifest.slicer_version,
            "slice_ruleset_version": manifest.slice_ruleset_version,
            # NULL 有含义（= 不拆），所以原样传 None，不填 0
            "split_threshold_chars": split_threshold_chars,
        },
    )
    document_id = doc["id"]

    if kept:
        store.insert_slices(
            token=token,
            rows=[
                {
                    "document_id": document_id,
                    "slice_id": s.slice_id,
                    "owner": owner,
                    "ordinal": s.ordinal,
                    "page_indices": list(s.page_indices),
                    "title": s.title,
                    "text": s.text,
                    "char_count": s.char_count,
                    "units": [u.model_dump(mode="json") for u in s.units],
                    "images": [i.model_dump(mode="json") for i in s.images],
                    "page_signals": [p.model_dump(mode="json") for p in s.page_signals],
                    "prev_slice_id": s.prev_slice_id,
                    "next_slice_id": s.next_slice_id,
                }
                for s in kept
            ],
        )

        # 组文本走 T0 变体（`retrieval.selection` 是那个选择的唯一出处）。
        # ⚠️ compose 要**整份** sliceset —— boilerplate 检测是文档范围的。
        texts = compose(sliceset, SELECTED_VARIANT)
        ordered = [texts[s.slice_id] for s in kept]
        vectors = embedder.embed(ordered)
        if len(vectors) != len(kept):
            raise ValueError(f"嵌入返回 {len(vectors)} 条，切片有 {len(kept)} 条")

        # ⚠️ **比的是 schema 的维度，不是嵌入器自称的维度。**
        # 第一版写的是 `len(vectors[0]) != embedder.dimensions` —— 那是拿嵌入器
        # 跟它自己比：一个声称 512 又返回 512 的嵌入器照样通过，然后向量进不了
        # `vector(1024)` 那一列。**一个永远不会失败的检查不是检查**（口径 F16）。
        # 被 `test_dimension_mismatch_raises` 抓出来的。
        #
        # `SELECTED_DIMENSIONS` 是这个数在代码里的唯一出处，SQL 的列定义也钉着它。
        if embedder.dimensions != SELECTED_DIMENSIONS:
            raise ValueError(
                f"嵌入器声称 {embedder.dimensions} 维，而 slice_embeddings.embedding "
                f"是 vector({SELECTED_DIMENSIONS})（retrieval.selection 是唯一出处）"
            )
        if vectors and len(vectors[0]) != SELECTED_DIMENSIONS:
            raise ValueError(
                f"向量维度 {len(vectors[0])} ≠ vector({SELECTED_DIMENSIONS})"
            )

        store.insert_embeddings(
            token=token,
            rows=[
                {
                    "document_id": document_id,
                    "slice_id": s.slice_id,
                    "owner": owner,
                    "model": EMBED_MODEL_ID(),
                    "embed_ruleset": EMBED_TEXT_RULESET_VERSION,
                    # pgvector 的文本输入格式。传参形状由 verify_corpus_prod.py 第 1 步
                    # 对真库确认过——本地验不了（PGlite 没有 vector 扩展）。
                    "embedding": "[" + ",".join(repr(float(x)) for x in v) + "]",
                }
                for s, v in zip(kept, vectors, strict=True)
            ],
        )

    return IngestResult(
        document_id=document_id,
        slice_count=len(kept),
        embedded_count=len(kept),
        skipped_pages=len(manifest.skipped_pages),
        restricted_pages_excluded=excluded,
        verdict=parsed.document.verdict,
    )
