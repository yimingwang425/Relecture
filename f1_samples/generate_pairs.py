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
from explain.prompt import SYSTEM_TEMPLATE_VERSION
from explain.prompt import build_system as production_build_system
from explain.prompt import build_user_turn as production_build_user_turn
from explain.style_prompt import render_all_style_rules, render_style_rules
from style_axes import DEFAULTS, PAIRS, B_CLASS_FRAGMENTS, describe, render_b_class_rules, style_config_for
# 成对判据的**唯一出处**是核对脚本（口径 F13）。生成脚本自己再实现一遍隶属集合，
# 就会出现 2026-09-19 那次的样子：生成侧判「这个样例自己达标」就停，核对侧判的却是
# 「它与同伴的关系」——两边各判各的，于是重掷成了盲掷。这里直接 import 它的 measure()。
from verify_pairs import measure as verify_measure

HERE = Path(__file__).resolve().parent
DECK = HERE / "synthetic_deck.md"
OUT = HERE / "pairs.json"
#: **每一次真实调用**的花费流水，含被丢弃的重生成。
#:
#: pairs.json 里的 `cost_usd_total` 只是**存活的那 16 个样例**的合计——
#: 核对不过重生成时，被丢掉的那次照样花了钱。只报文件里那个数，
#: 会把「16 个样例的成本」说成「产出这批样例的成本」，两者不是一回事。
SPEND_LOG = HERE / "spend_log.jsonl"


#: **每对一个问题、一组块**（2026-09-19）。
#:
#: 此前 8 对共用一个问题、共用同样的 5 块，于是 16 个样例讲的是同一件事——
#: 用户在 6 步里连读 12 段几乎一样的内容，**选了跟没选没区别**。
#: F01 要的归因（他选的是这一维上的差异）建立在「他读得出差异」之上，
#: 内容雷同时那个前提不成立。
#:
#: 块少 → 输出短。**不写「请简短」这类指令**：那会改变 prompt 形状，
#: 而 F19 要求样例的 prompt 形状与生产逐字节同形。
PAIR_CONTEXT: dict[str, tuple[str, tuple[str, ...]]] = {
    # ---- 详细程度那三对：喂 3 块，样例 600–900 字符 ----
    #
    # 这条轴量的是**段落粒度**（P0 30–120 / P2 25–55 / P3 15–40 字每段），
    # 而平均段长在短文本上是个**噪声很大的统计量**：一段 250 字的回答只有三五段，
    # 均值随便跨档。2026-09-19 实测：喂 1–2 块时 P3 连续 12 次落在 44–69，
    # P2 连续 18 次落在 60–88 —— 不是运气差，是**没有体量就没有粒度可言**。
    #
    # 所以样例长度按**这条轴需要多少才看得出差异**来定，不搞一刀切：
    # 详细程度的三对长一些（3 块），其余五对短（1–2 块）。
    # 2026-09-19 换题两次，第二次的理由推翻了第一次的一半：
    #
    # 原问「特征值是什么？它和特征向量是什么关系？」是**下定义**，答案天然成段论述，
    # P3（每段 15–40 字）连续 6 次落在 43–69。改成可枚举的「会出现哪几种情况」后
    # 仍然 6 次落在 49–60 —— 段长分布 [46, 28, 53, 23, **121**, 54] 说明了原因：
    # **可枚举的提问会写成 bullet 清单，而段落口径 D09 把连续列表项并成一段**
    # （逐条计段会把均值人为拉低）。其余五段本来都在区间内，是那一个并起来的块拉高的。
    # 所以要的不是「可枚举」，是「**不会写成清单**的逐句推进」。
    # 对比式提问（「乘普通向量和乘特征向量差别在哪」）试过 13 次，落在 41.8–76.0，
    # 成段论述又回来了。第三次照**已经到过 P3 的那个问题的形状**来
    # （detail_p2_p3.b 的 37.4，同一天、同一版 prompt 下生成——所以 P3 在 s3 下可达，
    # 不是规则把它堵死了）：「这几个**具体例子**各是多少，为什么」，模型一例一段、
    # 每段带一句为什么，段落自然短又不成清单。内容换一条轴问（问特征向量是哪些方向，
    # 不问特征值是多少），免得三对详细程度的题面读起来是同一道。
    # **判据一个数没动**。换题意味着这一对两个样例都要重掷（同对必须同上下文，
    # 见 verify_pairs.check_same_context）。
    "detail_p0_p3": ("反射、整体放大两倍、把一个方向压到原点，"
                     "这三种变换各自的特征向量是哪些方向，为什么？",
                     ("p1b1", "p2b1", "p3b1")),
    # ⚠️ 还有一条与体量同样重要：**问题的形态决定答案的形态**。
    # 问「为什么只是缩放」，答案天然是成段论述；问「这三种情况各是多少」，
    # 答案天然是分条短句 —— P2 正是靠换成后者才第一次够到 25–55。
    # **判据一个数没动**，动的是提问与喂料。
    "detail_p0_p2": ("镜面反射、整体放大两倍、特征值为 0，这三种情况的特征值各是多少？",
                     ("p1b1", "p2b1", "p3b1")),
    "detail_p2_p3": ("这些例子里的特征值分别是多少，为什么？", ("p2b1", "p3b1", "p4b1")),
    "examples_more_fewer": ("特征值在实际问题里用来做什么？", ("p7b1",)),
    "structure_conclusion_buildup": ("反复用同一个矩阵作用下去会发生什么？", ("p4b1",)),
    "depth_what_derivation": ("对称矩阵的特征值有什么特别？", ("p5b2",)),
    "depth_what_why": ("特征值等于 0 意味着什么？", ("p2b1", "p3b1")),
    "depth_why_derivation": ("为什么最大的那个特征值最后会主导？", ("p4b1",)),
}
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
#: 课件里可供选用的块数（块池）。**对不上要炸**：少一块的表现是某一对悄悄换了内容，
#: 而它不会报错（通则六）。每对喂哪几块由 `PAIR_CONTEXT` 指定，不是全喂。
EXPECTED_FED = 6


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


