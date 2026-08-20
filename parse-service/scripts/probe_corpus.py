"""语料探针：在 实验脚本/测试集/ 的 OCW 课件上跑解析，输出验证报告与 golden file。

用法::

    python -m scripts.probe_corpus                # 打印报告
    python -m scripts.probe_corpus --write-golden # 刷新 tests/golden/*.json
    python -m scripts.probe_corpus --compare-grid # 附带「网格近似 vs 精确并集」差异表

``--compare-grid`` 存在的原因：方案评审阶段的探针用 80x80 网格近似求图像并集面积，
实现改用精确扫描线后 coverage 会变。**差异必须显式列出，不能静默吸收**——
covreage 是扫描件判据的佐证项，它悄悄变了而没人知道，等于口径漂了。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "实验脚本" / "测试集"
GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parse_service.geometry import Rect, union_area  # noqa: E402
from parse_service.pdf import parse_pdf  # noqa: E402
from parse_service.scanned import SCAN_RULESET_VERSION  # noqa: E402


def grid_union(rects: list[Rect], page: Rect, n: int = 80) -> float:
    """方案评审阶段用的 n x n 网格近似，仅供对比，不参与任何判定。"""
    if not rects:
        return 0.0
    cw, ch = page.width / n, page.height / n
    hit = 0
    for i in range(n):
        cx = page.x0 + (i + 0.5) * cw
        for j in range(n):
            cy = page.y0 + (j + 0.5) * ch
            if any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects):
                hit += 1
    return hit / (n * n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-golden", action="store_true")
    ap.add_argument("--compare-grid", action="store_true")
    args = ap.parse_args()

    paths = sorted(CORPUS.glob("*.pdf"))
    if not paths:
        print(f"未找到语料：{CORPUS}", file=sys.stderr)
        return 1

    GOLDEN.mkdir(parents=True, exist_ok=True)
    summary: list[tuple[str, Counter, str, int]] = []
    diffs: list[tuple[str, int, float, float]] = []

    for path in paths:
        data = path.read_bytes()
        result = parse_pdf(data, path.name)
        kinds = Counter(p.kind for p in result.pages)
        summary.append(
            (path.name, kinds, result.document.verdict, result.document.usable_page_count)
        )

        print("=" * 96)
        print(f"{path.name}  ({result.document.page_count} 页)  producer={result.document.producer!r}")
        print(
            f"{'页':>3} {'kind':<13} {'规则':<15} {'不着墨':>7} {'白字':>6} "
            f"{'并集覆盖':>8} {'最大图':>7} {'字符':>6} {'内嵌字体':>7} {'块':>4} {'图':>4}"
        )
        for page in result.pages:
            e = page.evidence
            print(
                f"{page.index:>3} {page.kind:<13} {e.rule_fired:<15} "
                f"{e.invisible_text_ratio:>7.3f} {e.white_fill_text_ratio:>6.3f} "
                f"{e.image_coverage_union:>8.3f} {e.image_coverage_max:>7.3f} "
                f"{e.text_char_count:>6} {e.embedded_font_count:>7} "
                f"{len(page.blocks):>4} {len(page.images):>4}"
            )
        print(f"→ verdict={result.document.verdict}  {result.document.verdict_reason}")

        if args.compare_grid:
            import pymupdf

            doc = pymupdf.open(stream=data, filetype="pdf")
            for page in doc:
                pr = Rect.of(page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1)
                rects = [
                    Rect.of(r.x0, r.y0, r.x1, r.y1).clipped_to(pr)
                    for info in page.get_images(full=True)
                    for r in page.get_image_rects(info[0])
                ]
                rects = [r for r in rects if not r.is_empty]
                grid = grid_union(rects, pr)
                exact = union_area(rects) / (pr.area or 1.0)
                if abs(grid - exact) > 1e-4:
                    diffs.append((path.name, page.number + 1, grid, min(1.0, exact)))
            doc.close()

        if args.write_golden:
            out = {
                "file": path.name,
                "scan_ruleset_version": SCAN_RULESET_VERSION,
                "page_count": result.document.page_count,
                "verdict": result.document.verdict,
                "usable_page_count": result.document.usable_page_count,
                # golden 只对 kind 做回归，**不含 coverage** ——
                # coverage 是浮点几何量，换算法就会在末位变动，
                # 把它写进 golden 会让每次几何改动都红一片，掩盖真正的判定漂移
                "kinds": [p.kind for p in result.pages],
            }
            (GOLDEN / f"{path.stem}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    print("=" * 96)
    print("\n## 逐份汇总\n")
    print(f"{'文件':<34} {'digital':>8} {'scanned':>8} {'image':>7} {'empty':>6} {'verdict':>10}")
    for name, kinds, verdict, _ in summary:
        print(
            f"{name:<34} {kinds.get('digital_text', 0):>8} {kinds.get('scanned_ocr', 0):>8} "
            f"{kinds.get('image_only', 0):>7} {kinds.get('empty', 0):>6} {verdict:>10}"
        )

    if args.compare_grid:
        print("\n## 网格近似(80x80) vs 精确并集 —— 差异 > 1e-4 的页\n")
        if not diffs:
            print("（无）")
        else:
            print(f"{'文件':<30} {'页':>4} {'网格近似':>9} {'精确':>9} {'差值':>9}")
            for name, idx, grid, exact in diffs:
                print(f"{name:<30} {idx:>4} {grid:>9.4f} {exact:>9.4f} {exact - grid:>+9.4f}")
            vals = [abs(e - g) for _, _, g, e in diffs]
            print(
                f"\n受影响 {len(diffs)} 页；|差值| 最大 {max(vals):.4f}，均值 {sum(vals)/len(vals):.4f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
