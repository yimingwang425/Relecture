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

import { PROMPT_TEMPLATE_VERSION, type Arm, type AxisId, type StyleConfig } from "./axes";
import { applyFeedback, type FeedbackKind } from "./feedback";
import { TEXT_ARM_CALL } from "./text-arm";

// ---------------------------------------------------------------- 类型与取值域

export const EVENT_TYPES = [
  "style_pair_shown",
  "style_pair_chosen",
  "style_onboarding_completed",
  "style_text_mapped",
  "style_config_changed",
  // ---- 2026-09-19 开场标签。分两条的理由同 pair_shown / pair_chosen：
  // 只记最终结果的话，**弃流点**就没了——标签页有三块，用户在第几块走掉要能读出来。
  "style_taste_shown",
  "style_taste_chosen",
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
  style_taste_shown: ["board", "tag_ids"],
  // `field` **刻意不进清单**：可能是 null（没选领域），而 null 有含义（口径 F50）。
  style_taste_chosen: ["tags", "tone", "priors", "total_ms"],
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
/**
 * 标签页的某一块被展示了。`tag_ids` 记**当时那一版**的标签集合——
 * 标签表以后会改，不记的话事后没法按当时那一版读「哪些标签被点得多」。
 */
export function tasteShown(args: { board: string; tagIds: readonly string[] }): StyleEvent {
  return complete("style_taste_shown", { board: args.board, tag_ids: [...args.tagIds] });
}

/**
 * 三块选完。**`priors` 进事件**是为了事后能核一件事：先验有没有真的改变提问顺序。
 * 不记的话，「自适应」是不是空转就只能靠读代码判断，而代码会变。
 *
 * ⚠️ 这里**不记配置**：标签不产生配置（四条轴仍由成对比较选出来），
 * 记一份配置进来会让读数的人以为标签定了轴。
 */
export function tasteChosen(args: {
  tags: readonly string[];
  tone: string;
  priors: Record<string, string>;
  totalMs: number;
}): StyleEvent {
  return complete("style_taste_chosen", {
    tags: [...args.tags],
    tone: args.tone,
    priors: args.priors,
    total_ms: args.totalMs,
  });
}

export function onboardingCompleted(args: {
  config: StyleConfig;
  stepsCompleted: number;
  /** 生效的语气档（2026-09-19）。分析侧要靠它算 `prompt_profile_id`——
   *  非默认档的 prompt 多一行，不记的话那一批轮次算出来的 id 是错的。 */
  tone?: string;
  totalMs: number;
}): StyleEvent {
  return complete("style_onboarding_completed", {
    tone: args.tone ?? "neutral",
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

type QueuedRow = {
  user_id: string;
  event_type: EventType;
  arm: string;
  session_id: string;
  payload: EventPayload;
};

export type RecordResult =
  | { ok: true }
  | { ok: false; kind: "transient" | "permanent"; message: string; code: string | null };

/**
 * 瞬时失败的**白名单**。不在名单上的一律当永久失败。
 *
 * 方向是刻意的：**把瞬时错当成永久错，结果是一条响亮的报告；反过来会静默空转。**
 * 一个 `23514`（payload 缺字段）重试一万次也不会成功，而重试掩盖的正是
 * 「我们构造了一条坏事件」这个真 bug——它会以「队列一直不空」的样子存在，
 * 而没有人会去看队列。
 *
 * 空字符串在这里是**最重要的一项**：`fetch` 被网络层拒掉时
 * （`TypeError: Failed to fetch` / `ERR_CONNECTION_CLOSED`），
 * supabase-js 给不出数据库错误码，`code` 就是空的——请求连数据库都没到。
 */
const TRANSIENT_CODES = new Set([
  "PGRST301", // JWT 过期：刷新后可能成功
  "408",
  "429",
  "500",
  "502",
  "503",
  "504",
]);

export function classifyInsertFailure(error: { code?: string | null }): "transient" | "permanent" {
  const code = (error.code ?? "").trim();
  if (code === "") return "transient";
  return TRANSIENT_CODES.has(code) ? "transient" : "permanent";
}

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
  args: { userId: string; arm: Arm; sessionId: string; event: StyleEvent },
  opts: { delaysMs?: readonly number[]; sleep?: (ms: number) => Promise<void> } = {},
): Promise<RecordResult> {
  const delays = opts.delaysMs ?? SERVER_RETRY_DELAYS_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));

  const row: QueuedRow = {
    user_id: args.userId,
    event_type: args.event.event_type,
    arm: args.arm,
    session_id: args.sessionId,
    payload: args.event.payload,
  };

  // 服务端没有队列可排（请求一结束进程就走了），所以**在这次请求里把重试做完**。
  // 阶梯比浏览器那份短：用户正在等这一屏，不能为了一条事件把响应拖到 12 秒。
  let last: RecordResult = { ok: false, kind: "transient", message: "未尝试", code: null };
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    last = await attemptInsert(sink, row, attempt + 1);
    if (last.ok || last.kind === "permanent") return last;
    if (attempt < delays.length) await sleep(delays[attempt]);
  }
  return last;
}

