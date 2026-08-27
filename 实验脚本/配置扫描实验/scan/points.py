"""配置点。**这张表是本实验的自变量定义。**

## 为什么是「偏离程度」而不是「默认 vs 极端」

第 0 周那三维「两组曲线基本重合、全程达标」是在**固定中庸配置 + 固定课件**下测的。
用极端配置重测只是把外推方向反过来——真实用户会选什么配置，F1 还没做，现在不知道。
那是通则七第 6 例的形态：**观测范围与应用范围错配**。

取 5 个点扫描，等 F1 拿到真实配置分布，把它落在曲线上即可，不需要重跑。

## 偏离度怎么定义

按**模型的自然行为离区间边界还有多远**。第 0 周实测（无审查组）平均段落
**29–78 字**，中枢约 55。P0 的 30–120 把中枢放在区间中部，P3 的 15–40
把它放在区间**外**。

## 为什么详细程度占 3 个点、引用长度一个点都不占

- **详细程度**是三维里唯一在 F1 中被用户连续调节的维度，「拐点在哪」长在它身上
- **引用长度**的 100 字来自 PRD §12.1 的**版权约束，不是风格偏好**（口径 D16）。
  真实用户不会把它调到 40——把它当自变量扫，测的是产品里不存在的配置。
  它在本实验里是**因变量**：靠提问诱发（扫描臂第 3 轮），上限恒为 100
- **语言一致性**的可调空间只有阈值高低一维，且第 0 周实测两组都在 0.91–1.00，
  取一个点问「提到接近满分会不会开始触发」就够了

## 只扫偏短方向

偏长（100–200 字）对 LLM 是**容易的方向**——让模型多写从来不难；
偏短与模型的天然啰嗦相反，也正是 PRD §2 的 P1 痛点方向。**先扫会咬人的一侧。**
偏长方向记入待办，触发条件：F1 的真实配置分布显示存在偏长用户。

## P0–P3 是一条有序轴，P4 不是

主判据（口径 E05）是**触发率沿 P0→P3 单调不减**，因为单点 n=12 时置信区间
宽达 ±25pp，逐点分类会让实验在最关心的 P2/P3 上宣布自己没结论。
单调性的独立条件有两组：**轨迹 × 轴上的位置**，这才是 R17 要的重复。

**P4 不在这条轴上**，它是语言轴的孤点，按判读带（E05a）单独判。

决策日期：2026-08-22
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from a_rules.config import StyleConfig

__all__ = ["ConfigPoint", "POINTS", "AXIS_POINTS", "point_by_id", "build_config"]

#: 全部点共享的基底。跨语言 → chinese_ratio 激活、term_annotation 仍不激活（v1）。
BASE: dict[str, Any] = {"explain_language": "zh", "source_language": "en"}


@dataclass(frozen=True, slots=True)
class ConfigPoint:
    id: str
    overrides: dict[str, Any]
    axis: str | None
    #: 轴上的序号。主判据检查触发率沿它单调不减；`None` 表示不在轴上。
    axis_order: int | None
    deviation: str
    rationale: str

    def config(self) -> StyleConfig:
        return StyleConfig.from_dict({**BASE, **self.overrides})

    def to_dict(self) -> dict[str, Any]:
        cfg = self.config()
        return {
            "id": self.id,
            "axis": self.axis,
            "axis_order": self.axis_order,
            "deviation": self.deviation,
            "rationale": self.rationale,
            "overrides": self.overrides,
            "config_fingerprint": cfg.fingerprint(),
            "metric_set_id": cfg.metric_set_id(),
        }


POINTS: tuple[ConfigPoint, ...] = (
    ConfigPoint(
        id="P0",
        overrides={},
        axis="detail",
        axis_order=0,
        deviation="0（默认）",
        rationale="30–120 字，第 0 周实测校准值。模型自然中枢 ≈55 落在区间中部，"
        "余量充足。**与第 0 周同配置，成本与触发率可交叉引用**",
    ),
    ConfigPoint(
        id="P1",
        overrides={"paragraph_length": {"min": 40, "max": 90}},
        axis="detail",
        axis_order=1,
        deviation="轻",
        rationale="自然中枢仍在区间内，只是余量收窄。用来看拐点是否在很轻的偏离处就出现",
    ),
    ConfigPoint(
        id="P2",
        overrides={"paragraph_length": {"min": 25, "max": 55}},
        axis="detail",
        axis_order=2,
        deviation="中",
        rationale="自然中枢**贴上界**。这是最可能出现拐点的位置",
    ),
    ConfigPoint(
        id="P3",
        overrides={"paragraph_length": {"min": 15, "max": 40}},
        axis="detail",
        axis_order=3,
        deviation="强",
        rationale="自然中枢**在区间外**，模型必须真的压缩。"
        "⚠️ 这一点必须同时读 E10 的分段规避观察项——平均段落字数可以靠多敲回车满足",
    ),
    ConfigPoint(
        id="P4",
        overrides={"chinese_ratio": {"min": 0.95}},
        axis="language",
        axis_order=None,
        deviation="语言轴单点",
        rationale="第 0 周实测两组都在 0.91–1.00，0.85 的默认阈值离得很远。"
        "提到 0.95 问「接近满分会不会开始触发」。**不在详细程度轴上，按判读带单独判**",
    ),
)

#: 详细程度轴上的点，按偏离度排序。主判据（E05）在这条序列上检查单调性。
AXIS_POINTS: tuple[ConfigPoint, ...] = tuple(
    sorted((p for p in POINTS if p.axis == "detail"), key=lambda p: p.axis_order or 0)
)


def point_by_id(pid: str) -> ConfigPoint:
    for p in POINTS:
        if p.id == pid:
            return p
    raise KeyError(f"未知配置点 {pid!r}，可选 {[p.id for p in POINTS]}")


def build_config(pid: str) -> StyleConfig:
    return point_by_id(pid).config()
