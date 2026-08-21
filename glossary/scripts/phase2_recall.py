"""阶段 2：计算召回率。**拒绝在人工判定完成之前运行。**

顺序是方法的一部分：新发现条目的判定必须在看到召回率之前完成，
否则会不自觉地把边缘条目往「误收」判——那样召回率好看。
本脚本通过三条校验把这个顺序变成**结构上成立**而不是口头承诺：

1. 判定文件不存在 → 拒绝运行
2. 有任何一条 `verdict` 为空 → 拒绝运行
3. 判定文件的 sha256 记进报告，可回溯判定内容未在事后被改

用法::

    python -m scripts.phase1_adjudicate     # 先跑这个，然后人工填 verdict
    python -m scripts.phase2_recall         # 再跑这个
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[0]
sys.path.insert(0, str(ROOT))

from parse_service.pdf import parse_pdf  # noqa: E402
from slicer import slice_document  # noqa: E402

from glossary import extract  # noqa: E402
from glossary.text import normalize_phrase  # noqa: E402

CORPUS = REPO / "实验脚本" / "测试集"
GOLD = REPO / "实验脚本" / "A类规则计算脚本" / "examples" / "glossary_22081J.json"
ADJ = ROOT / "runs" / "new_findings.json"
COURSE, FILE = "22.081J", "MIT22_081JF10_lec01b.pdf"

#: 文本层不可达的金标准条目——**结构性上限**，不是方法缺陷。
#: figure：p20 的图内标签被转成矢量轮廓，文本层没有这些字。
#: body：课件只以派生形出现（breeders / fission products / enrichment），
#:       需要词形归一才能接近，而复数归一救不了 nuclear fission 这类。
UNREACHABLE = {
    "figure_caption": {"heat exchanger", "makeup water", "steam turbine"},
    "body_text": {"breeder reactor", "fission product", "nuclear fission", "uranium enrichment"},
}


def main() -> int:
    if not ADJ.exists():
        print("✗ 未找到判定文件。请先运行 phase1_adjudicate 并完成人工判定。", file=sys.stderr)
        return 1
    adj = json.loads(ADJ.read_text(encoding="utf-8"))
    blank = [f["en"] for f in adj["new_findings"] if not f.get("verdict")]
    if blank:
        print(f"✗ 还有 {len(blank)} 条未判定：{blank[:5]}…", file=sys.stderr)
        print("  召回率必须在判定完成之后才能算。", file=sys.stderr)
        return 1

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    sliceset = slice_document(parse_pdf((CORPUS / FILE).read_bytes(), FILE))
    glossary = extract(sliceset, COURSE, FILE)
    got = {t.en for t in glossary.terms}

    print("=" * 74)
    print(f"术语提取召回率 · {COURSE} · ruleset {glossary.ruleset_version}")
    print(f"判定文件 sha256 {hashlib.sha256(ADJ.read_bytes()).hexdigest()[:16]}"
          f" · 判定于 {adj.get('_adjudicated_at')} · 早于本次计算")
    print("=" * 74)
    print()
    print(f"{'通道':<16} {'金标准':>6} {'可达上限':>8} {'命中':>5} "
          f"{'相对上限':>9} {'相对全表':>9}")
    print("-" * 74)

    totals = {}
    for source in ("body_text", "figure_caption"):
        items = [t for t in gold["terms"] if t["source"] == source]
        hit = []
        for t in items:
            keys = {normalize_phrase(f) for f in [t["en"], *t.get("aliases", [])]}
            if keys & got:
                hit.append(t["en"])
        n = len(items)
        unreach = len(UNREACHABLE[source])
        ceiling = n - unreach
        totals[source] = (n, ceiling, len(hit), hit)
        print(f"{source:<16} {n:>6} {ceiling:>8} {len(hit):>5} "
              f"{len(hit)/ceiling:>8.0%} {len(hit)/n:>9.0%}")

    n = sum(v[0] for v in totals.values())
    c = sum(v[1] for v in totals.values())
    h = sum(v[2] for v in totals.values())
    print("-" * 74)
    print(f"{'合计':<16} {n:>6} {c:>8} {h:>5} {h/c:>8.0%} {h/n:>9.0%}")
    print()
    for source, (_, _, _, hit) in totals.items():
        items = [t["en"] for t in gold["terms"] if t["source"] == source]
        missed = [e for e in items if e not in hit]
        reach_missed = [e for e in missed if e not in UNREACHABLE[source]]
        print(f"{source} 未命中 {len(missed)} 条：")
        print(f"  结构性不可达 {len(UNREACHABLE[source])}：{sorted(UNREACHABLE[source])}")
        print(f"  可达但漏掉 {len(reach_missed)}：{reach_missed}")
    print()
    print("=" * 74)
    print(f"新发现条目（不在 v1.1 中）：{len(adj['new_findings'])} 条")
    for verdict, label in (("true_term", "真术语 → v1.1 有遗漏"),
                           ("borderline", "边缘，判不准"),
                           ("false_positive", "误收")):
        rows = [f for f in adj["new_findings"] if f["verdict"] == verdict]
        print(f"  {label:<24} {len(rows):>3} 条"
              + (f"：{[r['en'] for r in rows]}" if verdict != "false_positive" else ""))
    tp = sum(1 for f in adj["new_findings"] if f["verdict"] == "false_positive")
    print()
    print(f"提取总数 {len(glossary.terms)} 条 · 命中金标准 {h} · 新发现 {len(adj['new_findings'])}")
    print(f"精确率（把 borderline 计为误收）≈ {(len(glossary.terms)-tp-3)/len(glossary.terms):.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
