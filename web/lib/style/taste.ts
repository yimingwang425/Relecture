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

// ---------------------------------------------------------------- 语气（新维度）

/** 语气档。默认 `neutral` —— 与现有样例库的生成条件一致。 */
export const TONES = ["spoken", "neutral", "academic"] as const;
export type Tone = (typeof TONES)[number];
export const DEFAULT_TONE: Tone = "neutral";

export type BoardId = "tone" | "stuck";

export type Tag = {
  id: string;
  label: string;
  /** 这个标签**改变什么**。没有效果的标签不许存在——那就是「假装选择」。 */
  effect: { kind: "tone"; tone: Tone } | { kind: "prior"; axis: AxisId; value: string };
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
 * 两个板块（2026-09-19 去掉了「你学什么」那一块）。**每个标签都必须有 `effect`**：要么改语气、要么定领域、要么给一条轴的先验。
 * 加一个没有 effect 的标签就是让用户白点一次（`verify:logic` 十六节断言这件事）。
 */
/**
 * ⚠️ **去掉过一块「你学什么」（领域 10 选 1）**，理由记在这里免得有人再加回来：
 * 课件讲什么由老师定，不由学生的专业定；而且理工科里有纯文字课程、文科里有数学与
 * 逻辑学——**拿专业去猜内容类型本来就不成立**，那一块问到的东西不能用来选样例题目。
 */
export const BOARDS: readonly Board[] = [
  {
    id: "tone",
    title: "你想听哪一种讲法",
    lead: "最多选三个。这一项直接生效。",
    max: 3,
    min: 1,
    tags: [
      { id: "tone_peer", label: "口语", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_plain", label: "大白话", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_metaphor", label: "多打比方", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_peer2", label: "像同学讲给同学", effect: { kind: "tone", tone: "spoken" } },
      { id: "tone_clear", label: "平实", effect: { kind: "tone", tone: "neutral" } },
      { id: "tone_direct", label: "直给", effect: { kind: "tone", tone: "neutral" } },
      { id: "tone_written", label: "书面", effect: { kind: "tone", tone: "academic" } },
      { id: "tone_term_en", label: "术语用英文原文", effect: { kind: "tone", tone: "academic" } },
      { id: "tone_textbook", label: "教材式", effect: { kind: "tone", tone: "academic" } },
      { id: "tone_formal_sym", label: "多用公式符号", effect: { kind: "tone", tone: "academic" } },
    ],
  },
  {
    id: "stuck",
    title: "卡住的时候",
    // ⚠️ 这段文字是**纯文本渲染**的，不走 markdown —— 写 `**粗体**` 会原样显示成星号。
    lead: "最多选三个。它不直接改配置，只决定下一步先问哪几组。",
    max: 3,
    min: 1,
    tags: [
      { id: "stuck_conclusion", label: "先给结论", effect: { kind: "prior", axis: "structure", value: "conclusion_first" } },
      { id: "stuck_buildup", label: "先铺垫，后结论", effect: { kind: "prior", axis: "structure", value: "buildup_first" } },
      { id: "stuck_example", label: "要具体例子", effect: { kind: "prior", axis: "use_examples", value: "more" } },
      { id: "stuck_no_example", label: "不用例子", effect: { kind: "prior", axis: "use_examples", value: "fewer" } },
      { id: "stuck_why", label: "讲为什么", effect: { kind: "prior", axis: "depth", value: "why" } },
      { id: "stuck_derive", label: "要推导", effect: { kind: "prior", axis: "depth", value: "derivation" } },
      { id: "stuck_what", label: "知道是什么就行", effect: { kind: "prior", axis: "depth", value: "what" } },
      { id: "stuck_slow", label: "讲透，长一点没关系", effect: { kind: "prior", axis: "detail_level", value: "P0" } },
      { id: "stuck_short", label: "尽量短", effect: { kind: "prior", axis: "detail_level", value: "P3" } },
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
  const priors: Partial<Record<AxisId, string>> = {};

  for (const tag of tags) {
    if (tag.effect.kind === "tone") {
      toneVotes.set(tag.effect.tone, (toneVotes.get(tag.effect.tone) ?? 0) + 1);
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

  return { tone, priors, unknown };
}

/** 取值域校验：先验必须落在该轴的取值里，否则它进不了配置，只会在后面炸。 */
export function priorsAreValid(priors: Partial<Record<AxisId, string>>): boolean {
  return Object.entries(priors).every(([axis, value]) =>
    (AXES[axis as AxisId] as readonly string[]).includes(value as string),
  );
}

// ---------------------------------------------------------------- 自适应的 6 步

/*
 * ---------------------------------------------------------------- 删掉了什么
 *
 * 2026-09-22 删除：`plan()` / `stepAtWithPriors()` / `reachablePairIds()` /
 * `configFromPicksAndTone()` / `LEGACY_REACHABLE` / `Step` / `Picks`。
 *
 * 它们全部只为**成对比较的排序**存在——哪一轴先问、有先验时直接问细分那一对、
 * 6 步走完怎么拼成配置。成对比较 2026-09-20 撤掉之后（口径 F83），
 * 这些函数一个调用方都没有了。
 *
 * ⚠️ 连同删掉的还有它们的护栏（`verify:logic` 原第十六节的一批断言）。
 * **那不是「把判据关掉」**：判据测的东西没了，留着它就是在验一段没人跑的代码，
 * 而一条永远绿的断言比没有断言更危险（它让人以为验过了）。
 *
 * 标签 → 配置现在走 `configFromTaste()`（下面那个），它的护栏在第十八节。
 */



/**
 * 标签 → 配置（2026-09-20）。**成对比较去掉之后，标签就是配置的来源。**
 *
 * ## 这推翻了 `configFromPicksAndTone` 那条纪律，说清楚为什么
 *
 * 上面那个函数刻意**不让先验进配置**，理由是「没被比较选中的值不许进配置」——
 * 当时比较是可信的那一半，标签只是他「说的」。
 *
 * 用户 2026-09-20 的判断：「不论多短，用户都无法从两组如此密集的文字中获得
 * 有效信息，甚至这个信息还不是用户领域。」比较那一半因此不存在了。
 * 剩下标签是唯一的来源，所以它必须能变成配置——否则每个新用户拿到的都是默认档。
 *
 * ## 没被标签覆盖的轴回落默认，而且**报出来**
 *
 * 回落本身不可怕，可怕的是回落看不见：库里「他选的 P0」与「我们填的 P0」
 * 长得一模一样（这正是旧注释担心的那件事）。所以返回值里带 `fellBack`，
 * 调用方把它写进 `style_taste_chosen` 事件——「标签平均定下几条轴」
 * 因此是一个查得出来的数，而不是一个感觉。
 *
 * 真正的矫正发生在用户看过一次真实讲解之后（聊天里的「调整讲法」），
 * 那时他手上有具体的东西可以反应，而不是在对着两段陌生领域的文字猜。
 */
export function configFromTaste(
  priors: Partial<Record<AxisId, string>>,
  tone: Tone,
): { config: StyleConfig & { tone: Tone }; fellBack: AxisId[] } {
  const axisIds = Object.keys(AXES) as AxisId[];
  const fellBack = axisIds.filter((axis) => priors[axis] === undefined);
  return {
    config: { ...DEFAULTS, ...priors, tone } as StyleConfig & { tone: Tone },
    fellBack,
  };
}
