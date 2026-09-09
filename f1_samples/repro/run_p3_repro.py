"""发现 A 的单变量复现：**只换课件，看 P3 达不达标**。

    cd f1_samples && env $(grep -v '^#' ../实验脚本/配置扫描实验/.env | xargs) \
      ../实验脚本/A类规则计算脚本/.venv/bin/python repro/run_p3_repro.py

## 为什么要复现而不是直接判死 P3

配置扫描实验里 P3 达标过（否则算不出 E10 的「72% 来自分段」这个比例），
而主实验里 11 次 0 达标。**两件事同时为真**，说明差异藏在课件 / 提问 /
轮次结构里的某一处。在不知道是哪一处之前改产品档位，等于用一个未知变量的
结论做决策。

## 单变量：只有课件不同

| 保持不变 | 值 |
| --- | --- |
| 模型与请求形状 | `explain.llm.AnthropicModel()` 的默认值（口径 F19） |
| system 模板 | `generate_pairs.SYSTEM_TEMPLATE`，同一份 |
| 风格配置 | `style_config_for("P3")`，同一份 |
| 提问模板 | `请给我讲解这份课件里的 {concept}。` 字面不变 |

**同场跑 deck 1 作对照。** 已有的 11 个 P3 样本是本阶段早些时候生成的，
拿「deck 2 现在」比「deck 1 当时」，时间（模型侧的任何变化）也是个变量。
两份课件在同一次运行里交替跑，才真的只剩课件一个自变量。

## 一处不可消除的混杂，如实登记

提问里必然带概念名。课件换了，那个名词就得跟着换
（`Bloom filter` → `Nyquist sampling`）。**这不能被隔离掉，只能被说清**：
严格地说，本次比较的自变量是「课件 + 提问里的概念名」，不是「课件」本身。
两个概念名都是课件中心术语的英文名词短语，长度与形态相当，
但这仍是一个残留差异。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import _paths  # noqa: F401,E402

from a_rules.evaluator import evaluate  # noqa: E402
from explain.llm import AnthropicModel  # noqa: E402
from generate_pairs import SPEND_LOG, SYSTEM_TEMPLATE, build_system, deck_body  # noqa: E402
from style_axes import style_config_for  # noqa: E402

N_PER_DECK = 4
QUESTION_TEMPLATE = "请给我讲解这份课件里的 {concept}。"

DECKS = {
    "deck1_bloom": {
        "path": HERE.parent / "synthetic_deck.md",
        "concept": "Bloom filter",
        "role": "对照（主实验用的那一份）",
    },
    "deck2_nyquist": {
        "path": HERE / "synthetic_deck_2.md",
        "concept": "Nyquist sampling",
        "role": "复现材料（换主题，同规格）",
    },
}

P3_STYLE = {"detail_level": "P3", "use_examples": "more",
            "structure": "conclusion_first", "depth": "why"}


def body_of(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return (text.split("-->", 1)[1] if text.lstrip().startswith("<!--") else text).strip()


def main() -> int:
    model = AnthropicModel()
    cfg = style_config_for("P3")
    rng = cfg.paragraph_length
    print(f"模型 {model.name} · P3 目标区间 {rng.describe()} · 每份课件 {N_PER_DECK} 次\n")

    runs: list[dict] = []
    # 交替跑，不是先跑完一份再跑另一份——把「运行顺序」也摊平
    for i in range(N_PER_DECK):
        for deck_id, meta in DECKS.items():
            deck = body_of(meta["path"])
            system, _, _ = build_system(deck, P3_STYLE)
            # build_system 用的是主实验那份模板；课件由参数带入
            question = QUESTION_TEMPLATE.format(concept=meta["concept"])
            messages = [{"role": "user", "content": question}]

            c = model.complete(system, messages)
            ev = evaluate(c.text, cfg)
            para = ev.by_name("paragraph_length")
            ok = ev.all_activated_passed

            with SPEND_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "sample_id": f"repro/{deck_id}#{i+1}", "attempt": i + 1,
                    "model": c.model, "cost_usd": round(c.cost, 6),
                    "input_tokens": c.usage.input_tokens,
                    "output_tokens": c.usage.output_tokens,
                }, ensure_ascii=False) + "\n")

            runs.append({
                "deck": deck_id, "run": i + 1,
                "mean_paragraph_chars": round(para.raw, 1),
                "paragraph_count": para.details["count"],
                "total_chars": para.details["total_chars"],
                "production_passed": ok,
                "cost_usd": round(c.cost, 6),
                "text": c.text,
            })
            print(f"  {deck_id:<14} 第 {i+1} 次： {para.raw:5.1f} 字/段  "
                  f"{para.details['count']:>2} 段 / {para.details['total_chars']:>4} 字  "
                  f"{'✓ 达标' if ok else '✗ 不达标'}")

    print()
    summary = {}
    for deck_id, meta in DECKS.items():
        rs = [r for r in runs if r["deck"] == deck_id]
        hits = sum(1 for r in rs if r["production_passed"])
        means = [r["mean_paragraph_chars"] for r in rs]
        summary[deck_id] = {
            "role": meta["role"], "concept": meta["concept"],
            "n": len(rs), "passed": hits,
            "mean_range": [min(means), max(means)],
        }
        print(f"  {deck_id:<14} {hits}/{len(rs)} 达标   范围 {min(means)}–{max(means)} 字/段")

    out = {
        "purpose": "发现 A 的单变量复现：只换课件，看 P3 达不达标",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": {"id": model.name, "params": model.params},
        "target_range": rng.describe(),
        "question_template": QUESTION_TEMPLATE,
        "known_confound": (
            "提问里的概念名必须随课件改变（Bloom filter → Nyquist sampling）。"
            "严格地说自变量是「课件 + 概念名」，不是「课件」。不可消除，如实登记。"
        ),
        "not_in_pairs_json": True,
        "summary": summary,
        "runs": runs,
        "cost_usd_total": round(sum(r["cost_usd"] for r in runs), 6),
    }
    (HERE / "p3_repro_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写入 repro/p3_repro_results.json，本次花费 ${out['cost_usd_total']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
