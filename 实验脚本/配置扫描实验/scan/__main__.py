"""CLI：`python -m scan smoke|verify|report`。

**阶段 1 起才花钱**，`smoke` 与本目录的全部测试都用假模型，零 API 成本。

    $PY -m scan smoke                    # 假模型跑通全链路（零成本）
    $PY -m scan verify --run runs/<批次>  # 三层指纹守卫 + 缓存检查
    $PY -m scan report --run runs/<批次>  # 分臂表 + 判读

决策日期：2026-08-22
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import _paths  # noqa: F401
from .report import format_readout, format_scan_table
from .runner import summarise
from .verify_scan import verify_scan_run

HERE = Path(__file__).resolve().parent.parent


def _load_records(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def cmd_sanity(args: argparse.Namespace) -> int:
    from explain.fake import FakeExplainModel

    from .sanity import check_inputs_identical, check_points_distinguishable
    from .smoke import make_smoke_retriever

    retriever = make_smoke_retriever()
    model = FakeExplainModel()
    q = "发电厂烧了那么多煤，为什么不能把热全都变成电？"
    ok = True
    for check in (check_inputs_identical, check_points_distinguishable):
        result = check(model, retriever, q, "22.081J")
        print("\n".join(result.lines))
        ok = ok and result.passed
    return 0 if ok else 1


def cmd_smoke(args: argparse.Namespace) -> int:
    from .smoke import run_smoke

    out = run_smoke(Path(args.out) if args.out else HERE / "runs" / "smoke")
    print(f"假模型批次写入 {out}")
    ok, lines = verify_scan_run(out)
    print("\n".join(lines))
    records = _load_records(out)
    summary = summarise(records)
    print()
    print(format_scan_table(summary))
    print()
    print(format_readout(records, summary))
    return 0 if ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    ok, lines = verify_scan_run(Path(args.run))
    print("\n".join(lines))
    return 0 if ok else 1


def _merge(run_dirs: list[Path]) -> list[dict]:
    """跨批次合并。**合并前必须过第三层守卫。**

    这是 `metric_set_id` 唯一真正被用上的场合：阶段 1 与阶段 2 是两个批次，
    合并画在同一张图上之前，必须证明它们是同一个指标集。
    历史的 D02/D44 批次会在这里被挡住——那正是判断一要防的事。
    """
    all_records: list[dict] = []
    msids: set[str] = set()
    versions: set[str] = set()
    for d in run_dirs:
        rs = _load_records(d)
        msids |= {r.get("metric_set_id") for r in rs}
        versions |= {r.get("config_version") for r in rs}
        all_records += rs
    if len(msids) != 1:
        raise SystemExit(
            f"✗ 拒绝合并：{len(run_dirs)} 个批次含 {len(msids)} 个指标集 "
            f"{sorted(m or 'null' for m in msids)}。不同指标集的数据不得画进同一张图。"
        )
    if len(versions) != 1:
        raise SystemExit(f"✗ 拒绝合并：CONFIG_VERSION 不一致 {sorted(versions)}")
    return all_records


def cmd_report(args: argparse.Namespace) -> int:
    run_dirs = [Path(r) for r in args.run]
    run_dir = run_dirs[0]
    records = _merge(run_dirs)
    if len(run_dirs) > 1:
        print(f"· 已合并 {len(run_dirs)} 个批次，指标集一致\n")
    summary = summarise(records)
    print(format_scan_table(summary))
    if any(r.get("arm") == "cost" for r in records):
        print()
        print(format_scan_table(summary, arm="cost"))
    print()
    print(format_readout(records, summary))
    print()
    print(_calipers_multi(run_dirs, records))
    return 0


def _calipers_multi(run_dirs: list[Path], records: list[dict]) -> str:
    """P4 的三口径并列。**用本批次自己的 transcripts，不引用第 0 周样本。**

    旧样本测自 `term_annotation` 激活、prompt 明确要求写括注的配置；
    v1 的 prompt 里没有那句话。拿旧配置的敏感性分析解释新配置的结果，
    是观测/应用范围错配（通则七 #6）。
    """
    from .calipers import format_calipers, recompute_calipers

    texts = [
        p.read_text(encoding="utf-8")
        for d in run_dirs
        for p in sorted((d / "transcripts").glob("*.md"))
    ]
    counts = [r["observations"]["annotation_count"] for r in records]
    stats = {
        "total": sum(counts),
        "mean": (sum(counts) / len(counts)) if counts else 0.0,
        "rounds": len(counts),
        "rounds_with": sum(1 for c in counts if c),
    }
    glossary = (
        HERE.parent / "A类规则计算脚本" / "examples" / "glossary_22081J.json"
    )
    rows = recompute_calipers(texts, threshold=0.95, glossary_path=glossary)
    return format_calipers(rows, stats)


def cmd_run(args: argparse.Namespace) -> int:
    """**这是唯一会花钱的子命令。**"""
    from datetime import datetime

    from .live import run_live

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) if args.out else HERE / "runs" / stamp

    seen = {"n": 0, "cost": 0.0}

    def progress(record: dict) -> None:
        seen["n"] += 1
        seen["cost"] += record["round_cost_usd"]
        flag = "R" if record["triggered_regeneration"] else "."
        print(
            # 不要写 condition[:4]：with_review 与 without_review 的前 4 个字符
            # 都是 "with"，进度条会把两组显示成同一组
            f"[{seen['n']:>3}] {record['arm']}/{record['config_point_id']}/"
            f"{'审' if record['condition'] == 'with_review' else '无'} "
            f"r{record['round']:>2} {flag} ${seen['cost']:.4f}",
            flush=True,
        )

    from .live import build_retriever, stage1_config, stage2_config
    from .runner import run_scan
    from explain.llm import AnthropicModel

    retriever = build_retriever(budget=args.budget)
    params = dict(name="claude-sonnet-5", effort="medium",
                  thinking="adaptive", max_tokens=8000)
    build = {1: stage1_config, 2: stage2_config}[args.stage]
    scan = build(args.scan_rounds, args.cost_rounds)
    scan.extra_metadata["model_params"] = params
    scan.extra_metadata["retrieval_budget"] = args.budget
    planned = sum(a.planned_generations for a in scan.arms)
    print(f"计划生成 {planned} 次（不含重生成）→ {out}", flush=True)

    run_scan(
        scan=scan,
        retriever=retriever,
        make_model=lambda p, c, r: AnthropicModel(**params),
        out_dir=out,
        on_round=progress,
        resume=args.resume,
    )
    print(f"\n完成：{seen['n']} 轮 / ${seen['cost']:.4f}")
    ok, lines = verify_scan_run(out)
    print("\n".join(lines))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scan", description="F3a 配置扫描实验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sanity", help="输入同一性 + 配置点可分性（零 API 成本）")
    p.set_defaults(func=cmd_sanity)

    p = sub.add_parser("smoke", help="假模型跑通全链路（零 API 成本）")
    p.add_argument("--out")
    p.set_defaults(func=cmd_smoke)

    p = sub.add_parser("run", help="**真实调用**：阶段 1 / 阶段 2")
    p.add_argument("--stage", type=int, default=1, choices=(1, 2))
    p.add_argument("--out")
    p.add_argument("--resume", action="store_true",
                   help="续跑：跳过**已完整**的轨迹（只认整条，不认单轮）")
    p.add_argument("--budget", type=int, default=5)
    p.add_argument("--scan-rounds", type=int, default=None, help="截断扫描臂轮数（试跑用）")
    p.add_argument("--cost-rounds", type=int, default=None, help="截断成本臂轮数（试跑用）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("verify", help="三层指纹守卫 + 缓存检查")
    p.add_argument("--run", required=True)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("report", help="分臂表 + 判读（可传多个 --run 合并批次）")
    p.add_argument("--run", required=True, action="append")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
