#!/usr/bin/env python3
"""**混合检索的逐环节收益，在本地跑生产那一套代码**（检索评测集 v2，2026-10-15）。

    cd api
    # 1. 建索引：真的走 `ingest_document`（Haiku 定位语 + OpenAI 嵌入 + 词条），存成快照，之后每次评测读同一份
    env ANTHROPIC_API_KEY=… OPENAI_API_KEY=… .venv/bin/python scripts/eval_retrieval_local.py index
    # 2. 跑五组对照（每题每组一次检索；改写与嵌入按题缓存，各组共用）
    env ANTHROPIC_API_KEY=… OPENAI_API_KEY=… .venv/bin/python scripts/eval_retrieval_local.py run --set v2
    # 3. 汇总（不花钱）
    .venv/bin/python scripts/eval_retrieval_local.py report --set v2

## 与线上（`eval_retrieval_prod.py`）哪里一样、哪里不一样

**一样**：检索就是生产的 `api.retriever.SupabaseRetriever`——改写、两路检索、RRF、重排、回落、预算 B=5，
一行没换；摄入就是生产的 `api.ingest.ingest_document`——组文本、定位语、嵌入文本、词条，一行没换；
模型就是生产的那几个（`build_rag_model` 的 Haiku、`retrieval.selection` 的嵌入）。

**换掉的只有存储**（`LocalStore`），四个检索方法照迁移原文实现：
- 向量一路：精确余弦，`order by 距离, document_id, slice_id`（迁移 `20261007100100`；线上也没有 ANN 索引，
  `20260911100100` 里 hnsw 那一行是注释掉的，所以线上同样是精确余弦）。
- 词法一路：**真的 Postgres**（PGlite，`supabase/lexical_local.mjs`），与 `match_slices_lexical` 同一句 SQL，
  `ts_rank_cd` 逐字相同——不在 Python 里仿一个「差不多」的打分。
- 没有 RLS：本地只有一个人的一门课。RLS 管的是「谁看得见」，不改排序。

## 对照组（同 `eval_retrieval_prod.py`，多一组 C4）

| 组 | 关掉 | 回答什么 |
| --- | --- | --- |
| C0 | 改写 + 词法 + 重排 | 2026-10-07 之前（只有向量一路） |
| C1 | 词法 + 重排 | + 改写（单轮题上与 C0 结构性相同，只在多轮题上有区别） |
| C2 | 重排 | + 词法（RRF） |
| C3 | 无 | 全开（生产） |
| C4 | 词法 | 全开但不要词法——词法一路在完整流程里有没有贡献（F103 的触发条件） |

**改写与查询嵌入按题缓存、各组共用**：同一题在 C1–C4 拿到的是同一次改写、同一个查询向量。
不缓存的话，组间差异里会混进改写的随机性，配对比较就不干净了。重排没法共用（候选不同）。

判据与统计口径见预登记 `retrieval/runs/eval_hybrid_v2_prereg.md`。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import api  # noqa: F401,E402  —— 注入 explain / retrieval / slicer / parse_service 路径

RUNS = REPO / "retrieval" / "runs"
INDEX = RUNS / "local_index_v2.json"
CORPUS = REPO / "实验脚本" / "测试集"
FILES = ("MIT22_081JF10_lec01b.pdf", "MIT11_201F07_slides2.pdf")
EVAL_SETS = {
    "v2": REPO / "实验脚本" / "检索评测集" / "v2" / "评测集.json",
    "v1": REPO / "实验脚本" / "检索评测集" / "评测集.json",
}
TOKEN = "local"
OWNER = "00000000-0000-4000-8000-000000000001"
COURSE = "00000000-0000-4000-8000-0000000000c0"

GROUPS: dict[str, frozenset[str]] = {
    "C0": frozenset({"rewrite", "lexical", "rerank"}),
    "C1": frozenset({"lexical", "rerank"}),
    "C2": frozenset({"rerank"}),
    "C3": frozenset(),
    "C4": frozenset({"lexical"}),
}


def out_path(name: str, fake: bool = False) -> Path:
    return RUNS / f"eval_hybrid_{name}_local{'_FAKE' if fake else ''}.json"


def index_path(fake: bool) -> Path:
    return INDEX.with_name("local_index_v2_FAKE.json") if fake else INDEX


def _embedder(fake: bool) -> Any:
    """`--fake`：确定性词袋向量、不调任何模型——**只验证链路，不产出任何结论**，文件名带 `_FAKE`。"""
    from retrieval.selection import SELECTED_DIMENSIONS, SELECTED_MODEL

    if fake:
        from retrieval.embed import FakeEmbedder

        return FakeEmbedder(SELECTED_DIMENSIONS)
    from retrieval.embed import OpenAIEmbedder

    return OpenAIEmbedder(SELECTED_MODEL, SELECTED_DIMENSIONS)


# ---------------------------------------------------------------- 存储


class LexicalEngine:
    """`supabase/lexical_local.mjs` 的一个常驻子进程。一问一答，加锁串行。"""

    def __init__(self) -> None:
        self._p = subprocess.Popen(["node", "lexical_local.mjs"], cwd=REPO / "supabase", text=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=1)
        self._lock = threading.Lock()

    def call(self, msg: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            assert self._p.stdin and self._p.stdout
            self._p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            out = json.loads(self._p.stdout.readline())
        if "error" in out:
            raise RuntimeError(f"PGlite：{out['error']}")
        return out

    def close(self) -> None:
        if self._p.stdin:
            self._p.stdin.close()
        self._p.wait(timeout=10)


class LocalStore:
    """`SupabaseClient` 里摄入与检索用到的那几个方法。行的形状与真库返回的一致。"""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        snap = snapshot or {"documents": [], "slices": [], "embeddings": [], "lexicon": []}
        self.documents: list[dict[str, Any]] = snap["documents"]
        self.slices: list[dict[str, Any]] = snap["slices"]
        self.embeddings: list[dict[str, Any]] = snap["embeddings"]
        self.lexicon: list[dict[str, Any]] = snap["lexicon"]
        self._lex: LexicalEngine | None = None
        self._vec: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {"documents": self.documents, "slices": self.slices,
                "embeddings": self.embeddings, "lexicon": self.lexicon}

    # ---- 摄入写入
    def insert_document(self, *, token: str, row: dict[str, Any]) -> dict[str, Any]:
        # 确定性的 id：同一份文件每次建索引拿到同一个 id，并列时的 `order by document_id` 才可复现
        doc = {**row, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, row["content_sha256"]))}
        self.documents.append(doc)
        return doc

    def insert_slices(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        self.slices.extend(rows)

    def insert_embeddings(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        self.embeddings.extend(rows)

    def insert_lexicon(self, *, token: str, rows: list[dict[str, Any]]) -> None:
        self.lexicon.extend(rows)

    # ---- 检索读取
    def documents_in_course(self, token: str, course_id: str) -> list[dict[str, Any]]:
        cols = ("id", "filename", "verdict", "unusable_pages", "page_count", "index_ruleset", "vision_pages")
        return [{k: d.get(k) for k in cols} for d in self.documents if d["course_id"] == course_id]

    def _vectors(self):
        import numpy as np

        if self._vec is None:
            rules = {d["id"]: d["index_ruleset"] for d in self.documents}
            rows = [e for e in self.embeddings if e["embed_ruleset"] == rules.get(e["document_id"])]
            mat = np.array([json.loads(e["embedding"]) for e in rows], dtype=np.float64)
            mat /= np.linalg.norm(mat, axis=1, keepdims=True)
            self._vec = (rows, mat)
        return self._vec

    def match_slices_indexed(self, *, token: str, course_id: str, query: list[float],
                             model: str, limit: int) -> list[dict[str, Any]]:
        import numpy as np

        rows, mat = self._vectors()
        q = np.asarray(query, dtype=np.float64)
        sims = mat @ (q / np.linalg.norm(q))
        keep = [i for i, e in enumerate(rows) if e["model"] == model]
        # `order by e.embedding <=> p_query, e.document_id, e.slice_id`：距离升序 = 相似度降序
        keep.sort(key=lambda i: (-sims[i], rows[i]["document_id"], rows[i]["slice_id"]))
        return [{"document_id": rows[i]["document_id"], "slice_id": rows[i]["slice_id"],
                 "similarity": float(sims[i])} for i in keep[:limit]]

    def match_slices_lexical(self, *, token: str, course_id: str, query: str,
                             limit: int) -> list[dict[str, Any]]:
        if self._lex is None:
            rules = {d["id"]: d["index_ruleset"] for d in self.documents}
            self._lex = LexicalEngine()
            self._lex.call({"op": "load", "rows": [
                {"document_id": r["document_id"], "slice_id": r["slice_id"], "lexical_text": r["lexical_text"]}
                for r in self.lexicon if r["ruleset"] == rules.get(r["document_id"])]})
        return self._lex.call({"op": "query", "query": query, "limit": limit})["rows"]

    def slices_in_documents(self, token: str, document_ids: list[str]) -> list[dict[str, Any]]:
        cols = ("document_id", "slice_id", "ordinal", "page_indices", "title", "text", "char_count")
        want = set(document_ids)
        return [{k: s.get(k) for k in cols} for s in self.slices if s["document_id"] in want]

    def close(self) -> None:
        if self._lex is not None:
            self._lex.close()


# ---------------------------------------------------------------- 建索引


def cmd_index(args: Any) -> int:
    from explain.rag import Contextualizer, build_rag_model

    from api.ingest import ingest_document

    path = index_path(args.fake)
    if path.exists():
        raise SystemExit(f"{path} 已存在。索引是评测的一部分，冻结后不覆盖；要重建先手动删掉它。")
    store = LocalStore()
    # 与 main.py 上传路由同一份配置
    contextualizer = None if args.fake else Contextualizer(build_rag_model(2000, cache_system=True))
    cost = 0.0
    for name in FILES:
        r = ingest_document(
            data=(CORPUS / name).read_bytes(), filename=name, owner=OWNER, course_id=COURSE, token=TOKEN,
            store=store, embedder=_embedder(args.fake), contextualizer=contextualizer, vision=None)
        cost += r.llm_cost_usd or 0.0
        print(f"  {name}：{r.slice_count} 片，定位语 {r.contextualized_count} 片，口径 {r.index_ruleset}，"
              f"跳过页 {r.skipped_pages}")
    path.write_text(json.dumps({"built": str(date.today()), "llm_cost_usd": cost, **store.snapshot()},
                               ensure_ascii=False), "utf-8")
    print(f"定位语花费 ${cost:.4f} → {path}")
    return 0


# ---------------------------------------------------------------- 跑


class MemoRewriter:
    """同一题（问题 + 前文）只改写一次，C1–C4 共用。"""

    def __init__(self, inner: Any) -> None:
        self.inner, self._memo, self._lock = inner, {}, threading.Lock()

    def rewrite(self, question: str, history: list[dict[str, Any]] | None = None):
        key = json.dumps([question, history or []], ensure_ascii=False)
        with self._lock:
            ev = self._memo.get(key)
            if ev is None:
                ev = self._memo[key] = threading.Event(), [None]
                owner = True
            else:
                owner = False
        if owner:
            ev[1][0] = self.inner.rewrite(question, history)
            ev[0].set()
        ev[0].wait()
        return ev[1][0]


class MemoEmbedder:
    """同一句查询只嵌入一次，各组共用同一个向量。"""

    def __init__(self, inner: Any) -> None:
        self.inner, self._memo, self._lock = inner, {}, threading.Lock()
        self.dimensions = inner.dimensions

    def embed(self, texts: list[str]):
        out = []
        for t in texts:
            with self._lock:
                v = self._memo.get(t)
            if v is None:
                v = [float(x) for x in self.inner.embed([t])[0]]
                with self._lock:
                    self._memo[t] = v
            out.append(v)
        return out


def load_items(name: str) -> list[dict[str, Any]]:
    items = json.loads(EVAL_SETS[name].read_text("utf-8"))["items"]
    # v1 有 6 题的答案页在 14.129 / 15.316 上，这两份不入本地索引（同 eval_retrieval_prod.py），跳过并如实报
    return [it for it in items if all(g["source_file"] in FILES for g in it["gold_pages"])]


def cmd_run(args: Any) -> int:
    from explain.rag import LlmReranker, QueryRewriter, build_rag_model

    from api.retriever import SupabaseRetriever

    store = LocalStore(json.loads(index_path(args.fake).read_text("utf-8")))
    items = load_items(args.set)
    path = out_path(args.set, args.fake)
    done = json.loads(path.read_text("utf-8")) if path.exists() else {"set": args.set, "results": {}}
    # 与 main._course_retriever 同一份配置。--fake 时没有改写与重排（检索器按「原问题 + 融合排名」回落）
    rewriter = None if args.fake else MemoRewriter(QueryRewriter(build_rag_model(400)))
    reranker = None if args.fake else LlmReranker(build_rag_model(200))
    embedder = MemoEmbedder(_embedder(args.fake))
    lock = threading.Lock()

    def one(it: dict[str, Any]) -> None:
        for g, ablate in GROUPS.items():
            key = f"{it['id']}|{g}"
            if key in done["results"]:
                continue
            r = SupabaseRetriever(store=store, embedder=embedder, token=TOKEN, course_id=COURSE,
                                  history=it.get("history") or [], rewriter=rewriter, reranker=reranker,
                                  ablate=ablate)
            t = time.monotonic()
            pack = r.fetch(it["question"])
            row = {
                "id": it["id"], "group": g,
                "pages": [[s.source_file, p] for s in pack.slices for p in s.page_indices],
                "scores": [s.score for s in pack.slices],
                "rewrite": pack.meta["rewrite"], "rerank": pack.meta["rerank"],
                "channels": pack.meta["channels"], "llm_cost_usd": pack.meta["llm_cost_usd"],
                "wall_ms": round((time.monotonic() - t) * 1000),
            }
            with lock:
                done["results"][key] = row

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for k, _ in enumerate(ex.map(one, items), 1):
            if k % 10 == 0 or k == len(items):
                with lock:
                    path.write_text(json.dumps(done, ensure_ascii=False, indent=1), "utf-8")
                print(f"  {k}/{len(items)} 题")
    store.close()
    print(f"→ {path}")
    return 0


# ---------------------------------------------------------------- 统计


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def mcnemar_exact(b: int, c: int) -> float:
    """双侧精确 McNemar：不一致的 b + c 对里，「b 这么偏」的二项概率。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def cluster_bootstrap(diffs: list[tuple[str, int]], reps: int = 10000, seed: int = 20261015) -> tuple[float, float]:
    """按**答案页**分簇的 bootstrap（同一页上的题相关，不能当独立样本重抽）。`diffs` 是 (簇, 差值)。"""
    by: dict[str, list[int]] = {}
    for cl, d in diffs:
        by.setdefault(cl, []).append(d)
    clusters = list(by.values())
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        pick = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        n = sum(len(c) for c in pick)
        stats.append(sum(sum(c) for c in pick) / n)
    stats.sort()
    return (stats[int(0.025 * reps)], stats[int(0.975 * reps) - 1])


