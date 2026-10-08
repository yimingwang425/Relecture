"""多智能体链路的**真模型**验收（口径 F109–F114）。要钱：真调 Sonnet + Haiku。

    ANTHROPIC_API_KEY=... api/.venv/bin/python explain/scripts/agent_live_check.py [--budget 3.5]

## 测什么、不测什么

**测的是链路在真模型上跑不跑得通**——这几件事替身证明不了：

1. 真模型**会不会调工具**、调的时候入参合不合 schema（`strict: true` 在真 API 上收不收）；
2. 带 thinking 的多步对话里，上一步的内容块原样接回去，API 收不收；
3. 路由在真 Haiku 上分不分得开寒暄与课件问题；
4. 首字、关键路径、单轮成本落在什么量级。

**不测召回收益。** 题只有几道、每道一次，任何「agent 让答案变好了」的说法在这个 n 上
都不构成证据（R17）。收益要在评测集上多条件重复测，这份脚本不做那件事。

## 检索是本地的，不是生产那一套

这里没有 OpenAI 嵌入、没有 Supabase。首轮检索 = 查询改写（真 Haiku，与生产同一个
`QueryRewriter`）+ 本地词法（与生产词法一路同一个 `retrieval.lexical.tokens`）。
**比生产弱**（少了向量一路与重排），所以首轮更容易「不够」，agent 更容易去查——
这正好让工具那条路被走到，但**也意味着这里的工具调用频率不代表生产**。

语料：`实验脚本/测试集/MIT22_081JF10_lec01b.pdf`（OCW 公开授权，`SOURCES.md`）。

决策日期：2026-10-08
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "explain"))
sys.path.insert(0, str(REPO / "retrieval"))

import explain  # noqa: E402,F401 —— 注入 a_rules 路径
from explain.agent import RetrieverTools, run_with_fallback  # noqa: E402
from explain.context import ContextPack, ContextSlice  # noqa: E402
from explain.gap import GuardedGapChecker, build_model_gap_checker  # noqa: E402
from explain.llm import AnthropicModel  # noqa: E402
from explain.pipeline import Trajectory  # noqa: E402
from explain.rag import QueryRewriter, build_rag_model  # noqa: E402
from explain.review import RuleReviewer  # noqa: E402
from explain.router import build_router  # noqa: E402
from explain.style_axes import style_config_for  # noqa: E402
from explain.style_prompt import render_style_rules  # noqa: E402
from retrieval.lexical import tokens  # noqa: E402

DECK = REPO / "实验脚本" / "测试集" / "MIT22_081JF10_lec01b.pdf"

#: 题目：评测集里的四道（单页 / 跨页 / 无答案），加一句寒暄、一句追问。
QUESTIONS: list[dict[str, Any]] = [
    {"id": "Q001", "q": "发电厂烧了那么多煤，为什么不能把热全都变成电？", "gold": [20]},
    {"id": "Q013", "q": "核反应堆里那么高的温度，最后是怎么一步步变成插座里的电的？", "gold": [20, 27]},
    {"id": "Q013-follow", "q": "那中间把热变成电的那一步，效率大概能到多少？", "gold": [20], "follow": True},
    {"id": "Q014", "q": "一次核反应放出来的能量，跟烧一次煤差多少倍？", "gold": [28]},
    {"id": "Q018", "q": "核电站用过的燃料棒最后怎么处理？", "gold": []},
    {"id": "chat", "q": "谢谢，讲得很清楚！", "gold": [], "follow": True},
]


class LocalLexical:
    """本地词法检索（BM25）。`fetch()` 给首轮与 agent 的 `search_slices`，`page()` 给 `read_page`。"""

    def __init__(self, slices: list[ContextSlice], rewriter: QueryRewriter | None = None,
                 budget: int = 5) -> None:
        self.slices = slices
        self.rewriter = rewriter
        self.budget = budget
        self.docs = [tokens(f"{s.title or ''} {s.text}") for s in slices]
        self.df = Counter(t for d in self.docs for t in set(d))
        self.avg = sum(len(d) for d in self.docs) / max(1, len(self.docs))
        self.history: list[dict[str, Any]] = []

    def _bm25(self, q: list[str]) -> list[int]:
        n = len(self.docs)
        scores = []
        for i, d in enumerate(self.docs):
            tf = Counter(d)
            s = 0.0
            for t in set(q):
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / self.avg))
            scores.append((s, i))
        return [i for s, i in sorted(scores, reverse=True) if s > 0][: self.budget]

    def fetch(self, question: str) -> ContextPack:
        cost: float | None = 0.0
        terms = tokens(question)
        rewrite: dict[str, Any] = {"implementation": "none"}
        if self.rewriter is not None:
            rw = self.rewriter.rewrite(question, self.history)
            terms += tokens(" ".join(rw.keywords)) + tokens(rw.query)
            cost = rw.cost_usd
            rewrite = rw.to_dict()
        hits = self._bm25(terms)
        return ContextPack(question=question, slices=tuple(self.slices[i] for i in hits),
                           meta={"backend": "local_lexical", "llm_cost_usd": cost, "rewrite": rewrite})

    def page(self, source_file: str, page: int) -> ContextPack:
        out = tuple(s for s in self.slices if s.source_file == source_file and page in s.page_indices)
        return ContextPack(question=f"{source_file}#p{page}", slices=out, meta={"backend": "local_lexical"})


def load_slices() -> list[ContextSlice]:
    from parse_service.pdf import parse_pdf  # type: ignore[import-not-found]
    from slicer.core import slice_document

    ss = slice_document(parse_pdf(DECK.read_bytes(), DECK.name))
    return [ContextSlice(source_file=DECK.name, slice_id=s.slice_id, page_indices=tuple(s.page_indices),
                         title=s.title, text=s.text, char_count=s.char_count) for s in ss.slices]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=3.5, help="累计花费到这个数（美元）就停")
    ap.add_argument("--out", default=None)
    ap.add_argument("--fake", action="store_true",
                    help="不出网：替身模型跑通脚本本身（花之前先确认脚本没坑）")
    args = ap.parse_args()
    out_default = REPO / "explain" / "runs" / f"agent_live_{date.today():%Y%m%d}{'_fake' if args.fake else ''}.json"

    slices = load_slices()
    tools_backend = LocalLexical(slices)   # agent 自己的查询不再改写（与生产同一条）
    config = style_config_for("P2")
    if args.fake:
        from explain.fake import ScriptedToolModel
        from explain.gap import NullGapChecker
        from explain.llm import Completion, Usage
        from explain.router import Router

        class _Haiku:
            name = "fake:claude-haiku-4-5"

            def complete(self, system, messages):
                chat = "谢谢" in str(messages[-1]["content"])
                raw = '{"route": "chat", "reply": "不客气！"}' if chat else '{"route": "course", "reply": ""}'
                return Completion(text=raw, usage=Usage(30, 10), latency_ms=1.0, model=self.name)

        lex = LocalLexical(slices)
        model = ScriptedToolModel(script=[("tool", [("search_slices", {"query": "Carnot efficiency"})])] * 40)
        gap = NullGapChecker()
        router = Router(_Haiku())
    else:
        lex = LocalLexical(slices, rewriter=QueryRewriter(build_rag_model(400)))
        model = AnthropicModel()
        gap = GuardedGapChecker(build_model_gap_checker())
        router = build_router()

    spent = 0.0
    rows: list[dict[str, Any]] = []
    traj = Trajectory()
    for item in QUESTIONS:
        if spent >= args.budget:
            print(f"预算到了（${spent:.3f}），停在 {item['id']} 之前")
            break
        if not item.get("follow"):
            traj = Trajectory()
        lex.history = list(traj.history)
        events: list[dict[str, Any]] = []
        t0 = time.perf_counter()
        route = router.route(item["q"], traj.history)
        ctx = lex.fetch(item["q"])
        result = run_with_fallback(
            question=item["q"], trajectory=traj, retriever=lex, tools=RetrieverTools(tools_backend),
            model=model, reviewer=RuleReviewer(config), style_rules=render_style_rules(config),
            course_label="MIT 22.081J 能源导论", context=ctx, gap_checker=gap, route=route,
            on_event=events.append, started_at=t0)
        a = result.agent.to_dict()
        first_pages = sorted({p for s in ctx.slices for p in s.page_indices})
        final_pages = sorted({p for s in result.context.slices for p in s.page_indices})
        gold = set(item["gold"])
        row = {
            "id": item["id"], "question": item["q"],
            "route": (a["route"] or {}).get("route"), "route_impl": (a["route"] or {}).get("implementation"),
            "mode": a["mode"], "fallback_error": a["fallback_error"],
            "gap_verdict": result.gap.verdict,
            "tools": [(t["name"], t["input"], t["summary"]) for t in a["tools"]],
            "hit_round_cap": a["hit_round_cap"],
            "gold_pages": sorted(gold),
            "gold_in_first_retrieval": sorted(gold & set(first_pages)),
            "gold_in_final_context": sorted(gold & set(final_pages)),
            "accepted": result.accepted, "regenerations": result.regenerations,
            "first_token_ms": result.first_token_latency_ms,
            "critical_path_ms": round(result.latency_ms, 1), "wall_ms": result.wall_ms,
            "cost_usd": round(result.cost_usd, 5), "agent_cost_usd": round(a["cost_usd"], 5),
            "usage": result.usage.to_dict(),
            "stop_reasons": [s.stop_reason for s in result.agent.steps] + [x.completion.stop_reason for x in result.attempts],
            "text": result.final_text,
            "event_types": [e["type"] for e in events],
        }
        spent += result.cost_usd
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k not in ("text", "event_types", "usage")},
                         ensure_ascii=False))
        print("   ", result.final_text[:160].replace("\n", " "), "…\n")

    out = Path(args.out) if args.out else out_default
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"date": f"{date.today()}", "model": model.name, "spent_usd": round(spent, 4),
                               "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"合计花费 ${spent:.4f}（按 llm.PRICING 折算）· 写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