def blocks_for(pair_id: str, pool: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """这一对要喂的块。**按 id 取**，缺了就炸——静默少喂会让这一对换了内容而没人知道。"""
    want = PAIR_CONTEXT[pair_id][1]
    by_id = {b: (b, pg, txt) for b, pg, txt in pool}
    missing = [b for b in want if b not in by_id]
    if missing:
        raise SystemExit(f"{pair_id} 要的块不在池里：{missing}；池里有 {sorted(by_id)}")
    return [by_id[b] for b in want]


def build_prompt(blocks: list[tuple[str, int, str]],
                 style: dict[str, str], question: str) -> tuple[str, str, str, str]:
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
    user = production_build_user_turn(pack, question)
    return system, user, a_class, b_class


#: 单个样例最多试几次。用尽仍不过就**留最后一次**并标记，不留「最好的那次」。
MAX_TRIES = 6


def generate_one(model: AnthropicModel, blocks: list, sample_id: str, variant: str,
                 style: dict[str, str], attempt: int, question: str) -> dict:
    """发一次请求。花费当场记流水——**包括之后会被丢弃的那些次**。"""
    system, user, a_class, b_class = build_prompt(blocks, style, question)
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


def membership_of(rec: dict) -> frozenset[str]:
    """一个样例落进哪些产品档位。走核对脚本的 `measure()`，不另算一遍。"""
    return verify_measure(rec["text"], rec["style"]["detail_level"]["value"])["membership"]


def pair_relation_ok(rec: dict, partner: dict | None, want_same: bool) -> bool | None:
    """候选样例与**同伴**的关系是否成立；没有同伴可比时返回 None。

    目标轴是详细程度的三对要**不同**（不同才谈得上「用户读得出这一维的差异」），
    其余五对要**相同**（非目标轴上冒出可分辨差异就违反 F01）。这两句的判法
    与 `verify_pairs.check_pair` 同源。
    """
    if partner is None:
        return None
    same = membership_of(rec) == membership_of(partner)
    return same if want_same else not same


def generate_until_pass(model: AnthropicModel, blocks: list, sample_id: str, variant: str,
                        style: dict[str, str], base_attempt: int, question: str,
                        partner: dict | None = None, want_same: bool = False) -> dict:
    """**通过即停。留下的是第一个通过生产判定的样例，不是试出来的最好的那个。**

    「生成一批再挑」会把 F01 变成「选一个好看的」，而 F18 要的是**代表性**——
    用户真会拿到的输出长什么样。所以：一次一次地生成，不通过就丢弃、不比较、
    不记「差多少」，通过就停。用尽 `MAX_TRIES` 仍不过，**留最后一次**
    （不是最接近的那次）并标 `production_passed: false`，让核对红出来。

    「通过」有两条，**缺一不可**（2026-09-19 补上第二条）：

    1. 它自己过生产判定（a_rules 的档位区间）；
    2. 它与同伴的隶属集合关系成立（详细程度对要不同、其余对要相同）。

    只判第一条时，生成侧和核对侧判的不是同一件事：样例次次「达标」，核对次次红在
    成对关系上，重掷因此是盲掷——掷中与否取决于运气，而不是判据。第二条要有同伴
    才判得了；一对两个都重掷时，先掷的那个没有同伴（`partner=None`），只判第一条。

    每一次尝试都进 `spend_log.jsonl`——被丢弃的那些次照样花了钱（口径 F22）。
    """
    rec = None
    for k in range(1, MAX_TRIES + 1):
        rec = generate_one(model, blocks, sample_id, variant, style, base_attempt + k, question)
        rec["tries_in_run"] = k
        rel = pair_relation_ok(rec, partner, want_same)
        mark = "✓" if rec["production_passed"] else "✗"
        rel_mark = "" if rel is None else f"　成对 {'✓' if rel else '✗'} {sorted(membership_of(rec))}"
        print(f"      第 {k} 次 {rec['mean_paragraph_chars']:>5.1f} 字/段 {mark}{rel_mark}",
              flush=True)
        if rec["production_passed"] and rel is not False:
            return rec
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pairs", default="", help="只生成这几对（逗号分隔）；缺省为全部八对")
    # **按样例重生成**：`--pairs` 会把一对里两个样例都重掷，包括已经通过的那个——
    # 那既多花一次钱，又可能把一个通过的样例换成不通过的（2026-09-19 实测：
    # P2 反复不达标而同对的 P3 已通过，用 --pairs 重跑会把 P3 也重掷）。
    ap.add_argument("--samples", default="",
                    help="只重生成这几个样例（逗号分隔的 sample_id，如 detail_p0_p2.b）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发的 prompt，不调模型")
    args = ap.parse_args()

    wanted = {p.strip() for p in args.pairs.split(",") if p.strip()}
    known = {p[0] for p in PAIRS}
    if wanted - known:
        raise SystemExit(f"未知 pair_id：{sorted(wanted - known)}；可选 {sorted(known)}")

    wanted_samples = {s.strip() for s in args.samples.split(",") if s.strip()}
    known_samples = {f"{p[0]}.{suf}" for p in PAIRS for suf in ("a", "b")}
    if wanted_samples - known_samples:
        raise SystemExit(f"未知 sample_id：{sorted(wanted_samples - known_samples)}")
    if wanted_samples and wanted:
        raise SystemExit("--pairs 与 --samples 二选一，同时给会说不清到底重掷了哪些")

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
        pid = sorted(PAIR_CONTEXT)[0]
        system, user, _, _ = build_prompt(
            blocks_for(pid, blocks), {**DEFAULTS}, PAIR_CONTEXT[pid][0])
        print("=== system ===\n" + system)
        print("\n=== user ===\n" + user)
        return 0

    pairs_out = []
    total_cost = 0.0
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    for pair_id, axis, value_a, value_b in PAIRS:
        question, _ = PAIR_CONTEXT[pair_id]
        pair_blocks = blocks_for(pair_id, blocks)
        samples = []

        def will_regenerate(sample_id: str) -> bool:
            return (sample_id in wanted_samples if wanted_samples
                    else (not wanted or pair_id in wanted))

        # 非详细程度的对要求隶属集合**相同**，详细程度的三对要求**不同**。
        want_same = axis != "detail_level"
        for suffix, value in (("a", value_a), ("b", value_b)):
            sample_id = f"{pair_id}.{suffix}"
            if not will_regenerate(sample_id) and sample_id in prior:
                samples.append(prior[sample_id])
                continue
            # 同伴：b 的同伴永远是已定下来的 a（刚生成或沿用）；a 的同伴只有在
            # b 这次不重掷时才存在——两个都重掷时先掷的那个没有可比对象。
            other_id = f"{pair_id}.{'b' if suffix == 'a' else 'a'}"
            partner = (samples[0] if suffix == "b"
                       else (None if will_regenerate(other_id) else prior.get(other_id)))
            base = prior.get(sample_id, {}).get("attempt", 0)
            style = {**DEFAULTS, axis: value}
            rel = ("无同伴可比，只判自身达标" if partner is None
                   else f"与 {other_id} 的隶属集合须{'相同' if want_same else '不同'}")
            print(f"  {sample_id}（{axis}={value}）　{rel}", flush=True)
            samples.append(generate_until_pass(
                model, pair_blocks, sample_id, value, style, base, question,
                partner=partner, want_same=want_same))
        pairs_out.append({
            "pair_id": pair_id, "target_dimension": axis,
            # 每对自己的问题与块：读样例的人要能知道这两段在回答什么。
            "question": question,
            "fed_blocks": [{"id": b, "page": pg, "chars": len(txt)} for b, pg, txt in pair_blocks],
            "samples": samples,
        })

    reused = [s["sample_id"] for pair in pairs_out for s in pair["samples"] if s.get("reused_from")]
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
        # 顶层不再有单一问题：每对一个（见各 pair 的 `question`）。
        "questions": {pid: q for pid, (q, _) in PAIR_CONTEXT.items()},
        "prompt_shape": {
            "source": "explain.prompt.build_system / build_user_turn（口径 F19）",
            "note": "每对 1–2 块走 user turn；不执行检索",
            # system 骨架的版本。**不记的话，改了 system 而样例没重生成这件事是静默的**
            # （`explain/tests/test_style_axes.py` 有一条断言比对它与生产当前值）。
            "system_template_version": SYSTEM_TEMPLATE_VERSION,
            "course_label": COURSE_LABEL,
        },
        "selection_rule": (
            "通过即停：留下的是第一个**同时**通过生产判定与成对关系判据的样例，"
            "不是试出来的最好的那个（口径 F27）。成对关系判据与 verify_pairs.py 同源"
            "（口径 F13）。用尽 MAX_TRIES 仍不过则留最后一次并标 production_passed:false"
        ),
        "defaults": DEFAULTS,
        "b_class_prompt_fragments": B_CLASS_FRAGMENTS,
        "cost_usd_total": round(total_cost, 6),
        # 复用的样例（`reused_from`）记 0：那次调用的钱记在源样例上，两边都记会把总额说大。
        # 说明由脚本自己拼——手工补的话，下一次重生成又会把它覆盖掉（2026-09-19 踩到）。
        "cost_usd_total_note": (
            "只含存活的 16 个样例。核对不过被丢弃的重生成同样花了钱，"
            "全部真实调用的流水在 spend_log.jsonl"
            + (
                f"。其中 {len(reused)} 个是**复用**的（{'、'.join(reused)}），"
                "它们的 cost_usd / usage 记 0，真实花费在各自的源样例上，见该样例的 reuse_note"
                if reused else ""
            )
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
