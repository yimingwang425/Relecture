"""阶段 1 起的真实运行。**这是唯一会花钱的模块。**

## 凭据

照既有约定（`漂移实验/README.md`「运行前：凭据注入」）：
`.env` 局部注入，**不要全局 export**——全局设置会让同环境下的 Claude Code
从订阅计费切到 API 按量计费。本模块只读 `ANTHROPIC_API_KEY` 与 `OPENAI_API_KEY`，
不回退到其他来源，凭据不写入任何文件、不进日志、不进 metadata。

## 检索侧

真实语料 → 解析 → 切片 → T0 组文本 → 嵌入（带磁盘缓存）→ 内存精确余弦。
与 `retrieval` 的评测路径**同一套代码**，参数从 `selection.py` 读。

嵌入成本可忽略（全矩阵 < $0.02，且切片向量已在 `retrieval/cache` 里）；
提问向量每题一次，跨点跨组共用（口径 X02）。

决策日期：2026-08-22
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from . import _paths  # noqa: F401
from explain.llm import AnthropicModel
from explain.retrieval_adapter import RetrievalRetriever

from .runner import Arm, ScanConfig, run_scan

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent

#: 默认课件。14.129 由 F3b 引入（`--course` 切换）。
COURSE = "MIT22_081JF10_lec01b.pdf"
COURSE_LABELS = {
    "MIT22_081JF10_lec01b.pdf": "22.081J · Introduction to Sustainable Energy (lec01b)",
    "MIT14_129S25_lec02.pdf": "14.129 · Blockchain and the Design of Financial Systems (lec02)",
}


def _load_questions(name: str, limit: int | None = None) -> tuple[dict[str, Any], ...]:
    data = json.loads((HERE / "config" / name).read_text(encoding="utf-8"))
    qs = data["questions"]
    return tuple(qs[:limit] if limit else qs)


def build_retriever(budget: int = 5, course: str = COURSE) -> RetrievalRetriever:
    """只索引本实验用的那一份课件。

    不索引整个测试集：跨文档检索会让「片段来自哪门课」成为一个额外变量，
    而本实验的被测属性与它无关。

    ## ⚠️ 受限页必须排除（口径 G06，2026-09-05 修）

    原实现漏了 `RESTRICTED_PAGES`。22.081J 没有受限页，所以之前不咬人；
    **14.129 有 8 页第三方保留权利内容**（`测试集/SOURCES.md`），
    不排除的话它们照样被检索到、照样进 prompt，**没有任何报错**——
    合规问题的失败是静默的。

    页号从 `retrieval.corpus.RESTRICTED_PAGES` 读，**不在这里重抄一份**：
    抄一份就多一个会漂的副本，而那张清单以 `SOURCES.md` 为准、不靠正则重新推导。
    """
    import numpy as np
    from parse_service.pdf import parse_pdf
    from retrieval.compose import compose
    from retrieval.corpus import EXCLUDED_FILES, RESTRICTED_PAGES
    from retrieval.embed import EmbeddingCache, OpenAIEmbedder
    from retrieval.index import IndexedSlice, VectorIndex
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL, SELECTED_VARIANT
    from slicer import slice_document

    if course in EXCLUDED_FILES:
        raise ValueError(f"{course} 整份不入索引（见 retrieval/corpus.py）")

    path = REPO / "实验脚本" / "测试集" / course
    sliceset = slice_document(parse_pdf(path.read_bytes(), path.name))
    slicesets = {course: sliceset}
    banned = RESTRICTED_PAGES.get(course, frozenset())

    texts = compose(sliceset, SELECTED_VARIANT)
    items, ordered_texts = [], []
    for s in sliceset.slices:
        if any(p in banned for p in s.page_indices):
            continue  # 第三方保留权利页，G06
        items.append(
            IndexedSlice(
                source_file=course,
                slice_id=s.slice_id,
                ordinal=s.ordinal,
                page_indices=tuple(s.page_indices),
                char_count=s.char_count,
                prev_slice_id=s.prev_slice_id,
                next_slice_id=s.next_slice_id,
            )
        )
        ordered_texts.append(texts[s.slice_id])

    embedder = OpenAIEmbedder(SELECTED_MODEL, SELECTED_DIMENSIONS)
    cache = EmbeddingCache(REPO / "retrieval" / "cache")
    vectors = np.stack(cache.get_or_compute(embedder, ordered_texts))

    class _CachedEmbedder:
        """提问向量也走磁盘缓存：同一提问跨点跨组只嵌入一次，且可复现。"""

        name = embedder.name
        dimensions = embedder.dimensions

        def embed(self, texts: list[str]):
            return cache.get_or_compute(embedder, texts)

    return RetrievalRetriever(
        index=VectorIndex(items, vectors),
        embedder=_CachedEmbedder(),
        slicesets=slicesets,
        budget=budget,
    )


def build_corpus_retriever(budget: int = 5):
    """索引**整个语料**（三门课），供校准用——评测集 30 题横跨三门课。

    与 `build_retriever` 的差别只在范围：那个只索引一门课（跑批要隔离课程变量），
    这个走 `retrieval.corpus` 的既有路径，排除规则（15.316 整份、14.129 的 8 页）
    在那一层已经执行。
    """
    import numpy as np
    from retrieval.compose import compose_all
    from retrieval.corpus import build_items, load_slicesets
    from retrieval.embed import EmbeddingCache, OpenAIEmbedder
    from retrieval.index import VectorIndex
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL, SELECTED_VARIANT

    slicesets = load_slicesets()
    items = build_items(slicesets)
    texts = compose_all(slicesets, SELECTED_VARIANT)
    embedder = OpenAIEmbedder(SELECTED_MODEL, SELECTED_DIMENSIONS)
    cache = EmbeddingCache(REPO / "retrieval" / "cache")
    vectors = np.stack(
        cache.get_or_compute(embedder, [texts[(i.source_file, i.slice_id)] for i in items])
    )

    class _CachedEmbedder:
        name, dimensions = embedder.name, embedder.dimensions

        def embed(self, texts_):
            return cache.get_or_compute(embedder, texts_)

    return RetrievalRetriever(
        index=VectorIndex(items, vectors), embedder=_CachedEmbedder(),
        slicesets=slicesets, budget=budget,
    )


def build_gap_checker(enabled: bool):
    """兜底判断器。`enabled=False` 是 G13 的无判断基线组。

    ⚠️ **Haiku 那份配置已搬到 `explain.gap.build_model_gap_checker`**（内测前-P1，
    口径 F70）：生产 `/explain` 也调同一个函数，**不在这里留第二份**。
    参数逐项未改（模型 id、`effort=None`、关 thinking、`max_tokens=300`、两个缓存关），
    F3b 的数仍然对应现在这份代码。
    """
    from explain.gap import NullGapChecker, build_model_gap_checker

    if not enabled:
        return NullGapChecker()
    return build_model_gap_checker()


def f3b_config(course: str, gap_enabled: bool, points: tuple[str, ...],
               rounds: int | None = None) -> ScanConfig:
    """F3b 的跑批：**端点两点（P0/P3）× 2 组 × 1 轨 × 6 轮**。

    三件事一次回答（F3b 判断四）：
    1. `image_only` 通道在真实数据上通不通（14.129 有 4 页，22.081J 一页没有）
    2. F3a 的结论跨学科复不复现（端点复现，非完整第二条轴）
    3. F3b 的两分类在第二门课上准不准

    22.081J 那一组只跑 P0，用途不同：**对比有/无判断两组的显式标注计数**（G13）。
    """
    qfile = {
        "MIT14_129S25_lec02.pdf": "questions_14129.json",
        COURSE: "questions_scan.json",
    }[course]
    return ScanConfig(
        course_label=COURSE_LABELS[course],
        arms=(
            Arm(
                name="f3b_gap" if gap_enabled else "f3b_nogap",
                point_ids=points,
                questions=_load_questions(qfile, rounds),
                replicates=1,
                note=("含兜底判断" if gap_enabled else "无兜底判断（G13 的基线）")
                + f" · {course}",
            ),
        ),
        gap_checker=build_gap_checker(gap_enabled),
        extra_metadata={
            "stage": "f3b",
            "course": course,
            "gap_enabled": gap_enabled,
            "credentials_note": "凭据由 .env 局部注入，不进日志、不进本文件",
        },
    )


def stage1_config(scan_rounds: int | None = None, cost_rounds: int | None = None) -> ScanConfig:
    """阶段 1：5 点 × 2 组 × **1 轨** × 6 轮 ＋ 成本臂 15 轮 = 90 次计划生成。"""
    return ScanConfig(
        course_label="22.081J · Introduction to Sustainable Energy (lec01b)",
        arms=(
            Arm(
                name="scan",
                point_ids=("P0", "P1", "P2", "P3", "P4"),
                questions=_load_questions("questions_scan.json", scan_rounds),
                replicates=1,
                note="阶段 1：单轨。提问序列经过构造，触发率是压力测试上界（E07a）",
            ),
            Arm(
                name="cost",
                point_ids=("P0",),
                questions=_load_questions("questions_cost.json", cost_rounds),
                replicates=1,
                note="第 0 周原提问序列。单会话成本与真实使用率只从这条臂出（E06/E07a）",
            ),
        ),
        extra_metadata={
            "stage": 1,
            "credentials_note": "ANTHROPIC_API_KEY / OPENAI_API_KEY 由 .env 局部注入，"
            "不进日志、不进本文件",
        },
    )


def stage2_config(scan_rounds: int | None = None, cost_rounds: int | None = None) -> ScanConfig:
    """阶段 2：扫描臂**第 2 轨** + **P3 成本臂**。

    扫描臂用 `replicate_offset=1`，编号从 2 开始——阶段 1 的第 1 轨不重跑，
    两个批次合并时两条轨迹靠编号区分。

    **P3 成本臂的条件已触发**（阶段 1 实测 P0 = 0.000、P3 = 0.500，
    停止规则 3b 写的是「P3 触发率明显高于 P0 则补」，不需再确认）。
    它回答的是「**F1 该不该把 P3 这一档开放给用户**」——
    最严配置下的会话成本。与 P0 成本臂**同提问序列**，否则两条不可比。
    """
    return ScanConfig(
        course_label="22.081J · Introduction to Sustainable Energy (lec01b)",
        arms=(
            Arm(
                name="scan",
                point_ids=("P0", "P1", "P2", "P3", "P4"),
                questions=_load_questions("questions_scan.json", scan_rounds),
                replicates=1,
                replicate_offset=1,
                note="阶段 2：第 2 轨。主判据要求 ≥2 条独立轨迹同向（E05/R17）",
            ),
            Arm(
                name="cost_p3",
                point_ids=("P3",),
                questions=_load_questions("questions_cost.json", cost_rounds),
                replicates=1,
                note="停止规则 3b 已触发（P0=0.000 vs P3=0.500）。"
                "最严配置下的单会话成本，与 P0 成本臂同序列",
            ),
        ),
        extra_metadata={
            "stage": 2,
            "continues_run": "runs/stage1",
            "credentials_note": "ANTHROPIC_API_KEY / OPENAI_API_KEY 由 .env 局部注入，"
            "不进日志、不进本文件",
        },
    )


def run_live(out_dir: Path, scan_rounds=None, cost_rounds=None, budget: int = 5) -> Path:
    retriever = build_retriever(budget=budget)
    model_params = dict(name="claude-sonnet-5", effort="medium",
                        thinking="adaptive", max_tokens=8000)

    def make_model(point_id: str, condition: str, replicate: int) -> Any:
        # 所有格子同参数——两组唯一的差异是审查环节有没有开
        return AnthropicModel(**model_params)

    scan = stage1_config(scan_rounds, cost_rounds)
    scan.pricing_on = date.today()
    scan.extra_metadata["model_params"] = model_params
    scan.extra_metadata["retrieval_budget"] = budget
    return run_scan(scan=scan, retriever=retriever, make_model=make_model, out_dir=out_dir)
