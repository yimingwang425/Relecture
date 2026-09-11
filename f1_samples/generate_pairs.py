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
import re
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import _paths  # noqa: F401

from a_rules.config import CONFIG_VERSION
from a_rules.evaluator import evaluate
from explain.context import ContextPack, ContextSlice
from explain.llm import AnthropicModel
from explain.prompt import build_system as production_build_system
from explain.prompt import build_user_turn as production_build_user_turn
from explain.style_prompt import render_all_style_rules, render_style_rules
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


#: 提问模板。概念名随课件变——课件换了那个名词必须跟着换，这是不可消除的混杂。
QUESTION_TEMPLATE = "请给我讲解这份课件里的 {concept}。"
CONCEPT = "Bloom filter"
COURSE_LABEL = "合成课件"

#: 课件在 prompt 里的位置：**走生产的 user turn，不进 system**。
#:
#: ⚠️ 这一段是 2026-09-09 改的，起因是「发现 A」那次误判（见 README §三）。
#: 原先我自拼了一份 system 模板，把 2500 字英文课件放最前面、风格要求垫在最后。
#: 生产的 `explain.prompt.SYSTEM_TEMPLATE` 恰恰相反——风格要求紧跟角色行，
#: 课件片段走每轮的 user turn。实测这不是细微偏差，是**不同的输出分布**：
#: P3 在自拼模板下 1/19 达标，在生产装置下 2/4，扫描实验落盘数据 33/54。
#: 用自拼模板产出的样例做 onboarding，用户选了 P3 拿到的是另一种东西——
#: **F18 事实上没成立**。
#:
#: 仍然不执行检索（任务书指定）：整份课件包成**一个 `ContextSlice`**
#: 交给生产的 `build_system` / `build_user_turn`，检索一次都不跑，
#: 但 prompt 形状是生产的。


#: 课件里的块。`<!-- pXbY · FED -->` 标记的才喂进 user turn。
BLOCK_RE = re.compile(
    r"<!-- (p(\d+)b\d+)( · FED)? -->\n(.*?)(?=\n\n<!--|\n\n---|\Z)", re.S
)
#: 生产实测：每轮恒为 5 片。数量对不上要炸，不要静默少喂——
#: 少一片会改变整个输出分布，而它不会报错（通则六）。
EXPECTED_FED = 5


def fed_blocks() -> list[tuple[str, int, str]]:
    """→ [(块 id, 页号, 正文)]，只取 FED 的那几块。

    按**块 id**取，不靠行号或正则去猜内容边界：课件是人写的，
    行号会随任何一次编辑漂掉，而 id 是显式标注的。
    """
    text = DECK.read_text(encoding="utf-8")
    body = text.split("-->", 1)[1] if text.lstrip().startswith("<!--") else text
    out = [
        (bid, int(page), blk.strip())
        for bid, page, fed, blk in BLOCK_RE.findall(body)
        if fed
    ]
    if len(out) != EXPECTED_FED:
        raise SystemExit(
            f"课件里标了 {len(out)} 个 FED 块，应为 {EXPECTED_FED} 个："
            f"{[b for b, _, _ in out]}。少喂或多喂都会改变输出分布，不静默继续。"
        )
    return out


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def context_pack(blocks: list[tuple[str, int, str]]) -> ContextPack:
    """5 个 FED 块 = 5 个切片。**不执行检索**（口径 F28：我在扮演检索器）。

    规模按实测对齐：真实每轮恒 5 片、单片字符中位 271（范围 60–433）、
    每轮合计四分位 1128–1498。**规模像不等于质量像**——真实检索会带回不相关片、
    会漏关键片，这 5 片一种都没有，偏差方向单向（样例比真实情况好看）。
    """
    return ContextPack(
        question="",
        slices=tuple(
            ContextSlice(
                source_file="synthetic_deck.md", slice_id=bid, page_indices=(page,),
                title=None, text=txt, char_count=len(txt), score=None, via="hit",
            )
            for bid, page, txt in blocks
        ),
    )


def build_prompt(blocks: list[tuple[str, int, str]],
                 style: dict[str, str]) -> tuple[str, str, str, str]:
    """→ (system, user, A 类片段, B 类片段)。**system 与 user 都由生产函数产出。**

    A 类与 B 类**都走生产渲染**，拼接也走生产的 `render_all_style_rules`
    （2026-09-11：B 类已从本目录搬进 `explain/style_prompt.py`，口径 F45）。

    ⚠️ **拼法不在这里写第二遍**。样例是用户据以选择的东西；生产改了拼法而这里没改
    （或反过来），没有任何东西会报错，只是用户按样例选完拿到的不是他选的那个。
    `a_class` / `b_class` 仍单独算一份，那是**写进 pairs.json 的元数据**
    （核对时要能看见当时那两段原文），不是第二处拼接。
    """
    a_class = render_style_rules(style_config_for(style["detail_level"]))
    b_class = render_b_class_rules(style)
    pack = context_pack(blocks)
    system = production_build_system(
        render_all_style_rules(style_config_for(style["detail_level"]), style),
        pack,
        COURSE_LABEL,
    )
    user = production_build_user_turn(pack, QUESTION_TEMPLATE.format(concept=CONCEPT))
    return system, user, a_class, b_class


