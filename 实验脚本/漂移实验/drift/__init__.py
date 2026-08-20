"""PRD §8.2 漂移衰减曲线实验（A 类维度版本）。"""

from . import _paths  # noqa: F401  — 必须最先执行，注入 a_rules 路径

__all__ = ["models", "reviewer", "runner", "sanity"]
