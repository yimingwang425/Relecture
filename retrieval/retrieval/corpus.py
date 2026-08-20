"""语料与评测集装载。

解析 → 切片 → 索引条目，全部在内存里完成；本模块不写盘、不碰数据库
（架构决定：Python 侧无状态，持久化全部走 Supabase）。

**排除规则与评测集一致**，且在这里再执行一次而不是信任评测集：

- 15.316 整份不入索引（整份扫描件，``verdict=unusable``，0 切片）
- 14.129 的 8 页第三方保留权利内容不入索引
  （``测试集/SOURCES.md``：用作评测输入时应跳过这些页）

在索引侧也排除受限页，是为了**即使评测集哪天引用了它们，也不会真的被检索到**。
只在评测集侧排除属于「靠约定保证」，靠不住。

决策日期：2026-08-21
"""

from __future__ import annotations

import json
from pathlib import Path

from parse_service.pdf import parse_pdf
from slicer import slice_document
from slicer.schema import SliceSet

from .index import IndexedSlice

__all__ = ["REPO", "CORPUS", "EVAL", "RESTRICTED_PAGES", "EXCLUDED_FILES", "load_slicesets", "load_eval", "build_items"]

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "实验脚本" / "测试集"
EVAL = REPO / "实验脚本" / "检索评测集" / "评测集.json"

#: 第三方保留权利页（6 个权利人）。见 测试集/SOURCES.md。
#: 注意 p31 的 © 与 All rights reserved 被解析切在两个块里，
#: 直接正则只搜得到 7 页——该清单以 SOURCES.md 为准，不靠正则重新推导。
RESTRICTED_PAGES: dict[str, frozenset[int]] = {
    "MIT14_129S25_lec02.pdf": frozenset({10, 11, 25, 26, 31, 38, 40, 42}),
}

#: 整份排除的文件。
EXCLUDED_FILES = frozenset({"MIT15_316SU05_leader_dev.pdf"})


def load_slicesets() -> dict[str, SliceSet]:
    out: dict[str, SliceSet] = {}
    for path in sorted(CORPUS.glob("*.pdf")):
        if path.name in EXCLUDED_FILES:
            continue
        parsed = parse_pdf(path.read_bytes(), path.name)
        out[path.name] = slice_document(parsed)
    return out


def load_eval() -> list[dict]:
    return json.loads(EVAL.read_text(encoding="utf-8"))["items"]


def build_items(slicesets: dict[str, SliceSet]) -> list[IndexedSlice]:
    """展平成索引条目，并剔除受限页对应的切片。"""
    items: list[IndexedSlice] = []
    for name, ss in slicesets.items():
        banned = RESTRICTED_PAGES.get(name, frozenset())
        for s in ss.slices:
            if any(p in banned for p in s.page_indices):
                continue
            items.append(
                IndexedSlice(
                    source_file=name,
                    slice_id=s.slice_id,
                    ordinal=s.ordinal,
                    page_indices=tuple(s.page_indices),
                    char_count=s.char_count,
                    prev_slice_id=s.prev_slice_id,
                    next_slice_id=s.next_slice_id,
                )
            )
    return items
