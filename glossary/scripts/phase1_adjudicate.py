"""阶段 1：导出「新发现条目」供人工判定。**本脚本不计算召回率。**

顺序是方法的一部分，不是流程习惯：

> **新发现条目的人工判定必须在看到召回率之前完成。**

否则会不自觉地把边缘条目往「误收」判——那样召回率好看。
这与「阈值不迭代」是同一道防线，防的是**判定环节**而不是调参环节。

本脚本与 `phase2_recall.py` 分开，就是为了让这个顺序**结构上成立**：
阶段 2 拒绝在没有判定文件时运行，并把判定文件的哈希记进报告。

用法::

    python -m scripts.phase1_adjudicate            # 生成待判定文件
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
OUT = ROOT / "runs" / "new_findings.json"

COURSE = "22.081J"
FILE = "MIT22_081JF10_lec01b.pdf"


def gold_keys() -> set[str]:
    data = json.loads(GOLD.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for t in data["terms"]:
        for form in [t["en"], *t.get("aliases", [])]:
            k = normalize_phrase(form)
            if k:
                keys.add(k)
    return keys


def main() -> int:
    sliceset = slice_document(parse_pdf((CORPUS / FILE).read_bytes(), FILE))
    glossary = extract(sliceset, COURSE, FILE)
    known = gold_keys()

    new = [t for t in glossary.terms if t.en not in known]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_instructions": (
            "对每条填 verdict：true_term（真术语，说明 v1.1 有遗漏）/ "
            "false_positive（误收）/ borderline（边缘）。填 reason。"
            "**判定完成前不要运行 phase2_recall.py。**"
        ),
        "course": COURSE,
        "ruleset_version": glossary.ruleset_version,
        "extracted_total": len(glossary.terms),
        "gold_total": len(json.loads(GOLD.read_text(encoding='utf-8'))["terms"]),
        "new_findings": [
            {
                "en": t.en,
                "source": t.source,
                "confidence": t.confidence,
                "heuristics": t.heuristics,
                "pages": t.pages,
                "surface_forms": t.surface_forms,
                "verdict": None,
                "reason": "",
            }
            for t in sorted(new, key=lambda x: (x.source, x.en))
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"提取 {len(glossary.terms)} 条，其中不在 v1.1 中的新发现 {len(new)} 条")
    print(f"已写入 {OUT.relative_to(REPO)}")
    print()
    print("⚠️ 请先完成人工判定，再运行 phase2_recall.py。")
    print("   本脚本刻意不输出召回率——先看到召回率会影响判定。")
    print()
    print(f"{'source':<16} {'conf':<8} {'en'}")
    for t in sorted(new, key=lambda x: (x.source, x.en)):
        print(f"  {t.source:<14} {t.confidence:<8} {t.en}")
    print()
    print(f"待判定文件 sha256: {hashlib.sha256(OUT.read_bytes()).hexdigest()[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
