/**
 * 反馈按钮的语义。口径 F05 / F06 / F07 的**唯一实现**。
 *
 * 写成纯函数而不是塞进组件里，理由是它有三条口径要守，而组件里的分支不好断言：
 *
 * - **F05**：太啰嗦 → 详细程度下移一格（`P0 → P2 → P3`，不经过 P1）；
 *   讲太深 / 讲太浅 → 深度轴移一格。**离散轴上零新阈值**——
 *   若档位是连续区间，「太啰嗦」得回答「往哪个方向移多少」，那是个新的占位阈值。
 * - **F06**：「例子没懂」**不改配置**，只触发本轮重生成；`config_changed: false`，
 *   且**不进「配置修改频率」的分子**。§13.2 的飞轮假定每次反馈都回写配置，
 *   一个不产生配置变更的按钮混在里面，会让 §3.2 的「修改次数趋近 0」
 *   **因为按钮没接通而达标**。所以 `counts_as_config_edit` 是独立的一个字段，
 *   不是 `config_changed` 的别名——阶段 4 埋点直接读它。
 * - **F07**：已在端点再点同方向 → 配置不变 + `config_changed: false` +
 *   **前端给一句说明**。静默吞掉会让用户以为生效了，也会在埋点里多一次假的配置变更。
 *
 * 阶段 3 只有 `/settings/style` 消费它；阶段 4 的反馈接口应当调同一个函数，
 * 而不是在服务端再写一遍这三条映射。
 */

import {
  DEPTH_LADDER,
  DETAIL_LADDER,
  VALUE_LABEL,
  stepAlong,
  type StyleConfig,
} from "./axes";

export const FEEDBACK_KINDS = [
  "too_verbose",
  "too_deep",
  "too_shallow",
  "example_unclear",
] as const;
export type FeedbackKind = (typeof FEEDBACK_KINDS)[number];

export const FEEDBACK_LABEL: Record<FeedbackKind, string> = {
  too_verbose: "太啰嗦",
  too_deep: "讲太深",
  too_shallow: "讲太浅",
  example_unclear: "例子没懂",
};

export type FeedbackOutcome = {
  /** 应用反馈后的配置。未变更时**与入参逐字段相等**。 */
  config: StyleConfig;
  /** 配置是否真的变了。F06 / F07 两种情况都是 false。 */
  config_changed: boolean;
  /**
   * 是否计入「配置修改频率」的分子（口径 F06）。
   * 与 `config_changed` 分开：端点未移动（F07）也不该计入，但那是另一个理由。
   */
  counts_as_config_edit: boolean;
  /** 是否需要按当前配置重新生成本轮讲解（「例子没懂」的全部作用）。 */
  regenerate: boolean;
  /** 给用户看的一句话。**每种结果都有**——F07 要的就是不静默。 */
  message: string;
};

export function applyFeedback(config: StyleConfig, kind: FeedbackKind): FeedbackOutcome {
  // F06：不改配置，只重生成。放在最前面——它不参与下面的阶梯逻辑。
  if (kind === "example_unclear") {
    return {
      config,
      config_changed: false,
      counts_as_config_edit: false,
      regenerate: true,
      message: "知道了。这条不改你的风格配置，只会按现在的配置把这一段重新讲一遍。",
    };
  }

  // 阶梯方向写死在一张表里，不由「太啰嗦 = 负方向」这类推导得出——
  // 两条阶梯的语义方向相反（详细程度是「细 → 简洁」，深度是「浅 → 深」），
  // 推导版本每次读都要在脑子里过一遍符号，而这三行读一眼就能核对。
  const MOVE = {
    too_verbose: { axis: "detail_level", delta: 1 },   // 往简洁端
    too_deep: { axis: "depth", delta: -1 },            // 往浅端
    too_shallow: { axis: "depth", delta: 1 },          // 往深端
  } as const;

  const { axis, delta } = MOVE[kind];
  const ladder: readonly string[] = axis === "detail_level" ? DETAIL_LADDER : DEPTH_LADDER;
  const current: string = config[axis];
  const next = stepAlong(ladder, current, delta);

  // F07：端点。配置不变，但**说清楚为什么没变**，并说明已经在哪一端。
  if (next === null) {
    const endLabel = (VALUE_LABEL[axis] as Record<string, string>)[current];
    return {
      config,
      config_changed: false,
      counts_as_config_edit: false,
      regenerate: false,
      message:
        kind === "too_verbose"
          ? `已经是最简洁的一档（${endLabel}），没有更简洁的档位了，配置保持不变。`
          : kind === "too_deep"
            ? `已经是最浅的一档（${endLabel}），没有更浅的档位了，配置保持不变。`
            : `已经是最深的一档（${endLabel}），没有更深的档位了，配置保持不变。`,
    };
  }

  const labels = VALUE_LABEL[axis] as Record<string, string>;
  return {
    config: { ...config, [axis]: next } as StyleConfig,
    config_changed: true,
    counts_as_config_edit: true,
    regenerate: false,
    message: `${axis === "detail_level" ? "详细程度" : "深度"}已从「${labels[current]}」调到「${labels[next]}」。`,
  };
}
