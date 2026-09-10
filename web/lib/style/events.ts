/**
 * F1 埋点五事件：字段的**唯一构造处**。
 *
 * 表结构与 RLS 见 `supabase/migrations/20260910140000_f1_style_events.sql`
 * （一张表 + `event_type` 列 + `payload jsonb`，口径 F36）。
 *
 * ## 取值一律从已有模块读，不在这里另写一份（口径 F13）
 *
 * 三条硬要求，各自防的是一个「漂了不会报错」的坑：
 *
 * 1. **`call` 从 `text-arm.ts` 读。** `TEXT_ARM_CALL` 同时是真正发出去的请求参数和
 *    记进事件的记录。写两份必然漂，而漂掉之后**事后从数据里看不出来**——
 *    事件里记着 `effort: "low"`、实际发的是别的，两条臂的对照关系已经变了。
 *    所以 `textMapped()` **不接受 call 参数**，它自己从模块取。
 * 2. **`config_changed` 与 `feedback.ts` 同源。** `feedbackChange()` 不接受
 *    「变没变」这个判断，它自己调 `applyFeedback`——F06 要求「例子没懂」不进
 *    「配置修改频率」的分子，在埋点里重新判一遍就等于给这条口径开了第二个出处。
 * 3. **`left_variant` 与 `chosen_variant` 都必须记。** 选择记档位（位置随机后
 *    只有档位稳定），但**位置也要记**，否则位置效应事后分离不出来——
 *    阶段 3 做位置随机化的全部目的就在这里。
 *
 * ## 描述原文不进事件
 *
 * `textMapped()` **压根不接受描述文本这个参数**。用「记得别传」来保证是不行的；
 * 参数里没有这个东西，才是真的传不进来。
 *
 * ## 一步一个事件（口径 F37）
 *
 * 成对比较每一步发两条：渲染时 `style_pair_shown`、点选时 `style_pair_chosen`。
 * 数据量 ×6（30–50 用户 × 6 步 ≈ 几百行），换到的是**弃流点可见**
 * （onboarding 完成率是 A5 预登记的三个指标之一，只知道完成率不知道卡在哪
 * 等于没法改）与**位置效应可算**。
 */

import { PROMPT_TEMPLATE_VERSION, type AxisId, type StyleConfig } from "./axes";
import { applyFeedback, type FeedbackKind } from "./feedback";
import { TEXT_ARM_CALL } from "./text-arm";

// ---------------------------------------------------------------- 类型与取值域

