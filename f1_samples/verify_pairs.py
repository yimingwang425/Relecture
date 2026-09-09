"""三维核对 + 反向验证。调用**现有的** `a_rules`，不改它。

    ../实验脚本/A类规则计算脚本/.venv/bin/python verify_pairs.py

零 API 成本——只读 pairs.json 里已生成好的文本。

## 三条断言

| 维度 | 判据 |
| --- | --- |
| `paragraph_length` | **隶属集合判据**（下） |
| `chinese_ratio` | 全部 16 个样例 ≥ 0.85（生产默认阈值，不引入新数字） |
| `quote_length` | 全部 16 个样例 ≤ 100 字（PRD §12.1 合规线） |

## 隶属集合判据：零自由参数

对每个样例，算它的平均段落字数落进 `AXIS_POINTS` 四个区间中的**哪些**，
得到一个隶属集合。

- **非详细程度的 5 对**：两个样例的隶属集合必须**完全相同**
  ——含义是「在 F1 自己的档位轴上不可分辨」
- **详细程度的 3 对**：隶属集合必须**不同**

不引入「差异小于 N 字」这类阈值：判据的出处就是 F1 的档位定义本身
（同 F1方案 §5.1「离散轴上零新阈值」）。

⚠️ **第 2 对（P0 vs P1）天生最容易红**：P1(40–90) 完全被 P0(30–120) 包含，
P0 样例若恰好落在 40–90，两者隶属集合相同。**那是判据在正确工作**——
重新生成，不要改判据（F01 的使用纪律）。

## 观察项：打印，不判定

按 F1方案 §3.4 选项 B，打印每个样例的 `total_chars` 与 `paragraph_count`。
**这是 P3 分段问题在真实用户之前的第一手证据**（E10：P3 达标 72% 来自分段）。
不作判定，不据此调整档位。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import _paths  # noqa: F401

from a_rules.config import StyleConfig
from a_rules.evaluator import evaluate
from a_rules.metrics.chinese_ratio import chinese_ratio
from a_rules.metrics.paragraph_length import avg_paragraph_length
from a_rules.metrics.quote_length import max_quote_length
from scan.points import AXIS_POINTS
from style_axes import AXES, style_config_for

HERE = Path(__file__).resolve().parent
PAIRS_JSON = HERE / "pairs.json"
REVIEW_RESULT = HERE / "manual_review_result.md"

#: 生产默认阈值。**不是本脚本新引入的数字**——它就是 `StyleConfig` 的默认值。
CHINESE_RATIO_MIN = StyleConfig().chinese_ratio.min      # 0.85
MAX_QUOTE_CHARS = StyleConfig().max_quote_chars          # 100

passed = 0
failed = 0
#: 本轮失败的断言名。反向验证按**名字**比对，不按红的条数——
#: 单跑一对时「核对了 16 个样例」必然红，用条数比对会把它算进去，
#: 于是「目标断言变红了吗」这个问题根本没被回答（通则七 #5 的形态）。
failures: list[str] = []


def check(cond: bool, name: str, ok_detail: str = "", bad_detail: str = "") -> bool:
    global passed, failed
    detail = ok_detail if cond else bad_detail
    if cond:
        passed += 1
    else:
        failed += 1
        failures.append(name)
    mark = "\033[32m✓\033[0m" if cond else "\033[31m✗\033[0m"
    print(f"  {mark} {name}" + (f"   {detail}" if detail else ""))
    return cond


# ---------------------------------------------------------------- 判据

def tier_membership(mean: float) -> frozenset[str]:
    """平均段落字数 → 它落进哪些**产品档位**的区间。

    ⚠️ 遍历的是 `AXES["detail_level"]`（产品档位），**不是 `AXIS_POINTS` 的四个点**
    （口径 F23）。P1 退出产品后仍留在扫描点位里；用扫描点位算隶属集合，
    等于在一条用户选不到的档位上衡量「可分辨性」。区间数值仍只读
    `scan.points`（口径 F13）——变的是取哪几个点，不是那些点的数值。
    """
    by_id = {p.id: p for p in AXIS_POINTS}
    return frozenset(
        tier for tier in AXES["detail_level"]
        if by_id[tier].config().paragraph_length.contains(mean)
    )


def measure(text: str, detail_level: str) -> dict[str, Any]:
    """跑 a_rules 的三个维度 + **生产判定**。配置取该样例自己的档位。"""
    cfg = style_config_for(detail_level)
    # 生产链路里模型出完要过 a_rules 判定、不达标重生成，用户拿到的是过审后的
    # 输出。样例若只过隶属集合判据，它就不是「用户真会得到的东西」——F18 的
    # 目的没达到。这里跑的就是生产那一个 evaluate()，零新参数。
    ev = evaluate(text, cfg)
    para = avg_paragraph_length(text, cfg)
    ratio = chinese_ratio(text, cfg)
    quote = max_quote_length(text, cfg)
    return {
        "production_passed": ev.all_activated_passed,
        "production_failed_dims": [
            d.name for d in ev.dimensions
            if d.counts_toward_score and d.passed is False
        ],
        "mean": para.raw,
        "membership": tier_membership(para.raw) if para.raw is not None else frozenset(),
        "total_chars": (para.details or {}).get("total_chars"),
        "paragraph_count": (para.details or {}).get("count"),
        "chinese_ratio": ratio.raw,
        "chinese_ratio_activated": ratio.activated,
        "max_quote": quote.raw,
        "quote_count": len((quote.details or {}).get("quotes", []) or []),
    }


def check_pair(pair: dict, measured: dict[str, dict]) -> None:
    """一对样例的隶属集合判据。"""
    a, b = pair["samples"]
    ma, mb = measured[a["sample_id"]], measured[b["sample_id"]]
    is_detail = pair["target_dimension"] == "detail_level"
    sa, sb = ma["membership"], mb["membership"]
    label = f"{sorted(sa)} vs {sorted(sb)}"

    if is_detail:
        check(
            sa != sb,
            f"{pair['pair_id']}（目标轴=详细程度）隶属集合不同",
            label,
            f"{label} —— 两个样例在档位轴上不可分辨。**重新生成，不要改判据**",
        )
    else:
        check(
            sa == sb,
            f"{pair['pair_id']}（非目标轴）隶属集合相同",
            label,
            f"{label} —— 非目标维度上出现了可分辨差异，违反 F01。重新生成",
        )


def run(data: dict, *, quiet: bool = False) -> tuple[int, int, dict[str, dict]]:
    """跑完整套断言。返回 (绿, 红, 逐样例测量值)。"""
    global passed, failed
    before_p, before_f = passed, failed

    measured: dict[str, dict] = {}
    for pair in data["pairs"]:
        for s in pair["samples"]:
            measured[s["sample_id"]] = measure(s["text"], s["style"]["detail_level"]["value"])

    n = len(measured)
    # **扫了 0 个样例也会得到 0 条失败**（F16 那一类）。这条必须先成立。
    check(n == 16, "核对了 16 个样例", f"{n} 个",
          f"{n} 个 —— 下面所有「全部达标」都不成立")

    # **生产判定先于隶属集合判据**：一个没通过生产判定的样例，用户根本不会
    # 看到它——在它上面比较「两个集合是否不同」是在比较一个不存在的东西。
    #
    # 注意 `paragraph_length` 的判定就是**均值是否落进区间**
    # （`avg_paragraph_length`: `passed = rng.contains(mean)`），
    # 不是按段落比例算的——这条是读实现测出来的，不是推断。
    not_shipped = {sid: m["production_failed_dims"]
                   for sid, m in measured.items() if not m["production_passed"]}
    check(not not_shipped, "全部样例通过生产的 a_rules 判定",
          f"16/16 达标（判定 = evaluate() 的 all_activated_passed）",
          f"{len(not_shipped)} 个不达标：{not_shipped} —— "
          f"用户看不到这样的输出，在它上面比可分辨性没有意义。按 F01 重生成")

    for pair in data["pairs"]:
        check_pair(pair, measured)

    below = [(sid, m["chinese_ratio"]) for sid, m in measured.items()
             if m["chinese_ratio"] is None or m["chinese_ratio"] < CHINESE_RATIO_MIN]
    lows = [m["chinese_ratio"] for m in measured.values() if m["chinese_ratio"] is not None]
    check(not below, f"全部样例 chinese_ratio ≥ {CHINESE_RATIO_MIN}",
          f"最低 {min(lows):.4f}" if lows else "（无样例）",
          f"低于阈值：{below}")

    over = [(sid, m["max_quote"]) for sid, m in measured.items()
            if m["max_quote"] is not None and m["max_quote"] > MAX_QUOTE_CHARS]
    with_quote = sum(1 for m in measured.values() if m["quote_count"] > 0)
    check(not over, f"全部样例 quote_length ≤ {MAX_QUOTE_CHARS} 字",
          f"最长 {max([(m['max_quote'] or 0) for m in measured.values()], default=0)} 字"
          f"（{with_quote}/{n} 个样例含引用）",
          f"超限：{over}")
    # 一个样例都不含引用时，上面那条是空转的——说出来，不算通过。
    if with_quote == 0 and not quiet:
        print("  \033[33m!\033[0m quote_length 那条是空转的：16 个样例一个引用都没有，"
              "判据没有被任何数据触发过")

    return passed - before_p, failed - before_f, measured


# ---------------------------------------------------------------- 观察项

def observations(data: dict, measured: dict[str, dict]) -> None:
    print("\n\033[1m观察项（打印，不判定 —— F1方案 §3.4 选项 B）\033[0m")
    print(f"  {'sample_id':<34}{'档位':<6}{'区间':<11}{'字/段':>7}{'达标':>5}"
          f"{'总字数':>8}{'段数':>6}{'中文占比':>9}{'引用':>6}")
    for pair in data["pairs"]:
        for s in pair["samples"]:
            m = measured[s["sample_id"]]
            lvl = s["style"]["detail_level"]["value"]
            rng = style_config_for(lvl).paragraph_length.describe()
            print(f"  {s['sample_id']:<34}{lvl:<6}{rng:<11}"
                  f"{m['mean']:>7.1f}{'✓' if m['production_passed'] else '✗':>5}"
                  f"{m['total_chars']:>8}{m['paragraph_count']:>6}"
                  f"{m['chinese_ratio']:>9.3f}{m['max_quote'] or 0:>6}")

    print("\n  详细程度三对的实际差值：")
    for pair in data["pairs"]:
        if pair["target_dimension"] != "detail_level":
            continue
        a, b = pair["samples"]
        ma, mb = measured[a["sample_id"]], measured[b["sample_id"]]
        print(f"    {pair['pair_id']:<24}{a['variant_label']} {ma['mean']:.1f} 字/段"
              f"  vs  {b['variant_label']} {mb['mean']:.1f} 字/段"
              f"   差 {abs(ma['mean'] - mb['mean']):.1f} 字"
              f"   总字数 {ma['total_chars']} vs {mb['total_chars']}"
              f"   段数 {ma['paragraph_count']} vs {mb['paragraph_count']}")


# ---------------------------------------------------------------- 反向验证

def fake_pair(pair_id: str, axis: str, text_a: str, text_b: str,
              level_a: str = "P0", level_b: str = "P0") -> dict:
    def sample(sid: str, text: str, level: str) -> dict:
        return {"sample_id": sid, "variant_label": sid,
                "style": {"detail_level": {"value": level, "enforced": True}}, "text": text}
    return {"pair_id": pair_id, "target_dimension": axis,
            "samples": [sample(f"{pair_id}.a", text_a, level_a),
                        sample(f"{pair_id}.b", text_b, level_b)]}


#: 造样。实测值写在注释里——**造样自己必须落在真实档位里**，
#: 否则「两个都落不进任何档位 → 集合同为空 → 相等」会让 R1 悄悄失效。
#: 第一版就踩了这个：LONG(162 字/段) 与 ENGLISH(11.5 字/段) 隶属集合同为空集，
#: R1 报绿而目标断言根本没被触发。
#: 108 字/段 → {P0}，中文占比 1.0
CN_LONG = "\n\n".join(
    ["布隆过滤器用一个位数组和多个哈希函数回答集合成员问题，它不会漏报但是会误判。" * 3] * 3
)
#: 29 字/段 → {P2, P3}，中文占比 0.172（低于 0.85）
EN_SHORT = "\n\n".join(
    ["布隆过滤器 is a probabilistic set structure that uses k independent hash "
     "functions over an array of m bits, and it never reports a false negative."] * 3
)


def reverse_checks() -> None:
    global passed, failed
    print("\n\033[1m反向验证 —— 造出该红的样例，判据必须红\033[0m")

    def isolated(pairs: list[dict]) -> list[str]:
        """跑一组造出来的样例，返回**失败断言的名字**。不计入总账。"""
        global passed, failed, failures
        p0, f0, fx = passed, failed, list(failures)
        failures = []
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run({"pairs": pairs}, quiet=True)
        got = list(failures)
        passed, failed, failures = p0, f0, fx
        return got

    def named(got: list[str], needle: str) -> bool:
        return any(needle in n for n in got)

    # R1 · 一对在两个 A 类维度上都不同：段落长度 + 中文占比
    #
    # 先验造样自己：两个都必须落在**真实档位**里。若同为空集，
    # 「集合不同」不成立、R1 会绿得毫无意义——第一版正是这么失效的。
    m_a, m_b = measure(CN_LONG, "P0"), measure(EN_SHORT, "P0")
    check(bool(m_a["membership"]) and bool(m_b["membership"]),
          "R1 的造样各自落在真实档位里",
          f"{sorted(m_a['membership'])} / {sorted(m_b['membership'])} ← 这是 R1 有意义的前提",
          f"{sorted(m_a['membership'])} / {sorted(m_b['membership'])} —— 有空集，R1 会假绿")

    got = isolated([fake_pair("R1_two_dims", "use_examples", CN_LONG, EN_SHORT)])
    check(named(got, "R1_two_dims"), "R1 两个 A 类维度都不同 → 非目标维度那条变红",
          "隶属集合断言红了", f"隶属集合断言没红（红的是 {got}）")
    check(named(got, "chinese_ratio"), "R1 同一对 → chinese_ratio 那条也变红",
          "英文样例被 0.85 挡下", f"没红 —— 该维度没有分辨力（红的是 {got}）")

    # R2 · 详细程度那一对上无差异
    got = isolated([fake_pair("R2_no_diff", "detail_level", CN_LONG, CN_LONG, "P0", "P3")])
    check(named(got, "R2_no_diff"), "R2 详细程度上无差异 → 目标维度那条变红",
          "「隶属集合不同」红了", f"没红 —— 目标维度那条断言是空的（红的是 {got}）")

    # R3 · 扫 0 个样例
    got = isolated([])
    check(named(got, "核对了 16 个样例"), "R3 扫 0 个样例 → 样例数那条变红",
          "← 否则「全部达标」在空集合上恒成立", f"0 个样例居然没红（红的是 {got}）")

    # R5 · 生产判定那条：把一个 108 字/段的样例标成 P3（15–40）→ 必须红
    got = isolated([fake_pair("R5_out_of_band", "detail_level",
                              CN_LONG, EN_SHORT, "P3", "P3")])
    check(named(got, "生产的 a_rules 判定"), "R5 样例落在自己档位外 → 生产判定那条变红",
          "108 字/段 标成 P3 被挡下", f"没红 —— 生产判定那条是空的（红的是 {got}）")

    # R6 · 时效检查那条：把一个已标「通过」的对的 attempt 改掉 → 必须红。
    # 没有过期结论时那条恒绿，不验它就等于没有它。
    import json as _json
    bumped = _json.loads(PAIRS_JSON.read_text(encoding="utf-8"))
    for pair in bumped["pairs"]:
        if pair["pair_id"] == "examples_more_fewer":      # 表里标了「通过」的一对
            for smp in pair["samples"]:
                smp["attempt"] += 99
    global passed, failed, failures
    p0, f0, fx = passed, failed, list(failures)
    failures = []
    import io as _io, contextlib as _ctx
    with _ctx.redirect_stdout(_io.StringIO()):
        check_review_freshness(bumped)
    got = list(failures)
    passed, failed, failures = p0, f0, fx
    check(named(got, "版本已过期"), "R6 把已通过的对改掉版本 → 时效那条变红",
          "结论与样例脱锚被挡下", f"没红 —— 时效检查是恒绿的（红的是 {got}）")

    # R4 · 判据不是恒真：一对真正相同的样例，**那一对的断言**不该红。
    # 只看这一对的名字——样例数那条在单跑时必然红，把它算进来就等于没验。
    got = isolated([fake_pair("R4_same", "use_examples", CN_LONG, CN_LONG)])
    check(not named(got, "R4_same"), "R4 一对真正相同的样例 → 那一对不误报",
          "← 判据不是「永远红」", f"误报了（红的是 {got}）")


def check_review_freshness(data: dict) -> None:
    """人工结论是否还锚在当前样例版本上（口径 F25）。

    B 类三条轴没有自动判定，这张表是它们**仅有的**质量证据。一条标了「通过」
    但对应样例已经重生成的结论，比没有结论更糟——它会让人停止追问
    （通则七：失效的验证会让人停止追问）。所以只有**「通过」且版本对不上**判红；
    诚实的 `待核` / `暂缓` 不判红。
    """
    print("\n\033[1m人工结论的时效（口径 F25）\033[0m")
    if not REVIEW_RESULT.is_file():
        check(False, "manual_review_result.md 存在", "", "缺文件 —— B 类没有任何质量证据")
        return

    rows: dict[str, tuple[str, str]] = {}
    for line in REVIEW_RESULT.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6 or not cells[1].startswith("`"):
            continue
        rows[cells[1].strip("`")] = (cells[3], cells[4])   # (结论, 样例版本)

    current = {
        p["pair_id"]: f"{p['samples'][0]['attempt']}/{p['samples'][1]['attempt']}"
        for p in data["pairs"]
    }
    check(set(current) <= set(rows), "每一对都在结论表里有一行",
          f"{len(rows)} 行", f"缺行：{sorted(set(current) - set(rows))}")

    stale = {pid: (rows[pid][1], ver) for pid, ver in current.items()
             if pid in rows and rows[pid][0] == "通过" and rows[pid][1] != ver}
    check(not stale, "没有「已通过但版本已过期」的结论",
          f"{sum(1 for v in rows.values() if v[0] == '通过')} 条通过，版本全部对得上",
          f"过期：{stale}（表里版本 vs 当前版本）—— 结论是对旧文本做的，须重核")

    pending = [pid for pid, v in rows.items() if pid in current and v[0] != "通过"]
    if pending:
        print(f"  \033[33m—\033[0m 尚未通过的对：{pending}"
              f"（诚实状态，不判红；上线前须清零）")


def main() -> int:
    data = json.loads(PAIRS_JSON.read_text(encoding="utf-8"))
    print(f"\npairs.json：{data['generated_at']}"
          f" · 模型 {data['model']['id']}"
          f" · CONFIG_VERSION {data['a_rules_config_version']}"
          f" · 花费 ${data['cost_usd_total']}")

    print("\n\033[1m正向 —— 三维核对\033[0m")
    _, _, measured = run(data)
    observations(data, measured)
    check_review_freshness(data)
    reverse_checks()

    print(f"\n\033[1m合计\033[0m  绿 {passed} · 红 {failed}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
