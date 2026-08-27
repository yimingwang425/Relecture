"""把 `retrieval` 接到讲解链路上。**唯一 import retrieval 的模块。**

隔离的理由：`retrieval` 依赖 numpy 与 openai，而讲解链路核心不需要它们。
核心对着 `context.Retriever` 协议编程，本模块提供真实实现，
`InMemoryRetriever` 提供不需要网络的实现——**两者走同一条下游代码路径**。

## 沿用 retrieval 的既有口径，不重新论证

- 模型与维度取自 `retrieval.selection`（**维度在代码里的唯一出处**，R17/§7.3）
- 组文本走 T0 变体：R04/§7.4 实测四个变体无一致方向，**维持假设最少的那个**
- 扩展模式 `none`：三种模式同样没有同向证据（R17）
- 预算 B=5：与评测集报告的 `@5` 口径一致，换预算等于换口径

## `skipped_pages` 从哪来

`SliceManifest.skipped_pages` 是 slicer 的既有产出（F2 §12 第 4 条要求它到检索层）。
本模块把**本次语料涉及文档**的整份清单带进 ContextPack，
不按「与问题相关」筛选——判断相关性需要模型，那是 F3b。宁可多带。

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .context import ContextPack, ContextSlice, SkippedPageRef

__all__ = ["InMemoryRetriever", "RetrievalRetriever"]


@dataclass
class InMemoryRetriever:
    """不需要网络的检索后端：按预先给定的映射返回切片。

    用于单元测试与 `--fake` 全链路。**它不假装自己是检索**——
    返回什么完全由调用方给定，任何「召回率」类断言都不得建立在它之上
    （retrieval README §三 那条纪律：恒为 0 的链路验证是无效验证）。
    """

    by_question: dict[str, tuple[ContextSlice, ...]] = field(default_factory=dict)
    default_slices: tuple[ContextSlice, ...] = ()
    skipped_pages: tuple[SkippedPageRef, ...] = ()
    unusable_files: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=lambda: {"backend": "in_memory"})

    def fetch(self, question: str) -> ContextPack:
        return ContextPack(
            question=question,
            slices=self.by_question.get(question, self.default_slices),
            skipped_pages=self.skipped_pages,
            unusable_files=self.unusable_files,
            meta=dict(self.meta),
        )


@dataclass
class RetrievalRetriever:
    """真实后端。构造代价高（解析 + 切片 + 嵌入），故由调用方构造一次复用。

    `_slice_texts` 补的正是 `IndexedSlice` 刻意不带的东西——它只需要排序所需
    字段，讲解需要原文。**不改 retrieval 的数据结构**，在这里补映射。
    """

    index: Any
    embedder: Any
    slicesets: dict[str, Any]
    budget: int = 5
    #: None = 取 `selection.SELECTED_MODE`。**不在这里写 "none" 字面量**——
    #: 那个值的唯一出处是 selection.py，抄一份就多一个会漂的副本。
    mode: str | None = None
    tau_short: int | None = None
    _slice_texts: dict[tuple[str, str], Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self._slice_texts:
            for name, ss in self.slicesets.items():
                for s in ss.slices:
                    self._slice_texts[(name, s.slice_id)] = s

    @property
    def _skipped(self) -> tuple[SkippedPageRef, ...]:
        out: list[SkippedPageRef] = []
        for name, ss in sorted(self.slicesets.items()):
            for p in ss.manifest.skipped_pages:
                out.append(
                    SkippedPageRef(
                        source_file=name, index=p.index, kind=str(p.kind), reason=p.reason
                    )
                )
        return tuple(out)

    def fetch(self, question: str) -> ContextPack:
        from retrieval.search import EXPANSION_RULESET_VERSION, retrieve

        from retrieval.selection import SELECTED_MODE

        mode = self.mode or SELECTED_MODE
        vector = self.embedder.embed([question])[0]
        results = retrieve(self.index, vector, self.budget, mode, self.tau_short)

        slices: list[ContextSlice] = []
        for r in results:
            src = self._slice_texts.get((r.item.source_file, r.item.slice_id))
            slices.append(
                ContextSlice(
                    source_file=r.item.source_file,
                    slice_id=r.item.slice_id,
                    page_indices=tuple(r.item.page_indices),
                    title=getattr(src, "title", None),
                    text=getattr(src, "text", ""),
                    char_count=r.item.char_count,
                    score=r.score,
                    via=r.via,
                )
            )

        from retrieval.compose import EMBED_TEXT_RULESET_VERSION
        from retrieval.selection import (
            SELECTED_DIMENSIONS,
            SELECTED_MODEL,
            SELECTED_VARIANT,
        )

        return ContextPack(
            question=question,
            slices=tuple(slices),
            skipped_pages=self._skipped,
            meta={
                "backend": "retrieval",
                "budget": self.budget,
                "mode": mode,
                "tau_short": self.tau_short,
                # 选型三项一律从 selection.py 读，不在这里写字面量：
                # 那是这些数在代码里的唯一出处，抄一份等于多一个会漂的副本
                "embed_model": SELECTED_MODEL,
                "embed_dimensions": SELECTED_DIMENSIONS,
                "embed_variant": SELECTED_VARIANT,
                "selected_mode": SELECTED_MODE,
                "embed_text_ruleset": EMBED_TEXT_RULESET_VERSION,
                "expansion_ruleset": EXPANSION_RULESET_VERSION,
            },
        )