def hit(it: dict[str, Any], row: dict[str, Any]) -> tuple[int, float]:
    got = {(f, p) for f, p in row["pages"]}
    gold = {(g["source_file"], g["page_index"]) for g in it["gold_pages"]}
    inter = len(gold & got)
    return int(inter == len(gold)), inter / len(gold)


def cmd_report(args: Any) -> int:
    items = load_items(args.set)
    if args.alt_gold:
        # 敏感性分析（预登记补记）：两遍答案页不一致的题改用第二遍的答案页；第二遍为空的移出有答案题
        items = [{**it, "gold_pages": it["gold_pages_alt"]} if "gold_pages_alt" in it else it for it in items]
    res = json.loads(out_path(args.set, args.fake).read_text("utf-8"))["results"]
    ans = [it for it in items if it["gold_pages"]]
    cluster = lambda it: f"{it['gold_pages'][0]['source_file']}#{it['gold_pages'][0]['page_index']}"  # noqa: E731
    H = {(it["id"], g): hit(it, res[f"{it['id']}|{g}"]) for it in ans for g in GROUPS if f"{it['id']}|{g}" in res}

    f = lambda k, n: f"{k}/{n}（{100 * k / n:.0f}%）" if n else "—"  # noqa: E731
    lines = [f"# 混合检索 · 本地 · 评测集 {args.set} · 原始汇总" + ("（敏感性：第二遍答案页）" if args.alt_gold else ""), "",
             f"有答案 {len(ans)} 题（无答案 {len(items) - len(ans)} 题不进命中率）。", "",
             "## full-hit@5（正确页全部进前 5）", "",
             "| 组 | 命中 | 95% CI（Wilson） | page-recall@5 |", "| --- | --- | --- | --- |"]
    for g in GROUPS:
        hs = [H[(it["id"], g)] for it in ans if (it["id"], g) in H]
        k = sum(h[0] for h in hs)
        lo, hi = wilson(k, len(hs))
        rec = sum(h[1] for h in hs) / len(hs) if hs else math.nan
        lines.append(f"| {g} | {f(k, len(hs))} | {100 * lo:.0f}–{100 * hi:.0f}% | {rec:.3f} |")

    # 一题多 tag 时在**每个** tag 下各计一次（R15），所以各类之和可能大于题数，不得相加
    cats = sorted({t for it in ans for t in (it.get("tags") or ["?"])})
    lines += ["", "## 按类（各类**不相加**，R15）", "", "| 类 | n | " + " | ".join(GROUPS) + " |",
              "| --- | --- | " + " | ".join("---" for _ in GROUPS) + " |"]
    for c in cats:
        sub = [it for it in ans if c in (it.get("tags") or ["?"])]
        cells = [f"{sum(H[(it['id'], g)][0] for it in sub if (it['id'], g) in H)}" for g in GROUPS]
        lines.append(f"| {c} | {len(sub)} | " + " | ".join(cells) + " |")

    def paired(a: str, b: str, sub: list[dict[str, Any]], label: str) -> str:
        pairs = [(it, H[(it["id"], a)][0], H[(it["id"], b)][0]) for it in sub
                 if (it["id"], a) in H and (it["id"], b) in H]
        if not pairs:
            return f"| {label} | 0 | — | — | — | — |"
        gain = sum(1 for _, x, y in pairs if y and not x)
        loss = sum(1 for _, x, y in pairs if x and not y)
        p = mcnemar_exact(gain, loss)
        lo, hi = cluster_bootstrap([(cluster(it), y - x) for it, x, y in pairs])
        diff = (sum(y for _, _, y in pairs) - sum(x for _, x, _ in pairs)) / len(pairs)
        return (f"| {label} | {len(pairs)} | +{gain} / −{loss} | {100 * diff:+.1f} 点 "
                f"| {100 * lo:+.1f} ~ {100 * hi:+.1f} | {p:.4f} |")

    lines += ["", "## 配对比较（同一批题；不一致的题才有信息）", "",
              "| 比较 | n | 多中 / 少中 | 差 | 95% CI（按答案页分簇 bootstrap） | McNemar 精确 p |",
              "| --- | --- | --- | --- | --- | --- |",
              paired("C0", "C3", ans, "**主：C0 → C3（全开 vs 只有向量）**"),
              paired("C0", "C1", [it for it in ans if it.get("history")], "改写：C0 → C1（只看多轮题）"),
              paired("C1", "C2", ans, "词法：C1 → C2"),
              paired("C2", "C3", ans, "重排：C2 → C3"),
              paired("C4", "C3", ans, "完整流程里的词法：C4 → C3")]

    rows = list(res.values())
    fall_rw = sum(1 for r in rows if r["rewrite"].get("implementation") in ("error", "invalid"))
    fall_rr = sum(1 for r in rows if r["rerank"].get("implementation") in ("error", "invalid"))
    cost = sum(r["llm_cost_usd"] or 0 for r in rows)
    lex_used = sum(1 for r in rows if r["group"] in ("C2", "C3")
                   for fnl in r["channels"]["final"] if fnl["lexical_rank"] is not None and fnl["vector_rank"] is None)
    lines += ["", f"- 改写回落（error / invalid）：{fall_rw} 次；重排回落：{fall_rr} 次（按行计，回落照常计入成绩）",
              f"- C2 / C3 最终片里**只有词法一路命中**的片：{lex_used} 片次",
              f"- 改写 + 重排模型花费（各组之和，改写按题共用只计一次以上）：${cost:.4f}"]
    noans = [it for it in items if not it["gold_pages"]]
    if noans:
        top = lambda g: [max([s for s in res[f"{it['id']}|{g}"]["scores"] if s is not None], default=0.0)  # noqa: E731
                         for it in noans if f"{it['id']}|{g}" in res]
        ansv = [max([s for s in res[f"{it['id']}|C0"]["scores"] if s is not None], default=0.0)
                for it in ans if f"{it['id']}|C0" in res]
        lines += [f"- 无答案题 C0 top-1 相似度：中位 {sorted(top('C0'))[len(top('C0')) // 2]:.3f}；"
                  f"有答案题中位 {sorted(ansv)[len(ansv) // 2]:.3f}（只报分布，不定阈值，同 R16）"]
    out = RUNS / f"eval_hybrid_{args.set}_local{'_FAKE' if args.fake else ''}{'_altgold' if args.alt_gold else ''}_report_raw.md"
    out.write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ix = sub.add_parser("index")
    r = sub.add_parser("run")
    r.add_argument("--set", choices=EVAL_SETS, default="v2")
    r.add_argument("--workers", type=int, default=6)
    rp = sub.add_parser("report")
    rp.add_argument("--set", choices=EVAL_SETS, default="v2")
    rp.add_argument("--alt-gold", action="store_true", help="敏感性分析：不一致的题用第二遍的答案页")
    for p in (ix, r, rp):
        p.add_argument("--fake", action="store_true", help="假向量、不调模型：只验证链路，不产出结论")
    args = ap.parse_args()
    return {"index": cmd_index, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
