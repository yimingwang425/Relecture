"""F1 的四条风格轴：取值、默认值、prompt 片段。

**A 类与 B 类在这里分道**：

- A 类（详细程度）→ `StyleConfig`，由 `explain.style_prompt.render_style_rules`
  渲染，**同一份配置也驱动审查判定**（口径 X05）。档位区间只读
  `scan.points.AXIS_POINTS`，本文件不重写那四组数字（口径 F13）。
- B 类（举例 / 结构 / 深度）→ 本文件自己拼措辞。**它们只进 prompt、不进判定**
  （PRD §8.5 未校准前不做 B 类判断），所以每条都带 `enforced: false`。

本阶段刻意**不扩展 `explain/style_prompt.py`**（约束 1）：产品侧怎么渲染 B 类
是阶段 3/4 的事，现在把它焊进生产模块，等于在没有判定的情况下先定死接口形状。
"""

from __future__ import annotations

from typing import Any

import _paths  # noqa: F401  路径注入必须先于下面的导入

from a_rules.config import StyleConfig
from scan.points import BASE, point_by_id

__all__ = [
    "AXES", "DEFAULTS", "PAIRS", "ENFORCED",
    "style_config_for", "render_b_class_rules", "B_CLASS_FRAGMENTS",
    "PROMPT_TEMPLATE_VERSION", "PROMPT_TEMPLATE_DIGEST",
]

#: 四条轴的**产品取值域**。
#:
#: ⚠️ **`detail_level` 不再是 `AXIS_POINTS` 的四个点**（口径 F23）。
#: `AXIS_POINTS` 是配置扫描实验的自变量点位——扫描要覆盖参数空间，
#: 产品要用户可分辨，**两个用途对点位的要求不同**。P1 因为完全被 P0 包含、
#: 且模型在两条指令下的自然段落长度都落在 P1 区间里，作为产品档位不可分辨，
#: 已退出（见 README §三的发现）。
#:
#: 区间数值仍只读 `scan.points`（口径 F13 不变）——**变的是取哪几个点，
#: 不是那些点的数值**。
#:
#: ⚠️ `style_config` 表的 CHECK 仍允许 `P1`（阶段 1 建表时四档还都在）。
#: 现在没有任何写入路径会产生 P1，所以留着无害；改 CHECK 与 F05 的反馈映射
#: 一并在阶段 3 做，见 README §二。
AXES: dict[str, tuple[str, ...]] = {
    "detail_level": ("P0", "P2", "P3"),
    "use_examples": ("more", "fewer"),
    "structure": ("conclusion_first", "buildup_first"),
    "depth": ("what", "why", "derivation"),
}

#: 口径 F01：每对只在目标维度上不同，非目标维度一律取默认值。
DEFAULTS: dict[str, str] = {
    "detail_level": "P0",
    "use_examples": "more",
    "structure": "conclusion_first",
    "depth": "why",
}

#: 哪条轴被 A 类审查执行（F1方案 §2.2）。四条里只有一条。
ENFORCED: dict[str, bool] = {
    "detail_level": True,
    "use_examples": False,
    "structure": False,
    "depth": False,
}

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

# ---------------------------------------------------------------- A 类

#: 产品档位按区间从宽到窄排序。反馈按钮「太啰嗦」沿它下移一格（口径 F05）。
DETAIL_LADDER: tuple[str, ...] = ("P0", "P2", "P3")


def style_config_for(detail_level: str) -> StyleConfig:
    """档位 → `StyleConfig`。区间只读 `scan.points`（口径 F13）。

    `point_by_id("P0").config()` 里 P0 的 30–120 不是字面量，而是
    `StyleConfig` 的默认值（P0 的 `overrides` 是空的）——走 `.config()` 取值
    才自动正确，抄一份数字进来早晚会漂。
    """
    if detail_level not in AXES["detail_level"]:
        raise ValueError(f"未知档位 {detail_level!r}")
    cfg = point_by_id(detail_level).config()
    assert cfg.explain_language == BASE["explain_language"]  # zh 讲解
    assert cfg.source_language == BASE["source_language"]    # en 课件
    return cfg

