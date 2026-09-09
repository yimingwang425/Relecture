"""发现 A 的第二次单变量测试：**只换 prompt 装置，不换课件**。

    cd f1_samples && env $(grep -v '^#' ../实验脚本/配置扫描实验/.env | xargs) \
      ../实验脚本/A类规则计算脚本/.venv/bin/python repro/run_p3_harness_test.py

## 为什么要有这一次

扫描实验的落盘数据显示：P3 **首次尝试**（未经重生成）达标 **33/54**，
字/段中位数 36.5，同模型同参数。而我的样例 19 次 0 达标、范围 42.6–60.4。
**换课件（deck 2）也是 0/4**——所以课件不是那个变量。

差异在我自己的装置里。生产的 `explain.prompt.SYSTEM_TEMPLATE` 把风格要求放在
**角色行之后立刻**，课件片段走每轮的 user turn；而我自拼的模板把 2500 字课件
放在最前面、风格要求垫在最后。本次只换这一个东西。

不走检索仍然成立：把整份课件包成**一个 `ContextSlice`** 交给生产的
`build_system` / `build_user_turn`，检索一次都不执行，但 prompt 形状是生产的。
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
from explain.context import ContextPack, ContextSlice  # noqa: E402
from explain.llm import AnthropicModel  # noqa: E402
from explain.prompt import build_system, build_user_turn  # noqa: E402
from explain.style_prompt import render_style_rules  # noqa: E402
from generate_pairs import SPEND_LOG, build_system as my_build_system  # noqa: E402
from style_axes import render_b_class_rules, style_config_for  # noqa: E402
from run_p3_repro import DECKS, P3_STYLE, QUESTION_TEMPLATE, body_of  # noqa: E402

N = 4


def production_prompt(deck: str, concept: str, style: dict) -> tuple[str, str]:
    """生产的 prompt 形状。整份课件当作一个切片，不执行检索。"""
    cfg = style_config_for(style["detail_level"])
    # A 类走生产渲染；B 类仍是 f1_samples 自己拼的那三句（约束 1：不扩展 style_prompt）
    rules = render_style_rules(cfg) + "\n" + render_b_class_rules(style)
    pack = ContextPack(
        question="",
        slices=(ContextSlice(
            source_file="synthetic_deck.md", slice_id="whole", page_indices=(1,),
            title=None, text=deck, char_count=len(deck), score=None, via="hit",
        ),),
    )
    system = build_system(rules, pack, course_label="合成课件")
    user = build_user_turn(pack, QUESTION_TEMPLATE.format(concept=concept))
    return system, user


def main() -> int:
    model = AnthropicModel()
    cfg = style_config_for("P3")
    print(f"P3 目标 {cfg.paragraph_length.describe()} · 每种装置 {N} 次 · 课件固定为 deck1\n")

    meta = DECKS["deck1_bloom"]
    deck = body_of(meta["path"])
    runs = []

    for i in range(N):
        for harness in ("mine", "production"):
            if harness == "mine":
                system, _, _ = my_build_system(deck, P3_STYLE)
                user = QUESTION_TEMPLATE.format(concept=meta["concept"])
            else:
                system, user = production_prompt(deck, meta["concept"], P3_STYLE)

            c = model.complete(system, [{"role": "user", "content": user}])
            ev = evaluate(c.text, cfg)
            para = ev.by_name("paragraph_length")
            ok = ev.all_activated_passed

            with SPEND_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "sample_id": f"repro/harness-{harness}#{i+1}", "attempt": i + 1,
                    "model": c.model, "cost_usd": round(c.cost, 6),
                    "input_tokens": c.usage.input_tokens,
                    "output_tokens": c.usage.output_tokens,
                }, ensure_ascii=False) + "\n")

            runs.append({"harness": harness, "run": i + 1,
                         "mean_paragraph_chars": round(para.raw, 1),
                         "paragraph_count": para.details["count"],
                         "total_chars": para.details["total_chars"],
                         "production_passed": ok, "cost_usd": round(c.cost, 6)})
            print(f"  {harness:<11} 第 {i+1} 次： {para.raw:5.1f} 字/段  "
                  f"{para.details['count']:>2} 段 / {para.details['total_chars']:>4} 字  "
                  f"{'✓ 达标' if ok else '✗ 不达标'}")

    print()
    summary = {}
    for h in ("mine", "production"):
        rs = [r for r in runs if r["harness"] == h]
        means = [r["mean_paragraph_chars"] for r in rs]
        hits = sum(1 for r in rs if r["production_passed"])
        summary[h] = {"n": len(rs), "passed": hits, "mean_range": [min(means), max(means)]}
        print(f"  {h:<11} {hits}/{len(rs)} 达标   范围 {min(means)}–{max(means)} 字/段")

    out = {
        "purpose": "只换 prompt 装置（自拼 vs 生产 build_system），课件固定 deck1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": {"id": model.name, "params": model.params},
        "target_range": cfg.paragraph_length.describe(),
        "scan_baseline": "扫描实验 P3 首次尝试 33/54 达标，中位数 36.5 字/段",
        "not_in_pairs_json": True,
        "summary": summary, "runs": runs,
        "cost_usd_total": round(sum(r["cost_usd"] for r in runs), 6),
    }
    (HERE / "p3_harness_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写入 repro/p3_harness_results.json，本次花费 ${out['cost_usd_total']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