export const EVENT_TYPES = [
  "style_pair_shown",
  "style_pair_chosen",
  "style_onboarding_completed",
  "style_text_mapped",
  "style_config_changed",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

/**
 * 每个事件的必需 payload 字段。**镜像自迁移里的 `f1_event_required_keys`**，
 * 由 `scripts/verify_style_logic.mjs` 从 SQL 原文解析出来逐项比对。
 *
 * 为什么要在 TS 里也放一份：数据库那份是**最后一道**防线，它变红的时候
 * 事件已经丢了（insert 被拒，用户没有任何感知）。这一份让缺字段在**构造时**
 * 就抛出来，跑一次 `verify:logic` 就能发现，不用等到线上。
 * 两份存在 ≠ 两份一致，所以有那条比对（口径 F33）。
 */
export const REQUIRED_PAYLOAD_KEYS: Record<EventType, readonly string[]> = {
  style_pair_shown: ["step", "axis", "pair_id", "left_variant"],
  style_pair_chosen: [
    "step",
    "axis",
    "pair_id",
    "left_variant",
    "chosen_variant",
    "dwell_ms",
    "hidden_ms",
  ],
  style_onboarding_completed: ["config", "steps_completed", "total_ms", "prompt_template_version"],
  style_text_mapped: ["call", "axes_from_text", "axes_from_default", "ok", "prompt_template_version"],
  style_config_changed: [
    "before",
    "after",
    "trigger",
    "config_changed",
    "counts_as_config_edit",
    "prompt_template_version",
  ],
};

export type EventPayload = Record<string, unknown>;

export type StyleEvent = {
  event_type: EventType;
  payload: EventPayload;
};

/** 设置页那次写入是怎么来的：手动改轴，还是点了哪个反馈按钮。 */
export type ChangeTrigger = "manual" | `feedback:${FeedbackKind}`;

// ---------------------------------------------------------------- session_id

/**
 * 一次页面加载 = 一个 session。**必须在服务端调用**，把结果当 prop 传给客户端组件。
 *
 * 两个理由，第二个是硬的：
 *
 * 1. PRD §3.4.5 的会话切分规则至今未定。「一次页面加载」是**占位口径**，
 *    内测第 1 周数据落盘后再立——现在拍一个「30 分钟无操作即新会话」是瞎猜。
 * 2. 客户端组件的渲染在 SSR 与 hydrate **各跑一次**，渲染期取随机数两侧必然分叉
 *    （React #418，阶段 3 在 `makeFlips` 上踩过一次）。分叉之后 `session_id`
 *    就可能不是用户实际那一次会话的 id，而这不会报错。
 *
 * `scripts/verify_style_logic.mjs` 会推导出「函数体里有 `crypto.randomUUID` 的
 * 导出函数」名单，并要求任何 `"use client"` 组件都不得调用它们——所以这条
 * 「必须在服务端调用」不是注释里的约定，是一条会红的护栏。
 */
export function newSessionId(): string {
  return crypto.randomUUID();
}

// ---------------------------------------------------------------- payload 构造

/**
 * 构造时就检查必需字段。缺了直接抛，**不要交给数据库去拒**：
 * 数据库拒掉的表现是「这条事件没了」，而没了的事件在任何报表里都不出现，
 * 也没有任何东西会红（漏采集是最安静的一类 bug）。
 */
function complete(event_type: EventType, payload: EventPayload): StyleEvent {
  const missing = REQUIRED_PAYLOAD_KEYS[event_type].filter((k) => {
    const v = payload[k];
    return v === undefined || v === null || v === "";
  });
  if (missing.length > 0) {
    throw new Error(`${event_type} 的 payload 缺必需字段：${missing.join(", ")}`);
  }
  return { event_type, payload };
}

/** 成对比较每一步渲染时。`left_variant` 是**这一步谁在左**，位置效应靠它分离。 */
export function pairShown(args: {
  step: number;
  axis: AxisId;
  pairId: string;
  leftVariant: string;
}): StyleEvent {
  return complete("style_pair_shown", {
    step: args.step,
    axis: args.axis,
    pair_id: args.pairId,
    left_variant: args.leftVariant,
  });
}

/**
 * 成对比较每一步点选时。
 *
 * `chosen_variant` 存**档位**不存左右（位置随机后只有档位稳定），
 * 但 `left_variant` 必须一起记——只有两个都在，「他选的是右边那个」与
 * 「他选的是 P3」才能同时还原出来。
 */
export function pairChosen(args: {
  step: number;
  axis: AxisId;
  pairId: string;
  leftVariant: string;
  chosenVariant: string;
  dwellMs: number;
  hiddenMs: number;
}): StyleEvent {
  return complete("style_pair_chosen", {
    step: args.step,
    axis: args.axis,
    pair_id: args.pairId,
    left_variant: args.leftVariant,
    chosen_variant: args.chosenVariant,
    dwell_ms: args.dwellMs,
    hidden_ms: args.hiddenMs,
  });
}

/**
 * 收尾屏**落库成功之后**才发——不是「点了完成」，是「那一行真的变了」。
 *
 * **两条臂都要发。** onboarding 完成率是 A5 预登记的三个指标之一，而它是**按臂分组**读的；
 * 对照臂不发这个事件的话，那条臂的完成率根本算不出来，A5 少一半。
 *
 * ⚠️ `steps_completed` 在两条臂上**含义不同**，不可跨臂比较：
 * 主臂是走完的比较步数（1–6），对照臂恒为 1（一段描述）。
 * 它的用途是**臂内**的弃流点分析——「卡在第几步」只有主臂问得出来。
 * 跨臂能比的是 `arm` 这一列上的完成率本身，不是这个数。
 */
export function onboardingCompleted(args: {
  config: StyleConfig;
  stepsCompleted: number;
  totalMs: number;
}): StyleEvent {
  return complete("style_onboarding_completed", {
    config: args.config,
    steps_completed: args.stepsCompleted,
    total_ms: args.totalMs,
    // prompt_profile_id 的原始字段之一（口径 F39）。id 由 Python 侧分析时算，
    // TS 只负责把构成它的东西原样存下来。
    prompt_template_version: PROMPT_TEMPLATE_VERSION,
  });
}

/**
 * 对照臂分类返回后。
 *
 * **参数里没有描述原文**，所以它传不进来（口径 F32 的采集纪律 + PRD §12.2）。
 * `call` 也不是参数，直接从 `TEXT_ARM_CALL` 取——发出去的和记下来的必须是同一份。
 *
 * `ok=false` 时两个轴列表都是空数组：**字段仍然在**，只是没有内容。
 * 用「缺字段」表示失败的话，失败记录会被数据库的必需字段护栏拒掉，
 * 于是**失败永远不进库**，而失败率恰恰是这条臂最该看的数字之一。
 */
export function textMapped(args: {
  ok: boolean;
  axesFromText?: readonly AxisId[];
  axesFromDefault?: readonly AxisId[];
  failure?: string;
}): StyleEvent {
  return complete("style_text_mapped", {
    call: TEXT_ARM_CALL,
    axes_from_text: args.axesFromText ?? [],
    axes_from_default: args.axesFromDefault ?? [],
    ok: args.ok,
    prompt_template_version: PROMPT_TEMPLATE_VERSION,
    ...(args.failure ? { failure: args.failure } : {}),
  });
}

/**
 * 设置页手动改轴。`config_changed` 按字段逐一比较得出，
 * 手动改动一律计入「配置修改频率」的分子（它确实是一次配置修改）。
 */
export function manualChange(before: StyleConfig, after: StyleConfig): StyleEvent {
  const changed = (Object.keys(after) as AxisId[]).some((axis) => before[axis] !== after[axis]);
  return complete("style_config_changed", {
    before,
    after,
    trigger: "manual" satisfies ChangeTrigger,
    config_changed: changed,
    counts_as_config_edit: changed,
    prompt_template_version: PROMPT_TEMPLATE_VERSION,
  });
}

/**
 * 设置页点反馈按钮。
 *
 * **它自己调 `applyFeedback`，不接受调用方算好的结论**——`config_changed` 与
 * `counts_as_config_edit` 必须与 `lib/style/feedback.ts` 同源（任务书 §4.2 硬要求 3）。
 * 传进来的话，页面上应用的是一份判断、事件里记的是另一份，而两份不一致
 * **不会报错**：F06 要的「例子没懂不进分子」会因为埋点里少写一个分支而失效，
 * 表现是 §3.2 的「配置修改频率趋近 0」自己达标。
 *
 * `applyFeedback` 是纯函数，页面与这里各调一次得到的必然是同一个结果。
 */
export function feedbackChange(before: StyleConfig, kind: FeedbackKind): StyleEvent {
  const outcome = applyFeedback(before, kind);
  return complete("style_config_changed", {
    before,
    after: outcome.config,
    trigger: `feedback:${kind}` satisfies ChangeTrigger,
    config_changed: outcome.config_changed,
    // 口径 F06：与 config_changed 分开的第二个字段。「例子没懂」两者都是 false，
    // 但理由不同（一个是没改，一个是不该计数），端点（F07）也是两者都 false。
    counts_as_config_edit: outcome.counts_as_config_edit,
    prompt_template_version: PROMPT_TEMPLATE_VERSION,
  });
}

// ---------------------------------------------------------------- 写库

/**
 * 只要能 `.from("style_events").insert(...)` 就行。
 *
 * 刻意用结构类型而不是 `SupabaseClient`：同一个函数要同时被浏览器端
 * （`lib/supabase/client`）和 route handler（`lib/supabase/server`）调用，
 * 两者的具体类型不同，而这里用到的只有 insert 一个动作。
 */
type EventSink = {
  from(table: string): {
    insert(rows: unknown): PromiseLike<{ error: { message: string; code?: string } | null }>;
  };
};

export type RecordResult = { ok: true } | { ok: false; message: string };

/**
 * 写一条事件。
 *
 * ## 三个刻意的设计
 *
 * 1. **不 `.select()`。** 客户端对 `style_events` **只有 insert 权限**，
 *    带 `select` 的返回会直接 42501。写完读不回来是设计，不是缺陷——
 *    读事件是分析行为，走 service_role。
 * 2. **失败不抛，返回结果。** 埋点写不进去不该打断用户的 onboarding；
 *    但也**不能静默**，所以失败时输出一条结构化日志，调用方还能把它显示出来。
 * 3. **`arm` 不由调用方随便给。** 数据库有一条 `arm = f1_assign_arm(user_id)`
 *    的 CHECK（见迁移），传错会直接 23514。这里照样要求传进来，是因为
 *    页面本来就从自己那行读到了 `f1_arm`——传的是同一个值，只是多了一道校验。
 */
export async function recordEvent(
  sink: EventSink,
  args: { userId: string; arm: "pairwise" | "text"; sessionId: string; event: StyleEvent },
): Promise<RecordResult> {
  const { error } = await sink.from("style_events").insert({
    user_id: args.userId,
    event_type: args.event.event_type,
    arm: args.arm,
    session_id: args.sessionId,
    payload: args.event.payload,
  });

  if (error) {
    const message = `${error.message}（${error.code ?? "?"}）`;
    console.error(
      JSON.stringify({
        event: "style_event_insert_failed",
        event_type: args.event.event_type,
        code: error.code ?? null,
        message: error.message,
      }),
    );
    return { ok: false, message };
  }
  return { ok: true };
}
