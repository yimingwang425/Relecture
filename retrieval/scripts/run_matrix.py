"""跑 型号 × 文本变体 × 扩展模式 的矩阵，输出按 tag 分组的报告。

用法::

    # 假向量跑通全链路（无需 API key），并验证可复现
    .venv/bin/python -m scripts.run_matrix --fake
    .venv/bin/python -m scripts.run_matrix --fake --check-determinism

    # 真实模型（key 放本目录 .env，局部注入不要全局 export）
    env $(grep -v '^#' .env | xargs) .venv/bin/python -m scripts.run_matrix
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrieval.compose import VARIANTS, compose_all  # noqa: E402
from retrieval.corpus import build_items, load_eval, load_slicesets  # noqa: E402
from retrieval.embed import (  # noqa: E402
    EmbeddingCache,
    FakeEmbedder,
    OpenAIEmbedder,
    PlantedEmbedder,
    embed_texts,
)
from retrieval.index import VectorIndex  # noqa: E402
from retrieval.metrics import score_question, separability, summarize  # noqa: E402
from retrieval.report import (  # noqa: E402
    FOOTER,
    ZERO_CEILING_TAGS,
    format_run,
    format_separability,
    variant_note,
)
from retrieval.search import retrieve, retrieved_pages  # noqa: E402

CACHE = Path(__file__).resolve().parents[1] / "cache"

MODELS_REAL = [
    ("M1", "text-embedding-3-small", None),
    ("M2", "text-embedding-3-large", None),
    ("M3", "text-embedding-3-large", 1024),
    ("M4", "text-embedding-3-small", 512),
]


def make_embedder(kind: str, model: str, dims: int | None, questions=None, items=None):
    if kind == "planted":
        gold = {
            q["id"]: [(g["source_file"], _slice_of(items, g)) for g in q["gold_pages"]]
            for q in questions
        }
        return PlantedEmbedder({k: [g for g in v if g[1]] for k, v in gold.items()},
                               dimensions=dims or 256)
    if kind == "fake":
        return FakeEmbedder(dimensions=dims or 256)
    return OpenAIEmbedder(model, dims)


def _slice_of(items, gold_page):
    """gold 页对应的切片 id；页不产生切片（image_only）时返回 None。"""
    for it in items:
        if it.source_file == gold_page["source_file"] and gold_page["page_index"] in it.page_indices:
            return it.slice_id
    return None


def run_one(embedder, cache, slicesets, items, questions, variant, mode, budget, tau_short):
    texts = compose_all(slicesets, variant)
    keys = [(it.source_file, it.slice_id) for it in items]
    if isinstance(embedder, PlantedEmbedder):
        # 构造向量需要知道每条文本对应哪个切片 / 哪道题
        for k in keys:
            embedder.register_slice(texts[k], k)
        for q in questions:
            embedder.register_question(q["question"], q["id"])
    vectors = embed_texts(embedder, [texts[k] for k in keys], cache)
    index = VectorIndex(items, vectors)

    qvecs = embed_texts(embedder, [q["question"] for q in questions], cache)

    results = []
    for q, qv in zip(questions, qvecs):
        got = retrieve(index, qv, budget=budget, mode=mode, tau_short=tau_short)
        gold = [(g["source_file"], g["page_index"]) for g in q["gold_pages"]]
        top = next((r.score for r in got if r.score is not None), None)
        results.append(
            score_question(q["id"], q["tags"], gold, retrieved_pages(got), top)
        )
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="词袋哈希假向量，跑通链路，不调 API")
    ap.add_argument(
        "--format-sample",
        action="store_true",
        help="构造向量出格式样张：数字由种入的信号强度决定，**不是测量结果**",
    )
    ap.add_argument("--check-determinism", action="store_true", help="跑两遍并比对报告是否逐字节相同")
    ap.add_argument("--budget", type=int, default=5)
    ap.add_argument("--variants", default="T0,T1,T2,T3")
    ap.add_argument("--modes", default="none,always,short_only")
    ap.add_argument("--tau-short", type=int, default=150, help="short_only 的阈值（占位待定）")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    slicesets = load_slicesets()
    items = build_items(slicesets)
    questions = load_eval()
    cache = None if args.no_cache else EmbeddingCache(CACHE)

    kind = "planted" if args.format_sample else ("fake" if args.fake else "real")
    models = [("S1", "sample", None)] if kind == "planted" else (
        [("F1", "fake", None)] if kind == "fake" else MODELS_REAL
    )
    variants = [v for v in args.variants.split(",") if v in VARIANTS]
    modes = args.modes.split(",")

    out: list[str] = []
    banner = {
        "planted": "⚠️ 格式样张 · 向量为构造，数字由种入的信号强度直接决定，**不是测量结果**",
        "fake": "假向量（词袋哈希）· 仅验证链路可复现，语料英文/问题中文零重合故指标恒为 0",
        "real": "真实模型",
    }[kind]
    out.append("=" * 78)
    out.append(f"检索评测矩阵 · {banner}")
    out.append(f"语料：{len(items)} 个切片（已排除 15.316 整份与 14.129 的 8 页受限内容）")
    out.append(f"评测集：{len(questions)} 题 · 预算 B={args.budget}")
    out.append("")
    out.append(variant_note(kind))
    out.append("=" * 78)

    sep_holder = None
    for tag, model, dims in models:
        embedder = make_embedder(kind, model, dims, questions, items)
        for variant in variants:
            for mode in modes:
                results = run_one(
                    embedder, cache, slicesets, items, questions,
                    variant, mode, args.budget, args.tau_short,
                )
                summaries = summarize(results, ZERO_CEILING_TAGS)
                note = f"τ_short={args.tau_short}（占位待定）" if mode == "short_only" else None
                out.append("")
                out.append(
                    format_run(
                        f"[{tag} {embedder.name}] 文本 {variant} · 扩展 {mode} · B={args.budget}",
                        summaries, args.budget, note,
                    )
                )
                if sep_holder is None:
                    sep_holder = separability(results)

    if sep_holder is not None:
        out.append("")
        out.append("=" * 78)
        out.append(format_separability(sep_holder))
        if args.format_sample:
            from retrieval.metrics import Separability

            # 总是展示上面**没有出现**的那一支，两种形状一次看全
            if sep_holder.separable:
                demo = Separability(
                    with_gold=(0.42, 0.38, 0.35, 0.31, 0.28, 0.24),
                    no_answer=(0.40, 0.36, 0.29, 0.26, 0.22),
                )
                label = "若两组**重叠**"
            else:
                demo = Separability(
                    with_gold=(0.52, 0.48, 0.45, 0.41, 0.38, 0.34),
                    no_answer=(0.21, 0.18, 0.15, 0.12, 0.09),
                )
                label = "若两组**可分**"
            out.append("")
            out.append("-" * 78)
            out.append(f"【另一分支的样式 · 构造示例】{label}，同一位置会输出：")
            out.append("")
            out.append(format_separability(demo))

    out.append("")
    out.append("=" * 78)
    out.append(FOOTER)
    if cache is not None:
        out.append("")
        out.append(f"缓存：命中 {cache.hits} · 未命中 {cache.misses}")

    report = "\n".join(out)

    if args.check_determinism:
        # 清空计数后重跑一遍，比对报告是否逐字节相同
        cache2 = None if args.no_cache else EmbeddingCache(CACHE)
        out2: list[str] = []
        for tag, model, dims in models:
            embedder = make_embedder(kind, model, dims, questions, items)
            for variant in variants:
                for mode in modes:
                    results = run_one(
                        embedder, cache2, slicesets, items, questions,
                        variant, mode, args.budget, args.tau_short,
                    )
                    summaries = summarize(results, ZERO_CEILING_TAGS)
                    note = f"τ_short={args.tau_short}（占位待定）" if mode == "short_only" else None
                    out2.append(
                        format_run(
                            f"[{tag} {embedder.name}] 文本 {variant} · 扩展 {mode} · B={args.budget}",
                            summaries, args.budget, note,
                        )
                    )
        first = "\n".join(o for o in out if o.startswith("[") or o.startswith("  "))
        second = "\n".join(o for o in out2 if o.startswith("[") or o.startswith("  "))
        same = first == second
        print(report)
        print()
        print("=" * 78)
        print(f"可复现性检查：两次运行的分组表{'逐字节相同 ✓' if same else '不一致 ✗'}")
        return 0 if same else 1

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
