"""切片结果报告：切片数、char_count 分布、过短切片的图像覆盖率构成。

用法::

    python -m scripts.report_corpus

「过短切片各自的 image_coverage_union」是「过短页要不要合并」的判据来源：
纯目录页（覆盖率 ≈ 0）与图密集页（覆盖率高、实质内容在图里）需要完全不同的处置，
把它们混在一个「过短」桶里做合并会同时做错两件事。
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "实验脚本" / "测试集"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parse_service.pdf import parse_pdf  # noqa: E402

from slicer import slice_document  # noqa: E402

#: 「过短」的观察阈值。**不是判定阈值**——本报告不做任何判定，
#: 它只是把方案评审时用的 120 字符沿用下来，以便与方案里的「18 页」对得上。
SHORT_OBSERVATION_CHARS = 120


def dist(name: str, values: list[int]) -> None:
    if not values:
        print(f"  {name}: 无")
        return
    v = sorted(values)
    q = lambda p: v[min(len(v) - 1, int(p * len(v)))]  # noqa: E731
    print(
        f"  {name:<14} n={len(v):>4} min={v[0]:>5} p25={q(.25):>5} p50={statistics.median(v):>6.0f} "
        f"p75={q(.75):>5} p90={q(.90):>5} p95={q(.95):>5} max={v[-1]:>5}"
    )


def main() -> int:
    paths = sorted(CORPUS.glob("*.pdf"))
    if not paths:
        print(f"未找到语料：{CORPUS}", file=sys.stderr)
        return 1

    all_chars: list[int] = []
    short: list[tuple[str, int, int, float, int, str]] = []
    total_slices = total_skipped = 0

    print("## 逐份切片结果\n")
    print(f"{'文件':<32} {'总页':>4} {'切片':>4} {'跳过':>4} {'char_count 中位':>13} {'max':>6}")
    for path in paths:
        parsed = parse_pdf(path.read_bytes(), path.name)
        ss = slice_document(parsed)
        chars = [s.char_count for s in ss.slices]
        all_chars += chars
        total_slices += ss.manifest.slice_count
        total_skipped += len(ss.manifest.skipped_pages)
        med = f"{statistics.median(chars):.0f}" if chars else "—"
        mx = max(chars) if chars else 0
        print(
            f"{path.name:<32} {ss.manifest.page_count:>4} {ss.manifest.slice_count:>4} "
            f"{len(ss.manifest.skipped_pages):>4} {med:>13} {mx:>6}"
        )
        for s in ss.slices:
            if s.char_count < SHORT_OBSERVATION_CHARS:
                sig = s.page_signals[0]
                short.append(
                    (
                        path.name,
                        s.page_indices[0],
                        s.char_count,
                        sig.image_coverage_union,
                        len(s.images),
                        (s.title or "")[:34],
                    )
                )

    print(f"\n  合计：切片 {total_slices}，跳过页 {total_skipped}")

    print("\n## char_count 分布（全部切片）\n")
    dist("char_count", all_chars)
    print(f"\n  > 2000 字符的切片：{sum(1 for c in all_chars if c > 2000)}"
          f"（超长页拆分路径 0 触发）")

    print(f"\n## char_count < {SHORT_OBSERVATION_CHARS} 的切片（n={len(short)}）\n")
    print(f"{'文件':<26} {'页':>4} {'chars':>6} {'图覆盖':>7} {'图数':>4}  标题")
    for name, page, chars, cov, nimg, title in sorted(short, key=lambda r: -r[3]):
        print(f"{name[:24]:<26} {page:>4} {chars:>6} {cov:>7.3f} {nimg:>4}  {title!r}")

    print("\n### 按图像覆盖率分组（不做判定，只呈现构成）\n")
    buckets = [
        ("图覆盖 = 0（无图，纯文本页）", lambda c: c == 0.0),
        ("0 < 图覆盖 < 0.2", lambda c: 0.0 < c < 0.2),
        ("0.2 ≤ 图覆盖 < 0.5", lambda c: 0.2 <= c < 0.5),
        ("图覆盖 ≥ 0.5（实质内容很可能在图里）", lambda c: c >= 0.5),
    ]
    for label, pred in buckets:
        hit = [r for r in short if pred(r[3])]
        pct = f"{len(hit)/len(short):.0%}" if short else "—"
        pages = ", ".join(f"{r[0][3:9]} p{r[1]}" for r in hit) or "—"
        print(f"  {label:<38} {len(hit):>2} 页 ({pct:>4})  {pages}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
