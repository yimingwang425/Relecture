/**
 * 讲解之后的「调整讲法」：**相对调整**，每一个按钮都正好动一样东西（口径 F107）。
 *
 * ## 为什么换掉原来那套标签
 *
 * 原来这个弹窗复用开场的两块标签（「口语」「大白话」「先给结论」「要推导」……），
 * 选了之后**按多数票重新算语气、把标签当先验整条覆盖**——用户点「大白话」想要的是
 * 「比现在白一点」，拿到的是「语气被重置成 spoken」，而且看不出别的轴有没有被动。
 *
 * 现在每个按钮都说的是**相对现在怎么变**：「再短一点」就是详略沿阶梯走一格，
 * 别的什么都不动。这正是用户看完一段讲解之后的那个念头的形状。
 *
 * ## 同一样东西的一对按钮互斥
 *
 * 「再短一点」与「再细一点」同时点没有意义，UI 上选一个就取消另一个
 * （`PAIRS` 给出成对关系）。
 *
 * ## 到头了就说出来，不静默（口径 F07）
 *
 * 已经最简洁还点「再短一点」：配置不变，`atEdge` 里报出这一项，页面说明为什么没变。
 */

import { DEPTH_LADDER, DETAIL_LADDER, stepAlong, type StyleConfig } from "./axes";
import type { Tone } from "./taste";

/** 语气阶梯，**从书面到口语**。「口语一点」向口语端走一格。 */
export const TONE_LADDER: readonly Tone[] = ["academic", "neutral", "spoken"];

export type Current = { config: StyleConfig; tone: Tone };

type Adjustment = {
  id: string;
  label: string;
  /** 与它互斥的那一个。 */
  pair: string;
  /** 算出调整后的值；已经在那一头返回 null。 */
  apply: (cur: Current) => Current | null;
};

const withConfig = (cur: Current, patch: Partial<StyleConfig>): Current => ({
  config: { ...cur.config, ...patch },
  tone: cur.tone,
});

export const ADJUSTMENTS: readonly Adjustment[] = [
  {
    id: "shorter",
    label: "再短一点",
    pair: "longer",
    apply: (c) => {
      const v = stepAlong(DETAIL_LADDER, c.config.detail_level, 1);
      return v ? withConfig(c, { detail_level: v }) : null;
    },
  },
  {
    id: "longer",
    label: "再细一点",
    pair: "shorter",
    apply: (c) => {
      const v = stepAlong(DETAIL_LADDER, c.config.detail_level, -1);
      return v ? withConfig(c, { detail_level: v }) : null;
    },
  },
  {
    id: "more_examples",
    label: "多举例子",
    pair: "fewer_examples",
    apply: (c) => (c.config.use_examples === "more" ? null : withConfig(c, { use_examples: "more" })),
  },
  {
    id: "fewer_examples",
    label: "少举例子",
    pair: "more_examples",
    apply: (c) => (c.config.use_examples === "fewer" ? null : withConfig(c, { use_examples: "fewer" })),
  },
  {
    id: "shallower",
    label: "讲浅一点",
    pair: "deeper",
    apply: (c) => {
      const v = stepAlong(DEPTH_LADDER, c.config.depth, -1);
      return v ? withConfig(c, { depth: v }) : null;
    },
  },
  {
    id: "deeper",
    label: "讲深一点",
    pair: "shallower",
    apply: (c) => {
      const v = stepAlong(DEPTH_LADDER, c.config.depth, 1);
      return v ? withConfig(c, { depth: v }) : null;
    },
  },
  {
    id: "conclusion_first",
    label: "先说结论",
    pair: "buildup_first",
    apply: (c) =>
      c.config.structure === "conclusion_first" ? null : withConfig(c, { structure: "conclusion_first" }),
  },
  {
    id: "buildup_first",
    label: "先铺垫再说结论",
    pair: "conclusion_first",
    apply: (c) =>
      c.config.structure === "buildup_first" ? null : withConfig(c, { structure: "buildup_first" }),
  },
  {
    id: "more_spoken",
    label: "口语一点",
    pair: "more_formal",
    apply: (c) => {
      const v = stepAlong(TONE_LADDER, c.tone, 1);
      return v ? { config: c.config, tone: v } : null;
    },
  },
  {
    id: "more_formal",
    label: "书面一点",
    pair: "more_spoken",
    apply: (c) => {
      const v = stepAlong(TONE_LADDER, c.tone, -1);
      return v ? { config: c.config, tone: v } : null;
    },
  },
];

export function adjustmentById(id: string): Adjustment | undefined {
  return ADJUSTMENTS.find((a) => a.id === id);
}

/** 点一个按钮：选中它，并取消与它成对的那一个；再点一次取消。 */
export function toggleAdjustment(picked: readonly string[], id: string): string[] {
  if (picked.includes(id)) return picked.filter((x) => x !== id);
  const pair = adjustmentById(id)?.pair;
  return [...picked.filter((x) => x !== pair), id];
}

/**
 * 把选中的调整依次作用在当前配置上。**没点到的东西原样保留**——
 * 这是整件事最要紧的一条（同 from-text 那条「矫正以当前配置为基线」）。
 */
export function applyAdjustments(
  current: Current,
  picked: readonly string[],
): Current & { atEdge: string[]; unknown: string[] } {
  let cur: Current = { config: { ...current.config }, tone: current.tone };
  const atEdge: string[] = [];
  const unknown: string[] = [];
  for (const id of picked) {
    const adj = adjustmentById(id);
    if (!adj) {
      unknown.push(id);
      continue;
    }
    const next = adj.apply(cur);
    if (next) cur = next;
    else atEdge.push(adj.label);
  }
  return { ...cur, atEdge, unknown };
}
