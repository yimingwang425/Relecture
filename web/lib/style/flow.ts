/**
 * 成对比较的固定顺序（6 次）。纯逻辑，无 I/O——所以它能同时跑在服务端与浏览器，
 * 而 `pairs.ts`（要读 `f1_samples/pairs.json`）只跑在服务端。
 *
 * ```
 * #1 详细程度  P0 vs P3     选 P0 → #2 P0 vs P2 → 结果 P0 或 P2
 *                           选 P3 → #2 P2 vs P3 → 结果 P2 或 P3
 * #3 举例      more vs fewer
 * #4 结构      conclusion_first vs buildup_first
 * #5 深度      what vs derivation
 *                           选 what       → #6 what vs why        → 结果 what 或 why
 *                           选 derivation → #6 why vs derivation  → 结果 why 或 derivation
 * ```
 *
 * **不做主动选题**（后验估计 + 信息增益选下一对）：4 轴 / 6 次没有可选的，
 * 触发条件是维度数或预算显著变大（`f1_samples/README.md` §二）。
 *
 * 二分只在详细程度与深度上；举例与结构各一次，因为它们只有两个取值。
 * 8 对样例恰好覆盖这棵树的全部分支——`verify_style_axes.mjs` 断言两边逐一对得上。
 */

import { AXES, DEFAULTS, type AxisId, type StyleConfig } from "./axes";

export const TOTAL_STEPS = 6;

/** 已经选出来的轴。第 6 步走完后四条轴全部有值。 */
export type Picks = Partial<Record<AxisId, string>>;

export type Step = {
  /** 1–6，给用户看的进度。 */
  index: number;
  /** `pairs.json` 里的 `pair_id`。 */
  pairId: string;
  axis: AxisId;
};

/**
 * 第 `index` 步该展示哪一对。分支只看**已经选过的那条轴**，不看步数以外的状态——
 * 刷新页面重放同一串选择必然得到同一串比较。
 */
export function stepAt(index: number, picks: Picks): Step {
  switch (index) {
    case 1:
      return { index, pairId: "detail_p0_p3", axis: "detail_level" };
    case 2:
      return {
        index,
        pairId: picks.detail_level === "P0" ? "detail_p0_p2" : "detail_p2_p3",
        axis: "detail_level",
      };
    case 3:
      return { index, pairId: "examples_more_fewer", axis: "use_examples" };
    case 4:
      return { index, pairId: "structure_conclusion_buildup", axis: "structure" };
    case 5:
      return { index, pairId: "depth_what_derivation", axis: "depth" };
    case 6:
      return {
        index,
        pairId: picks.depth === "what" ? "depth_what_why" : "depth_why_derivation",
        axis: "depth",
      };
    default:
      throw new Error(`第 ${index} 步不存在，固定顺序只有 ${TOTAL_STEPS} 步`);
  }
}

/** 这棵树可能走到的全部 `pair_id`。护栏用它比对 `pairs.json`。 */
export const REACHABLE_PAIR_IDS: readonly string[] = [
  "detail_p0_p3",
  "detail_p0_p2",
  "detail_p2_p3",
  "examples_more_fewer",
  "structure_conclusion_buildup",
  "depth_what_derivation",
  "depth_what_why",
  "depth_why_derivation",
];

/**
 * 6 步走完 → 完整配置。
 *
 * 未选中的轴回落 `DEFAULTS` 在这里是**不该发生**的（6 步覆盖四条轴），
 * 所以缺轴时抛异常而不是悄悄补默认值：补默认值会让「用户选的」和「默认的」
 * 在库里长得一模一样，而 F1 的全部产出就是这个归因。
 */
export function configFromPicks(picks: Picks): StyleConfig {
  const missing = (Object.keys(AXES) as AxisId[]).filter((axis) => picks[axis] === undefined);
  if (missing.length > 0) {
    throw new Error(`成对比较未走完，缺少轴：${missing.join(", ")}`);
  }
  return { ...DEFAULTS, ...picks } as StyleConfig;
}


/**
 * 每步一次抛硬币，决定 `samples[0]` 放左（0）还是放右（1）。
 *
 * 抽成纯函数是为了能被断言：写在组件里的 `Math.random() < 0.5` 一旦写成
 * `< 0`（或 `<= 0`），位置就恒定了——而**页面看起来完全正常**，
 * 只是每个用户都拿到同一个左右顺序，偏斜照旧发生且没有任何东西会红。
 *
 * `rng` 可注入，测试里喂一个可控序列即可覆盖两个分支。
 */
export function makeFlips(n: number, rng: () => number = Math.random): (0 | 1)[] {
  return Array.from({ length: n }, () => (rng() < 0.5 ? 0 : 1));
}