#: 单个样例最多试几次。用尽仍不过就**留最后一次**并标记，不留「最好的那次」。
MAX_TRIES = 6


def generate_one(model: AnthropicModel, blocks: list, sample_id: str, variant: str,
                 style: dict[str, str], attempt: int) -> dict:
    """发一次请求。花费当场记流水——**包括之后会被丢弃的那些次**。"""
    system, user, a_class, b_class = build_prompt(blocks, style)
    messages = [{"role": "user", "content": user}]
    cfg = style_config_for(style["detail_level"])

    completion = model.complete(system, messages)
    ev = evaluate(completion.text, cfg)

    with SPEND_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sample_id": sample_id, "attempt": attempt,
            "model": completion.model, "cost_usd": round(completion.cost, 6),
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
            "production_passed": ev.all_activated_passed,
            "mean_paragraph_chars": round(ev.by_name("paragraph_length").raw, 1),
        }, ensure_ascii=False) + "\n")

    return {
        "sample_id": sample_id,
        "variant_label": variant,
        "style": describe(style),
        "style_config": cfg.to_dict(),
        "config_fingerprint": cfg.fingerprint(),
        "metric_set_id": cfg.metric_set_id(),
        "prompt": {
            "shape": "生产：explain.prompt.build_system / build_user_turn（口径 F19）",
            "a_class_rules": a_class,
            "b_class_rules": b_class,
            "system_sha256": sha256(system),
            "system_chars": len(system),
            "user_sha256": sha256(user),
            "request_signature": model.request_signature(system, messages),
        },
        "text": completion.text,
        "production_passed": ev.all_activated_passed,
        "mean_paragraph_chars": round(ev.by_name("paragraph_length").raw, 1),
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


def generate_until_pass(model: AnthropicModel, blocks: list, sample_id: str, variant: str,
                        style: dict[str, str], base_attempt: int) -> dict:
    """**通过即停。留下的是第一个通过生产判定的样例，不是试出来的最好的那个。**

    「生成一批再挑」会把 F01 变成「选一个好看的」，而 F18 要的是**代表性**——
    用户真会拿到的输出长什么样。所以：一次一次地生成，不通过就丢弃、不比较、
    不记「差多少」，通过就停。用尽 `MAX_TRIES` 仍不过，**留最后一次**
    （不是最接近的那次）并标 `production_passed: false`，让核对红出来。

    每一次尝试都进 `spend_log.jsonl`——被丢弃的那些次照样花了钱（口径 F22）。
    """
    rec = None
    for k in range(1, MAX_TRIES + 1):
        rec = generate_one(model, blocks, sample_id, variant, style, base_attempt + k)
        rec["tries_in_run"] = k
        mark = "✓" if rec["production_passed"] else "✗"
        print(f"      第 {k} 次 {rec['mean_paragraph_chars']:>5.1f} 字/段 {mark}", flush=True)
        if rec["production_passed"]:
            return rec
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", default="", help="只生成这几对（逗号分隔）；缺省为全部八对")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发的 prompt，不调模型")
    args = ap.parse_args()

    wanted = {p.strip() for p in args.pairs.split(",") if p.strip()}
    known = {p[0] for p in PAIRS}
    if wanted - known:
        raise SystemExit(f"未知 pair_id：{sorted(wanted - known)}；可选 {sorted(known)}")

    blocks = fed_blocks()
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
        system, user, _, _ = build_prompt(blocks, {**DEFAULTS})
        print("=== system ===\n" + system)
        print("\n=== user ===\n" + user)
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
            base = prior.get(sample_id, {}).get("attempt", 0)
            style = {**DEFAULTS, axis: value}
            print(f"  {sample_id}（{axis}={value}）", flush=True)
            samples.append(generate_until_pass(model, blocks, sample_id, value, style, base))
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
            "sha256": sha256(DECK.read_text(encoding="utf-8")),
            "fed_blocks": [
                {"id": b, "page": pg, "chars": len(t)} for b, pg, t in blocks
            ],
            "fed_chars_total": sum(len(t) for _, _, t in blocks),
            "fed_note": (
                "5 块由人工选定，非检索产出（口径 F28）。规模对齐实测："
                "真实每轮恒 5 片、单片中位 271 字符、每轮合计四分位 1128–1498"
            ),
            "note": "自写合成内容，非第三方课件（PRD §12.1）",
        },
        "model": {
            # 取自 explain.llm.AnthropicModel 的默认值 —— 生产侧的唯一出处。
            "id": model.name,
            "params": model.params,
            "source": "explain.llm.AnthropicModel() 的默认值（口径 F19）",
        },
        "a_rules_config_version": CONFIG_VERSION,
        "question": QUESTION_TEMPLATE.format(concept=CONCEPT),
        "prompt_shape": {
            "source": "explain.prompt.build_system / build_user_turn（口径 F19）",
            "note": "整份课件包成一个 ContextSlice 走 user turn；不执行检索",
            "course_label": COURSE_LABEL,
        },
        "selection_rule": (
            "通过即停：留下的是第一个通过生产判定的样例，不是试出来的最好的那个"
            "（口径 F27）。用尽 MAX_TRIES 仍不过则留最后一次并标 production_passed:false"
        ),
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
