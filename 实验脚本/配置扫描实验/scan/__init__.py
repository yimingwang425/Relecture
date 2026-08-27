"""F3a · 配置扫描对照实验。

**自变量是配置偏离默认的程度**，不是「默认 vs 极端」的二元对照。
产出不是「审查有没有用」，是「**审查的收益在配置空间哪个区域出现、拐点在哪**」。

框架**复用漂移实验**，不重写：`drift.verify` 原样 import，
模型抽象与定价已提升到 `explain.llm`。本目录只写这次实验特有的部分——
配置点、编排、三层指纹守卫、判读规则、报告。

判读规则（主判据 / 判读带 / E10）写在 `readout.py` 里，**跑之前定死**。
"""

from . import _paths  # noqa: F401  — 必须最先执行

__all__ = ["points", "runner", "readout", "report", "sanity", "verify_scan"]
