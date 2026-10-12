/**
 * 四条风格轴在 TS 侧的取值域、阶梯与文案。
 *
 * ## 这份文件不是第三份口径
 *
 * 口径正文在 `f1_samples/README.md` §一，产品取值域在 `f1_samples/style_axes.py`，
 * 数据库取值域在 `supabase/migrations/*.sql` 的 CHECK。TS 需要这些取值是因为
 * 浏览器跑不了 Python、也读不到 CHECK——**但三处一致不能靠人记得同时改**，
 * 所以有一条护栏 `scripts/verify_style_axes.mjs` 把三份解析出来逐项比对，
 * 不一致就让 `npm run verify:guards` 变红。
 *
 * ## 这里一个数字都没有（口径 F13）
 *
 * 「P0 = 30–120 字/段」这类区间**只存在于** `实验脚本/配置扫描实验/scan/points.py`
 * 的 `AXIS_POINTS`。本文件只有档位 id 和中文描述，没有区间数值——
 * 抄一份数字进 TS，就是 F13 那个坑（Python 一份 TS 一份，改一边另一边不报错）。
 *
 * 连带的产品决策（2026-09-09 拍板）：**`/settings/style` 不显示区间数字**，
 * 用文字描述档位。这让 `f1_samples/README.md` §二「把 AXIS_POINTS 提升到生产模块」
 * 的触发条件**不成立**，那一项继续挂着。哪天真要显示数字，先做提升，不要在这里写字面量。
 */

/**
 * B 类三条轴的 prompt 措辞版本。**镜像自 `f1_samples/style_axes.py` 的
 * `PROMPT_TEMPLATE_VERSION`**，由 `scripts/verify_style_axes.mjs` 逐字比对。
 *
 * 它是 `prompt_profile_id` 的构成字段之一（口径 F04 / F39）：事件里存
 * 「B 类三条轴的取值 + 这个版本号」，分析时由 Python 侧的
 * `explain.prompt_profile.prompt_profile_id()` 算出 id。**TS 不碰那个算法。**
 *
 * ⚠️ 措辞改了必须改这个版本号，否则同一个 id 会盖住两份不同的 prompt。
 * 靠人记得是不行的，所以护栏另有一条：`style_axes.py` 里 `PROMPT_TEMPLATE_DIGEST`
 * 记着措辞本身的摘要，措辞一动摘要就对不上，`verify:axes` 直接变红
 * （口径 F33 的形态——多份出处存在 ≠ 多份出处一致，后者要有人去量）。
 */
export const PROMPT_TEMPLATE_VERSION = "b1";

/**
 * A5 的分臂。**取值域与迁移里的 CHECK 同一件事**（`style_config_f1_arm_check`）。
 *
 * - `pairwise` —— 只有 6 次成对比较（对照臂）
 * - `taste` —— 开场三块标签 + 成对比较（2026-09-19 起的新臂）
 * - `text` —— **历史臂**：一段话描述自己想要的讲法。不再分配给新用户，
 *   但已有行不回改（口径 F10；那批人确实走过那一版，改写等于篡改 A5 要读的历史）
 */
export type Arm = "pairwise" | "text" | "taste";

export const AXIS_IDS = ["detail_level", "use_examples", "structure", "depth"] as const;
export type AxisId = (typeof AXIS_IDS)[number];

/** 产品取值域。与 `style_axes.AXES` 及数据库 CHECK 逐项相等（护栏断言）。 */
export const AXES = {
  detail_level: ["P0", "P2", "P3"],
  use_examples: ["more", "fewer"],
  structure: ["conclusion_first", "buildup_first"],
  depth: ["what", "why", "derivation"],
} as const satisfies Record<AxisId, readonly string[]>;

export type AxisValue<A extends AxisId> = (typeof AXES)[A][number];

/** 口径 F01：非目标维度取默认值。与 `style_axes.DEFAULTS` 相等（护栏断言）。 */
export const DEFAULTS = {
  detail_level: "P0",
  use_examples: "more",
  structure: "conclusion_first",
  depth: "why",
} as const satisfies { [A in AxisId]: AxisValue<A> };

