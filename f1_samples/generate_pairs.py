"""生成 8 对 16 个样例 → pairs.json。

    env $(grep -v '^#' ../实验脚本/配置扫描实验/.env | xargs) \
      ../实验脚本/A类规则计算脚本/.venv/bin/python generate_pairs.py
    # 只重生成某几对（核对不过时用）：--pairs detail_p0_p1,depth_what_why

**凭据局部注入，不要全局 export**——全局设置会让同环境下的 Claude Code
从订阅计费切到 API 按量计费（沿用 漂移实验/README.md 的既有约定）。

## 口径 F18：样例必须由模型真实生成

手写会得到「理想中的 P3」，而用户拿到的是「模型在 P3 下实际产出的」。两者不同，
用户按样例选完拿到的就不是他选的那个——F1 的整个前提垮掉。E10 已实测 P3 达标
72% 来自分段，手写样例不会有这个问题而真实输出会有。

## F18 的连带约束：模型与请求形状都要和生产一致

任务书只写了「模型真实生成」。但**用另一个模型生成，F18 的前提同样垮掉**——
用户选到的仍然不是他真会得到的东西。所以本脚本直接用 `explain.llm.AnthropicModel`
的默认值，而不是自己拼一个 API 调用：模型 id、effort、thinking、max_tokens
全部取生产侧的唯一出处，且那个类**真的被执行到**（通则七 #7：替身用顺手了，
就会忘记问「真实实现被哪条断言碰过」）。

## 生成不过核对时重新生成，不修改样例文本

F01 的使用纪律。16 个样例的生成成本一次性，不构成放宽判据的理由。
每次重生成会把该样例的 `attempt` 加一，历史尝试次数留在文件里。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import _paths  # noqa: F401

from a_rules.config import CONFIG_VERSION
from explain.llm import AnthropicModel
from explain.style_prompt import render_style_rules
from style_axes import DEFAULTS, PAIRS, B_CLASS_FRAGMENTS, describe, render_b_class_rules, style_config_for

HERE = Path(__file__).resolve().parent
DECK = HERE / "synthetic_deck.md"
OUT = HERE / "pairs.json"
#: **每一次真实调用**的花费流水，含被丢弃的重生成。
#:
#: pairs.json 里的 `cost_usd_total` 只是**存活的那 16 个样例**的合计——
#: 核对不过重生成时，被丢掉的那次照样花了钱。只报文件里那个数，
#: 会把「16 个样例的成本」说成「产出这批样例的成本」，两者不是一回事。
SPEND_LOG = HERE / "spend_log.jsonl"

#: 固定提问。**必须对 16 个样例完全相同**，否则差异就不只来自风格配置了
#: （通则二：处理组多做的任何动作都可能在无关维度引入偏差）。
#: 措辞刻意中性——若问「误判率是怎么来的」，`what` 那档会被问题本身逼着做推导，
#: 深度轴的差异就成了问题的差异而不是配置的差异。
QUESTION = "请给我讲解这份课件里的 Bloom filter。"

#: system 模板。`{deck}` / `{a_class}` / `{b_class}` 三个槽。
#:
#: **这不是生产的 system prompt。** 生产侧 `explain.prompt.build_system` 还含
#: 检索片段清单、X06 的「片段可能不完整，缺什么就说缺什么」、X07 的跳过页事实，
#: 三者都依赖 ContextPack。本阶段输入是课件全文、不走检索（任务书指定），
#: 那三段没有对应物。**代价已登记**（README §三）：样例展示的是风格差异，
#: 不代表带检索时的真实行为。
SYSTEM_TEMPLATE = """你是一位课件讲解助手，帮助学生理解他上传的课程材料。

下面是课件全文。只讲课件里写了的内容；课件没写的，说没写，不要补全。

<courseware>
{deck}
</courseware>

## 风格要求

