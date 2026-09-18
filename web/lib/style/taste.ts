/**
 * 开场的偏好采集：**认出来，而不是说出来**。
 *
 * ## 为什么把「你希望它怎么给你讲？」换掉（2026-09-19 拍板）
 *
 * 那一问的前提是「用户能内省到自己的讲解偏好并描述出来」。这个前提在偏好研究里
 * 被直接否定过：Nisbett & Wilson 1977《Telling More Than We Can Know》——人报告的
 * 「我为什么选它」常常不是真实原因，而是事后编的解释；「选择盲视」实验里，
 * 把受试者选的东西偷偷换掉，多数人不但没察觉，还会为**自己没选的那个**说出理由。
 *
 * 更要命的是它与本产品的存在理由自相矛盾：**用户要是能精确描述出想要的讲法，
 * 他就不需要这个产品了。**
 *
 * 换成标签的依据是「认出 > 回忆」：给选项让人认，认知负担与放弃率都远低于
 * 给空白让人描述（交互设计里的成熟原则）。
 *
 * ## ⚠️ 但标签**仍然是自述**，所以它不是答案，只是先验
 *
 * 标签更容易填，不等于更准。所以：
 *
 * - **语气**（`tone`）直接采用。它是「口味」，不是「理解方式」——用户认得准，
 *   而且拿它去做成对比较会与别的轴混淆（见下）。
 * - **四条轴（详略 / 举例 / 结构 / 深度）不由标签决定**，仍然由 6 次成对比较
 *   在**真实输出**上选出来。标签只用于**挑更有信息量的那一对**（`plan()`）。
 *
 *   **不拿先验去跳过比较**：跳过等于让「他说的」直接变成配置，而那正是不可信的
 *   那一半。自适应提问的正确用法是把轮次花在不确定的地方，不是省掉轮次。
 *
 * ## 为什么限制每个板块最多选几个
 *
 * 允许无限多选时，人倾向于「什么都想要」，结果是不产生区分度——这是评分量表
 * 相对于强制取舍（MaxDiff / 最优最劣量表）的已知缺陷。限制数量才逼出排序。
 *
 * ## 为什么每板 10 个而不是 5 个
 *
 * 「选项越多越瘫痪」在 50 个实验的元分析里平均效应接近零，不能当铁律；但它
 * **在我们这种情形下确实成立**：选项之间相似、用户在这个领域没有专业判断力。
 * 所以约束不在数量，在**彼此可辨**——10 个里不许有两个是同义词换皮。
 */

import { AXES, DEFAULTS, type AxisId, type StyleConfig } from "./axes";
import { REACHABLE_PAIR_IDS, TOTAL_STEPS, type Picks, type Step } from "./flow";

// ---------------------------------------------------------------- 语气（新维度）

/** 语气档。默认 `neutral` —— 与现有样例库的生成条件一致。 */
export const TONES = ["spoken", "neutral", "academic"] as const;
export type Tone = (typeof TONES)[number];
export const DEFAULT_TONE: Tone = "neutral";

export type BoardId = "tone" | "field" | "stuck";

export type Tag = {
  id: string;
  label: string;
  /** 这个标签**改变什么**。没有效果的标签不许存在——那就是「假装选择」。 */
  effect: { kind: "tone"; tone: Tone } | { kind: "field"; field: string } | { kind: "prior"; axis: AxisId; value: string };
};

export type Board = {
  id: BoardId;
  title: string;
  lead: string;
  /** 最多选几个。1 = 单选。 */
  max: number;
  /** 至少选几个才能继续。0 = 可以跳过。 */
  min: number;
  tags: readonly Tag[];
};

/**
 * 三个板块。**每个标签都必须有 `effect`**：要么改语气、要么定领域、要么给一条轴的先验。
 * 加一个没有 effect 的标签就是让用户白点一次（`verify:logic` 十六节断言这件事）。
 */
