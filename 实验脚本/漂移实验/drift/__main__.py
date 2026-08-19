"""实验 CLI。

    python -m drift sanity   --config config/experiment.json
    python -m drift run      --config config/experiment.json --fake
    python -m drift run      --config config/experiment.json      # 真实调用

先用 --fake 跑通全链路（确认 JSONL 结构、排序、成本日志都正确），
再跑真实调用——正式实验最好一次成。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import _paths  # noqa: F401
from a_rules.config import CONFIG_VERSION, ConfigError, StyleConfig
from a_rules.glossary import Glossary, GlossaryError

from .aggregate import DIMENSIONS, aggregate, format_table
from .models import FAKE_PREFIX, AnthropicModel, FakeModel, Model
from .reviewer import RuleReviewer
from .runner import RoundRecord, run_condition
from .sanity import SanityResult, check_inputs_identical, sample_first_round

ROOT = Path(__file__).resolve().parent.parent


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def _build(cfg: dict[str, Any], cfg_dir: Path, fake: bool) -> tuple[Any, str, StyleConfig, Glossary, list[str]]:
    courseware = _resolve(cfg_dir, cfg["courseware_text"]).read_text(encoding="utf-8")
    style = StyleConfig.from_file(_resolve(cfg_dir, cfg["style_config"]))
    glossary = Glossary.from_file(_resolve(cfg_dir, cfg["glossary"]))
    questions = _load_json(_resolve(cfg_dir, cfg["questions"]))
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise SystemExit("questions 文件必须是字符串数组")

    system = cfg["system_prompt_template"].format(courseware=courseware)

    m = cfg["model"]

    def make_model(replicate: int = 0) -> Model:
        """每个 replicate 拿一个全新实例——replicate 之间必须互不影响。"""
        if fake:
            return FakeModel(
                name=FAKE_PREFIX + m["name"], seed=cfg.get("seed", 0), replicate=replicate
            )
        return AnthropicModel(
            name=m["name"],
            effort=m.get("effort", "medium"),
            max_tokens=m.get("max_tokens", 8000),
            thinking=m.get("thinking", "adaptive"),
        )

    return make_model, system, style, glossary, questions


def _metadata(cfg: dict[str, Any], model: Model, style: StyleConfig, glossary: Glossary, fake: bool) -> dict[str, Any]:
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "experiment": cfg.get("name", "drift"),
        "fake_model": fake,
        "model": {"name": model.name, "params": getattr(model, "params", {})},
        "config_version": CONFIG_VERSION,
        "config_fingerprint": style.fingerprint(),
        "style_config": style.to_dict(),
        "glossary_version": glossary.version,
        "glossary_frozen": glossary.frozen,
        "glossary_stats": glossary.stats(),
        "max_regenerations": cfg.get("max_regenerations", 2),
        "repeats": cfg.get("repeats", 3),
        "repeat_semantics": (
            "每个 replicate 是一次独立的完整对话轨迹，不是同一条对话内的重采样。"
            "漂移是轨迹随轮次演化出的性质，轨迹内重采样没有统计意义。"
        ),
        "error_band": (
            "作图用均值 + 全距（min–max）。n 很小时样本标准差不稳定，"
            "±1SD 会暗示一个并不存在的分布假设；标准差仅作参考量输出。"
        ),
        "retrieval": "none — 课件全量在 system prompt 中；结论仅适用于该条件",
        "b_class_dimensions": "not included — 等 judge 校准就绪后再做",
        "randomness_note": (
            "temperature/top_p/top_k 在 Claude Opus 5 / Sonnet 5 上已移除，传入返回 400。"
            "可固定并记录的是 effort 与 thinking，见 model.params。"
        ),
    }


def cmd_sanity(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config).resolve()
    cfg = _load_json(cfg_path)
    make_model, system, style, glossary, questions = _build(cfg, cfg_path.parent, args.fake)
    model = make_model()
    reviewer = RuleReviewer(style, glossary)

    identical, sig_a, sig_b = check_inputs_identical(model, system, questions[0])
    samples = (
        sample_first_round(
            model=model, system=system, question=questions[0], reviewer=reviewer, repeats=args.repeats
        )
        if args.repeats
        else {"repeats": 0, "note": "未采样（--repeats 0）"}
    )
    result = SanityResult(identical, sig_a, sig_b, samples)

    print(json.dumps({"metadata": _metadata(cfg, model, style, glossary, args.fake),
                      "sanity": result.to_dict()}, ensure_ascii=False, indent=2))
    if not result.ok:
        print("\n两组第 1 轮的输入不一致——实现有 bug，后续曲线不可信。", file=sys.stderr)
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config).resolve()
    cfg = _load_json(cfg_path)
    make_model, system, style, glossary, questions = _build(cfg, cfg_path.parent, args.fake)
    reviewer = RuleReviewer(style, glossary)
    repeats = max(1, int(args.repeats if args.repeats is not None else cfg.get("repeats", 3)))

    identical, sig_a, sig_b = check_inputs_identical(make_model(), system, questions[0])
    if not identical and not args.skip_sanity:
        print("两组第 1 轮输入不一致，已中止。用 --skip-sanity 强行继续。", file=sys.stderr)
        return 1

    out_dir = Path(args.out or ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = _metadata(cfg, make_model(), style, glossary, args.fake)
    meta["sanity"] = {"inputs_identical": identical, "signature": sig_a}
    (out_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    jsonl = (out_dir / "rounds.jsonl").open("w", encoding="utf-8")
    transcripts = out_dir / "transcripts"
    transcripts.mkdir(exist_ok=True)

    all_records: list[RoundRecord] = []

    def sink(record: RoundRecord) -> None:
        all_records.append(record)
        jsonl.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        jsonl.flush()
        # replicate 与轮次都零填充，保证按文件名排序 = 按 (replicate, 轮次) 排序
        name = f"{record.condition}-r{record.replicate:02d}-{record.round_index:02d}.md"
        (transcripts / name).write_text(record.final_text, encoding="utf-8")
        rv = record.final_review
        passed = "—" if rv is None else ("✓" if rv.passed else "✗")
        print(
            f"  [{record.condition:<14} r{record.replicate}] 第 {record.round_index:2d} 轮 "
            f"{passed} 重生成 {record.regenerations} "
            f"${record.cost:.4f}",
            flush=True,
        )

    planned = repeats * 2 * len(questions)
    print(f"计划生成 {planned} 次（{repeats} replicate × 2 组 × {len(questions)} 轮）；"
          f"重生成会在此之上追加，上限 ×{cfg.get('max_regenerations', 2) + 1}")

    try:
        for condition, enabled in (("with_review", True), ("without_review", False)):
            for replicate in range(1, repeats + 1):
                print(f"\n=== {condition} （审查{'开' if enabled else '关'}） replicate {replicate}/{repeats} ===")
                run_condition(
                    condition=condition,
                    replicate=replicate,
                    questions=questions,
                    model=make_model(replicate),  # 每条轨迹一个全新实例
                    system=system,
                    reviewer=reviewer,
                    review_enabled=enabled,
                    max_regenerations=cfg.get("max_regenerations", 2),
                    on_round=sink,
                )
    finally:
        jsonl.close()

    points = aggregate(r.to_dict() for r in all_records)
    (out_dir / "summary.json").write_text(
        json.dumps([p.to_dict() for p in points], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total_cost = sum(r.cost for r in all_records)
    total_calls = sum(len(r.attempts) for r in all_records)
    print("\n" + "\n\n".join(format_table(points, d) for d in DIMENSIONS))
    print(f"\n实际生成 {total_calls} 次（计划 {planned} + 重生成 {total_calls - planned}），"
          f"总成本 ${total_cost:.4f}")

    print(f"\n输出：{out_dir}")
    print("  metadata.json  实验元数据（模型参数、配置指纹、术语表版本、重复次数与误差带口径）")
    print("  rounds.jsonl   每轮每 replicate 一条完整记录")
    print("  summary.json   按 (条件, 轮次, 维度) 聚合的均值与全距 —— 作图数据源")
    print("  transcripts/   每轮最终讲解文本")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="drift", description="PRD §8.2 漂移衰减曲线实验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("sanity", cmd_sanity), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--config", default=str(ROOT / "config" / "experiment.json"))
        p.add_argument("--fake", action="store_true", help="用确定性假模型跑通链路，不调 API")
        p.set_defaults(func=fn)

    sub.choices["sanity"].add_argument("--repeats", type=int, default=3)
    sub.choices["run"].add_argument("--out", help="输出目录，缺省按时间戳新建")
    sub.choices["run"].add_argument("--repeats", type=int, default=None,
                                    help="每条件的独立轨迹数，缺省取配置里的 repeats")
    sub.choices["run"].add_argument("--skip-sanity", action="store_true")

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, GlossaryError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"错误：文件不存在 {exc.filename}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
