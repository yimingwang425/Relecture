"""假模型全链路。**零 API 成本，且不是模拟真实行为。**

它验证的是链路：审查触发、重生成计数、日志结构、成本核算、三层指纹守卫、
检索同一性、观察项。**曲线形状是写死的，不能当结论看**——
`FakeExplainModel` 的段落长度由参数直接决定，与任何真实模型无关。

检索侧用 `InMemoryRetriever`：它也不假装自己是检索。
retrieval README §三 那条纪律在这里同样适用——
**恒为 0 的链路验证是无效验证**，所以本模块不产出任何召回相关的数字。

假跑用一个**刻意造成 P0→P3 单调上升**的假模型：段落目标长度固定，
配置越紧越容易越界。这样主判据、判读带、E10 三条读法都会被真的执行一遍——
否则它们要到花钱那一轮才第一次运行。

决策日期：2026-08-22
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import _paths  # noqa: F401
from explain.context import ContextSlice, SkippedPageRef
from explain.fake import FakeExplainModel
from explain.retrieval_adapter import InMemoryRetriever

from .runner import Arm, ScanConfig, run_scan

HERE = Path(__file__).resolve().parent.parent
COURSE = "MIT22_081JF10_lec01b.pdf"


def _slices() -> tuple[ContextSlice, ...]:
    """占位切片。文本刻意用英文——真实语料就是英文课件、中文讲解。"""
    return (
        ContextSlice(
            source_file=COURSE, slice_id="s020", page_indices=(20,),
            title="REAL HEAT ENGINES",
            text="A heat engine converts thermal energy into mechanical work. "
                 "The Carnot efficiency is eta = 1 - Te/Ti with absolute temperatures.",
            char_count=145, score=0.51,
        ),
        ContextSlice(
            source_file=COURSE, slice_id="s021", page_indices=(21,),
            title="WASTE HEAT", 
            text="Waste heat is rejected to the environment through the condenser.",
            char_count=64, score=0.44,
        ),
    )


def _load_questions(name: str) -> tuple[dict[str, Any], ...]:
    data = json.loads((HERE / "config" / name).read_text(encoding="utf-8"))
    return tuple(data["questions"])


def make_smoke_retriever() -> InMemoryRetriever:
    return InMemoryRetriever(
        default_slices=_slices(),
        skipped_pages=(
            SkippedPageRef(source_file=COURSE, index=33, kind="image_only",
                           reason="不着墨文本占比低于阈值"),
        ),
    )


def run_smoke(out_dir: Path) -> Path:
    retriever = make_smoke_retriever()

    scan = ScanConfig(
        course_label="22.081J（假跑）",
        arms=(
            Arm(
                name="scan",
                point_ids=("P0", "P1", "P2", "P3", "P4"),
                questions=_load_questions("questions_scan.json"),
                replicates=2,
                note="假模型，仅验证链路",
            ),
            Arm(
                name="cost",
                point_ids=("P0",),
                questions=_load_questions("questions_cost.json")[:4],
                replicates=1,
                note="假跑只取前 4 轮，验证两条臂能共存于同一份 JSONL",
            ),
        ),
        extra_metadata={
            "smoke": True,
            "smoke_warning": "假模型批次。段落长度由参数直接决定，"
                             "任何触发率/成本数字都不得当作结论",
        },
    )

    def make_model(point_id: str, condition: str, replicate: int) -> Any:
        # 固定输出形态，让「配置越紧越容易越界」成立——
        # 衰减/违规挂在配置上，不挂调用次数（第 0 周踩过后者的坑）
        return FakeExplainModel(
            seed=0, replicate=replicate, target_chars=70, corrected_chars=45
        )

    return run_scan(scan=scan, retriever=retriever, make_model=make_model, out_dir=out_dir)