export const BOARDS: readonly Board[] = [
  {
    id: "tone",
    title: "你想听哪一种讲法",
    lead: "按读起来的感觉选，最多三个。这一项直接生效，之后每次讲解都按它来。",
    max: 3,
    min: 1,
    tags: [
      { id: "tone_peer", label: "像同学讲给同学", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_plain", label: "大白话，别端着", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_metaphor", label: "多打比方", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_light", label: "轻松一点，可以有梗", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_clear", label: "清楚就好，不用花哨", effect: { kind: "tone", tone: "neutral" } },
      { id: "tone_direct", label: "直给，别铺垫", effect: { kind: "tone", tone: "neutral" } },
      { id: "tone_written", label: "书面、规范", effect: { kind: "tone", tone: "academic" } },
      { id: "tone_term_en", label: "术语用英文原文，别硬翻", effect: { kind: "tone", tone: "academic" } },
      { id: "tone_textbook", label: "像教材那样严谨", effect: { kind: "tone", tone: "academic" } },
      { id: "tone_formal_sym", label: "多用公式和符号", effect: { kind: "tone", tone: "academic" } },
    ],
  },
  {
    id: "field",
    title: "你主要在学什么",
    lead: "选一个。它决定下一步那几段示例讲解用哪个领域的题目。",
    max: 1,
    min: 1,
    tags: [
      { id: "field_math", label: "数学", effect: { kind: "field", field: "math" } },
      { id: "field_physics", label: "物理", effect: { kind: "field", field: "physics" } },
      { id: "field_cs", label: "计算机", effect: { kind: "field", field: "cs" } },
      { id: "field_eng", label: "电子与工程", effect: { kind: "field", field: "engineering" } },
      { id: "field_chem", label: "化学与材料", effect: { kind: "field", field: "chemistry" } },
      { id: "field_bio", label: "生物与医学", effect: { kind: "field", field: "biomed" } },
      { id: "field_econ", label: "经济与金融", effect: { kind: "field", field: "econ" } },
      { id: "field_psych", label: "心理与社科", effect: { kind: "field", field: "social" } },
      { id: "field_humanities", label: "人文与历史", effect: { kind: "field", field: "humanities" } },
      { id: "field_other", label: "别的", effect: { kind: "field", field: "other" } },
    ],
  },
  {
    id: "stuck",
    title: "讲到你卡住的时候",
    lead: "你希望它怎么做？最多三个。这一项**不直接生效**，它只决定下一步先问你哪几组对比。",
    max: 3,
    min: 1,
    tags: [
      { id: "stuck_conclusion", label: "先给结论，再解释", effect: { kind: "prior", axis: "structure", value: "conclusion_first" } },
      { id: "stuck_buildup", label: "先铺垫，最后收结论", effect: { kind: "prior", axis: "structure", value: "buildup_first" } },
      { id: "stuck_example", label: "给个具体例子", effect: { kind: "prior", axis: "use_examples", value: "more" } },
      { id: "stuck_no_example", label: "别举例了，说要点", effect: { kind: "prior", axis: "use_examples", value: "fewer" } },
      { id: "stuck_why", label: "讲清为什么，不只是是什么", effect: { kind: "prior", axis: "depth", value: "why" } },
      { id: "stuck_derive", label: "把推导写出来", effect: { kind: "prior", axis: "depth", value: "derivation" } },
      { id: "stuck_what", label: "先知道是什么就够了", effect: { kind: "prior", axis: "depth", value: "what" } },
      { id: "stuck_slow", label: "每段讲透，长一点没关系", effect: { kind: "prior", axis: "detail_level", value: "P0" } },
      { id: "stuck_short", label: "尽量短，我时间紧", effect: { kind: "prior", axis: "detail_level", value: "P3" } },
      { id: "stuck_steps", label: "拆成小步骤", effect: { kind: "prior", axis: "detail_level", value: "P0" } },
    ],
  },
];

export const ALL_TAGS: readonly Tag[] = BOARDS.flatMap((b) => b.tags);

function tagById(id: string): Tag | undefined {
  return ALL_TAGS.find((t) => t.id === id);
}

// ---------------------------------------------------------------- 标签 → 结果

export type TasteResult = {
  tone: Tone;
  field: string | null;
  /** 四条轴上的先验。**不是配置**——配置仍由成对比较选出来。 */
  priors: Partial<Record<AxisId, string>>;
  /** 认不出的标签 id。**报出来，不静默丢**：静默丢会让前后端标签表漂了也没人知道。 */
  unknown: string[];
};

