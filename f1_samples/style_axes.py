"""F1 的四条风格轴 —— **本文件已退化为再导出**（2026-09-11）。

取值域、默认值、档位 → 区间、B 类措辞与渲染，**全部搬到了 `explain/`**：

============================  ===============================================
`explain.style_axes`          `AXES` / `DEFAULTS` / `ENFORCED` / `DETAIL_LADDER`
                              `STYLE_BASE` / `DETAIL_OVERRIDES` / `style_config_for`
`explain.style_prompt`        `B_CLASS_FRAGMENTS` / `render_b_class_rules`
                              `PROMPT_TEMPLATE_VERSION` / `PROMPT_TEMPLATE_DIGEST`
============================  ===============================================

两条理由，都在口径里：

- **F45**：B 类不判定 ≠ B 类不进 prompt。不渲染的话，6 步成对比较里有 4 步
  在采集一个对输出毫无影响的东西。
- **F46**：生产路径不得依赖实验脚本。原来 `style_config_for()` `import scan.points`，
  于是 F1→F3 接线会把 `实验脚本/配置扫描实验/` 拖进生产依赖。

**留下的只有样例生成自己的东西**：`PAIRS`（8 对的定义）与 `describe()`。
再导出而不是让调用方改 import，是 `llm.py` 从漂移实验提升上来时的同一处置——
`generate_pairs.py` 与 `repro/` 一行都不用改，它们跑的仍是同一份措辞。
"""

from __future__ import annotations

from typing import Any

import _paths  # noqa: F401  路径注入必须先于下面的导入

from explain.style_axes import (  # noqa: F401  —— 再导出
    AXES,
    DEFAULTS,
    DETAIL_LADDER,
    DETAIL_OVERRIDES,
    ENFORCED,
    STYLE_BASE,
    style_config_for,
)
from explain.style_prompt import (  # noqa: F401  —— 再导出
    B_CLASS_FRAGMENTS,
    PROMPT_TEMPLATE_DIGEST,
    PROMPT_TEMPLATE_VERSION,
    render_b_class_rules,
)

__all__ = [
    "AXES", "DEFAULTS", "PAIRS", "ENFORCED", "DETAIL_LADDER", "DETAIL_OVERRIDES",
    "STYLE_BASE", "style_config_for", "render_b_class_rules", "B_CLASS_FRAGMENTS",
    "PROMPT_TEMPLATE_VERSION", "PROMPT_TEMPLATE_DIGEST", "describe",
]

# ---------------------------------------------------------------- 样例专有

#: 八对。三对详细程度 + 一对举例 + 一对结构 + 三对深度 = 6 次比较的全部分支。
PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("detail_p0_p3", "detail_level", "P0", "P3"),
    # 原 P0 vs P1。P1 退出后改为 P0 vs P2——二分仍成立，比较次数仍是 6：
    #   #1 P0 vs P3 → 选 P0 → #2 P0 vs P2 → 结果 P0 或 P2
    #                 选 P3 → #2 P2 vs P3 → 结果 P2 或 P3
    ("detail_p0_p2", "detail_level", "P0", "P2"),
    ("detail_p2_p3", "detail_level", "P2", "P3"),
    ("examples_more_fewer", "use_examples", "more", "fewer"),
    ("structure_conclusion_buildup", "structure", "conclusion_first", "buildup_first"),
    ("depth_what_derivation", "depth", "what", "derivation"),
    ("depth_what_why", "depth", "what", "why"),
    ("depth_why_derivation", "depth", "why", "derivation"),
)


def describe(style: dict[str, str]) -> dict[str, Any]:
    """样例的风格描述，含每条轴是否被审查执行。

    `enforced: false` 必须落进 pairs.json：v1.1 启用 B 类判定时要读历史数据，
    没有这个标记就分不清「当时判了但没过」和「当时根本没判」（F1方案 §2.2）。
    """
    return {axis: {"value": style[axis], "enforced": ENFORCED[axis]} for axis in AXES}
