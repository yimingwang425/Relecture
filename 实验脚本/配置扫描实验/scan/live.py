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
COURSE = "MIT22_081JF10_lec01b.pdf"


def _load_questions(name: str, limit: int | None = None) -> tuple[dict[str, Any], ...]:
    data = json.loads((HERE / "config" / name).read_text(encoding="utf-8"))
    qs = data["questions"]
    return tuple(qs[:limit] if limit else qs)


def build_retriever(budget: int = 5) -> RetrievalRetriever:
    """只索引本实验用的那一份课件。

    不索引整个测试集：跨文档检索会让「片段来自哪门课」成为一个额外变量，
    而本实验的被测属性与它无关。语料排除规则（15.316 整份、14.129 的 8 页
    受限内容）在这里自然不适用——那份课件根本没进来。
    """
    import numpy as np
    from parse_service.pdf import parse_pdf
    from retrieval.compose import compose
    from retrieval.embed import EmbeddingCache, OpenAIEmbedder
    from retrieval.index import IndexedSlice, VectorIndex
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL, SELECTED_VARIANT
    from slicer import slice_document

    path = REPO / "实验脚本" / "测试集" / COURSE
    sliceset = slice_document(parse_pdf(path.read_bytes(), path.name))
    slicesets = {COURSE: sliceset}

    texts = compose(sliceset, SELECTED_VARIANT)
    items, ordered_texts = [], []
    for s in sliceset.slices:
        items.append(
            IndexedSlice(
                source_file=COURSE,
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
