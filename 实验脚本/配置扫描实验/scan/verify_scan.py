"""跑完之后的核验。**三层指纹守卫 + 缓存生效检查。**

## 为什么不能直接用 `drift/verify.py`

`drift.verify_run` 查的是「本批次指纹 == 当前配置指纹」。
**这条检查在本实验里失效**——配置扫描的指纹**按设计逐点不同**，
5 个合法指纹会把它撑破。

不改 `drift/verify.py`（改了会让历史批次变红，它们的元数据里没有新字段），
而是在这里包一层：先跑它能跑的部分，再加本实验特有的检查。

## 三层守卫各挡什么

======  ==========================  ==================================
点内    同一配置点指纹唯一          这条轨迹中途没被换过配置
点间    全部点 metric_set_id 一致   5 个点画在同一张图上是合法的
批次间  metric_set_id ≠ 历史批次    四维口径的 D02/D44 数据不会混进来
======  ==========================  ==================================

## 缓存：区分「未生效」与「未命中」

接检索后 system 只剩风格约束与课件清单，可能低于最小可缓存长度——
此时断点静默无效。把它报成「未命中」会让人去查断点位置，
而真正该做的是承认 **PRD §7.3 的论断前提在新架构下不成立**。

决策日期：2026-08-22
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import _paths  # noqa: F401
from drift.verify import verify_run  # 原样复用，不改它

__all__ = ["verify_scan_run", "HISTORICAL_METRIC_SETS"]

#: 已知的历史指标集。四维口径（CONFIG_VERSION 1.0/1.1）的批次一律不得合并。
#: 记的是 CONFIG_VERSION 而不是具体的 metric_set_id：历史批次的元数据里
#: 根本没有 metric_set_id 这个字段——**它是这次才引入的**，
#: 所以「历史批次」的判据只能是「版本号不是 2.x」。
HISTORICAL_CONFIG_VERSIONS = ("1.0", "1.1")
HISTORICAL_METRIC_SETS: tuple[str, ...] = ()


def _load(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    rounds = [
        json.loads(line)
        for line in (run_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return meta, rounds


def verify_scan_run(run_dir: Path) -> tuple[bool, list[str]]:
    """返回 (是否全部通过, 逐行报告)。"""
    lines: list[str] = []
    ok = True

    for name in ("metadata.json", "rounds.jsonl"):
        if not (run_dir / name).is_file():
            return False, [f"✗ 缺少 {name}"]

    meta, rounds = _load(run_dir)
    if not rounds:
        return False, ["✗ rounds.jsonl 为空"]

    # ---- 层一 · 点内指纹唯一 ----
    by_point: dict[str, set[str]] = defaultdict(set)
    for r in rounds:
        by_point[r["config_point_id"]].add(r["config_fingerprint"])
    bad = {p: fps for p, fps in by_point.items() if len(fps) != 1}
    if bad:
        ok = False
        lines.append(f"✗ 点内指纹不唯一：{ {p: sorted(f) for p, f in bad.items()} }")
        lines.append("    说明某条轨迹中途换过配置，这批数据不能按点聚合。")
    else:
        shown = "、".join(f"{p}={next(iter(f))}" for p, f in sorted(by_point.items()))
        lines.append(f"✓ 点内指纹唯一（{len(by_point)} 个点）：{shown}")

    # ---- 层一附加 · 各点指纹必须互不相同 ----
    #  两个点用了同一个指纹 = 配置写重了，扫描退化成重复跑同一个点。
    #  它不会报错，只会让曲线上多出一个看起来是独立观测的点。
    flat = [next(iter(f)) for f in by_point.values() if len(f) == 1]
    if len(flat) != len(set(flat)):
        ok = False
        lines.append("✗ 有两个配置点的指纹相同——配置写重了，扫描退化为重复跑同一点")

    # ---- 层二 · 点间 metric_set_id 一致 ----
    msids = {r.get("metric_set_id") for r in rounds}
    if len(msids) != 1:
        ok = False
        lines.append(f"✗ 点间指标集不一致：{sorted(m or 'null' for m in msids)}")
        lines.append("    不同指标集的点**不得画进同一张图**。")
    else:
        lines.append(f"✓ 点间指标集一致 {next(iter(msids))}")

    # ---- 层三 · 与历史批次隔离 ----
    versions = {r.get("config_version") for r in rounds}
    stale = {v for v in versions if v in HISTORICAL_CONFIG_VERSIONS}
    if stale:
        ok = False
        lines.append(f"✗ 混入了历史口径的数据：CONFIG_VERSION {sorted(stale)}")
        lines.append("    那是四维指标集（含 term_annotation），与 v1 三维不可比。")
    else:
        lines.append(f"✓ 无历史口径混入（CONFIG_VERSION {sorted(v or '?' for v in versions)}）")

    # ---- 计分维度必须恰好是 v1 三维 ----
    expected = ["chinese_ratio", "paragraph_length", "quote_length"]
    counted = {tuple(r["final_evaluation"]["counted_dimensions"]) for r in rounds}
    if counted != {tuple(expected)}:
        ok = False
        lines.append(f"✗ 计分维度不是 v1 三维：{sorted(counted)}")
    else:
        lines.append("✓ 计分维度 = 详细程度 / 引用长度 / 讲解语言一致性")

    # ---- 检索同一性：同一 (臂, 点, 轮) 下两组共用一份 ContextPack ----
    #
    # **键必须含臂**：两条臂用的是不同的提问序列，但都跑 P0，
    # 只按 (点, 轮) 分组会把「扫描臂第 1 题」与「成本臂第 1 题」当成同一格，
    # 于是报出一个并不存在的分歧。假跑第一次运行就撞上了这个，
    # 说明这条守卫本身也需要被验证（通则七）。
    ctx: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    questions: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for r in rounds:
        key = (r.get("arm", "?"), r["config_point_id"], r["round"])
        ctx[key].add(r["retrieval"]["signature"])
        questions[key].add(r["question"])
    diverged = {k: v for k, v in ctx.items() if len(v) > 1}
    if diverged:
        ok = False
        lines.append(f"✗ {len(diverged)} 个 (臂, 点, 轮) 上两组的检索结果不同")
        lines.append("    两组输入不一样，对照就废了（第 0 周为此干脆不含检索）。")
    else:
        lines.append(f"✓ 检索同一性：{len(ctx)} 个 (臂, 点, 轮) 上各组共用同一份上下文")

    # ---- 提问同一性：同一格里的提问必须逐字相同 ----
    #  检索签名相同但提问不同是可能的（比如两个提问检索到同一批片），
    #  那时对照依然是坏的——差异跑到了提问上。两条都要查。
    mixed = {k for k, v in questions.items() if len(v) > 1}
    if mixed:
        ok = False
        lines.append(f"✗ {len(mixed)} 个 (臂, 点, 轮) 上的提问不一致——配置与提问混淆了")
    else:
        lines.append("✓ 提问同一性：每格内提问逐字相同")

    # ---- 缓存：未生效 vs 未命中 ----
    if meta.get("fake_model"):
        lines.append("· 假模型批次，缓存检查跳过")
    else:
        expected_effective = meta.get("system_cache", {}).get("system_cache_expected_effective")
        if expected_effective is False:
            lines.append(
                "· system 段低于最小可缓存长度，其缓存断点**未生效**（不是未命中）。"
                "这是接检索后的预期形态——报告不得写成「缓存已开启」，"
                "并须回答 PRD §7.3 的论断在新架构下还成不成立"
            )
        later = [r for r in rounds if r["round"] >= 2]
        if later:
            misses = [
                r for r in later
                if (r["attempts"][0]["completion"]["usage"].get("cache_read_input_tokens") or 0) == 0
            ]
            if misses:
                ok = False
                lines.append(f"✗ {len(misses)}/{len(later)} 轮对话历史缓存未命中")
                lines.append("    历史是接检索后唯一还值得缓存的东西，未命中成本会明显上升。")
            else:
                lines.append(f"✓ 第 2 轮起 {len(later)}/{len(later)} 轮命中历史缓存")

    # ---- 复用 drift 的批次级检查（缓存命中、成本汇总）----
    #
    # **drift 的「配置指纹一致」那条不适用于本实验**：它假定整批只有一个指纹，
    # 而配置扫描的指纹按点不同。传 None 会让它打印一个恒为 ✓ 的行——
    # 那是一条空转的检查，看起来像通过了什么（通则七 #5）。
    # 故显式滤掉该行，并说明它已被层一/层二替代。
    drift_ok, drift_lines = verify_run(run_dir, expected_fingerprint=None)
    lines.append("--- drift.verify（复用，未修改）---")
    lines.append("· 「配置指纹一致」不适用：本实验指纹按点不同，已由层一/层二替代")
    lines.extend(l for l in drift_lines if "配置指纹" not in l)
    return (ok and drift_ok), lines
