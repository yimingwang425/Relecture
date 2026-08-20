"""命令行入口，供 PRD §8.2 的批量实验使用。

    python -m a_rules --text 输出.md --glossary examples/glossary_22081J.json
    python -m a_rules --dir 样本目录/ --after "输出：" --jsonl > rounds.jsonl

单文件默认打人类可读的表；`--jsonl` 输出每行一条完整记录（含 details），
是漂移曲线的数据源。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .config import ConfigError, StyleConfig
from .evaluator import Evaluation, evaluate
from .glossary import Glossary, GlossaryError

_SUFFIXES = (".txt", ".md")

_CN_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")}


def _cn_number(token: str) -> int | None:
    """把「十二」「二十」「三」这类中文数字读成整数（只到 99，够用）。"""
    if not token or any(c not in _CN_DIGITS and c != "十" for c in token):
        return None
    if "十" not in token:
        return sum(_CN_DIGITS[c] for c in token) if len(token) == 1 else None
    head, _, tail = token.partition("十")
    return (_CN_DIGITS[head] if head else 1) * 10 + (_CN_DIGITS[tail] if tail else 0)


def _sort_key(path: Path) -> tuple[int, int | str]:
    """按轮次而非码位排序。

    漂移曲线的横轴就是轮次。`sorted()` 直接排文件名会得到
    「第一轮、第三轮、第二轮…」——中文数字按码位排，曲线的 x 轴会静默错乱。
    """
    stem = path.stem
    digits = re.search(r"\d+", stem)
    if digits:
        return (0, int(digits.group()))
    cn = re.search(r"[零一二三四五六七八九十]+", stem)
    if cn:
        value = _cn_number(cn.group())
        if value is not None:
            return (0, value)
    return (1, stem)


def _load_text(path: Path, after: str | None) -> str:
    raw = path.read_text(encoding="utf-8")
    if after and after in raw:
        # 真实样本常带「模型/Prompt/输出：」头部。不剥掉会把提示词计进统计——
        # 提示词里往往就写着术语要求，会虚高命中数
        return raw.split(after, 1)[1].strip()
    return raw


def _format(name: str, ev: Evaluation) -> str:
    lines = [f"# {name}", f"  配置 {ev.config_version}/{ev.config_fingerprint}  术语表 {ev.glossary_version or '—'}"]
    for d in ev.dimensions:
        if not d.activated:
            mark, value = "·", f"未激活（{d.reason}）"
        elif d.raw is None:
            mark, value = "·", f"无法计算（{d.reason}）"
        else:
            mark = "✓" if d.passed else "✗"
            value = f"{d.raw:.4g}"
        lines.append(f"  {mark} {d.name:<18}{value:<28}目标 {d.target}")
    verdict = {True: "全部达标", False: "未全部达标", None: "无可计分维度"}[ev.all_activated_passed]
    lines.append(f"  → {verdict}（{len(ev.scored)}/4 计分）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="a_rules", description="风格遵循度 A 类维度计算")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", type=Path, help="单个文本文件")
    src.add_argument("--dir", type=Path, help="目录，批量处理 .txt/.md")
    src.add_argument("--stdin", action="store_true", help="从标准输入读取")
    ap.add_argument("--config", type=Path, help="风格配置 JSON，缺省用默认值")
    ap.add_argument("--glossary", type=Path, help="术语表 JSON，缺省则术语维度不激活")
    ap.add_argument("--after", help="只取该标记之后的内容（如 '输出：'）")
    ap.add_argument("--jsonl", action="store_true", help="每行一条完整 JSON 记录")
    ap.add_argument("--json", action="store_true", help="输出单个 JSON 数组")
    args = ap.parse_args(argv)

    try:
        config = StyleConfig.from_file(args.config) if args.config else StyleConfig()
        glossary = Glossary.from_file(args.glossary) if args.glossary else None
    except (ConfigError, GlossaryError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.stdin:
        items = [("<stdin>", sys.stdin.read())]
    elif args.text:
        if not args.text.is_file():
            print(f"错误：文件不存在 {args.text}", file=sys.stderr)
            return 2
        items = [(args.text.name, _load_text(args.text, args.after))]
    else:
        if not args.dir.is_dir():
            print(f"错误：目录不存在 {args.dir}", file=sys.stderr)
            return 2
        files = sorted((p for p in args.dir.iterdir() if p.suffix in _SUFFIXES), key=_sort_key)
        if not files:
            print(f"错误：{args.dir} 下没有 .txt/.md 文件", file=sys.stderr)
            return 2
        items = [(p.name, _load_text(p, args.after)) for p in files]

    records = []
    for name, text in items:
        ev = evaluate(text, config, glossary)
        if args.jsonl:
            print(json.dumps({"source": name, **ev.to_dict()}, ensure_ascii=False))
        elif args.json:
            records.append({"source": name, **ev.to_dict()})
        else:
            print(_format(name, ev))
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
