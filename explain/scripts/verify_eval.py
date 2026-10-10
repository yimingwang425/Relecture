"""出处核对 agent 的注入错误评测（口径 F121–F123）。判据见 `explain/runs/verify_eval_prereg.md`。

    api/.venv/bin/python explain/scripts/verify_eval.py generate          # 新生成 14 段讲解（Sonnet，要钱）
    api/.venv/bin/python explain/scripts/verify_eval.py perturb           # 三类注入 → items.json（不花钱），人工检查
    api/.venv/bin/python explain/scripts/verify_eval.py run --model haiku [--limit N]   # 核对（要钱）
    api/.venv/bin/python explain/scripts/verify_eval.py report            # 汇总（不花钱）

凡是要钱的子命令都要 `ANTHROPIC_API_KEY`（单条命令里映射，不 export）。

课件是自写的 `f1_samples/synthetic_deck.md`，不含任何第三方内容（PRD §12.1）——这份评测的数字可以写进简历。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "explain"))
sys.path.insert(0, str(REPO / "explain" / "scripts"))
sys.path.insert(0, str(REPO / "retrieval"))

import explain  # noqa: E402,F401
from explain.context import ContextPack, ContextSlice  # noqa: E402
from explain.verify import VerifyAgent, build_verify_agent, cited_pages, normalize  # noqa: E402

RUNS = REPO / "explain" / "runs"
BASE = RUNS / "verify_eval_base.json"
ITEMS = RUNS / "verify_eval_items.json"
#: 留出集（预登记补充 `verify_eval_prereg_holdout.md`）：主集的结果看过之后，改 prompt 与校验用的是主集的失败样子；
#: 改完的版本**只在留出集上**算数。留出集只有新生成的讲解，问题与主集不重。
HOLDOUT_BASE = RUNS / "verify_eval_holdout_base.json"
HOLDOUT_ITEMS = RUNS / "verify_eval_holdout_items.json"


def _paths(which: str) -> tuple[Path, Path]:
    return (BASE, ITEMS) if which == "main" else (HOLDOUT_BASE, HOLDOUT_ITEMS)


def _results_path(which: str, model: str, tag: str) -> Path:
    if which == "main" and tag == "v1":
        return RUNS / f"verify_eval_results_{model}.json"          # 预登记那一轮的文件名，不改
    return RUNS / f"verify_eval_{which}_results_{model}_{tag}.json"
DECK = REPO / "f1_samples" / "synthetic_deck.md"
DECK_NAME = "synthetic_deck.md"

BLOCK_RE = re.compile(r"<!-- (p(\d+)b\d+)( · FED)? -->\n(.*?)(?=\n\n<!--|\n\n---|\Z)", re.S)

NEW_QUESTIONS = [
    ("特征值是负数是什么意思？", "P2"),
    ("对称矩阵的特征值和特征向量有什么特点？", "P3"),
    ("为什么平面旋转没有实数特征值？", "P0"),
    ("复数特征值应该怎么理解？", "P2"),
    ("特征值怎么手算？", "P3"),
    ("特征值之和、之积分别等于什么？", "P0"),
    ("什么是对角化？有什么用？", "P2"),
    ("为什么剪切变换不能对角化？", "P3"),
    ("软件里是怎么算特征值的？", "P0"),
    ("幂法是怎么回事？", "P2"),
    ("PCA 和特征向量有什么关系？", "P3"),
    ("桥梁振动和特征值有什么关系？", "P0"),
    ("为什么反复作用之后最大的那个因子最重要？", "P2"),
    ("矩阵为什么说是线性的？", "P3"),
]

#: 留出集的问题。与上面、与 pairs.json 都不重；讲法档位同样在 P0 / P2 / P3 间轮换。
HOLDOUT_QUESTIONS = [
    ("镜面反射的特征向量是哪些方向？", "P2"),
    ("把向量压到原点的变换有什么特征值？", "P3"),
    ("整体放大的变换，特征向量有哪些？", "P0"),
    ("特征方程是怎么来的？", "P2"),
    ("为什么对称矩阵的特征向量互相垂直？", "P3"),
    ("迹和行列式跟特征值是什么关系？", "P0"),
    ("什么样的矩阵不能对角化？", "P2"),
    ("幂法为什么会收敛到最大特征值对应的方向？", "P3"),
    ("协方差矩阵的特征向量代表什么？", "P0"),
    ("旋转矩阵的特征值是什么？", "P2"),
    ("特征值为 1 意味着什么？", "P3"),
    ("特征向量为什么不能是零向量？", "P0"),
    ("实际计算特征值为什么不用特征方程？", "P2"),
    ("振动问题里特征值代表什么？", "P3"),
    ("特征值为负数时，向量会怎样？", "P0"),
    ("为什么说特征向量是变换下方向不变的向量？", "P2"),
    # ---- 追加（预登记补充「不足 15 条就追加问题」）：剔除后改数字 7、换概念 7、标错页 14。
    ("反射变换的两个特征值分别是多少？", "P3"),
    ("一个 2×2 矩阵的迹是 5、行列式是 6，特征值是多少？", "P0"),
    ("为什么四分之一圈旋转的特征值是 i 和 -i？", "P2"),
    ("特征值大于 1 和小于 1 对反复迭代有什么影响？", "P3"),
    ("对称矩阵的特征值为什么一定是实数？", "P0"),
    ("把平面压扁到一条直线上的变换，特征值是什么？", "P2"),
    ("剪切变换有几个特征值？", "P3"),
    ("PCA 里最大的特征值说明什么？", "P0"),
    ("复数特征值的模长有什么意义？", "P2"),
    ("特征值为 0 的矩阵可逆吗？", "P3"),
    ("为什么镜面上的向量特征值是 1？", "P0"),
    ("幂法每一步为什么要重新缩放？", "P2"),
    ("2×2 矩阵的特征多项式是几次的？", "P3"),
    ("对角化之后算矩阵的 10 次方为什么容易？", "P0"),
    ("最大特征值小于 1 时，系统会怎样？", "P2"),
    ("垂直于镜面的向量会怎样？", "P3"),
    ("一个变换把所有向量放大两倍，它的特征值是什么？", "P0"),
    ("协方差矩阵为什么是对称矩阵？", "P2"),
    ("负特征值和旋转有什么区别？", "P3"),
    ("为什么不同特征值的特征向量是垂直的，这对对称矩阵有什么用？", "P0"),
]


def deck_slices() -> list[ContextSlice]:
    text = DECK.read_text(encoding="utf-8")
    body = text.split("-->", 1)[1] if text.lstrip().startswith("<!--") else text
    return [ContextSlice(source_file=DECK_NAME, slice_id=bid, page_indices=(int(pg),), title=None,
                         text=blk.strip(), char_count=len(blk.strip()))
            for bid, pg, _fed, blk in BLOCK_RE.findall(body)]


def deck_retriever():
    from agent_live_check import LocalLexical

    return LocalLexical(deck_slices())


# ---------------------------------------------------------------- generate


def cmd_generate(args: Any) -> int:
    from agent_live_check import LocalLexical

    from explain.agent import RetrieverTools, run_with_fallback
    from explain.gap import GuardedGapChecker, build_model_gap_checker
    from explain.llm import AnthropicModel
    from explain.pipeline import Trajectory
    from explain.rag import QueryRewriter, build_rag_model
    from explain.review import RuleReviewer
    from explain.style_axes import style_config_for
    from explain.style_prompt import render_style_rules

    slices = deck_slices()
    lex = LocalLexical(slices, rewriter=QueryRewriter(build_rag_model(400)))
    tools = RetrieverTools(LocalLexical(slices))
    model, gap = AnthropicModel(), GuardedGapChecker(build_model_gap_checker())
    base_path, _ = _paths(args.set)
    # 续跑：已经生成过的不再生成（留出集追加问题时用）。**不挑**——已有的一段都不动。
    prev = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else {"answers": [], "spent_usd": 0}
    rows, spent = list(prev["answers"]), float(prev["spent_usd"])
    have = {r["id"] for r in rows}
    questions = NEW_QUESTIONS if args.set == "main" else HOLDOUT_QUESTIONS
    prefix = "gen" if args.set == "main" else "hold"
    for i, (q, level) in enumerate(questions, 1):
        if f"{prefix}{i:02d}" in have:
            continue
        config = style_config_for(level)
        ctx = lex.fetch(q)
        r = run_with_fallback(question=q, trajectory=Trajectory(), retriever=lex, tools=tools, model=model,
                              reviewer=RuleReviewer(config), style_rules=render_style_rules(config),
                              course_label="线性代数 · 特征值（自写课件）", context=ctx, gap_checker=gap,
                              started_at=time.perf_counter())
        spent += r.cost_usd
        rows.append({"id": f"{prefix}{i:02d}", "question": q, "detail_level": level, "text": r.final_text,
                     "context": [s.slice_id for s in r.context.slices], "cost_usd": round(r.cost_usd, 5),
                     "mode": r.agent.mode if r.agent else None})
        print(f"{prefix}{i:02d} {level} ${r.cost_usd:.4f} 页码 {sorted(cited_pages(r.final_text))}  {q}")
    base_path.write_text(json.dumps({"spent_usd": round(spent, 4), "answers": rows}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"合计 ${spent:.4f} → {base_path}")
    return 0


# ---------------------------------------------------------------- perturb

NUM_MAP = {"0": "1", "1": "3", "-1": "-2", "2": "3"}
CONCEPT_SWAPS = [("垂直", "平行"), ("平行", "垂直"), ("最大", "最小"), ("最小", "最大"), ("实数", "复数"),
                 ("复数", "实数"), ("对称矩阵", "反对称矩阵"), ("行列式", "迹"), ("迹", "行列式"),
                 ("拉伸", "旋转"), ("协方差矩阵", "相关矩阵")]
_PAGE = re.compile(r"第\s*(\d+)\s*页")
_NUM = re.compile(r"(?<![\d.])-?\d+(?![\d.])")


def base_answers(which: str = "main") -> list[dict[str, Any]]:
    if which != "main":
        return [{**a, "source": "generated"}
                for a in json.loads(HOLDOUT_BASE.read_text(encoding="utf-8"))["answers"]]
    pairs = json.loads((REPO / "f1_samples" / "pairs.json").read_text(encoding="utf-8"))
    out = []
    for p in pairs["pairs"]:
        for smp in p["samples"]:
            out.append({"id": smp["sample_id"], "question": p["question"], "text": smp["text"],
                        "context": [b["id"] for b in p["fed_blocks"]], "source": "pairs.json"})
    if BASE.exists():
        for a in json.loads(BASE.read_text(encoding="utf-8"))["answers"]:
            out.append({**a, "source": "generated"})
    return out


def _sentence(text: str, start: int, end: int) -> str:
    a = max(text.rfind(c, 0, start) for c in "。！？\n") + 1
    bs = [i for i in (text.find(c, end) for c in "。！？\n") if i >= 0]
    return text[a:(min(bs) + 1 if bs else len(text))].strip()


def perturb(text: str, kind: str) -> dict[str, Any] | None:
    pages = [(m.start(), m.end()) for m in _PAGE.finditer(text)]
    in_page = lambda i: any(a <= i < b for a, b in pages)  # noqa: E731
    if kind == "number":
        if "两倍" in text:
            i = text.index("两倍")
            return {"start": i, "end": i + 2, "old": "两倍", "new": "三倍"}
        for m in _NUM.finditer(text):
            if in_page(m.start()):
                continue
            old = m.group(0)
            new = NUM_MAP.get(old, str(int(old) + 1))
            return {"start": m.start(), "end": m.end(), "old": old, "new": new}
        return None
    if kind == "concept":
        hits = []
        for old, new in CONCEPT_SWAPS:
            i = text.find(old)
            if i >= 0 and not (old == "迹" and text[max(0, i - 1):i + 1] in ("轨迹", "痕迹")):
                hits.append((i, old, new))
        if not hits:
            return None
        i, old, new = min(hits)
        return {"start": i, "end": i + len(old), "old": old, "new": new}
    if kind == "page":
        m = _PAGE.search(text)
        if not m:
            return None
        n = int(m.group(1))
        new_n = n + 3 if n + 3 <= 10 else n - 3
        return {"start": m.start(), "end": m.end(), "old": m.group(0), "new": f"第{new_n}页", "new_page": new_n}
    raise ValueError(kind)


def cmd_perturb(args: Any) -> int:
    _, items_path = _paths(args.set)
    answers = base_answers(args.set)
    excluded = json.loads(items_path.read_text(encoding="utf-8")).get("excluded", {}) if items_path.exists() else {}
    items = []
    for a in answers:
        items.append({"item": f"{a['id']}:clean", "answer_id": a["id"], "kind": "clean", "text": a["text"],
                      "context": a["context"]})
        for kind in ("number", "concept", "page"):
            p = perturb(a["text"], kind)
            if p is None:
                continue
            new_text = a["text"][:p["start"]] + p["new"] + a["text"][p["end"]:]
            sent = _sentence(new_text, p["start"], p["start"] + len(p["new"]))
            iid = f"{a['id']}:{kind}"
            items.append({"item": iid, "answer_id": a["id"], "kind": kind, "text": new_text,
                          "context": a["context"], "change": p, "sentence": sent,
                          "excluded": excluded.get(iid)})
    by = Counter(i["kind"] for i in items if not i.get("excluded"))
    items_path.write_text(json.dumps({"excluded": excluded, "items": items}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    for i in items:
        if i["kind"] != "clean":
            flag = f"  [剔除：{i['excluded']}]" if i.get("excluded") else ""
            print(f"{i['item']:28s} {i['change']['old']}→{i['change']['new']} ｜ {i['sentence'][:90]}{flag}")
    print(f"\n可用：{dict(by)} → {items_path}")
    return 0


# ---------------------------------------------------------------- run


def _context_pack(ids: list[str], by_id: dict[str, ContextSlice]) -> ContextPack:
    return ContextPack(question="", slices=tuple(by_id[i] for i in ids if i in by_id), meta={})


def _model(name: str):
    from explain.llm import AnthropicModel

    if name == "haiku":
        # 显式写出来，不依赖 build_verify_agent 的默认——生产换了模型之后，「haiku」这一组仍然是 Haiku。
        return AnthropicModel(name="claude-haiku-4-5-20251001", effort=None, thinking="disabled", max_tokens=3000,
                              cache_system=False, cache_conversation=False)
    if name == "sonnet":
        return AnthropicModel(name="claude-sonnet-5", effort="medium", thinking="disabled", max_tokens=4000,
                              cache_system=False, cache_conversation=False)
    raise ValueError(name)


def cmd_run(args: Any) -> int:
    data = json.loads(_paths(args.set)[1].read_text(encoding="utf-8"))
    slices = deck_slices()
    by_id = {s.slice_id: s for s in slices}
    items = [i for i in data["items"] if not i.get("excluded")]
    if args.only_answers:
        keep = set(args.only_answers.split(","))
        items = [i for i in items if i["answer_id"] in keep]
    if args.limit:
        items = items[: args.limit]
    out_path = _results_path(args.set, args.model, args.tag)
    done = {r["item"]: r for r in json.loads(out_path.read_text(encoding="utf-8"))["results"]} \
        if out_path.exists() else {}
    spent = sum(r["run"]["cost_usd"] for r in done.values())
    for it in items:
        if it["item"] in done:
            continue
        if spent >= args.budget:
            print(f"预算到了（${spent:.3f}），停")
            break
        agent: VerifyAgent = build_verify_agent(retriever=deck_retriever(), model=_model(args.model))
        run = agent.verify(answer=it["text"], context=_context_pack(it["context"], by_id))
        d = run.to_dict()
        spent += d["cost_usd"]
        done[it["item"]] = {"item": it["item"], "kind": it["kind"], "answer_id": it["answer_id"], "run": d}
        print(f"{it['item']:28s} {d['status']:7s} {d['counts']} rej={d['rejected']} ${d['cost_usd']:.4f} "
              f"{d['latency_ms'] / 1000:.1f}s {d['error'] or ''}")
        out_path.write_text(json.dumps({"model": args.model, "set": args.set, "tag": args.tag,
                                        "prompt_version": d["prompt_version"], "spent_usd": round(spent, 4),
                                        "results": list(done.values())}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"合计 ${spent:.4f} → {out_path}")
    return 0


# ---------------------------------------------------------------- report


def _match(item: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    """那一处改动所在的说法（判据见预登记）。"""
    sent = normalize(item["sentence"])
    if item["kind"] == "page":
        cands = [c for c in claims if c["kind"] == "fact" and c.get("cited_page") == item["change"]["new_page"]]
        cands = [c for c in cands if normalize(c["claim"]) in sent or sent in normalize(c["claim"])] or cands
        return cands[0] if cands else None
    new = normalize(item["change"]["new"])
    for c in claims:
        cl = normalize(c["claim"])
        if new in cl and (cl in sent or sent in cl or _overlap(cl, sent)):
            return c
    return None


def _overlap(a: str, b: str, n: int = 12) -> bool:
    return any(a[i:i + n] in b for i in range(0, max(1, len(a) - n + 1), 4))


def cmd_report(args: Any) -> int:
    data = json.loads(_paths(args.set)[1].read_text(encoding="utf-8"))
    items = {i["item"]: i for i in data["items"]}
    lines = []
    for spec in args.models.split(","):
        model, _, tag = spec.partition(":")
        tag = tag or "v1"
        path = _results_path(args.set, model, tag)
        if not path.exists():
            continue
        res = json.loads(path.read_text(encoding="utf-8"))["results"]
        if args.only_answers:
            keep = set(args.only_answers.split(","))
            res = [r for r in res if r["answer_id"] in keep]
        det: dict[str, Counter] = {k: Counter() for k in ("number", "concept", "page")}
        fa_items = fa_claims = facts = clean_n = 0
        flagged: list[tuple[str, str, str]] = []
        costs, lat, statuses = [], [], Counter()
        for r in res:
            it, run = items[r["item"]], r["run"]
            costs.append(run["cost_usd"])
            lat.append(run["latency_ms"])
            statuses[run["status"]] += 1
            if it["kind"] == "clean":
                clean_n += 1
                bad = [c for c in run["claims"] if c["kind"] == "fact" and c["verdict"] != "supported"]
                facts += sum(c["kind"] == "fact" for c in run["claims"])
                fa_claims += len(bad)
                fa_items += bool(bad)
                flagged += [(r["item"], c["verdict"], c["claim"]) for c in bad]
                continue
            if run["status"] != "written":
                det[it["kind"]]["failed"] += 1
                continue
            c = _match(it, run["claims"])
            if c is None:
                det[it["kind"]]["not_extracted"] += 1
            elif c["kind"] == "supplement":
                det[it["kind"]]["as_supplement"] += 1
            elif c["verdict"] == "supported":
                det[it["kind"]]["supported"] += 1
            else:
                det[it["kind"]]["caught"] += 1
        lines.append(f"## {args.set} · {model} · {tag}" + ("（只算 --only-answers 那一批）" if args.only_answers else "") + "\n")
        lines.append("| 类型 | n | 抓到 | 检出率 | 判 supported | 归为 supplement | 没提取 | 核对失败 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for k, c in det.items():
            n = sum(c.values())
            rate = f"{c['caught'] / n:.0%}" if n else "—"
            lines.append(f"| {k} | {n} | {c['caught']} | {rate} | {c['supported']} | {c['as_supplement']} | "
                         f"{c['not_extracted']} | {c['failed']} |")
        lines.append(f"\n未改样例 {clean_n} 段：逐段误报 {fa_items}/{clean_n}；逐条误报 {fa_claims}/{facts} 条 fact"
                     + (f"（{fa_claims / facts:.1%}）" if facts else ""))
        mean_cost = sum(costs) / len(costs) if costs else 0
        lines.append(f"\n单次核对：均值 ${mean_cost:.4f}（n = {len(costs)}）· 延迟均值 {sum(lat) / max(1, len(lat)) / 1000:.1f} s · "
                     f"状态 {dict(statuses)}")
        lines.append(f"占单轮讲解成本：固定流水线 $0.0151 的 {mean_cost / 0.0151:.0%} · agent 课件题 $0.027 的 {mean_cost / 0.027:.0%}")
        if flagged:
            lines.append("\n误报待人工复核：")
            lines += [f"- {i}｜{v}｜{c}" for i, v, c in flagged]
        lines.append("")
    text = "\n".join(lines)
    print(text)
    (RUNS / f"verify_eval_{args.set}_report_raw.md").write_text(text, encoding="utf-8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "perturb"):
        sub.add_parser(name).add_argument("--set", choices=["main", "holdout"], default="main")
    r = sub.add_parser("run")
    r.add_argument("--set", choices=["main", "holdout"], default="main")
    r.add_argument("--tag", default="v1", help="结果文件的标签：prompt / 校验版本")
    r.add_argument("--model", choices=["haiku", "sonnet"], default="haiku")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--budget", type=float, default=1.5)
    r.add_argument("--only-answers", default="")
    rp = sub.add_parser("report")
    rp.add_argument("--set", choices=["main", "holdout"], default="main")
    rp.add_argument("--models", default="haiku,sonnet", help="model[:tag]，逗号分隔；tag 缺省 v1")
    rp.add_argument("--only-answers", default="", help="只算这些讲解（Haiku 与 Sonnet 比同一批时用）")
    args = ap.parse_args()
    return {"generate": cmd_generate, "perturb": cmd_perturb, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