/**
 * 选中的标签 → 语气 / 领域 / 先验。
 *
 * 语气按**多数票**：选了两个口语 + 一个学术，结果是口语。平票回落默认（中性），
 * **不按选择顺序破平**——那会把「先点哪个」变成一个没人知道的隐藏变量。
 */
export function resolveTaste(selected: readonly string[]): TasteResult {
  const unknown = selected.filter((id) => !tagById(id));
  const tags = selected.map(tagById).filter((t): t is Tag => t !== undefined);

  const toneVotes = new Map<Tone, number>();
  let field: string | null = null;
  const priors: Partial<Record<AxisId, string>> = {};

  for (const tag of tags) {
    if (tag.effect.kind === "tone") {
      toneVotes.set(tag.effect.tone, (toneVotes.get(tag.effect.tone) ?? 0) + 1);
    } else if (tag.effect.kind === "field") {
      field = tag.effect.field;
    } else {
      // 同一条轴被点了互相矛盾的两个标签（「尽量短」+「讲透一点」）→ **不猜**，
      // 这条轴当作没有先验。猜一个等于替用户做了他没做的决定，而且不可见。
      const axis = tag.effect.axis;
      if (axis in priors && priors[axis] !== tag.effect.value) delete priors[axis];
      else priors[axis] = tag.effect.value;
    }
  }

  let tone: Tone = DEFAULT_TONE;
  let best = 0;
  let tied = false;
  for (const t of TONES) {
    const v = toneVotes.get(t) ?? 0;
    if (v > best) {
      best = v;
      tone = t;
      tied = false;
    } else if (v === best && v > 0) {
      tied = true;
    }
  }
  if (best === 0 || tied) tone = DEFAULT_TONE;

  return { tone, field, priors, unknown };
}

/** 取值域校验：先验必须落在该轴的取值里，否则它进不了配置，只会在后面炸。 */
export function priorsAreValid(priors: Partial<Record<AxisId, string>>): boolean {
  return Object.entries(priors).every(([axis, value]) =>
    (AXES[axis as AxisId] as readonly string[]).includes(value as string),
  );
}

// ---------------------------------------------------------------- 自适应的 6 步

/**
 * 按先验排出这 6 步的顺序。
 *
 * 两条规则，都是「把轮次花在不确定的地方」：
 *
 * 1. **没有先验的轴排在前面。** 那里我们一无所知，一次比较的信息量最大。
 * 2. **有先验的轴仍然要问**，但问的是**细分那一对**：先验说「要细」就直接
 *    P0 vs P2（P0 vs P3 的答案几乎已知，问了等于浪费一轮）。
 *
 * 详细程度与深度各两步（二分），举例与结构各一步——总数仍是 6，
 * 与 A5 预登记的「onboarding 完成率 / 平均步数」可比。
 */
export function plan(priors: Partial<Record<AxisId, string>>): Step[] {
  const groups: { axis: AxisId; steps: (picks: Picks) => { pairId: string }[] }[] = [
    {
      axis: "detail_level",
      steps: (picks) => {
        // 先验 P0 / P3 → 第一问就是细分那一对；无先验 → 先问跨度最大的 P0 vs P3。
        const first =
          priors.detail_level === "P0" ? "detail_p0_p2"
          : priors.detail_level === "P3" ? "detail_p2_p3"
          : "detail_p0_p3";
        const second =
          first !== "detail_p0_p3" ? "detail_p0_p3"
          : picks.detail_level === "P0" ? "detail_p0_p2"
          : "detail_p2_p3";
        return [{ pairId: first }, { pairId: second }];
      },
    },
    { axis: "use_examples", steps: () => [{ pairId: "examples_more_fewer" }] },
    { axis: "structure", steps: () => [{ pairId: "structure_conclusion_buildup" }] },
    {
      axis: "depth",
      steps: (picks) => {
        const first =
          priors.depth === "what" ? "depth_what_why"
          : priors.depth === "derivation" ? "depth_why_derivation"
          : "depth_what_derivation";
        const second =
          first !== "depth_what_derivation" ? "depth_what_derivation"
          : picks.depth === "what" ? "depth_what_why"
          : "depth_why_derivation";
        return [{ pairId: first }, { pairId: second }];
      },
    },
  ];

  // 规则 1：没有先验的轴排前面。同类之间保持声明顺序，**不引入随机**——
  // 顺序随机会让「第几步弃流」在用户之间不可比。
  const ordered = [...groups].sort((a, b) => {
    const pa = priors[a.axis] === undefined ? 0 : 1;
    const pb = priors[b.axis] === undefined ? 0 : 1;
    return pa - pb;
  });

  const out: Step[] = [];
  for (const g of ordered) {
    for (const s of g.steps({})) {
      out.push({ index: out.length + 1, pairId: s.pairId, axis: g.axis });
    }
  }
  if (out.length !== TOTAL_STEPS) {
    throw new Error(`排出了 ${out.length} 步，应为 ${TOTAL_STEPS} 步`);
  }
  return out;
}

