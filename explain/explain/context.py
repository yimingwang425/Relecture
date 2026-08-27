"""检索结果 → 讲解 agent 的上下文包。

## 三件事在这里做

1. **把检索片装成可讲解的形态**（原文 + 页号 + 标题），而不只是 id 和分数。
   `retrieval.IndexedSlice` 刻意只带排序所需字段（它不需要文本），
   本模块自建 `(source_file, slice_id) → Slice` 的映射补上文本，
   **不改 retrieval 的数据结构**。
2. **把 `skipped_pages` / `unusable_files` 一路带下来**（口径 X07）。
   `SliceManifest.skipped_pages` 早就有完整结构，F2 §12 第 4 条要求它到检索层；
   本轮**不写兜底文案**（那是 F3b），只保证通道通、日志里有、GapChecker 拿得到。
3. **算一个 `signature`**。两组对照共用同一份 ContextPack（口径 X02），
   签名让「共用」这件事可被核验，而不是靠约定。

## 为什么 ContextPack 与配置、与条件、与历史都无关

检索 query = **本轮提问原文**，不含对话历史改写（口径 X01，
依据 PRD §7.5「本场景的检索意图单一，不需要多跳推理」）。

由此得到一条结构性质：**同一个提问在任何配置点、任何条件下检索结果完全相同。**
第 0 周的漂移实验为了保住对照干脆不含检索（「引入检索后两组可能取到不同片段，
连输入都不一样，对照就废了」），这条性质正是本轮能把检索接进对照实验的原因。

决策日期：2026-08-22
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = ["ContextSlice", "SkippedPageRef", "ContextPack", "Retriever"]


@dataclass(frozen=True, slots=True)
class ContextSlice:
    """一个检索到的切片。`score is None` 表示它是被扩展进来的邻居。"""

    source_file: str
    slice_id: str
    page_indices: tuple[int, ...]
    title: str | None
    text: str
    char_count: int
    score: float | None = None
    via: str = "hit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "slice_id": self.slice_id,
            "pages": list(self.page_indices),
            "title": self.title,
            "chars": self.char_count,
            "score": self.score,
            "via": self.via,
        }


@dataclass(frozen=True, slots=True)
class SkippedPageRef:
    """被跳过的页。**不带文案**——文案是 F3b 的事，这里只带事实。

    `kind` 区分两类失败，它们在用户侧的表现完全不同（PRD §6.2）：
    `image_only` 是「内容在图里，我读不了」，不是「课件里没提到」。
    """

    source_file: str
    index: int
    kind: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "index": self.index,
            "kind": self.kind,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ContextPack:
    question: str
    slices: tuple[ContextSlice, ...]
    #: 本次检索涉及到的文档里被跳过的页。**整份文档级**，不是「与问题相关的页」——
    #: 判断相关性需要模型，那是 F3b。这里宁可多带，也不做没有依据的筛选。
    skipped_pages: tuple[SkippedPageRef, ...] = ()
    #: 整份不可用的文件（如扫描件）。
    unusable_files: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.slices

    @property
    def total_chars(self) -> int:
        return sum(s.char_count for s in self.slices)

    def signature(self) -> str:
        """内容指纹。用于核验两组、各配置点拿到的是同一份上下文（口径 X02）。

        只取切片身份与顺序，不取分数——分数是浮点，跨平台重跑可能末位不同，
        而它对「两组输入是否相同」这个判断没有贡献。
        """
        payload = json.dumps(
            {
                "question": self.question,
                "slices": [[s.source_file, s.slice_id] for s in self.slices],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature(),
            "slice_count": len(self.slices),
            "context_chars": self.total_chars,
            "slices": [s.to_dict() for s in self.slices],
            "skipped_pages_in_scope": [p.to_dict() for p in self.skipped_pages],
            "unusable_files": list(self.unusable_files),
            "meta": self.meta,
        }


class Retriever(Protocol):
    """检索后端。

    定成协议而不是直接 import `retrieval`：`retrieval` 依赖 numpy / openai，
    而讲解链路本身不需要它们。真实后端在 `explain/retrieval_adapter.py`，
    测试与假跑用内存实现，**两者走同一条代码路径**。
    """

    def fetch(self, question: str) -> ContextPack: ...
