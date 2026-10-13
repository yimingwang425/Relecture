"""风格审查员（j2）的校准评测（2026-10-13）。判据见 `explain/runs/style_judge_eval_prereg.md`（A 部分）。

    api/.venv/bin/python explain/scripts/style_judge_eval.py run       # 168 次审查（Sonnet，要钱）
    api/.venv/bin/python explain/scripts/style_judge_eval.py report    # 汇总（不花钱）

复用 `style_review_eval_base.json` 的 84 段讲解与 `style_review_eval.build_items()` 的 168 条，不重新生成。
要钱的子命令要 `ANTHROPIC_API_KEY`（单条命令里映射，不 export）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "explain"))
sys.path.insert(0, str(REPO / "explain" / "scripts"))

import explain  # noqa: E402,F401
from explain.style_axes import style_config_for  # noqa: E402
from explain.style_review import STYLE_JUDGE_PROMPT_VERSION, build_style_judge, render_preferences  # noqa: E402
from style_review_eval import BASE, OPPOSITE, build_items  # noqa: E402

RUNS = REPO / "explain" / "runs"
OUT = RUNS / f"style_judge_eval_results_{STYLE_JUDGE_PROMPT_VERSION}.json"


def cmd_run(args: Any) -> int:
    texts = {r["id"]: r for r in json.loads(BASE.read_text(encoding="utf-8"))["texts"]}
    items = build_items()
    if args.fit_sample:
        # 只测误报：从 64 条「符合」里等距取 N 条（覆盖各个问题与四条轴），同一批条目可与上一版逐条对照。
        fit = [it for it in items if it["set"] == "fit"]
        step = len(fit) // args.fit_sample
        items = fit[::step][: args.fit_sample]
    done = {r["id"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))["results"]} if OUT.exists() else {}
    todo = [it for it in items if it["id"] not in done]
    print(f"已有 {len(done)} 条，要跑 {len(todo)} 条")

    def one(it: dict[str, Any]) -> dict[str, Any]:
        p = it["pref"]
        prefs = render_preferences(style_config_for(p["detail_level"]), p["detail_level"],
                                   {k: p[k] for k in ("use_examples", "structure", "depth")})
        j = build_style_judge().judge(preferences=prefs, question=it["question"], text=texts[it["text_id"]]["text"])
        return {**it, "verdict": j.verdict, "run": j.to_dict(),
                "usage": j.usage.to_dict() if j.usage else None}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for k, res in enumerate(ex.map(one, todo), 1):
            done[res["id"]] = res
            passed = res["verdict"] != "rewrite"
            mark = "✓" if passed == (res["expect"] == "ok") else "✗"
            print(f"  {mark} {res['id']:<40} 期望 {res['expect']:<7} 得到 {res['verdict']}")
    order = {it["id"]: i for i, it in enumerate(items)}
    rows = sorted(done.values(), key=lambda r: order.get(r["id"], 1e9))
    OUT.write_text(json.dumps({"prompt_version": STYLE_JUDGE_PROMPT_VERSION, "results": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"共 {len(rows)} 条，花费 ${sum(r['run']['cost_usd'] or 0 for r in rows):.4f} → {OUT}")
    return 0


def rate(rows: list[dict[str, Any]], want: str) -> tuple[int, int]:
    if want == "rewrite":
        return sum(1 for r in rows if r["verdict"] == "rewrite"), len(rows)
    return sum(1 for r in rows if r["verdict"] != "rewrite"), len(rows)


def pct(a: int, n: int) -> str:
    return f"{a}/{n}（{100 * a / n:.1f}%）" if n else "—"


def cmd_report(_args: Any) -> int:
    res = json.loads(OUT.read_text(encoding="utf-8"))["results"]
    by = lambda s, axis=None: [r for r in res if r["set"] == s and (axis is None or r["axis"] == axis)]  # noqa: E731
    lines = [f"# 风格审查员校准 · 原始汇总（{STYLE_JUDGE_PROMPT_VERSION}）", "",
             "| 指标 | 结果 | 达标线 |", "| --- | --- | --- |",
             f"| **检出率**（偏离 → 打回） | {pct(*rate(by('deviate'), 'rewrite'))} | ≥ 70% |",
             f"| **误报率**（符合 → 打回） | {pct(*rate(by('fit'), 'rewrite'))} | ≤ 10% |",
             f"| 冲突 → 放行 | {pct(*rate(by('conflict'), 'ok'))} | — |",
             f"| 冲突对照 → 打回 | {pct(*rate(by('control'), 'rewrite'))} | — |",
             "", "| 轴 | 检出率 | 误报率 | 冲突放行 | 对照打回 |", "| --- | --- | --- | --- | --- |"]
    for axis in OPPOSITE:
        lines.append(f"| {axis} | {pct(*rate(by('deviate', axis), 'rewrite'))} | {pct(*rate(by('fit', axis), 'rewrite'))} "
                     f"| {pct(*rate(by('conflict', axis), 'ok'))} | {pct(*rate(by('control', axis), 'rewrite'))} |")
    pair = {(r["text_id"], r["set"]): r["verdict"] for r in res}
    c: Counter = Counter()
    for (tid, s), v in pair.items():
        if s != "fit" or (tid, "deviate") not in pair:
            continue
        a, b = v == "rewrite", pair[(tid, "deviate")] == "rewrite"
        c["方向对" if (not a and b) else "方向反" if (a and not b) else "两次相同"] += 1
    failed = [r for r in res if r["verdict"] is None]
    costs = [r["run"]["cost_usd"] for r in res if r["run"]["cost_usd"] is not None]
    lat = sorted(r["run"]["latency_ms"] for r in res)
    lines += ["", f"- 不依赖标签的分辨力（64 对）：{dict(c)}",
              f"- 失败（按放行计）：{len(failed)} 条" + (f"：{failed[0]['run']['error'][:80]}" if failed else ""),
              f"- 单次成本：均值 ${statistics.mean(costs):.4f}，中位 ${statistics.median(costs):.4f}",
              f"- 延迟：中位 {statistics.median(lat) / 1000:.1f} s，P90 {lat[int(0.9 * len(lat)) - 1] / 1000:.1f} s",
              "", "## 判错的条目", ""]
    for r in res:
        if (r["verdict"] != "rewrite") == (r["expect"] == "ok"):
            continue
        lines.append(f"- `{r['id']}` 期望 {r['expect']}、得到 {r['verdict']}：{r['run']['reason'][:60]}"
                     f"{' ｜ ' + r['run']['feedback'][:60] if r['run']['feedback'] else ''}")
    out = RUNS / f"style_judge_eval_report_raw_{STYLE_JUDGE_PROMPT_VERSION}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))
    return 0


def cmd_thresholds(_args: Any) -> int:
    """j5：按预登记（`style_judge_j5_prereg.md`）在开发集上选阈值，只在留出集上报成绩。不花钱。"""
    res = [r for r in json.loads(OUT.read_text(encoding="utf-8"))["results"]]
    dev_q = {"m01", "m02", "m03", "m04"}

    def verdict(r: dict[str, Any], th: int) -> str | None:
        sc = r["run"].get("scores")
        return None if not sc else ("rewrite" if max(sc.values()) >= th else "ok")

    def split(rows, s):
        return [r for r in rows if r["set"] == s]

    def stats(rows, th):
        rw = lambda s: sum(1 for r in split(rows, s) if verdict(r, th) == "rewrite")  # noqa: E731
        ok = lambda s: sum(1 for r in split(rows, s) if verdict(r, th) != "rewrite")  # noqa: E731
        return {"检出": (rw("deviate"), len(split(rows, "deviate"))), "误报": (rw("fit"), len(split(rows, "fit"))),
                "冲突放行": (ok("conflict"), len(split(rows, "conflict"))),
                "对照打回": (rw("control"), len(split(rows, "control")))}

    dev = [r for r in res if r["text_id"][:3] in dev_q]
    hold = [r for r in res if r["text_id"][:3] not in dev_q]
    lines = [f"# 风格审查员 j5 · 阈值（开发集选、留出集报）", "", "| 阈值 | 开发集 检出 | 开发集 误报 | 留出集 检出 | 留出集 误报 | 留出集 冲突放行 | 留出集 对照打回 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    pick = None
    cands = []
    for th in (2, 3):
        d, h = stats(dev, th), stats(hold, th)
        f = lambda x: f"{x[0]}/{x[1]}（{100 * x[0] / x[1]:.0f}%）" if x[1] else "—"  # noqa: E731
        lines.append(f"| ≥ {th} | {f(d['检出'])} | {f(d['误报'])} | {f(h['检出'])} | {f(h['误报'])} | "
                     f"{f(h['冲突放行'])} | {f(h['对照打回'])} |")
        det, fp = d["检出"][0] / d["检出"][1], d["误报"][0] / d["误报"][1]
        cands.append((th, det, fp))
    ok70 = [c for c in cands if c[1] >= 0.7]
    pick = min(ok70, key=lambda c: c[2])[0] if ok70 else max(cands, key=lambda c: c[1] - c[2])[0]
    failed = sum(1 for r in res if not r["run"].get("scores"))
    lines += ["", f"- 按预登记的规则选出的阈值：**≥ {pick}**", f"- 没给出合格分数（按放行计）：{failed} 条"]
    out = RUNS / f"style_judge_eval_thresholds_{STYLE_JUDGE_PROMPT_VERSION}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--fit-sample", type=int, default=0, help="只测误报：等距抽 N 条「符合」条目")
    sub.add_parser("report")
    sub.add_parser("thresholds")
    args = ap.parse_args()
    return {"run": cmd_run, "report": cmd_report, "thresholds": cmd_thresholds}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