/**
 * 哪条轴会被审查。2026-10-12 起四条都交给风格审查 agent，按「大致符合」判，明显偏离才打回。
 * 与 `explain/explain/style_axes.py` 的 `ENFORCED` 相等（verify:axes 护栏断言）。
 */
export const ENFORCED = {
  detail_level: true,
  use_examples: true,
  structure: true,
  depth: true,
} as const satisfies Record<AxisId, boolean>;

/**
 * 详细程度阶梯，**从最详细到最简洁**。口径 F05 的「太啰嗦 → 下移一格」沿它走。
 *
 * P1 不在里面（口径 F23）。顺序与 `style_axes.DETAIL_LADDER` 相等（护栏断言）。
 */
export const DETAIL_LADDER = AXES.detail_level;

/** 深度阶梯，**从浅到深**。「讲太深」向浅端移一格，「讲太浅」向深端移一格。 */
export const DEPTH_LADDER = AXES.depth;

export type StyleConfig = { [A in AxisId]: AxisValue<A> };

/** 服务端与客户端共用的取值域校验。非法取值**判失败，不改写**（同 F10）。 */
export function isAxisValue<A extends AxisId>(axis: A, value: unknown): value is AxisValue<A> {
  return typeof value === "string" && (AXES[axis] as readonly string[]).includes(value);
}

// ---------------------------------------------------------------- 文案

/** 轴的展示名。回归工具按 `data-axis` 定位，不按这些中文字（改文案不该让断言静默变绿）。 */
export const AXIS_LABEL: Record<AxisId, string> = {
  detail_level: "详细程度",
  use_examples: "举例",
  structure: "结构",
  depth: "深度",
};

/**
 * 档位的展示名与一句话说明。**没有区间数字**（见文件头）。
 *
 * 措辞取自各轴的 prompt 片段所要求的行为，不是新发明的定义：
 * B 类三条抄的是 `style_axes.B_CLASS_FRAGMENTS` 的意思，
 * 详细程度那条描述的是段落粒度而不是区间边界。
 */
export const VALUE_LABEL: { [A in AxisId]: Record<AxisValue<A>, string> } = {
  detail_level: { P0: "讲得细", P2: "适中", P3: "讲得简洁" },
  use_examples: { more: "多举例子", fewer: "少举例子" },
  structure: { conclusion_first: "先给结论", buildup_first: "先铺垫后收结论" },
  depth: { what: "只讲是什么", why: "讲到为什么", derivation: "讲到推导" },
};

export const VALUE_HINT: { [A in AxisId]: Record<AxisValue<A>, string> } = {
  detail_level: {
    P0: "每段展开讲透，段落偏长。",
    P2: "段落长度适中，讲清楚为止。",
    P3: "段落短，只留要点。",
  },
  use_examples: {
    more: "每讲一个要点，配一个具体例子或类比。",
    fewer: "把要点本身讲清楚即可，不额外补例子。",
  },
  structure: {
    conclusion_first: "开头第一段就说出最重要的那句判断，后面再解释。",
    buildup_first: "按「问题 → 机制 → 结论」展开，判断放在最后。",
  },
  depth: {
    what: "说清楚是什么、拿来做什么用，不解释为什么。",
    why: "在是什么之外，解释这个机制为什么成立。",
    derivation: "在为什么之上，把关键结论一步步推出来。",
  },
};

/** 沿阶梯移动一格。到端点返回 `null`——**由调用方给说明，不静默吞掉**（口径 F07）。 */
export function stepAlong<T extends string>(
  ladder: readonly T[],
  current: T,
  delta: 1 | -1,
): T | null {
  const i = ladder.indexOf(current);
  if (i < 0) throw new Error(`取值 ${current} 不在阶梯 ${ladder.join(" → ")} 上`);
  const next = i + delta;
  return next >= 0 && next < ladder.length ? ladder[next] : null;
}