/** 服务端的退避阶梯。**比浏览器那份短**——用户正在等这一屏。 */
const SERVER_RETRY_DELAYS_MS = [200, 700] as const;

/**
 * 一次插入尝试。**队列与服务端共用这一份**——分类规则、日志形状、错误文案
 * 各写一份的话，「什么算瞬时失败」就有了两个答案，而它们漂开不会报错。
 */
async function attemptInsert(sink: EventSink, row: QueuedRow, attempt: number): Promise<RecordResult> {
  const { error } = await sink.from("style_events").insert(row);

  if (error) {
    const code = (error.code ?? "").trim() || null;
    const kind = classifyInsertFailure(error);
    console.error(
      JSON.stringify({
        event: "style_event_insert_failed",
        event_type: row.event_type,
        code,
        kind,
        attempt,
        message: error.message,
      }),
    );
    return { ok: false, kind, message: `${error.message}（${code ?? "无错误码"}）`, code };
  }
  return { ok: true };
}

// ---------------------------------------------------------------- 失败之后

/**
 * 有序、带重试、跨页面存活的事件队列。
 *
 * ## 它修的是什么（2026-09-11 人工验收第 1 组的红）
 *
 * 实测：某个账号走完 6 步后 `style_pair_chosen` 有 6 条而 `style_pair_shown` 只有 5 条，
 * **缺的固定是 step 1**，Console 里对应一行
 * `ERR_CONNECTION_CLOSED` + `TypeError: Failed to fetch`；另一个账号同样流程 6/6 全绿。
 *
 * **判红的不是「网络会抖」——网络抖是必然的。** 红的是失败之后没有任何补偿：
 * 事件丢了、日志里一行、然后就没了。而 shown/chosen 配对是算位置效应的基础，
 * 缺 shown 的那一步位置效应无从算起——那正是阶段 3 做位置随机化要保住的东西。
 *
 * ## 为什么偏偏是 step 1（**这是与证据相符的推断，不是已证实的机制**）
 *
 * step 1 的事件是这次页面加载中**浏览器对 Supabase 主机的第一个请求**
 * （服务端组件那次取数发生在服务端）。浏览器此时可能去复用上一次导航留下的
 * keep-alive 连接，而服务端早已按空闲超时关掉了它——复用失败即 `ERR_CONNECTION_CLOSED`。
 * 浏览器**会**在这种情况下自动重发幂等的 GET，**但不会自动重发 POST**，
 * 所以页面本身照常加载，只有我们这条 POST 死掉。step 2–6 用的是已经握好手的连接。
 *
 * 这解释了「固定在 step 1」与「概率性」两点。**但它只是与证据相符**：
 * 能确证的三件事是——① `createBrowserClient` 在浏览器里是单例（`@supabase/ssr` 源码），
 * 所以「每次发事件新建 client」不是原因；② 失败发生在 fetch 层，没有数据库错误码；
 * ③ 浏览器不自动重发 POST。真要坐实机制得抓一次 net-export，代价不值。
 *
 * **而这不影响处置**：重试对上面任何一种机制都有效，
 * 而「把首屏事件延后到 client 就绪」只降低概率、让它更罕见因而更难被抓到——
 * 那是把判据的分辨力拿去换一个好看的数字（同 F16 反对的那件事）。所以没有那样做。
 *
 * ## 四条设计
 *
 * 1. **有序**：永远发 `queue[0]`，成功或永久失败才出队。
 *    乱序会让 `style_pair_chosen` 先于同一步的 `style_pair_shown` 落库——
 *    配对还在，但「先看到再点选」这个事实没了。
 * 2. **瞬时失败不出队**：本轮重试用尽后**留在队列里**并标 `stalled`，
 *    等下一次 `enqueue` 或下一次页面加载再试。**没有任何东西因为网络而被丢掉。**
 * 3. **永久失败才丢**，并且丢得很响（`dropped` 计数 + 页面上的一句话 + console.error）。
 *    那是「我们构造了一条坏事件」，要的就是吵。
 * 4. **落 `sessionStorage`**：`router.refresh()` 不卸载组件，但用户点「回到首页」会。
 *    存起来之后，下次打开三个风格页中的任何一个都会把它冲出去。
 *    ⚠️ **残留风险如实登记**：关掉标签页且再也不回来的那部分仍然会丢。
 *    这一条靠**检出**兜底而不是靠承诺——验收清单里有一条按 session 比
 *    `shown` / `chosen` 条数并查 step 连号的判据（F42）。
 */
export type EventQueueState = {
  /** 还没写进去的条数。 */
  pending: number;
  /** 永久失败、已放弃的条数。**只可能是内容或权限问题**，不会是网络。 */
  dropped: number;
  /** 瞬时失败已用尽本轮重试，仍在队列里等下一次。**不是「丢了」。** */
  stalled: boolean;
  lastError: string | null;
};

