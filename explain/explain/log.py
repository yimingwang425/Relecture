"""每轮一条 JSONL 记录。

PRD §9.1：「**从第一天起**将每次调用的 token 数与成本写入日志。
面试必问单位经济，能报出具体数字与结构的候选人极少。」

## 三条不肯妥协的字段设计

1. **token 分四类记**（未命中输入 / 缓存读 / 缓存写 / 输出），不合并。
   三类输入互不重叠，合并之后就再也算不回成本结构——第 0 周正是靠这个分解
   才发现「输出占 68%，检索只作用于输入侧」。
2. **成本带定价与到期日**。`claude-sonnet-5` 的 introductory 价 8/31 到期。
   日志里只写一个数字，事后无法判断它按哪套价算的。
3. **配置标识两个都记**：`config_fingerprint`（阈值变没变）与
   `metric_set_id`（指标集是不是同一个）。配置扫描里前者按设计逐点不同，
   只有后者能挡住跨指标集的误合并。

**首字延迟**：2026-10-07 起流式（口径 F94），记的是 `run_round` 测到的那个数；
没有正文流出来时仍写 `null`，不塞假数。`round_latency_ms` 是**关键路径**
（判断与首版生成并行，口径 F93），真实墙钟另记 `wall_ms`。

决策日期：2026-08-22
"""

from __future__ import annotations

from datetime import date
from typing import Any

from a_rules.config import StyleConfig

from .llm import PRICING, rates_for
from .pipeline import RoundResult

__all__ = ["round_record", "pricing_record"]


def pricing_record(model_name: str, on: date | None = None) -> dict[str, Any]:
    """本批次实际使用的价目。**带到期日**，否则事后说不清按哪套价算的。"""
    entry = PRICING.get(model_name.split(":")[-1], {})
    rates = rates_for(model_name, on)
    return {
        "pricing_model": model_name,
        "pricing_rates_per_mtok": {"input": rates[0], "output": rates[1]} if rates else None,
        "pricing_intro_until": entry.get("intro_until"),
        "pricing_as_of": (on or date.today()).isoformat(),
    }


def round_record(
    result: RoundResult,
    *,
    config: StyleConfig,
    config_point_id: str,
    condition: str,
    replicate: int,
    trace_id: str,
    question_tags: list[str] | None = None,
    gold_pages: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = result.context
    retrieved_pages = [
        {"source_file": s.source_file, "page": p} for s in ctx.slices for p in s.page_indices
    ]
    hit_gold = None
    if gold_pages is not None:
        want = {(g["source_file"], g["page_index"]) for g in gold_pages}
        got = {(d["source_file"], d["page"]) for d in retrieved_pages}
        hit_gold = bool(want & got)

    record: dict[str, Any] = {
        # ---- 标识 ----
        "trace_id": trace_id,
        "config_point_id": config_point_id,
        "condition": condition,
        "replicate": replicate,
        "round": result.round_index,
        "question": result.question,
        "question_tags": question_tags or [],
        # ---- 配置 ----
        "config_fingerprint": config.fingerprint(),
        "metric_set_id": config.metric_set_id(),
        "config_version": result.final_review.evaluation.config_version,
        "dims_activated": config.describe_activation(),
        # ---- 检索 ----
        "retrieval": ctx.to_dict(),
        "request_signature": result.request_signature,
        # ---- F3b 插入点（本轮恒 null）----
        **result.gap.to_dict(),
        # ---- 生成与审查 ----
        "attempts": [a.to_dict() for a in result.attempts],
        "regenerations": result.regenerations,
        "triggered_regeneration": result.triggered_regeneration,
        "accepted": result.accepted,
        "failed_dimensions": list(result.final_review.failed_dimensions),
        "final_evaluation": result.final_review.evaluation.to_dict(),
        # ---- 用量与成本 ----
        "round_usage": result.usage.to_dict(),
        "round_cost_usd": result.cost_usd,
        # 两个延迟都记：总延迟是用户等待的，生成延迟用于分离兜底判断的增量
        "round_latency_ms": round(result.latency_ms, 1),
        "generation_latency_ms": round(result.generation_latency_ms, 1),
        # 流式之后测得到（口径 F94）；没有正文时仍是 null，不塞假数
        "first_token_latency_ms": result.first_token_latency_ms,
        "wall_ms": result.wall_ms,
        "discarded": [d.to_dict() for d in result.discarded],
        # ---- 观察项（不进指标）----
        "observations": {**result.observations.to_dict(), "retrieval_hit_gold": hit_gold},
    }
    if extra:
        record.update(extra)
    return record