/**
 * 第 `index` 步展示哪一对。二分的第二步要看**已经选过的那条轴**，
 * 所以这里重算一遍（同 `flow.stepAt` 的性质：刷新重放同一串选择得到同一串比较）。
 */
export function stepAtWithPriors(
  index: number,
  picks: Picks,
  priors: Partial<Record<AxisId, string>>,
): Step {
  const base = plan(priors);
  const slot = base[index - 1];
  if (!slot) throw new Error(`第 ${index} 步不存在，共 ${TOTAL_STEPS} 步`);

  // 同一条轴的第二步：第一步选完之后才知道问哪一对。
  const sameAxisBefore = base.slice(0, index - 1).filter((s) => s.axis === slot.axis).length;
  if (sameAxisBefore === 1 && priors[slot.axis] === undefined) {
    const picked = picks[slot.axis];
    if (slot.axis === "detail_level") {
      return { ...slot, pairId: picked === "P0" ? "detail_p0_p2" : "detail_p2_p3" };
    }
    if (slot.axis === "depth") {
      return { ...slot, pairId: picked === "what" ? "depth_what_why" : "depth_why_derivation" };
    }
  }
  return slot;
}

/** 这套排法可能走到的全部 `pair_id` —— 必须仍是 `pairs.json` 里那 8 对，不多不少。 */
export function reachablePairIds(): string[] {
  const ids = new Set<string>();
  const axisValues: Record<string, (string | undefined)[]> = {
    detail_level: [undefined, "P0", "P3"],
    depth: [undefined, "what", "derivation"],
    use_examples: [undefined, "more", "fewer"],
    structure: [undefined, "conclusion_first", "buildup_first"],
  };
  for (const d of axisValues.detail_level) {
    for (const dep of axisValues.depth) {
      const priors: Partial<Record<AxisId, string>> = {};
      if (d) priors.detail_level = d;
      if (dep) priors.depth = dep;
      for (let i = 1; i <= TOTAL_STEPS; i++) {
        for (const picks of [{}, { detail_level: "P0" }, { detail_level: "P3" }, { depth: "what" }, { depth: "derivation" }] as Picks[]) {
          ids.add(stepAtWithPriors(i, picks, priors).pairId);
        }
      }
    }
  }
  return [...ids].sort();
}

/** 6 步走完 → 配置。**先验不参与**：没被比较选中的值不许进配置（同 `configFromPicks`）。 */
export function configFromPicksAndTone(picks: Picks, tone: Tone): StyleConfig & { tone: Tone } {
  const missing = (Object.keys(AXES) as AxisId[]).filter((axis) => picks[axis] === undefined);
  if (missing.length > 0) {
    throw new Error(`成对比较未走完，缺少轴：${missing.join(", ")}`);
  }
  return { ...DEFAULTS, ...picks, tone } as StyleConfig & { tone: Tone };
}

/** 护栏用：这套排法与旧的固定顺序必须覆盖同一批样例。 */
export const LEGACY_REACHABLE = REACHABLE_PAIR_IDS;
