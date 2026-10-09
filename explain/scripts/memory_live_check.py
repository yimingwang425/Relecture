"""上下文压缩 agent 的**真模型**验收（口径 F116–F118）。要钱：真调 Haiku。

    ANTHROPIC_API_KEY=... api/.venv/bin/python explain/scripts/memory_live_check.py [--fake]

## 测什么

替身证明不了的四件事：

1. 真模型**会不会按需读**：目录里只有回答开头，它会不会去读「没懂」「调整了讲法」的那几轮；
2. `strict: true` 的三个工具、最后一步的强制提交，Haiku 4.5 收不收；
3. 交上来的笔记**能不能过代码校验**，被退回之后会不会改；
4. 第二次整理时，旧笔记里的概念有没有被悄悄丢掉（校验会退回，这里看它是第一次就做对还是靠退回）。

**不测笔记的好坏**：「讲解助手拿着笔记讲得更连贯」要在真会话上多条件比较，n = 2 的场景说明不了（R17）。

## 对话是自己写的

两段场景都是手写的线性代数对话（不含任何第三方课件原文），专门埋了几个点：
第 2 轮学生说没懂、第 4 轮要求多举例、第 5 轮调整了讲法、第 7–8 轮是一个没讲完的推导
（第 7 轮在候选范围末尾、第 8 轮在保留原文那边——看它会不会把第 7 轮留下）。

决策日期：2026-10-09
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "explain"))

import explain  # noqa: E402,F401
from explain.memory import (  # noqa: E402
    MEMORY_CHAR_BUDGET, TOOL_READ, TOOL_WRITE, Concept, MemoryAgent, MemoryNote, Turn,
    apply_memory, build_memory_agent, plan_compaction,
)

QA = [
    ("特征值和特征向量到底是什么？", "P2",
     "把矩阵看成一个变换：大多数向量被它作用之后方向会变，但有少数方向很特别——变换只把它们拉长或压短，"
     "方向不变。这样的方向叫特征向量，拉伸的倍数叫特征值。比如把平面沿 x 轴拉长 2 倍、y 轴不动，"
     "那么 x 轴方向就是特征向量，特征值是 2；y 轴方向也是，特征值是 1。"),
    ("没懂，为什么方向不变就特别？", "P2",
     "换个角度：一般的向量经过变换，既转了方向又变了长度，要描述它得说两件事。特征向量只变长度，"
     "一个数就说清了。这让很多计算变简单——比如把同一个变换做一百次，对特征向量来说就是乘一百次同一个数。"),
    ("哦，那特征值是负数是什么意思？", "P2",
     "负的特征值表示这个方向被翻转了：向量还在同一条直线上，但指向反过来。比如关于 y 轴的镜面反射，"
     "x 轴方向的向量被翻到反面，特征值是 -1；y 轴方向不动，特征值是 1。"),
    ("能多举几个例子吗？我觉得例子比公式好懂", "P2",
     "好。① 旋转 90°：平面上没有方向保持不变，所以没有实数特征值。② 投影到 x 轴：x 轴方向特征值 1，"
     "y 轴方向被压成零，特征值 0。③ 剪切变换：只有 x 轴方向不变，特征值 1，而且只有这一个方向。"),
    ("讲得太长了，能简短一点吗", "P0",
     "可以。要点：特征向量 = 变换后方向不变的向量；特征值 = 它被拉伸的倍数。"),
    ("那怎么求特征值？", "P0",
     "解方程 det(A - λI) = 0。这个行列式是 λ 的多项式，叫特征多项式，它的根就是特征值。"),
    ("为什么是 det(A - λI) = 0？这一步我想推一下", "P0",
     "从定义出发：Av = λv，v 不是零向量。移项得 (A - λI)v = 0。一个非零向量被 A - λI 送到零，"
     "说明 A - λI 把某个方向压扁了——我们下一步来看「压扁」和行列式为零的关系。"),
    ("压扁和行列式为零有什么关系？", "P0",
     "行列式衡量变换对面积（体积）的缩放。把某个方向压扁，面积就变成零，所以行列式为零。"
     "反过来，行列式为零就一定有方向被压扁，也就有非零向量被送到零。这就是 det(A - λI) = 0 的来历。"),
    ("懂了！那 2×2 矩阵能直接套公式吗", "P0",
     "能。λ² - (a+d)λ + (ad - bc) = 0，其中 a+d 是迹，ad-bc 是行列式。"),
    ("迹是什么", "P0", "迹就是主对角线元素之和，2×2 矩阵是 a + d。它等于所有特征值之和。"),
    ("那行列式也等于特征值的什么？", "P0", "等于所有特征值的乘积。"),
]


def turns() -> list[Turn]:
    return [Turn(index=i + 1, question=q, answer=a, detail_level=lv, confusion=(i + 1 == 3))
            for i, (q, lv, a) in enumerate(QA)]


def fake_model():
    from explain.fake import ScriptedToolModel

    note = {"fold_through_turn": 6, "summary": "在学特征值。", "concepts": [
        {"name": "特征值", "turns": [1, 2], "status": "struggling", "note": "拉伸的例子"}],
        "open_questions": [], "preferences": ["多举例"], "dropped_concepts": ["特征向量"]}
    return ScriptedToolModel(name="fake:claude-haiku-4-5", script=[
        ("tool", [(TOOL_READ, {"from_turn": 2, "to_turn": 5})]),
        ("tool", [(TOOL_WRITE, note)])] * 2)


def show(title: str, run, plan) -> dict:
    d = run.to_dict()
    print(f"\n== {title} ==  候选 {plan.lo}–{plan.hi}（可留到 {plan.min_through}），保留原文 {list(plan.tail)}")
    for t in run.tools:
        print(f"   {t.name:14s} {json.dumps(t.input, ensure_ascii=False)[:110]}  → {t.summary}")
    print(f"   status={run.status} steps={len(run.steps)} rejected={run.rejected} "
          f"cost=${run.cost_usd:.4f} {run.latency_ms / 1000:.1f}s error={run.error}")
    if run.note:
        print("   ---- 笔记（%d 字，上限 %d）----" % (len(run.note.render()), MEMORY_CHAR_BUDGET))
        print("   " + run.note.render().replace("\n", "\n   "))
    return {"title": title, "plan": plan.to_dict(), **d,
            "note": run.note.to_dict() if run.note else None,
            "rendered_chars": len(run.note.render()) if run.note else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="不出网：替身跑通脚本本身")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    agent = MemoryAgent(fake_model()) if args.fake else build_memory_agent()
    rows: list[dict] = []
    ts = turns()

    # 场景一：第一次整理（11 轮原文，无旧笔记）
    plan1 = plan_compaction([t.index for t in ts])
    assert plan1 is not None
    run1 = agent.compact(prev=None, turns=ts, plan=plan1)
    rows.append(show("第一次整理", run1, plan1))

    # 场景二：带旧笔记的第二次整理。旧笔记是手写的（不依赖场景一的结果），
    # 含一个之后再也没提过的概念「行列式的几何意义」——看它是保留、显式丢弃，还是悄悄丢掉被退回。
    prev = MemoryNote(through_turn=3, summary="在学特征值的含义。", concepts=(
        Concept("特征值", (1, 2), "struggling", "拉伸的例子讲过，第 2 轮没懂，换了「一个数说清」的角度"),
        Concept("负特征值", (3,), "understood", "镜面反射的例子"),
        Concept("行列式的几何意义", (1,), "unclear", "只顺带提过"),
    ))
    live = [t.index for t in ts if t.index > prev.through_turn] + [12, 13, 14]
    extra = [Turn(12, "特征向量怎么求", "把 λ 代回 (A - λI)v = 0 解方程组。", "P0"),
             Turn(13, "解出来不唯一怎么办", "特征向量本来就不唯一，任意非零倍数都是。", "P0"),
             Turn(14, "那对角化是什么", "用特征向量组成新基，矩阵变成对角阵。", "P0")]
    plan2 = plan_compaction(live)
    assert plan2 is not None
    run2 = agent.compact(prev=prev, turns=[t for t in ts if t.index > 3] + extra, plan=plan2)
    rows.append(show("第二次整理（带旧笔记）", run2, plan2))

    # 注入讲解请求的样子
    if run2.note:
        hist = [{"role": r, "content": c, "turn_index": t.index}
                for t in [t for t in ts if t.index > 3] + extra
                for r, c in (("user", t.question), ("assistant", t.answer))]
        msgs = apply_memory(run2.note, hist)
        print(f"\n== 下一轮讲解请求的历史：{len(msgs)} 条消息，第一条 {len(msgs[0]['content'])} 字 ==")

    spent = sum(r["cost_usd"] for r in rows)
    print(f"\n合计 ${spent:.4f}（按 llm.PRICING 折算）")
    out = Path(args.out) if args.out else (
        REPO / "explain" / "runs" / f"memory_live_{date.today():%Y%m%d}{'_fake' if args.fake else ''}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"date": f"{date.today()}", "model": getattr(agent.model, "name", None),
                               "spent_usd": round(spent, 5), "runs": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
