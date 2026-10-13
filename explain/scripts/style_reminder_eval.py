"""下一轮风格提醒 · 真链路多轮会话评测（2026-10-13）。判据见 `explain/runs/style_judge_eval_prereg.md`（B 部分）。

    api/.venv/bin/python explain/scripts/style_reminder_eval.py run --arm on     # 有提醒（生产行为，开出处核对）
    api/.venv/bin/python explain/scripts/style_reminder_eval.py run --arm off    # 无提醒（每轮抹掉存下的提醒）
    api/.venv/bin/python explain/scripts/style_reminder_eval.py report           # 汇总（不花钱）

走的是 `api.main.create_app` 的真 `/explain`：真模型 + 假库（`api/tests/fake_db.FakeRest`，存会话消息，
所以下一轮读得到上一轮存的提醒）+ 合成课件 20 块全部喂入。每次模型调用的花费由 `AnthropicModel.stream`
的一层记录器按 system 归类（讲解 / 路由 / 兜底判断 / 风格审查 / 出处核对 / 其他）。
要钱的子命令要 `ANTHROPIC_API_KEY`（单条命令里映射，不 export）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
for sub in ("explain", "explain/scripts", "api", "api/tests", "retrieval"):
    sys.path.insert(0, str(REPO / sub))

import explain  # noqa: E402,F401
from explain import llm  # noqa: E402

RUNS = REPO / "explain" / "runs"
BUDGET_USD = 5.0          # B 部分的上限（预登记两部分合计 $6，A 部分约 $1）

#: 6 个会话：(讲法偏好, 5 轮提问)。偏好四轴 + 语气轮换；提问是同一主题下的追问。
Q_A = ["特征值和特征向量是什么？", "为什么特征向量在变换之后方向不变？", "特征值是负数是什么意思？",
       "那怎么求一个矩阵的特征值？", "对角化又是做什么用的？"]
Q_B = ["什么是对角化？", "什么样的矩阵不能对角化？", "为什么剪切变换不行？",
       "对称矩阵的特征值有什么特点？", "幂法是怎么回事？"]
Q_C = ["幂法是怎么回事？", "为什么反复作用之后最大的特征值最重要？", "PCA 和特征向量有什么关系？",
       "软件里实际是怎么算特征值的？", "复数特征值应该怎么理解？"]
CONVERSATIONS: list[tuple[dict[str, str], list[str]]] = [
    ({"detail_level": "P3", "use_examples": "fewer", "structure": "conclusion_first", "depth": "what", "tone": "neutral"}, Q_A),
    ({"detail_level": "P0", "use_examples": "more", "structure": "buildup_first", "depth": "derivation", "tone": "academic"}, Q_B),
    ({"detail_level": "P2", "use_examples": "fewer", "structure": "buildup_first", "depth": "why", "tone": "spoken"}, Q_C),
    ({"detail_level": "P3", "use_examples": "more", "structure": "conclusion_first", "depth": "derivation", "tone": "neutral"}, Q_B),
    ({"detail_level": "P0", "use_examples": "fewer", "structure": "conclusion_first", "depth": "what", "tone": "spoken"}, Q_C),
    ({"detail_level": "P2", "use_examples": "more", "structure": "buildup_first", "depth": "what", "tone": "academic"}, Q_A),
]


# ---------------------------------------------------------------- 花费记录


class Spend:
    """包一层 `AnthropicModel.stream`：每次调用记下 (类别, 模型, 成本, 耗时)。顺序跑，一轮一清。"""

    def __init__(self) -> None:
        from explain.gap import GAP_SYSTEM_PROMPT
        from explain.memory import MEMORY_SYSTEM
        from explain.rag import RERANK_SYSTEM, REWRITE_SYSTEM
        from explain.router import ROUTER_SYSTEM
        from explain.style_review import STYLE_JUDGE_SYSTEM
        from explain.verify import VERIFY_SYSTEM

        self.kinds = [(ROUTER_SYSTEM, "router"), (GAP_SYSTEM_PROMPT, "gap"), (STYLE_JUDGE_SYSTEM, "style_judge"),
                      (VERIFY_SYSTEM, "verify"), (MEMORY_SYSTEM, "memory"), (REWRITE_SYSTEM, "rag"),
                      (RERANK_SYSTEM, "rag")]
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        orig = llm.AnthropicModel.stream
        spend = self

        def stream(model_self, system, messages, **kw):
            c = orig(model_self, system, messages, **kw)
            kind = next((k for s, k in spend.kinds if str(system).startswith(s[:60])), "explain")
            with spend.lock:
                spend.calls.append({"kind": kind, "model": c.model, "cost": c.cost, "ms": round(c.latency_ms)})
            return c

        llm.AnthropicModel.stream = stream

    def take(self) -> list[dict[str, Any]]:
        with self.lock:
            out, self.calls = self.calls, []
        return out


# ---------------------------------------------------------------- 跑


def deck_retriever():
    from verify_eval import deck_slices

    from explain.retrieval_adapter import InMemoryRetriever

    return InMemoryRetriever(default_slices=tuple(deck_slices()),
                             meta={"backend": "in_memory", "note": "评测：合成课件 20 块全部喂入"})


def cmd_run(args: Any) -> int:
    import os

    from fastapi.testclient import TestClient

    from api.auth import TokenVerifier
    from api.main import create_app
    from api.settings import Settings
    from api.supabase import SupabaseClient
    from conftest import AUDIENCE, ISSUER, KeyPair, StaticJwks
    from fake_db import FakeRest, row

    out_path = RUNS / f"style_reminder_eval_{args.arm}.json"
    spend = Spend()
    kp = KeyPair("eval-kid")
    settings = Settings(supabase_url="https://example.supabase.co", supabase_publishable_key="sb_publishable_eval",
                        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"])
    users = {f"{i + 1:08d}-1111-4111-8111-{i + 1:012d}": prof for i, (prof, _) in enumerate(CONVERSATIONS)}
    convs = {f"{i + 1:08d}-2222-4222-8222-{i + 1:012d}": {"id": f"{i + 1:08d}-2222-4222-8222-{i + 1:012d}",
                                                          "course_id": None, "title": f"eval{i + 1}"}
             for i in range(len(CONVERSATIONS))}
    rest = FakeRest({u: [row(u, **p)] for u, p in users.items()}, conversations=convs)
    app = create_app(
        settings=settings,
        verifier=TokenVerifier(StaticJwks(kp), issuer=ISSUER, audience=AUDIENCE),
        supabase=SupabaseClient(settings.rest_url, settings.supabase_publishable_key, transport=rest),
        retriever=deck_retriever(),
        # 有提醒臂开出处核对（算单轮成本）；无提醒臂关掉省钱。记忆整理 5 轮内不会触发。
        verify_factory=None if args.arm == "on" else (lambda _r: None),
    )
    client = TestClient(app, raise_server_exceptions=False)

    rows: list[dict[str, Any]] = []
    total = 0.0
    for ci, ((user, prof), (cid, _conv)) in enumerate(zip(users.items(), convs.items()), 1):
        questions = CONVERSATIONS[ci - 1][1]
        for ti, q in enumerate(questions, 1):
            if total > BUDGET_USD:
                print(f"超过预算 ${BUDGET_USD}，停在会话 {ci} 第 {ti} 轮")
                break
            spend.take()
            started = time.perf_counter()
            r = client.post("/explain", json={"question": q, "conversation_id": cid},
                            headers={"Authorization": f"Bearer {kp.sign(sub=user)}"})
            wall = round((time.perf_counter() - started) * 1000)
            calls = spend.take()
            body = r.json() if r.status_code == 200 else {}
            would_carry = None
            if args.arm == "off":
                # 无提醒臂：把这一轮存下的提醒抹掉（记下本来会带的那一句），下一轮读不到。
                for m in reversed(rest.messages):
                    if m.get("conversation_id") == cid and m.get("role") == "assistant":
                        would_carry = (m.get("meta") or {}).pop("style_reminder", None)
                        break
            cost = sum(c["cost"] for c in calls)
            total += cost
            sr = body.get("style_review") or {}
            rows.append({
                "conv": ci, "turn": ti, "question": q, "prefs": prof, "status": r.status_code,
                "error": None if r.status_code == 200 else r.text[:300],
                "regenerations": body.get("regenerations"), "accepted": body.get("accepted"),
                "route": ((body.get("agent") or {}).get("route") or {}).get("route"),
                "reminder": body.get("style_reminder"), "would_carry": would_carry,
                "style_attempts": [a.get("verdict") for a in sr.get("attempts", [])],
                "style_feedback": [a.get("feedback") for a in sr.get("attempts", []) if a.get("feedback")],
                "verification": (body.get("verification") or {}).get("status"),
                "cost": round(cost, 5), "by_kind": _by_kind(calls), "wall_ms": wall,
                "text": body.get("text", "")[:2000],
            })
            rem = body.get("style_reminder") or {}
            print(f"  会话{ci} 第{ti}轮 {r.status_code} 重写{body.get('regenerations')} "
                  f"带提醒={'是' if rem.get('applied') else '否'} 留={'是' if (rem.get('next') or would_carry) else '否'} "
                  f"${cost:.4f} {wall / 1000:.1f}s  累计 ${total:.3f}")
            out_path.write_text(json.dumps({"arm": args.arm, "spent_usd": round(total, 4), "turns": rows},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{args.arm}：{len(rows)} 轮，花费 ${total:.4f} → {out_path}")
    return 0


def _by_kind(calls: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in calls:
        out[c["kind"]] = round(out.get(c["kind"], 0.0) + c["cost"], 5)
    return out


# ---------------------------------------------------------------- 汇总


def _rate(turns: list[dict[str, Any]]) -> str:
    n = len(turns)
    k = sum(1 for t in turns if (t["regenerations"] or 0) > 0)
    return f"{k}/{n}（{100 * k / n:.0f}%）" if n else "—"


def cmd_report(_args: Any) -> int:
    arms = {a: json.loads((RUNS / f"style_reminder_eval_{a}.json").read_text(encoding="utf-8"))
            for a in ("on", "off") if (RUNS / f"style_reminder_eval_{a}.json").exists()}
    lines = ["# 下一轮风格提醒 · 真链路多轮会话 · 原始汇总", ""]
    for a, d in arms.items():
        ok = [t for t in d["turns"] if t["status"] == 200]
        lines.append(f"- 臂 `{a}`：{len(d['turns'])} 轮（成功 {len(ok)}），花费 ${d['spent_usd']}")
    on = [t for t in arms.get("on", {}).get("turns", []) if t["status"] == 200]
    off = [t for t in arms.get("off", {}).get("turns", []) if t["status"] == 200]
    if on:
        applied = [t for t in on if (t["reminder"] or {}).get("applied")]
        not_applied = [t for t in on if not (t["reminder"] or {}).get("applied")]
        lines += ["", "## 1 · 有提醒臂里：带提醒 vs 不带（观察性，见预登记的 ⚠️）", "",
                  f"- 带了提醒的轮次：重写率 {_rate(applied)}",
                  f"- 没带提醒的轮次：重写率 {_rate(not_applied)}",
                  f"  - 其中第 2–5 轮：{_rate([t for t in not_applied if t['turn'] > 1])}"]

    def prev_rewrote(turns, t):
        p = next((x for x in turns if x["conv"] == t["conv"] and x["turn"] == t["turn"] - 1), None)
        return bool(p and (p["regenerations"] or 0) > 0)

    if on and off:
        lines += ["", "## 2 · 两臂对比", "",
                  "| | 有提醒 | 无提醒 |", "| --- | --- | --- |",
                  f"| 第 1 轮重写率（两臂条件相同，看噪声） | {_rate([t for t in on if t['turn'] == 1])} | {_rate([t for t in off if t['turn'] == 1])} |",
                  f"| 第 2–5 轮重写率 | {_rate([t for t in on if t['turn'] > 1])} | {_rate([t for t in off if t['turn'] > 1])} |",
                  f"| 上一轮被打回过的轮次的重写率 | {_rate([t for t in on if t['turn'] > 1 and prev_rewrote(on, t)])} "
                  f"| {_rate([t for t in off if t['turn'] > 1 and prev_rewrote(off, t)])} |",
                  f"| 全部轮次重写率 | {_rate(on)} | {_rate(off)} |"]
    lines += ["", "## 3 · 单轮成本（按 llm.PRICING）", ""]
    for a, turns in (("on", on), ("off", off)):
        if not turns:
            continue
        costs = [t["cost"] for t in turns]
        kinds: dict[str, list[float]] = {}
        for t in turns:
            for k, v in t["by_kind"].items():
                kinds.setdefault(k, []).append(v)
        no_verify = [t["cost"] - t["by_kind"].get("verify", 0.0) for t in turns]
        lat = sorted(t["wall_ms"] for t in turns)
        lines += [f"- 臂 `{a}`（{len(turns)} 轮）：均值 ${statistics.mean(costs):.4f}，中位 ${statistics.median(costs):.4f}；"
                  f"不含出处核对 均值 ${statistics.mean(no_verify):.4f}",
                  "  - 按组成（每轮均值，含没调用的轮按 0 计）："
                  + "，".join(f"{k} ${sum(v) / len(turns):.4f}" for k, v in sorted(kinds.items())),
                  f"  - 墙钟 中位 {statistics.median(lat) / 1000:.1f} s，P90 {lat[int(0.9 * len(lat)) - 1] / 1000:.1f} s"]
    out = RUNS / "style_reminder_eval_report_raw.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", choices=["on", "off"], required=True)
    sub.add_parser("report")
    args = ap.parse_args()
    return {"run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
