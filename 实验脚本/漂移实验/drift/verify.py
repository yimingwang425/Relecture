"""跑完一轮之后的两项核验。

刻意只查两件容易出错、且出错代价高的事：

1. **配置指纹**与当前配置一致——指纹对不上说明这批数据不是用你以为的配置
   跑出来的，后续所有解读都站不住。
2. **第 2 轮起缓存是否命中**——没命中成本翻倍，且往往到账单出来才发现。

不做更细的校验：核验脚本本身出错或过于繁琐，比它能挡住的问题更麻烦。

决策日期：2026-08-20
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["verify_run"]


def verify_run(run_dir: Path, expected_fingerprint: str | None = None) -> tuple[bool, list[str]]:
    """返回 (是否全部通过, 逐行报告)。"""
    lines: list[str] = []
    ok = True

    meta_path, rounds_path = run_dir / "metadata.json", run_dir / "rounds.jsonl"
    for p in (meta_path, rounds_path):
        if not p.is_file():
            return False, [f"✗ 缺少 {p.name}"]

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    records = [json.loads(l) for l in rounds_path.read_text(encoding="utf-8").splitlines() if l]

    # 1 · 配置指纹
    actual = meta.get("config_fingerprint")
    if expected_fingerprint and actual != expected_fingerprint:
        ok = False
        lines.append(f"✗ 配置指纹不符：数据是 {actual}，当前配置是 {expected_fingerprint}")
        lines.append("    这批数据不是用当前配置跑的，不要拿它和别的批次比。")
    else:
        lines.append(f"✓ 配置指纹 {actual}")

    if meta.get("fake_model"):
        lines.append("· 这是假模型跑的数据，缓存检查跳过")
        return ok, lines

    # 2 · 第 2 轮起的缓存命中
    later = [r for r in records if r["round"] >= 2]
    if not later:
        lines.append("· 只有第 1 轮，无从检查缓存")
        return ok, lines

    misses = [
        r for r in later
        if (r["attempts"][0]["completion"]["usage"].get("cache_read_input_tokens") or 0) == 0
    ]
    if misses:
        ok = False
        where = "、".join(f"{m['condition']} r{m['replicate']} 第{m['round']}轮" for m in misses[:5])
        lines.append(f"✗ {len(misses)}/{len(later)} 轮缓存未命中（如 {where}）")
        lines.append("    成本会接近翻倍。检查 cache_conversation 的断点位置。")
    else:
        reads = [r["attempts"][0]["completion"]["usage"]["cache_read_input_tokens"] for r in later]
        lines.append(
            f"✓ 第 2 轮起 {len(later)}/{len(later)} 轮命中缓存"
            f"（读取 {min(reads)}–{max(reads)} token）"
        )

    total = sum(r["round_cost_usd"] for r in records)
    calls = sum(len(r["attempts"]) for r in records)
    lines.append(f"· {len(records)} 轮 / {calls} 次调用 / 合计 ${total:.4f}")
    return ok, lines