export type EventQueue = {
  enqueue(event: StyleEvent): void;
  /** 手动催一次。页面挂载时调它，把上一次页面加载没发完的冲出去。 */
  flush(): Promise<void>;
  state(): EventQueueState;
  subscribe(fn: (s: EventQueueState) => void): () => void;
};

/** `sessionStorage` 的最小面。抽成接口是为了能在断言里喂一个假的。 */
export type QueueStorage = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
};

const STORAGE_KEY = "f1.style_events.pending";

/**
 * 退避阶梯。**这不是 F38 说的那种阈值常量**——F38 管的是 `dwell_ms` 的**判读**
 * （多短算「乱点」），那种线现在拍就是瞎猜；这里是重试节奏，改了只影响
 * 「多久放弃这一轮」，不影响任何数据的解释。
 */
const RETRY_DELAYS_MS = [300, 1000, 3000, 8000] as const;

export function createEventQueue(
  sink: EventSink,
  ctx: { userId: string; arm: Arm; sessionId: string },
  opts: {
    delaysMs?: readonly number[];
    sleep?: (ms: number) => Promise<void>;
    storage?: QueueStorage | null;
  } = {},
): EventQueue {
  const delays = opts.delaysMs ?? RETRY_DELAYS_MS;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const storage =
    opts.storage !== undefined
      ? opts.storage
      : typeof sessionStorage !== "undefined"
        ? sessionStorage
        : null;

  const queue: QueuedRow[] = load();
  let dropped = 0;
  let stalled = false;
  let lastError: string | null = null;
  /**
   * 正在跑的那一轮。**存的是 promise 不是布尔**——存布尔的话，
   * `flush()` 在已有一轮在跑时会直接返回，调用方以为「冲完了」而其实刚开始。
   * 这一条是被 `verify:logic` 的 8.2 抓出来的：第一版用布尔，
   * 断言读到「尝试 1 次、落库 0 条」。
   */
  let current: Promise<void> | null = null;
  const listeners = new Set<(s: EventQueueState) => void>();

  function load(): QueuedRow[] {
    if (!storage) return [];
    try {
      const raw = storage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? (parsed as QueuedRow[]) : [];
    } catch {
      // 存坏了就当空的。**不抛**——一个读不出来的队列不该让 onboarding 打不开。
      return [];
    }
  }

  function persist() {
    if (!storage) return;
    try {
      if (queue.length === 0) storage.removeItem(STORAGE_KEY);
      else storage.setItem(STORAGE_KEY, JSON.stringify(queue));
    } catch {
      // 配额满 / 隐私模式。忽略，内存队列照常工作。
    }
  }

  function snapshot(): EventQueueState {
    return { pending: queue.length, dropped, stalled, lastError };
  }

  function notify() {
    const s = snapshot();
    for (const fn of listeners) fn(s);
  }

  /**
   * 催一轮。已经有一轮在跑就**返回那一轮的 promise**（而不是直接返回）。
   *
   * 末尾那次 re-check 补的是一个小窗口：`drain()` 刚跑完、`current` 还没清空时
   * 进来的 `enqueue`，会拿到一个即将 resolve 的 promise，然后那条事件就没人管了。
   */
  function kick(): Promise<void> {
    if (current) return current;
    current = (async () => {
      try {
        await drain();
      } finally {
        current = null;
      }
      if (queue.length > 0 && !stalled) await kick();
    })();
    return current;
  }

  async function drain() {
    {
      while (queue.length > 0) {
        const row = queue[0];
        let failure: { kind: "transient" | "permanent"; message: string } | null = null;

        for (let attempt = 0; attempt <= delays.length; attempt++) {
          const r = await attemptInsert(sink, row, attempt + 1);
          if (r.ok) {
            failure = null;
            break;
          }
          failure = { kind: r.kind, message: r.message };
          if (r.kind === "permanent") break;
          if (attempt < delays.length) await sleep(delays[attempt]);
        }

        if (failure === null) {
          queue.shift();
          stalled = false;
          lastError = null;
          persist();
          notify();
          continue;
        }

        lastError = failure.message;
        if (failure.kind === "permanent") {
          // 内容或权限问题。重试一万次也不会成功，**丢掉并吵一声**。
          queue.shift();
          dropped++;
          persist();
          notify();
          continue;
        }

        // 瞬时失败用尽本轮重试：**留在队列里**，等下一次 enqueue 或下一次页面加载。
        stalled = true;
        persist();
        notify();
        return;
      }
    }
  }

  return {
    enqueue(event) {
      queue.push({
        user_id: ctx.userId,
        event_type: event.event_type,
        arm: ctx.arm,
        session_id: ctx.sessionId,
        payload: event.payload,
      });
      // 新事件进来 → 重新试一轮。上一轮若是因为网络卡住的（stalled），
      // 这次连它一起重试：队列是有序的，卡住的那条仍然排在最前面。
      stalled = false;
      persist();
      notify();
      void kick();
    },
    async flush() {
      await kick();
    },
    state: snapshot,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