# ---------------------------------------------------------------- B 类

#: **B 类三条轴的措辞写死在这里**，并原样进 pairs.json，供人工核对时对照
#: （任务 D）。措辞变了，样例的可比性就变了——它和档位区间一样是口径。
B_CLASS_FRAGMENTS: dict[str, dict[str, str]] = {
    "use_examples": {
        "more": "多举例子：每讲一个要点，配一个具体例子或类比帮助理解。",
        "fewer": "少举例子：把要点本身讲清楚即可，不额外补例子或类比。",
    },
    "structure": {
        "conclusion_first": (
            "先给结论：开头第一段就说出最重要的那句判断，后面再解释它为什么成立。"
        ),
        "buildup_first": (
            "先铺垫后收结论：按「问题 → 机制 → 结论」的顺序展开，"
            "把最重要的那句判断放在最后。"
        ),
    },
    "depth": {
        "what": (
            "只讲是什么：说清楚这个概念是什么、拿来做什么用。"
            "不解释它为什么这样设计，也不做推导。"
        ),
        "why": (
            "讲到为什么：在说清是什么之外，解释这个机制为什么成立、为什么这样设计。"
        ),
        "derivation": (
            "讲到推导：在解释为什么的基础上，把关键结论一步步推出来，"
            "写出中间步骤和用到的假设。"
        ),
    },
}


#: B 类措辞的**版本号**。它是 `prompt_profile_id` 的构成字段之一
#: （口径 F04 / F39）：埋点事件里存「B 类三条轴的取值 + 这个版本号」，
#: 分析时由 `explain.prompt_profile.prompt_profile_id()` 算出 id。
#:
#: ⚠️ **上面的措辞改了，这个版本号必须一起改。** 不改的话，同一个
#: `prompt_profile_id` 会盖住两份不同的 prompt——而那正是 F04 要这个标识
#: 回答的问题（「讲解质量变了，是阈值变了还是 prompt 变了」）失效的方式，
#: 且它**不会报错**。
PROMPT_TEMPLATE_VERSION: str = "b1"

#: 上一行那个版本号对应的**措辞摘要**：`B_CLASS_FRAGMENTS` 块正文
#: （压掉空白后）的 sha256 前 12 位。
#:
#: 靠人记得同时改版本号是不行的（F13 那个坑的又一个入口），所以摘要在这里，
#: 由 `web/scripts/verify_style_axes.mjs` 重算并比对：措辞一动摘要就对不上，
#: `npm run verify:axes` 直接变红，逼人把版本号一起改。
#:
#: 它对块内的**任何**字符变化都敏感（包括缩进以外的注释），宁可多红一次也不漏。
#: 真要改措辞：先改措辞 → 跑 verify:axes 看它红并抄下新摘要 → 同时改
#: PROMPT_TEMPLATE_VERSION 与 web/lib/style/axes.ts 的同名常量。
PROMPT_TEMPLATE_DIGEST: str = "292b62f6b396"


def render_b_class_rules(style: dict[str, str]) -> str:
    """渲染 B 类三条。顺序固定——system 要能被缓存，任何非确定性都会让缓存失效。"""
    lines = []
    for axis in ("use_examples", "structure", "depth"):
        value = style[axis]
        if value not in AXES[axis]:
            raise ValueError(f"{axis} 取值 {value!r} 不在取值域 {AXES[axis]}")
        lines.append(f"- {B_CLASS_FRAGMENTS[axis][value]}")
    return "\n".join(lines)


def describe(style: dict[str, str]) -> dict[str, Any]:
    """样例的风格描述，含每条轴是否被审查执行。

    `enforced: false` 必须落进 pairs.json：v1.1 启用 B 类判定时要读历史数据，
    没有这个标记就分不清「当时判了但没过」和「当时根本没判」（F1方案 §2.2）。
    """
    return {axis: {"value": style[axis], "enforced": ENFORCED[axis]} for axis in AXES}
