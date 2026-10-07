/**
 * 开场的讲解偏好：**四种有代表性的讲法，选一种**（2026-10-07 起，口径 F107）。
 *
 * ## 为什么换掉那两块标签
 *
 * 原来是两块各 10 个标签：「你想听哪一种讲法」+「卡住的时候」。用户的判断（2026-10-07）：
 * 打开看到这么多选项**不知所云**。量下来确实如此：
 *
 * - 第一块的 10 个标签**只落到 3 个语气档**上——「口语」「大白话」「像同学讲给同学」
 *   甚至「多打比方」都是同一个 `spoken`。10 个近义词让人挑，挑哪个结果都一样。
 *   「多打比方」本该是举例那条轴，却只改了语气。
 * - 第二块「卡住的时候」问的是一个用户答不了的问题：难点要**用户自己指出来**，
 *   指出的方式就是追问；而追问时我们能做的只有再讲一遍——更浅、带例子，
 *   这又回到了「你想听哪一种讲法」。这一块因此整块去掉，追问时换讲法的事交给
 *   服务端（`api/api/main.py` 的追问限定语）。
 *
 * ## 现在：一种讲法 = 一整套配置，**配一段看得见的示例**
 *
 * 真正决定输出的是五样东西：语气 + 四条轴（详略 / 举例 / 结构 / 深度）。
 * 每张卡片把这五样定死，并用**同一个问题**的两三行示例把差别摆出来——
 * 用户靠读出来的差别选，不靠对名字的想象选（「认出 > 回忆」那条原则仍然成立，
 * 只是认的对象从形容词换成了真实的讲法）。
 *
 * 四种之间**彼此可辨**：任意两种至少在两样上不同（`verify:logic` 十六节断言）。
 * 选完之后的微调发生在看过真实讲解之后（聊天里的「调整讲法」，`adjust.ts`）。
 *
 * 2026-09-19 那一版为什么用标签、为什么不让人描述自己的偏好，理由仍然成立，
 * 记在 git 历史与 `docs/项目进度总结.md`；这里不再重复。
 */

import { AXES, type AxisId, type StyleConfig } from "./axes";

// ---------------------------------------------------------------- 语气（新维度）

/** 语气档。默认 `neutral` —— 与现有样例库的生成条件一致。 */
export const TONES = ["spoken", "neutral", "academic"] as const;
export type Tone = (typeof TONES)[number];
export const DEFAULT_TONE: Tone = "neutral";

/** 语气档的展示名与一句话说明（设置页用）。措辞对应 `style_prompt.TONE_FRAGMENTS` 的意思。 */
export const TONE_LABEL: Record<Tone, string> = { spoken: "口语", neutral: "平实", academic: "书面" };
export const TONE_HINT: Record<Tone, string> = {
  spoken: "像同学之间讲题，句子短，直接用「你」。",
  neutral: "不刻意口语也不刻意书面。",
  academic: "措辞规范，术语附英文原文，需要时直接写公式。",
};

export type Preset = {
  id: string;
  name: string;
  /** 一句话说清这种讲法**做什么**，不用形容词堆。 */
  summary: string;
  /** 同一个问题（`SAMPLE_QUESTION`）按这种讲法的开头两三行。 */
  sample: string;
  tone: Tone;
  config: StyleConfig;
};

/** 四张卡片的示例都回答它。选一个人人都见过、与任何课件都无关的现象。 */
export const SAMPLE_QUESTION = "为什么冰会浮在水面上？";

export const PRESETS: readonly Preset[] = [
  {
    id: "preset_beginner",
    name: "讲给小白听",
    summary: "大白话，多打比方，先说结论再讲为什么。",
    sample:
      "简单说：冰比水轻，所以浮起来。水结冰时，分子会排成一个很松的「架子」，中间留着空隙——" +
      "就像同样多的人手拉手站成一圈，比挤成一团更占地方。占的地方大了、重量没变，冰就比水轻。",
    tone: "spoken",
    config: { detail_level: "P2", use_examples: "more", structure: "conclusion_first", depth: "why" },
  },
  {
    id: "preset_brief",
    name: "直接给要点",
    summary: "不绕弯，只讲结论和关键事实，越短越好。",
    sample:
      "冰的密度（约 0.92 g/cm³）比液态水（约 1.00 g/cm³）小，所以浮在水面上。" +
      "这是水的反常之处：多数物质的固态比液态更致密。",
    tone: "neutral",
    config: { detail_level: "P3", use_examples: "fewer", structure: "conclusion_first", depth: "what" },
  },
  {
    id: "preset_thorough",
    name: "一步步讲透",
    summary: "从前提讲起，一步接一步，配例子，讲清为什么。",
    sample:
      "先看一般规律：大多数物质降温会收缩，固体比液体更紧密，所以固体会沉下去。" +
      "水在 4°C 以下却开始膨胀——分子之间用氢键互相「拉住」，排成六边形的格子，格子中间是空的。" +
      "体积变大、质量不变，密度就变小了，所以冰浮在水上。",
    tone: "neutral",
    config: { detail_level: "P0", use_examples: "more", structure: "buildup_first", depth: "why" },
  },
  {
    id: "preset_rigorous",
    name: "严谨推导",
    summary: "书面表达，术语附英文，从原理一步步推出结论。",
    sample:
      "由阿基米德原理（Archimedes' principle），浮力 F = ρ水·g·V排。漂浮平衡时 F = ρ冰·g·V，" +
      "得 V排/V = ρ冰/ρ水 ≈ 0.917，即约 8% 的体积露出水面。" +
      "ρ冰 < ρ水 源于氢键（hydrogen bond）构成的四面体网络，其堆积比液态更疏松。",
    tone: "academic",
    config: { detail_level: "P0", use_examples: "fewer", structure: "buildup_first", depth: "derivation" },
  },
];

export function presetById(id: string | null | undefined): Preset | undefined {
  return PRESETS.find((p) => p.id === id);
}

/** 当前配置恰好是哪一种讲法（设置页高亮用）。调过任何一样就不再是任何一种，返回 undefined。 */
export function presetMatching(config: StyleConfig, tone: string): Preset | undefined {
  return PRESETS.find(
    (p) => p.tone === tone && (Object.keys(AXES) as AxisId[]).every((a) => p.config[a] === config[a]),
  );
}

/** 两种讲法在几样上不同（语气 + 四条轴）。护栏用它断言四种彼此可辨。 */
export function presetDistance(a: Preset, b: Preset): number {
  const axes = (Object.keys(AXES) as AxisId[]).filter((x) => a.config[x] !== b.config[x]).length;
  return axes + (a.tone === b.tone ? 0 : 1);
}

/** 选中的讲法 → 写进 `style_config` 的那一份。取值域校验在这里做：卡片表写错了就抛，不写进库。 */
export function configFromPreset(id: string): { config: StyleConfig; tone: Tone } {
  const p = presetById(id);
  if (!p) throw new Error(`没有这种讲法：${id}`);
  for (const axis of Object.keys(AXES) as AxisId[]) {
    if (!(AXES[axis] as readonly string[]).includes(p.config[axis])) {
      throw new Error(`讲法 ${id} 的 ${axis}=${p.config[axis]} 不在取值域里`);
    }
  }
  return { config: { ...p.config }, tone: p.tone };
}
