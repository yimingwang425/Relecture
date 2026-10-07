"""把已入库的文档升级到当前的检索索引口径（2026-10-07，口径 F97 / F99）。

## 为什么需要它

`documents.index_ruleset` 默认 `embed-text-1.0`：本轮之前上传的文档没有定位语、
没有词条。检索按每份文档自己的口径取行，所以它们**照样检索得到**（向量一路），
只是吃不到词法一路与定位语。本模块把它们补齐。

## 只用落库的东西，不要 PDF

PDF 原件不存（PRD §12.1）。重建需要的全在库里：`slices.units`（T0 组文本的输入，
`retrieval.compose.compose_t0` 是同一个函数）、标题、原文。**图片页转写补不了**——
那要看原图，只有重新上传才行；这一点进返回值，不静默。

## 幂等：先删本口径的旧行，再写

中途失败（嵌入超时、写到一半）之后重跑，新口径下可能已有一部分行，直接插会撞主键。
所以写之前先删掉**本文档、本口径**的行（`slice_embeddings` / `slice_lexicon` 都授了
delete），最后才把 `documents.index_ruleset` 切过去——**切换是最后一步**：
在那之前检索仍按老口径取行，用户看不到一个半成品的索引。

决策日期：2026-10-07
"""

from __future__ import annotations

from typing import Any

__all__ = ["reindex_document"]


def reindex_document(*, store: Any, token: str, owner: str, document: dict[str, Any],
                     embedder: Any, contextualizer: Any | None) -> dict[str, Any]:
    """重建一份文档的向量与词条。返回这一份的结果摘要。"""
    from retrieval.compose import compose_t0
    from retrieval.selection import SELECTED_DIMENSIONS

    from .indexing import INDEX_RULESET, embedding_text, lexicon_text
    from .ingest import EMBED_MODEL_ID

    doc_id = str(document["id"])
    slices = store.slices_for_reindex(token, doc_id)
    if not slices:
        return {"document_id": doc_id, "slices": 0, "skipped": "这份文档没有切片"}

    contexts: dict[str, str] = {}
    llm_cost: float | None = 0.0
    if contextualizer is not None:
        r = contextualizer.contextualize(document.get("filename", ""), [
            (s["slice_id"], list(s.get("page_indices") or []), s.get("title"), s.get("text") or "")
            for s in slices])
        contexts, llm_cost = r.contexts, r.cost_usd

    ids = [s["slice_id"] for s in slices]
    composed = [
        # 转写片（v###）没有 units，组文本就是原文——与摄入时的处置相同。
        compose_t0([str(u.get("text", "")) for u in (s.get("units") or [])],
                   s.get("text") or "", s["slice_id"])
        for s in slices
    ]
    vectors = [[float(x) for x in row]
               for row in embedder.embed([embedding_text(contexts.get(i), c)
                                          for i, c in zip(ids, composed, strict=True)])]
    if len(vectors) != len(ids) or (vectors and len(vectors[0]) != SELECTED_DIMENSIONS):
        raise ValueError(f"嵌入返回 {len(vectors)} 条 / 维度不是 {SELECTED_DIMENSIONS}")

    store.delete_index_rows(token=token, document_id=doc_id, ruleset=INDEX_RULESET)
    store.insert_embeddings(token=token, rows=[
        {"document_id": doc_id, "slice_id": sid, "owner": owner, "model": EMBED_MODEL_ID(),
         "embed_ruleset": INDEX_RULESET,
         "embedding": "[" + ",".join(repr(float(x)) for x in v) + "]"}
        for sid, v in zip(ids, vectors, strict=True)])
    store.insert_lexicon(token=token, rows=[
        {"document_id": doc_id, "slice_id": s["slice_id"], "owner": owner,
         "ruleset": INDEX_RULESET, "context": contexts.get(s["slice_id"]),
         "lexical_text": lexicon_text(s.get("title"), contexts.get(s["slice_id"]), s.get("text") or "")}
        for s in slices])
    # **最后一步**才切口径：在此之前检索仍按老口径取行。
    store.update_document(token=token, document_id=doc_id, fields={"index_ruleset": INDEX_RULESET})

    return {"document_id": doc_id, "slices": len(slices), "contextualized": len(contexts),
            "llm_cost_usd": llm_cost, "index_ruleset": INDEX_RULESET,
            # 老文档的图片页**补不了转写**（要原图）。说出来，不静默。
            "vision_not_backfilled": bool(document.get("unusable_pages"))}
