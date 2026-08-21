"""提取主入口：合并两条通道的命中，过 G 判据，分档置信度。

**误差方向**（PRD §8.1，照做）：保守，宁漏勿错。
遗漏项不进分母，只降低指标灵敏度；误收普通词会导致审查误判。两者不对称。

决策日期：2026-08-21
"""

from __future__ import annotations

from collections import defaultdict

from slicer.schema import SliceSet

from .channels import Hit, body_hits, figure_hits
from .criteria import check
from .schema import Evidence, Glossary, Term
from .text import normalize_phrase

__all__ = ["extract", "HIGH_MIN_HEURISTICS", "HIGH_MIN_PAGES"]

#: `high` 档要求：命中 ≥2 条独立启发式 **且** 跨 ≥2 页。
#: 来源：**占位待定**。确定路径：新发现条目的人工判定结果——
#: 若 high 档里出现误收，说明门槛不够。
HIGH_MIN_HEURISTICS = 2
HIGH_MIN_PAGES = 2


def _confidence(heuristics: set[str], pages: set[int], joined: bool) -> tuple[str, str]:
    """分档是**规则**不是模型打分。

    上限为 `high`，但 `high` 不宣称满足 G2/G3——那两条需要语义判断，
    自动提取做不到（见 criteria.py）。故 reason 里始终写明这一点。
    """
    if joined:
        return "low", "由碎片拼接得到；G2/G3 未经语义验证"
    if len(heuristics) >= HIGH_MIN_HEURISTICS and len(pages) >= HIGH_MIN_PAGES:
        return "high", f"命中 {len(heuristics)} 条启发式且跨 {len(pages)} 页；G2/G3 未经语义验证"
    if heuristics == {"B3"}:
        return "low", "仅靠跨页频次命中；G2/G3 未经语义验证"
    return "medium", f"命中 {sorted(heuristics)}；G2/G3 未经语义验证"


def extract(sliceset: SliceSet, course: str, source_file: str) -> Glossary:
    hits: list[Hit] = [*body_hits(sliceset), *figure_hits(sliceset)]

    grouped: dict[str, list[Hit]] = defaultdict(list)
    for h in hits:
        if not h.key:
            continue
        if not check(h.phrase).passed:
            continue
        grouped[h.key].append(h)

    # **子短语抑制**：若存在更长的术语，且短的那条全部证据都来自同一批块，
    # 则短的是碎片而非独立术语（`well known` vs `well known issue`）。
    blocks_of = {k: {h.block_id for h in v} for k, v in grouped.items()}
    dropped = {
        short
        for short in grouped
        for long in grouped
        if short != long
        and f" {short} " in f" {long} "
        and blocks_of[short] <= blocks_of[long]
    }
    grouped = {k: v for k, v in grouped.items() if k not in dropped}

    terms: list[Term] = []
    for key, group in sorted(grouped.items()):
        heuristics = {h.heuristic for h in group}
        pages = {h.page for h in group}
        joined = heuristics <= {"F-JOIN"}
        # 通道归属：只要有图注通道命中就算图注来源——
        # 图注通道是新增的，混进正文会看不出它有没有用（PRD §8.1 要求分开报）
        source = "figure_caption" if any(h.startswith("F-") for h in heuristics) else "body_text"
        conf, reason = _confidence(heuristics, pages, joined)

        seen_blocks: set[str] = set()
        evidence: list[Evidence] = []
        for h in sorted(group, key=lambda x: (x.page, x.block_id)):
            if h.block_id in seen_blocks:
                continue
            seen_blocks.add(h.block_id)
            evidence.append(
                Evidence(
                    page=h.page,
                    block_id=h.block_id,
                    bbox_norm=[round(v, 4) for v in h.bbox_norm],
                    text=h.block_text[:120],
                )
            )

        flags: list[str] = []
        if len(group) == 1:
            flags.append("single_occurrence")
        if "F-JOIN" in heuristics:
            flags.append("joined_from_fragments")

        terms.append(
            Term(
                en=key,
                surface_forms=sorted({h.phrase for h in group}),
                source=source,  # type: ignore[arg-type]
                heuristics=sorted(heuristics),
                confidence=conf,  # type: ignore[arg-type]
                confidence_reason=reason,
                pages=sorted(pages),
                occurrences=len(group),
                evidence=evidence[:5],
                flags=flags,
            )
        )

    return Glossary(course=course, source_file=source_file, terms=terms)