{a_class}
{b_class}
"""


def deck_body() -> str:
    """课件正文。剥掉文件头那段 HTML 注释——它是给人看的元信息，不该进 prompt。"""
    text = DECK.read_text(encoding="utf-8")
    body = text.split("-->", 1)[1] if text.lstrip().startswith("<!--") else text
    return body.strip()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_system(deck: str, style: dict[str, str]) -> tuple[str, str, str]:
    """→ (完整 system, A 类片段, B 类片段)。"""
    a_class = render_style_rules(style_config_for(style["detail_level"]))
    b_class = render_b_class_rules(style)
    return SYSTEM_TEMPLATE.format(deck=deck, a_class=a_class, b_class=b_class), a_class, b_class


def generate_one(model: AnthropicModel, deck: str, sample_id: str, variant: str,
                 style: dict[str, str], attempt: int) -> dict:
    system, a_class, b_class = build_system(deck, style)
    messages = [{"role": "user", "content": QUESTION}]
    cfg = style_config_for(style["detail_level"])

    completion = model.complete(system, messages)

    with SPEND_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample_id": sample_id, "attempt": attempt,
            "model": completion.model, "cost_usd": round(completion.cost, 6),
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
        }, ensure_ascii=False) + "\n")

    return {
        "sample_id": sample_id,
        "variant_label": variant,
        "style": describe(style),
        "style_config": cfg.to_dict(),
        "config_fingerprint": cfg.fingerprint(),
        "metric_set_id": cfg.metric_set_id(),
        "prompt": {
            # 模板存一份在文件顶层，这里只存随样例变化的两段 + 全文 hash。
            # 任务书允许「完整 prompt 或其 hash + 模板」——16 份近乎相同的
            # 全文会把这个文件变成没人读的东西。
            "a_class_rules": a_class,
            "b_class_rules": b_class,
            "system_sha256": sha256(system),
            "system_chars": len(system),
            "request_signature": model.request_signature(system, messages),
        },
        "text": completion.text,
        "usage": {
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
            "cache_creation_input_tokens": completion.usage.cache_creation_input_tokens,
            "cache_read_input_tokens": completion.usage.cache_read_input_tokens,
        },
        "cost_usd": round(completion.cost, 6),
        "latency_ms": round(completion.latency_ms, 1),
        "stop_reason": completion.stop_reason,
        "model_reported": completion.model,
        "attempt": attempt,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", default="", help="只生成这几对（逗号分隔）；缺省为全部八对")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发的 prompt，不调模型")
    args = ap.parse_args()

    wanted = {p.strip() for p in args.pairs.split(",") if p.strip()}
    known = {p[0] for p in PAIRS}
    if wanted - known:
        raise SystemExit(f"未知 pair_id：{sorted(wanted - known)}；可选 {sorted(known)}")

    deck = deck_body()
    model = AnthropicModel()

    existing: dict = {}
    if OUT.is_file():
        existing = json.loads(OUT.read_text(encoding="utf-8"))
    prior = {
        s["sample_id"]: s
        for pair in existing.get("pairs", [])
        for s in pair["samples"]
    }

    if args.dry_run:
        style = {**DEFAULTS}
        system, _, _ = build_system(deck, style)
        print(system)
        print("\n--- user ---\n" + QUESTION)
        return 0

    pairs_out = []
    total_cost = 0.0
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    for pair_id, axis, value_a, value_b in PAIRS:
        samples = []
        for suffix, value in (("a", value_a), ("b", value_b)):
            sample_id = f"{pair_id}.{suffix}"
            regenerate = not wanted or pair_id in wanted
            if not regenerate and sample_id in prior:
                samples.append(prior[sample_id])
                continue
            attempt = prior.get(sample_id, {}).get("attempt", 0) + 1
            style = {**DEFAULTS, axis: value}
            print(f"  生成 {sample_id}（{axis}={value}，第 {attempt} 次）…", flush=True)
            samples.append(generate_one(model, deck, sample_id, value, style, attempt))
        pairs_out.append({"pair_id": pair_id, "target_dimension": axis, "samples": samples})

    for pair in pairs_out:
        for s in pair["samples"]:
            total_cost += s["cost_usd"]
            for k in totals:
                totals[k] += s["usage"][k]

    out = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deck": {
            "path": "synthetic_deck.md",
            "sha256": sha256(deck),
            "note": "自写合成内容，非第三方课件（PRD §12.1）",
        },
        "model": {
            # 取自 explain.llm.AnthropicModel 的默认值 —— 生产侧的唯一出处。
            "id": model.name,
            "params": model.params,
            "source": "explain.llm.AnthropicModel() 的默认值（口径 F19）",
        },
        "a_rules_config_version": CONFIG_VERSION,
        "question": QUESTION,
        "system_template": SYSTEM_TEMPLATE,
        "defaults": DEFAULTS,
        "b_class_prompt_fragments": B_CLASS_FRAGMENTS,
        "cost_usd_total": round(total_cost, 6),
        "cost_usd_total_note": (
            "只含存活的 16 个样例。核对不过被丢弃的重生成同样花了钱，"
            "全部真实调用的流水在 spend_log.jsonl"
        ),
        "usage_total": totals,
        "pairs": pairs_out,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n = sum(len(p["samples"]) for p in pairs_out)
    print(f"\n写入 {OUT.name}：{len(pairs_out)} 对 / {n} 个样例"
          f"；**文件内这 16 个**合计 ${total_cost:.4f}"
          f"（含被丢弃重生成的全部花费见 {SPEND_LOG.name}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
