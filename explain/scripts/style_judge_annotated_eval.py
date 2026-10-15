"""风格审查员对着**人工标注**的校准（2026-10-15）。判据见 `explain/runs/style_judge_j6_prereg.md`。

    api/.venv/bin/python explain/scripts/style_judge_annotated_eval.py j5      # 用已有 j5 分数重算（不花钱）
    api/.venv/bin/python explain/scripts/style_judge_annotated_eval.py run     # 当前版本审一遍（Sonnet，要钱）
    api/.venv/bin/python explain/scripts/style_judge_annotated_eval.py report  # 当前版本的汇总（不花钱）

「符合」64 条取 `style_fit_annotation_sheet.md` 里**标注后**的版本：标注人把其中几条的讲解语言改成「需要英文」、
给几段讲解补了英文括注——审查员看到的偏好与讲解就是标注人判断时看到的那一份。
「偏离 / 冲突 / 对照」照旧取 `style_review_eval.build_items()`（没有人工标注，期望值来自生成条件）。
要钱的子命令要 `ANTHROPIC_API_KEY`（单条命令里映射，不 export）。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "explain"))
sys.path.insert(0, str(REPO / "explain" / "scripts"))

import explain  # noqa: E402,F401
from explain import style_review as sr  # noqa: E402
from explain.style_axes import style_config_for  # noqa: E402
from style_review_eval import BASE, build_items  # noqa: E402

RUNS = REPO / "explain" / "runs"
SHEET = RUNS / "style_fit_annotation_sheet.md"
OUT = RUNS / f"style_judge_annotated_results_{sr.STYLE_JUDGE_PROMPT_VERSION}.json"
J5 = RUNS / "style_judge_eval_results_j5.json"

DIMS = {"详细程度": "detail_level", "举例": "use_examples", "结构": "structure", "深度": "depth", "讲解语言": "language"}
#: 标注人在表头补充第 2 条：详细程度「放至最宽，只要不是要求详细回答极简、要求极简回答详细这种非常明显的都算过」。
#: 「符合」集里没有这种条目，所以主口径里详细程度一律算符合；另报五个方框全勾的严口径。
LENIENT_SKIP = ("detail_level",)


def annotations() -> list[dict[str, Any]]:
    """解析标注表。`checks` 五个方框；`need_en` 标注人改成了「需要英文」；`text` 标注后的讲解。"""
    base = {r["id"]: r["text"] for r in json.loads(BASE.read_text(encoding="utf-8"))["texts"]}
    out = []
    for b in re.split(r"\n## (?=\d+\. `)", SHEET.read_text(encoding="utf-8"))[1:]:
        tid = re.search(r"`([^`]+)`", b).group(1)
        checks = {DIMS[m.group(2)]: "x" in m.group(1).lower()
                  for m in re.finditer(r"^- \[([ xX]*)\] *(详细程度|举例|结构|深度|讲解语言)\s*$", b, re.M)}
        quoted = re.search(r"\*\*讲解\*\*：\n\n(.*?)\n\n\*\*标注", b, re.S).group(1)
        text = "\n".join(line[2:] if line.startswith("> ") else "" if line == ">" else line
                         for line in quoted.split("\n")).strip()
        out.append({"text_id": tid, "checks": checks, "need_en": "专业术语需要在括号里" in b,
                    "text": text, "edited": text != base[tid].strip(),
                    # 五个方框全空：标注人跳过了这一条（讲解先给结论、有例子、讲了为什么——不像是逐项判了不符合）
                    "unannotated": not any(checks.values())})
    return out


def human_ok(a: dict[str, Any], strict: bool = False) -> bool:
    return all(v for k, v in a["checks"].items() if strict or k not in LENIENT_SKIP)


def prefs_for(item: dict[str, Any], need_en: bool) -> str:
    p = item["pref"]
    prefs = sr.render_preferences(style_config_for(p["detail_level"]), p["detail_level"],
                                  {k: p[k] for k in ("use_examples", "structure", "depth")})
    if need_en:  # 生产里由术语处理 / 学术语气决定；评测集的偏好没有这两项，直接换那一行
        prefs = prefs.replace(sr._LANGUAGE_OPTIONAL, sr._LANGUAGE_REQUIRED)
    return prefs


def eval_items() -> list[dict[str, Any]]:
    ann = {a["text_id"]: a for a in annotations()}
    texts = {r["id"]: r["text"] for r in json.loads(BASE.read_text(encoding="utf-8"))["texts"]}
    rows = []
    for it in build_items():
        a = ann.get(it["text_id"]) if it["set"] == "fit" else None
        if a and a["unannotated"]:
            continue
        rows.append({**it, "prefs": prefs_for(it, bool(a and a["need_en"])),
                     "text": a["text"] if a else texts[it["text_id"]],
                     "human": a and {"checks": a["checks"], "ok": human_ok(a), "ok_strict": human_ok(a, True),
                                     "need_en": a["need_en"], "edited": a["edited"]}})
    return rows


def cmd_run(args: Any) -> int:
    items = eval_items()
    done = {r["id"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))["results"]} if OUT.exists() else {}
    todo = [it for it in items if it["id"] not in done]
    print(f"{sr.STYLE_JUDGE_PROMPT_VERSION}：已有 {len(done)} 条，要跑 {len(todo)} 条")

    def one(it: dict[str, Any]) -> dict[str, Any]:
        j = sr.build_style_judge().judge(preferences=it["prefs"], question=it["question"], text=it["text"])
        return {**it, "verdict": j.verdict, "run": j.to_dict()}

    spent = sum(r["run"]["cost_usd"] or 0 for r in done.values())
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(one, todo):
            done[res["id"]] = res
            spent += res["run"]["cost_usd"] or 0
            print(f"  {res['id']:<42} 期望 {res['expect']:<7} 得到 {res['verdict']}  累计 ${spent:.3f}")
    order = {it["id"]: i for i, it in enumerate(items)}
    rows = sorted(done.values(), key=lambda r: order.get(r["id"], 1e9))
    OUT.write_text(json.dumps({"prompt_version": sr.STYLE_JUDGE_PROMPT_VERSION, "results": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"共 {len(rows)} 条，花费 ${spent:.4f} → {OUT}")
    return 0


def f(a: int, n: int) -> str:
    return f"{a}/{n}（{100 * a / n:.0f}%）" if n else "—"


def summarize(rows: list[dict[str, Any]], title: str) -> list[str]:
    """`rows` 每条要有 `set`、`human`、`scores`（None = 没判成，按放行）。"""
    rw = lambda r: bool(r["scores"]) and max(r["scores"].values()) >= sr.REWRITE_THRESHOLD  # noqa: E731
    fit = [r for r in rows if r["set"] == "fit"]
    ok, bad = [r for r in fit if r["human"]["ok"]], [r for r in fit if not r["human"]["ok"]]
    strict = [r for r in fit if r["human"]["ok_strict"]]
    lines = [f"### {title}", "", "| 口径 | 打回 |", "| --- | --- |",
             f"| **误报**：人工判符合（详细程度按放宽口径） | **{f(sum(map(rw, ok)), len(ok))}** |",
             f"| 误报（严口径：五个方框全勾） | {f(sum(map(rw, strict)), len(strict))} |",
             f"| 人工判**不符合**的「符合」样例 → 打回 | {f(sum(map(rw, bad)), len(bad))} |"]
    for s, label in (("deviate", "检出：偏离样例 → 打回"), ("control", "冲突对照 → 打回")):
        xs = [r for r in rows if r["set"] == s]
        if xs:
            lines.append(f"| {label} | {f(sum(map(rw, xs)), len(xs))} |")
    xs = [r for r in rows if r["set"] == "conflict"]
    if xs:
        lines.append(f"| 冲突 → 放行 | {f(sum(1 for r in xs if not rw(r)), len(xs))} |")
    lines += ["", "逐轴（「符合」样例；审查员该轴 ≥ 阈值 = 判不符合）：", "",
              "| 轴 | 人工符合 → 审查员也判符合 | 人工不符合 → 审查员也判不符合 |", "| --- | --- | --- |"]
    for k in DIMS.values():
        sc = [r for r in fit if r["scores"]]
        hk = [r for r in sc if r["human"]["checks"][k] or k in LENIENT_SKIP]
        hn = [r for r in sc if not (r["human"]["checks"][k] or k in LENIENT_SKIP)]
        lines.append(f"| {k} | {f(sum(1 for r in hk if r['scores'][k] < sr.REWRITE_THRESHOLD), len(hk))} "
                     f"| {f(sum(1 for r in hn if r['scores'][k] >= sr.REWRITE_THRESHOLD), len(hn))} |")
    lines += ["", f"- 没给出合格分数（按放行计）：{sum(1 for r in rows if not r['scores'])} 条"]
    return lines


def cmd_j5(_args: Any) -> int:
    """已有 j5 分数 × 人工标注。只取标注人**没改过偏好与讲解**的条目——改过的那几条 j5 审的不是同一份东西。"""
    ann = {a["text_id"]: a for a in annotations()}
    rows = []
    for r in json.loads(J5.read_text(encoding="utf-8"))["results"]:
        a = ann.get(r["text_id"]) if r["set"] == "fit" else None
        if r["set"] == "fit" and (a["unannotated"] or a["need_en"] or a["edited"]):
            continue
        rows.append({"set": r["set"], "scores": r["run"].get("scores"),
                     "human": a and {"checks": a["checks"], "ok": human_ok(a), "ok_strict": human_ok(a, True)}})
    lines = summarize(rows, "j5 分数 × 人工标注（未改动的「符合」样例，不花钱）")
    print("\n".join(lines))
    return 0


def reparsed(r: dict[str, Any]) -> dict[str, int] | None:
    """没判成的那几条，用**当前**解析器重读存下来的输出（错误信息里留了前 200 字，分数在里面）。事后口径。"""
    if r["run"].get("scores"):
        return r["run"]["scores"]
    err = r["run"].get("error") or ""
    if not err.startswith("输出不合法："):
        return None
    data = sr._judge_obj(err.removeprefix("输出不合法："))
    return sr._scores(data.get("scores")) if data else None


def cmd_report(_args: Any) -> int:
    res = json.loads(OUT.read_text(encoding="utf-8"))["results"]
    rows = [{**r, "scores": r["run"].get("scores")} for r in res]
    lines = summarize(rows, f"{sr.STYLE_JUDGE_PROMPT_VERSION} × 人工标注")
    lines += [""] + summarize([{**r, "scores": reparsed(r)} for r in res],
                              f"{sr.STYLE_JUDGE_PROMPT_VERSION} × 人工标注 · 事后：没判成的几条用修好的解析器重读同一份输出")
    costs = [r["run"]["cost_usd"] for r in res if r["run"]["cost_usd"] is not None]
    lat = sorted(r["run"]["latency_ms"] for r in res)
    lines += [f"- 花费 ${sum(costs):.4f}；单次均值 ${statistics.mean(costs):.4f}",
              f"- 延迟：中位 {statistics.median(lat) / 1000:.1f} s，P90 {lat[int(0.9 * len(lat)) - 1] / 1000:.1f} s",
              "", "#### 人工判符合、审查员打回的条目", ""]
    for r in rows:
        if r["set"] == "fit" and r["human"]["ok"] and r["scores"] and max(r["scores"].values()) >= sr.REWRITE_THRESHOLD:
            hi = {k: v for k, v in r["scores"].items() if v >= sr.REWRITE_THRESHOLD}
            lines.append(f"- `{r['text_id']}` {hi}：{r['run']['reason'][:80]}")
    out = RUNS / f"style_judge_annotated_report_raw_{sr.STYLE_JUDGE_PROMPT_VERSION}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--workers", type=int, default=8)
    sub.add_parser("report")
    sub.add_parser("j5")
    args = ap.parse_args()
    return {"run": cmd_run, "report": cmd_report, "j5": cmd_j5}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
