#!/usr/bin/env python3
"""**混合检索的收益，在真库、真模型上测**（口径 F96–F99 的测量手段）。

    cd api && .venv/bin/python scripts/eval_retrieval_prod.py \
        --base https://<service>.onrender.com \
        --supabase-url https://<ref>.supabase.co --publishable-key sb_publishable_... \
        --email <验收账号> --password <密码> \
        --course-id <装着 22.081J 与 11.201 两份讲义的课> [--reindex-between]

## 为什么需要它

2026-10-07 那一轮把检索换成了「改写 → 向量 + 词法 → RRF → 重排」，外加入库时的定位语。
**四个环节的召回收益一个都没测过**（那一轮没有 API key）。在测之前能说的只有
「它们接上了、失败时会回落」，**不能说「检索变好了」**——那是两句话。

## 对照组：逐环节关掉

| 组 | 关掉 | 等于什么 |
| --- | --- | --- |
| C0 | 改写 + 词法 + 重排 | 2026-10-07 之前的检索（只剩向量一路） |
| C1 | 词法 + 重排 | + 改写 |
| C2 | 重排 | + 改写 + 词法（RRF） |
| C3 | 无 | 全开 |

定位语是**索引**层面的，查询时关不掉：`--reindex-between` 先在老索引上跑四组，
调 `/courses/{id}/reindex` 升级之后再跑四组。课程里的文档必须是**老口径**上传的
（2026-10-07 之前），否则两轮是同一个索引。

## 判据：R17

**单格差异一律不算证据，无论大小。** 一个环节的收益要在多个独立条件下同向重复——
这里的条件是 tag 分组（synonym / multipage / …）与两轮索引。脚本只报数，
不替你下结论；结论写进 `docs/F2/`，并回填 `f1_samples/README.md` 的 F96–F99。

## 花多少钱

每题每组：一次嵌入 + 至多两次 Haiku（改写、重排）。30 题 × 4 组 × 2 轮 ≈ 240 次检索，
量级 $0.5。**不生成讲解**——那一项每题约 $0.3，是这里的 60 倍。

## 只能用 22.081J 与 11.201

同 `ingest_real.py` 的理由：14.129 有 8 页第三方保留权利的内容，传上去就等于
把它们发给 OpenAI（以及现在的 Anthropic 看图转写）。15.316 是整份扫描件。
评测集里 gold 页落在别的文件上的题自动跳过，跳过多少题如实打印。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))
import api  # noqa: F401,E402  —— 注入 explain / a_rules / retrieval 路径

from ingest_real import call  # noqa: E402  —— 同一个 HTTP 小工具，不再抄一份
from prod_verify import wake_service  # noqa: E402
from retrieval.metrics import score_question, summarize  # noqa: E402

CONDITIONS: dict[str, list[str]] = {
    "C0 只有向量": ["rewrite", "lexical", "rerank"],
    "C1 +改写": ["lexical", "rerank"],
    "C2 +改写+词法": ["rerank"],
    "C3 全开": [],
    # 2026-10-07 第一次实测后加：C3 在两轮都是满分，而 C1→C2（加词法）在新索引上是 −1 题。
    # 只有这一组能回答「完整流程里词法一路到底有没有贡献」——C2 没有重排，C3 有词法，
    # 两者之差混着两个变量。
    "C4 全开但关词法": ["lexical"],
}

#: ⚠️ **C1 与 C0 在单轮题上结构性相同**：没有历史时向量一路嵌入的是原问题（改写只产出
#: 关键词，而关键词只给词法一路用），C1 又关了词法。所以评测集全是单轮题时 C1 == C0
#: **不是「改写没用」**，是这份评测集测不到改写——改写的价值在多轮追问的指代消解。

#: 结构上恒为 0 的 tag（gold 页是图片页，老索引里不产生切片）。
#: 新索引带看图转写时它**可能不再是 0**——那正是 F100 要测的东西，所以只标注不剔除。
ZERO_CEILING = frozenset({"unreadable_page"})


def run_round(base: str, token: str, course_id: str, questions: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, ablate in CONDITIONS.items():
        results = []
        for q in questions:
            s, body = call(f"{base}/courses/{course_id}/retrieve", method="POST", token=token,
                           payload={"question": q["question"], "ablate": ablate})
            if s != 200 or not isinstance(body, dict):
                raise SystemExit(f"/retrieve 失败（{name} · {q['id']}）：HTTP {s} {body}")
            slices = body["retrieval"]["slices"]
            retrieved = [(sl["source_file"], p) for sl in slices for p in sl["pages"]]
            gold = [(g["source_file"], g["page_index"]) for g in q["gold_pages"]]
            top = slices[0]["score"] if slices and slices[0]["score"] is not None else None
            results.append(score_question(q["id"], q["tags"], gold, retrieved, top))
        summ = summarize(results, ZERO_CEILING)
        out[name] = {
            t: {"n": v.n, "full_hit": v.full_hits, "page_recall": round(v.page_recall, 3),
                "missed": v.missed, "ceiling_zero": v.ceiling_zero}
            for t, v in sorted(summ.items())
        }
        print(f"\n  \033[1m{name}\033[0m（关掉：{', '.join(ablate) or '无'}）")
        if name.startswith("C1"):
            print("    \033[2m（单轮题上与 C0 结构性相同：改写只影响有历史时的嵌入与词法一路的关键词）\033[0m")
        for t, v in out[name].items():
            note = "  ← 老索引上结构性为 0" if v["ceiling_zero"] else ""
            print(f"    {t:<16} n={v['n']:<3} full-hit {v['full_hit']:<3} "
                  f"page-recall {v['page_recall']:.3f}  未命中 {v['missed']}{note}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--supabase-url", required=True)
    ap.add_argument("--publishable-key", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--course-id", required=True)
    ap.add_argument("--reindex-between", action="store_true",
                    help="先在老索引上跑，再调 /reindex 升级后重跑（测定位语）")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    if not wake_service(base):
        return 1
    s, body = call(f"{args.supabase_url.rstrip('/')}/auth/v1/token?grant_type=password",
                   method="POST", apikey=args.publishable_key,
                   payload={"email": args.email, "password": args.password})
    if s != 200 or not isinstance(body, dict) or "access_token" not in body:
        print(f"登录失败：HTTP {s} {body}")
        return 1
    token = body["access_token"]

    evalset = json.loads((REPO / "实验脚本" / "检索评测集" / "评测集.json").read_text("utf-8"))
    # 先空跑一题：确认这门课检索得通（老库没跑迁移时这里就是 404 / 400）。
    s, probe = call(f"{base}/courses/{args.course_id}/retrieve", method="POST", token=token,
                    payload={"question": "probe", "ablate": ["rewrite", "lexical", "rerank"]})
    if s != 200:
        print(f"这门课检索不了：HTTP {s} {probe}")
        return 1
    labels = {sl["source_file"] for sl in probe["retrieval"]["slices"]}
    s, all_docs = call(f"{args.supabase_url.rstrip('/')}/rest/v1/documents"
                       f"?select=filename,index_ruleset&course_id=eq.{args.course_id}",
                       token=token, apikey=args.publishable_key)
    files = {d["filename"] for d in (all_docs or [])} | labels
    keep = [q for q in evalset["items"]
            if all(g["source_file"] in files for g in q["gold_pages"])]
    print(f"\n课程里的文件：{sorted(files)}")
    print(f"评测集 {len(evalset['items'])} 题，gold 全在这门课里的 {len(keep)} 题"
          f"（跳过 {len(evalset['items']) - len(keep)} 题）")
    rulesets = sorted({d.get("index_ruleset") for d in (all_docs or [])})
    print(f"这门课的索引口径：{rulesets}")

    report: dict[str, object] = {"date": date.today().isoformat(), "course_id": args.course_id,
                                 "questions": len(keep), "rulesets_before": rulesets}
    print("\n\033[1m第一轮\033[0m")
    report["round1"] = run_round(base, token, args.course_id, keep)

    if args.reindex_between:
        s, rb = call(f"{base}/courses/{args.course_id}/reindex", method="POST", token=token,
                     timeout=900)
        print(f"\n/reindex → HTTP {s}：{json.dumps(rb, ensure_ascii=False)[:400]}")
        if s != 200:
            return 1
        report["reindex"] = rb
        print("\n\033[1m第二轮（定位语 + 词条已补齐）\033[0m")
        report["round2"] = run_round(base, token, args.course_id, keep)

    out = REPO / "retrieval" / "runs" / f"eval_hybrid_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n结果写进 {out.relative_to(REPO)}。\n"
          "\033[33m判据是 R17：单格差异不算证据，要在多个 tag / 两轮索引上同向重复。\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
