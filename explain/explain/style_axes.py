"""四条风格轴的**产品定义**：取值域、默认值、档位 → 区间。

## 这份文件是从 `f1_samples/style_axes.py` 提升上来的（2026-09-11）

`f1_samples/README.md` §二 一直挂着一项「把 `AXIS_POINTS` 提升到生产模块」，
触发条件是「有生产消费方」。**现在触发了**：F1→F3 接线要求生产路径能把
`style_config.detail_level`（`'P0'`/`'P2'`/`'P3'`）变成 `StyleConfig`，
而原来唯一做这件事的 `f1_samples.style_axes.style_config_for()`
`import scan.points` —— **生产路径依赖实验脚本**（口径 F46）。

搬家的方向是「**生产拥有，实验再导出**」，与 `llm.py` 从漂移实验提升上来的先例一致：
`scan/points.py` 的 P0/P2/P3 现在从这里读，P1 与 P4 仍由它自己定义
（P1 已退出产品档位（F23），P4 是语言轴的孤点，两者都不是产品的东西）。

**数值一个都没动。** `explain/tests/test_style_axes.py` 把搬家前测得的三个
`config_fingerprint` 钉死在断言里：`P0 a223ca42e77a` / `P2 702c48759329` /
`P3 4e0fbd6bec46`。搬家改变了出处，**不允许改变任何一个数**——
改了的话，`pairs.json` 里 16 个样例与生产就分叉了（口径 F19）。

## 与 `a_rules` 的分工

`a_rules.StyleConfig` 是**阈值**的家，它不认识「产品档位」这个概念——
正如它刻意不含 B 类字段（口径 F04）。「P0 对应哪个区间」是产品决策，
所以住在这里；`explain` 是配置同时驱动 prompt 与判定的地方（口径 X05）。

决策日期：2026-09-11
"""

from __future__ import annotations

from typing import Any

from a_rules.config import StyleConfig

__all__ = [
    "AXIS_IDS", "AXES", "DEFAULTS", "ENFORCED", "DETAIL_LADDER",
    "STYLE_BASE", "DETAIL_OVERRIDES", "style_config_for",
]

AXIS_IDS: tuple[str, ...] = ("detail_level", "use_examples", "structure", "depth")

#: 四条轴的**产品取值域**。
#:
#: ⚠️ **`detail_level` 不再是 `AXIS_POINTS` 的四个点**（口径 F23）。
#: `AXIS_POINTS` 是配置扫描实验的自变量点位——扫描要覆盖参数空间，
#: 产品要用户可分辨，**两个用途对点位的要求不同**。P1 因为完全被 P0 包含、
#: 且模型在两条指令下的自然段落长度都落在 P1 区间里，作为产品档位不可分辨，
#: 已退出（见 README §三的发现）。
#:
#: 区间数值的唯一出处是下面的 `DETAIL_OVERRIDES`（口径 F13）。**2026-09-11 起
#: 方向反过来了**：过去是本表读 `scan.points`，现在是 `scan/points.py` 的
#: P0/P2/P3 读这里——生产不依赖实验脚本（口径 F46）。
#:
#: `style_config` 表的 CHECK 已在阶段 3（迁移 `20260909130000`）收紧到 P0/P2/P3。
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

#: 产品档位按区间从宽到窄排序。反馈按钮「太啰嗦」沿它下移一格（口径 F05）。
DETAIL_LADDER: tuple[str, ...] = ("P0", "P2", "P3")

# ---------------------------------------------------------------- 档位 → 区间

#: 全部档位共享的基底。跨语言 → `chinese_ratio` 激活、`term_annotation` 仍不激活（v1）。
#:
#: 原 `scan.points.BASE`。搬上来之后 `scan/points.py` 从这里读，不再自己定义——
#: 两处各写一份，漂了不会报错，只会让实验与产品测的是两个对象。
STYLE_BASE: dict[str, Any] = {"explain_language": "zh", "source_language": "en"}

#: 产品档位 → `StyleConfig` 的覆盖项。**区间数值在代码里的唯一出处**（口径 F13）。
#:
#: ⚠️ **P0 是空字典，这不是遗漏**：30–120 是 `StyleConfig` 的**默认值**，
#: 不是任何地方的字面量。写成 `{"paragraph_length": {"min": 30, "max": 120}}`
#: 会凭空造出第二处出处，而它与默认值漂开时没有任何东西会红。
#:
#: P1 不在这里（口径 F23：完全被 P0 包含，作为产品档位不可分辨，已退出）。
#: P4 也不在（语言轴的实验孤点，不是产品档位）。两者仍由 `scan/points.py` 自己定义。
DETAIL_OVERRIDES: dict[str, dict[str, Any]] = {
    "P0": {},
    "P2": {"paragraph_length": {"min": 25, "max": 55}},
    "P3": {"paragraph_length": {"min": 15, "max": 40}},
}


def style_config_for(detail_level: str) -> StyleConfig:
    """产品档位 → `StyleConfig`。

    这是 `style_config.detail_level` 那一列（`'P0'`/`'P2'`/`'P3'`）进入生成链路的
    **唯一入口**：F1 存的是档位字符串，而 prompt 渲染与 A 类判定都吃 `StyleConfig`。

    取值域外的档位**抛异常，不回落默认值**：回落会让「用户选了 P3」和
    「库里有一个我们不认识的值」在生成结果里长得一模一样（同 F32 的「回落必须显式」）。
    """
    if detail_level not in DETAIL_OVERRIDES:
        raise ValueError(
            f"未知档位 {detail_level!r}，产品档位只有 {sorted(DETAIL_OVERRIDES)}"
            "（P1 已退出，见口径 F23）"
        )
    return StyleConfig.from_dict({**STYLE_BASE, **DETAIL_OVERRIDES[detail_level]})
