"""风格审查 agent 的评测（2026-10-12）。判据见 `explain/runs/style_review_eval_prereg.md`。

    api/.venv/bin/python explain/scripts/style_review_eval.py generate              # 生成 84 段讲解（Sonnet，要钱）
    api/.venv/bin/python explain/scripts/style_review_eval.py run --model haiku     # 审查 168 条（要钱）
    api/.venv/bin/python explain/scripts/style_review_eval.py report [--model haiku]  # 汇总（不花钱）

凡是要钱的子命令都要 `ANTHROPIC_API_KEY`（单条命令里映射，不 export）。

课件是自写的 `f1_samples/synthetic_deck.md`，不含任何第三方内容（PRD §12.1）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "explain"))
sys.path.insert(0, str(REPO / "explain" / "scripts"))
sys.path.insert(0, str(REPO / "retrieval"))

import explain  # noqa: E402,F401
from explain.style_axes import DEFAULTS, style_config_for  # noqa: E402
from explain.style_review import (  # noqa: E402
    StyleReviewAgent,
    build_style_review_agent,
    render_preferences,
    split_paragraphs,
)

RUNS = REPO / "explain" / "runs"
BASE = RUNS / "style_review_eval_base.json"
LABEL = "线性代数 · 特征值（自写课件）"
BUDGET_USD = 8.0

#: 每条轴两个相反的取值（预登记表）。
OPPOSITE: dict[str, tuple[str, str]] = {
    "detail_level": ("P0", "P3"),
    "use_examples": ("more", "fewer"),
    "structure": ("conclusion_first", "buildup_first"),
    "depth": ("what", "derivation"),
}

#: 主集 8 个问题。
QUESTIONS = [
    "特征值和特征向量是什么？",
    "为什么特征向量在变换之后方向不变？",
    "特征值是负数是什么意思？",
    "怎么求一个矩阵的特征值？",
    "什么是对角化？有什么用？",
    "为什么平面旋转没有实数特征值？",
    "幂法是怎么回事？",
    "PCA 和特征向量有什么关系？",
]

#: 冲突集：每条轴 (存的偏好, 这一句要的取值, 附在提问后面的那句要求)。
CONFLICT_RULE: dict[str, tuple[str, str, str]] = {
    "detail_level": ("P3", "P0", "请讲得详细一些，把每个点都展开讲。"),
    "use_examples": ("fewer", "more", "能多举几个具体的例子吗？"),
    "structure": ("conclusion_first", "buildup_first", "先别直接告诉我结论，一步步带我推出来。"),
    "depth": ("what", "derivation", "请把推导过程一步步写出来。"),
}
#: 冲突集的 5 个问题（与主集不重）。
CONFLICT_QUESTIONS = [
    "特征方程是怎么来的？",
    "对称矩阵的特征值有什么特点？",
    "迹和行列式跟特征值是什么关系？",
    "为什么剪切变换不能对角化？",
    "反复作用一个矩阵之后，为什么最大的特征值最重要？",
]


def style_of(axis: str, value: str) -> dict[str, str]:
    s = dict(DEFAULTS)
    s[axis] = value
    return s


def texts_plan() -> list[dict[str, Any]]:
    plan = []
    for qi, q in enumerate(QUESTIONS, 1):
        for axis, vals in OPPOSITE.items():
            for v in vals:
                plan.append({"id": f"m{qi:02d}-{axis}-{v}", "set": "main", "axis": axis, "value": v,
                             "question": q, "asked": q})
    for qi, q in enumerate(CONFLICT_QUESTIONS, 1):
        for axis, (_stored, wanted, req) in CONFLICT_RULE.items():
            plan.append({"id": f"c{qi:02d}-{axis}", "set": "conflict", "axis": axis, "value": wanted,
                         "question": q, "asked": f"{q}{req}"})
    return plan


# ---------------------------------------------------------------- generate


def _gen_one(row: dict[str, Any]) -> dict[str, Any]:
    from verify_eval import deck_slices

    from explain.context import ContextPack

    from explain.llm import AnthropicModel
    from explain.prompt import build_system, build_user_turn
    from explain.style_prompt import render_all_style_rules

    style = style_of(row["axis"], row["value"])
    config = style_config_for(style["detail_level"])
    b_class = {k: style[k] for k in ("use_examples", "structure", "depth")}
    # 整份课件全部喂入（预登记「数据」一节）：测的是讲法，不是检索。
    ctx = ContextPack(question=row["asked"], slices=tuple(deck_slices()))
    system = build_system(render_all_style_rules(config, b_class), ctx, LABEL)
    c = AnthropicModel().complete(system, [{"role": "user", "content": build_user_turn(ctx, row["asked"])}])
    return {**row, "style": style, "text": c.text, "cost_usd": round(c.cost, 5)}


def cmd_generate(args: Any) -> int:
    prev = json.loads(BASE.read_text(encoding="utf-8")) if BASE.exists() else {"texts": []}
    have = {r["id"]: r for r in prev["texts"] if r.get("text", "").strip()}
    todo = [r for r in texts_plan() if r["id"] not in have]
    print(f"已有 {len(have)} 段，要生成 {len(todo)} 段")
    out = dict(have)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(_safe(_gen_one), todo):
            if r.get("error"):
                print(f"  ✗ {r['id']}: {r['error']}")
                continue
            out[r["id"]] = r
            print(f"  {r['id']:<34} {len(r['text']):>5} 字  ${r['cost_usd']:.4f}")
    rows = [out[p["id"]] for p in texts_plan() if p["id"] in out]
    spent = sum(r["cost_usd"] for r in rows)
    BASE.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M"), "spent_usd": round(spent, 4),
                                "texts": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"共 {len(rows)} 段，生成花费 ${spent:.4f} → {BASE}")
    return 0


def _safe(fn):
    def go(row):
        try:
            return fn(row)
        except Exception as exc:  # noqa: BLE001
            return {**row, "error": f"{type(exc).__name__}: {exc}"}
    return go


# ---------------------------------------------------------------- items


def build_items() -> list[dict[str, Any]]:
    """预登记的四个集。同一段讲解在「符合」与「偏离」里各出现一次。"""
    texts = {r["id"]: r for r in json.loads(BASE.read_text(encoding="utf-8"))["texts"]}
    items = []
    for tid, t in texts.items():
        axis, v = t["axis"], t["value"]
        if t["set"] == "main":
            a, b = OPPOSITE[axis]
            other = b if v == a else a
            items.append({"id": f"{tid}~fit", "set": "fit", "text_id": tid, "axis": axis,
                          "pref": style_of(axis, v), "question": t["question"], "expect": "ok"})
            items.append({"id": f"{tid}~dev", "set": "deviate", "text_id": tid, "axis": axis,
                          "pref": style_of(axis, other), "question": t["question"], "expect": "rewrite"})
        else:
            stored = CONFLICT_RULE[axis][0]
            items.append({"id": f"{tid}~conflict", "set": "conflict", "text_id": tid, "axis": axis,
                          "pref": style_of(axis, stored), "question": t["asked"], "expect": "ok"})
            items.append({"id": f"{tid}~control", "set": "control", "text_id": tid, "axis": axis,
                          "pref": style_of(axis, stored), "question": t["question"], "expect": "rewrite"})
    return items


# ---------------------------------------------------------------- run


def make_agent(model: str, no_timeout: bool = False) -> StyleReviewAgent:
    a = _make_agent(model)
    if no_timeout:
        # 偏离预登记的诊断跑（见结果文件）：生产的 8 s / 15 s 时限下大多数审查超时，
        # 这一跑放开时限，只为看「审完的话判得准不准」。prompt、工具、校验都不变。
        a.call_timeout_s, a.deadline_s = 120.0, 300.0
    return a


def _make_agent(model: str) -> StyleReviewAgent:
    if model == "haiku":
        return build_style_review_agent()
    from explain.llm import AnthropicModel

    return StyleReviewAgent(AnthropicModel(name="claude-sonnet-5", thinking="disabled", max_tokens=1200,
                                           cache_system=False, cache_conversation=False))


def results_path(model: str, no_timeout: bool = False) -> Path:
    return RUNS / f"style_review_eval_results_{model}{'_notimeout' if no_timeout else ''}.json"


def cmd_run(args: Any) -> int:
    texts = {r["id"]: r for r in json.loads(BASE.read_text(encoding="utf-8"))["texts"]}
    items = build_items()
    if args.sets:
        items = [it for it in items if it["set"] in args.sets.split(",")]
    if args.limit:
        items = items[: args.limit]
    path = results_path(args.model, args.no_timeout)
    done = {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["results"]} if path.exists() else {}
    todo = [it for it in items if it["id"] not in done]
    print(f"{args.model}：已有 {len(done)} 条，要跑 {len(todo)} 条")

    def one(it: dict[str, Any]) -> dict[str, Any]:
        p = it["pref"]
        prefs = render_preferences(style_config_for(p["detail_level"]), p["detail_level"],
                                   {k: p[k] for k in ("use_examples", "structure", "depth")})
        started = time.perf_counter()
        r = make_agent(args.model, args.no_timeout).review(preferences=prefs, question=it["question"],
                                          text=texts[it["text_id"]]["text"], history=())
        d = r.to_dict()
        return {**it, "verdict": r.verdict, "status": r.status, "wall_ms": round((time.perf_counter() - started) * 1000),
                "run": d}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for k, res in enumerate(ex.map(one, todo), 1):
            done[res["id"]] = res
            mark = "✓" if (res["verdict"] or "ok") == res["expect"] else "✗"
            tools = ",".join(t["name"] for t in res["run"]["tools"])
            print(f"  {mark} {res['id']:<40} 期望 {res['expect']:<7} 得到 {str(res['verdict']):<7} [{tools}]")
            if k % 20 == 0:
                _save(path, args.model, done)
    _save(path, args.model, done)
    spent = sum(r["run"]["cost_usd"] or 0 for r in done.values())
    print(f"共 {len(done)} 条，审查花费 ${spent:.4f} → {path}")
    return 0


def _save(path: Path, model: str, done: dict[str, Any]) -> None:
    order = {it["id"]: i for i, it in enumerate(build_items())}
    rows = sorted(done.values(), key=lambda r: order.get(r["id"], 1e9))
    path.write_text(json.dumps({"model": model, "results": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- report


def suspicious_detail_pairs() -> set[str]:
    """预登记的标签效度检查：同一问题 P0 版总字数 < P3 版的 1.3 倍 → 这一对可疑。返回可疑讲解的 id。"""
    texts = {r["id"]: r for r in json.loads(BASE.read_text(encoding="utf-8"))["texts"]}
    bad: set[str] = set()
    for qi in range(1, len(QUESTIONS) + 1):
        a, b = f"m{qi:02d}-detail_level-P0", f"m{qi:02d}-detail_level-P3"
        if a in texts and b in texts and len(texts[a]["text"]) < 1.3 * len(texts[b]["text"]):
            bad |= {a, b}
    return bad


def rate(rows: list[dict[str, Any]], want: str) -> tuple[int, int]:
    """want = rewrite：数 rewrite；want = ok：数放行（ok 与失败都算放行，按生产处置）。"""
    if want == "rewrite":
        return sum(1 for r in rows if r["verdict"] == "rewrite"), len(rows)
    return sum(1 for r in rows if r["verdict"] != "rewrite"), len(rows)


def pct(a: int, n: int) -> str:
    return f"{a}/{n}（{100 * a / n:.1f}%）" if n else "—"


def cmd_report(args: Any) -> int:
    res = json.loads(results_path(args.model, args.no_timeout).read_text(encoding="utf-8"))["results"]
    by = lambda s, axis=None: [r for r in res if r["set"] == s and (axis is None or r["axis"] == axis)]  # noqa: E731
    sus = suspicious_detail_pairs()
    tag = f"{args.model}{'_notimeout' if args.no_timeout else ''}"
    lines = [f"# 风格审查 agent 评测 · 原始汇总（{tag}）", ""]
    x, y, z, ctl = rate(by("deviate"), "rewrite"), rate(by("fit"), "rewrite"), rate(by("conflict"), "ok"), \
        rate(by("control"), "rewrite")
    lines += [
        "| 指标 | 结果 | 达标线 |", "| --- | --- | --- |",
        f"| **X** 偏离 → 打回 | {pct(*x)} | ≥ 70% |",
        f"| **Y** 符合 → 误打回 | {pct(*y)} | ≤ 10% |",
        f"| **Z** 冲突 → 放行 | {pct(*z)} | ≥ 70% |",
        f"| 冲突对照（去掉那句要求）→ 打回 | {pct(*ctl)} | — |",
        "", "## 按轴", "", "| 轴 | X 偏离打回 | Y 符合误打回 | Z 冲突放行 | 对照打回 |", "| --- | --- | --- | --- | --- |",
    ]
    for axis in OPPOSITE:
        lines.append(f"| {axis} | {pct(*rate(by('deviate', axis), 'rewrite'))} | {pct(*rate(by('fit', axis), 'rewrite'))} "
                     f"| {pct(*rate(by('conflict', axis), 'ok'))} | {pct(*rate(by('control', axis), 'rewrite'))} |")
    failed = [r for r in res if r["status"] == "failed"]
    costs = [r["run"]["cost_usd"] for r in res if r["run"]["cost_usd"] is not None]
    lat = [r["run"]["latency_ms"] for r in res]
    steps = [r["run"]["steps"] for r in res]
    used = [",".join(t["name"] for t in r["run"]["tools"]) for r in res]
    from collections import Counter
    lines += [
        "", "## 代价", "",
        f"- 条数 {len(res)}；agent 失败（按放行计）{len(failed)} 条" + (
            "：" + "；".join(f"{r['id']} {r['run']['error']}" for r in failed[:5]) if failed else ""),
        f"- 单次成本：均值 ${statistics.mean(costs):.4f}，中位 ${statistics.median(costs):.4f}，"
        f"= agent 课件题单轮 $0.027 的 +{100 * statistics.mean(costs) / 0.027:.0f}%（成本未知 {len(res) - len(costs)} 条）",
        f"- 延迟：中位 {statistics.median(lat) / 1000:.1f} s，P90 {sorted(lat)[int(0.9 * len(lat)) - 1] / 1000:.1f} s，"
        f"最大 {max(lat) / 1000:.1f} s",
        f"- 步数分布：{dict(sorted(Counter(steps).items()))}；提交被退回合计 {sum(r['run']['rejected'] for r in res)} 次",
        f"- 工具用法（前 6）：{Counter(used).most_common(6)}",
        "", "## 标签效度（预登记的自动检查）", "",
        f"- detail_level 可疑讲解 {len(sus)} 段：{sorted(sus) or '无'}",
    ]
    if sus:
        keep = [r for r in res if r["text_id"] not in sus]
        sx = rate([r for r in keep if r["set"] == "deviate"], "rewrite")
        sy = rate([r for r in keep if r["set"] == "fit"], "rewrite")
        lines.append(f"- 去掉可疑对之后（参考，不是标题数字）：X {pct(*sx)}，Y {pct(*sy)}")
    lines += ["", "## 判错的条目", ""]
    for r in res:
        passed_it = r["verdict"] != "rewrite"
        if passed_it == (r["expect"] == "ok"):
            continue
        iss = "；".join(f"第{i['paragraph']}段 {i['aspect']}「{i['quote'][:20]}」{i['fix'][:40]}"
                       for i in r["run"]["issues"]) or "；".join(n["comment"][:50] for n in r["run"]["notes"][:2])
        lines.append(f"- `{r['id']}` 期望 {r['expect']}、得到 {r['verdict']}：{iss}")
    out = RUNS / f"style_review_eval_report_raw_{tag}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))
    print(f"→ {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--workers", type=int, default=6)
    r = sub.add_parser("run")
    r.add_argument("--model", choices=["haiku", "sonnet"], default="haiku")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--sets", default="", help="只跑这些集，逗号分隔：fit,deviate,conflict,control")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--no-timeout", action="store_true", help="放开生产时限的诊断跑（偏离预登记）")
    p = sub.add_parser("report")
    p.add_argument("--model", choices=["haiku", "sonnet"], default="haiku")
    p.add_argument("--no-timeout", action="store_true")
    args = ap.parse_args()
    return {"generate": cmd_generate, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
