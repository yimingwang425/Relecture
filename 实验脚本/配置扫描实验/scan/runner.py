"""编排：配置点 × 条件 × 轨迹 × 轮次。

## 每轮只检索一次，两组共用（口径 X02）

检索只取决于提问（口径 X01），所以 `ContextPack` 与配置点、条件、历史都无关。
本模块**按 (轮次) 缓存 ContextPack**，两组、五个点全部共用同一份，
签名进日志，`verify_scan` 会核对。这是本轮能把检索接进对照实验的原因。

## 两条臂

- **扫描臂**：5 点 × 2 组 × N 轨 × 6 轮。提问序列**构造过**
  （第 3 轮诱发引用、第 5/6 轮压长度），故它的触发率是**压力测试上界**
- **成本臂**：P0 × 2 组 × 1 轨 × 15 轮，用第 0 周原提问序列。
  **真实使用率与单会话成本只能从这条臂出**（口径 E07 / E07a）

两条臂在**同一次运行、同一份 JSONL** 里——触发率与成本必须同源。

决策日期：2026-08-22
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from . import _paths  # noqa: F401
from explain.context import ContextPack, Retriever
from explain.gap import NullGapChecker
from explain.llm import Model
from explain.log import pricing_record, round_record
from explain.pipeline import Trajectory, run_round
from explain.prompt import build_system, system_cache_status
from explain.review import RuleReviewer
from explain.style_prompt import render_style_rules

from explain.gap import GapChecker

from .points import POINTS, ConfigPoint, point_by_id

__all__ = ["Arm", "ScanConfig", "run_scan"]

CONDITIONS = ("with_review", "without_review")


@dataclass(frozen=True, slots=True)
class Arm:
    """一条实验臂。"""

    name: str
    point_ids: tuple[str, ...]
    questions: tuple[dict[str, Any], ...]
    replicates: int
    note: str
    #: 轨迹编号的起点偏移。阶段 2 只跑第 2 轨时设为 1，编号从 2 开始——
    #: **不能让两个批次都出现 replicate=1**，否则合并时两条独立轨迹会被
    #: 当成同一条，而「重复是整条轨迹的重复」这条纪律正是靠编号区分的。
    replicate_offset: int = 0

    @property
    def planned_generations(self) -> int:
        return len(self.point_ids) * len(CONDITIONS) * self.replicates * len(self.questions)


@dataclass
class ScanConfig:
    course_label: str
    arms: tuple[Arm, ...]
    max_regenerations: int = 2
    #: 兜底判断器。F3a 用 NullGapChecker；F3b 起可注入 ModelGapChecker。
    gap_checker: Any = None
    glossary: Any = None
    pricing_on: date | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)


def _context_cache(retriever: Retriever) -> Callable[[str], ContextPack]:
    """同一提问只检索一次，全实验共用。

    共用而不是「各算各的但应该一样」——后者靠约定，前者靠结构。
    """
    cache: dict[str, ContextPack] = {}

    def fetch(question: str) -> ContextPack:
        if question not in cache:
            cache[question] = retriever.fetch(question)
        return cache[question]

    return fetch


def completed_trajectories(out_dir: Path, arms: tuple[Arm, ...]) -> set[tuple[str, str, str, int]]:
    """已经**跑完整条**的轨迹。用于崩溃后续跑（`resume`）。

    **只认整条轨迹，不认单轮。** 轨迹是有状态的：第 k 轮的输入含前 k−1 轮的
    历史。从中途接续要把历史精确重建出来，一处细微不符就会让两组的输入
    不再逐字节相同——而那正是本实验唯一要保住的东西。
    半条轨迹重跑的代价是几分钱，接错的代价是整批数据不可用。
    """
    path = out_dir / "rounds.jsonl"
    if not path.is_file():
        return set()
    counts: dict[tuple[str, str, str, int], int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r.get("arm", "?"), r["config_point_id"], r["condition"], r["replicate"])
        counts[key] = counts.get(key, 0) + 1
    wanted = {a.name: len(a.questions) for a in arms}
    return {k for k, n in counts.items() if n >= wanted.get(k[0], 10**9)}


def _prune_incomplete(rounds_path: Path, done: set[tuple[str, str, str, int]]) -> int:
    """只保留已完整轨迹的记录，返回丢弃的轮次数。"""
    if not rounds_path.is_file():
        return 0
    kept, dropped = [], 0
    for line in rounds_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r.get("arm", "?"), r["config_point_id"], r["condition"], r["replicate"])
        if key in done:
            kept.append(line)
        else:
            dropped += 1
    rounds_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return dropped


def run_scan(
    *,
    scan: ScanConfig,
    retriever: Retriever,
    make_model: Callable[[str, str, int], Model],
    out_dir: Path,
    on_round: Callable[[dict[str, Any]], None] | None = None,
    resume: bool = False,
) -> Path:
    """跑完全部臂，写 `metadata.json` + `rounds.jsonl` + `transcripts/`。

    `make_model(point_id, condition, replicate)` 由调用方提供——真实实验里
    它对所有格子返回同参数的模型，假跑时可以返回可控的假模型。

    **`metadata.json` 先写后补**（2026-08-23）：原实现只在跑完时写，
    于是一次中途失败（实测：API 余额耗尽）会留下**已经付过钱、却无法核验**的
    `rounds.jsonl`——`verify` 第一句就是「缺少 metadata.json」。
    现在开跑前先落一份 `status="running"`，结束时补成 `complete`，
    异常时补成 `aborted` 再抛出。付过的钱不会因为一次崩溃变成不可用的数据。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "transcripts").mkdir(exist_ok=True)
    rounds_path = out_dir / "rounds.jsonl"

    done = completed_trajectories(out_dir, scan.arms) if resume else set()
    if resume:
        # **不完整轨迹的残留轮次必须先删掉。**
        # 以 "a" 模式续写时，中断那条轨迹已落盘的前几轮仍留在文件里；
        # 而它会被整条重跑（见 completed_trajectories 的说明）。
        # 不清理的话，同一条 (臂,点,条件,轨) 下会出现两套轮次——
        # 报告按轮次求均值，多出来的几轮**不会报错，只会让数字偏移**。
        _prune_incomplete(rounds_path, done)
    records: list[dict[str, Any]] = []
    system_probe: dict[str, Any] | None = None
    model_name = ""

    fetch = _context_cache(retriever)

    def write_metadata(status: str) -> None:
        (out_dir / "metadata.json").write_text(
            json.dumps(
                _metadata(scan, model_name, system_probe, status, sorted(map(list, done))),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    write_metadata("running")

    try:
      with rounds_path.open("a" if resume else "w", encoding="utf-8") as fh:
        for arm in scan.arms:
            for point_id in arm.point_ids:
                point: ConfigPoint = point_by_id(point_id)
                config = point.config()
                style_rules = render_style_rules(config)
                reviewer = RuleReviewer(config, scan.glossary)

                for condition in CONDITIONS:
                    for replicate in range(
                        arm.replicate_offset + 1,
                        arm.replicate_offset + arm.replicates + 1,
                    ):
                        if (arm.name, point_id, condition, replicate) in done:
                            continue
                        trajectory = Trajectory()
                        for index, q in enumerate(arm.questions, 1):
                            model = make_model(point_id, condition, replicate)
                            model_name = model.name
                            result = run_round(
                                round_index=index,
                                question=q["question"],
                                trajectory=trajectory,
                                retriever=retriever,
                                model=model,
                                reviewer=reviewer,
                                style_rules=style_rules,
                                course_label=scan.course_label,
                                review_enabled=(condition == "with_review"),
                                max_regenerations=scan.max_regenerations,
                                gap_checker=scan.gap_checker or NullGapChecker(),
                                context=fetch(q["question"]),
                            )
                            if system_probe is None:
                                system_probe = system_cache_status(result.system_text)

                            record = round_record(
                                result,
                                config=config,
                                config_point_id=point_id,
                                condition=condition,
                                replicate=replicate,
                                trace_id=f"{arm.name}:{point_id}:{condition}:r{replicate}",
                                question_tags=q.get("tags", []),
                                gold_pages=q.get("gold_pages"),
                                extra={"arm": arm.name, "arm_note": arm.note},
                            )
                            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                            fh.flush()
                            records.append(record)
                            if on_round is not None:
                                on_round(record)

                            name = (
                                f"{arm.name}_{point_id}_{condition}_r{replicate}"
                                f"_{index:02d}.md"
                            )
                            (out_dir / "transcripts" / name).write_text(
                                result.final_text, encoding="utf-8"
                            )

    except BaseException:
        # 中途失败也要留下可核验的批次——付过的钱不该因为一次崩溃变成废数据
        write_metadata("aborted")
        raise

    write_metadata("complete")
    return out_dir


def _metadata(
    scan: ScanConfig,
    model_name: str,
    system_probe: dict[str, Any] | None,
    status: str,
    resumed_from: list[Any],
) -> dict[str, Any]:
    metadata = {
        "experiment": "config-scan-v1",
        "status": status,
        "resumed_skipping": resumed_from,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "course_label": scan.course_label,
        "fake_model": model_name.startswith("fake:"),
        "model": model_name,
        "max_regenerations": scan.max_regenerations,
        "points": [p.to_dict() for p in POINTS],
        "arms": [
            {
                "name": a.name,
                "points": list(a.point_ids),
                "rounds": len(a.questions),
                "replicates": a.replicates,
                "planned_generations": a.planned_generations,
                "note": a.note,
            }
            for a in scan.arms
        ],
        "system_cache": system_probe or {},
        **pricing_record(model_name, scan.pricing_on),
        # 边界声明，随数据一起走，好过写在报告里等人记得抄
        "boundaries": [
            "v1 三维指标集：详细程度 / 引用长度 / 讲解语言一致性。"
            "term_annotation 已下线（PRD §8.1），冗余句式未实现",
            "不含 B 类维度（judge 未校准，PRD §8.5）",
            "不含 F3b 的三分类兜底判断，gap_verdict 恒为 null",
            "不流式，故首字延迟无数据（PRD §9.2 的 P95 首字 < 3s 本轮不可测）",
            "当前模型已移除 temperature/top_p/top_k，可固定的是 effort 与 thinking",
            "扫描臂的提问序列经过构造（诱发引用 / 压长度），"
            "其触发率是**压力测试上界**；真实使用率只能从成本臂出（口径 E07a）",
        ],
        **scan.extra_metadata,
    }
    return metadata


def summarise(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """按 (臂, 点, 条件) 汇总触发率、达标率、成本。**不合并两条臂。**"""
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in records:
        key = (r.get("arm", "?"), r["config_point_id"], r["condition"])
        b = buckets.setdefault(
            key,
            {"rounds": 0, "triggered": 0, "accepted": 0, "cost_usd": 0.0,
             "regenerations": 0, "total_chars": 0, "paragraph_count": 0},
        )
        b["rounds"] += 1
        b["triggered"] += int(r["triggered_regeneration"])
        b["accepted"] += int(r["accepted"])
        b["regenerations"] += r["regenerations"]
        b["cost_usd"] += r["round_cost_usd"]
        b["total_chars"] += r["observations"]["total_chars"]
        b["paragraph_count"] += r["observations"]["paragraph_count"]

    out: dict[str, Any] = {}
    for (arm, point, condition), b in sorted(buckets.items()):
        n = b["rounds"]
        out[f"{arm}/{point}/{condition}"] = {
            **b,
            "trigger_rate": b["triggered"] / n if n else None,
            "pass_rate": b["accepted"] / n if n else None,
            "cost_per_round": b["cost_usd"] / n if n else None,
            "mean_total_chars": b["total_chars"] / n if n else None,
            "mean_paragraph_count": b["paragraph_count"] / n if n else None,
        }
    return out
