/**
 * 会话内的轮次账本。**`turn_index` 的口径住在这里，只有这一处。**
 *
 * ## 为什么它值得一个单独的纯模块
 *
 * `turn_index` 是 PRD §3.1 北极星指标「平均会话轮次」的分子来源，
 * 而它的口径有**两条容易反着写的规则**，两条都在 §13.0 里明文写着：
 *
 * > ⚠️ **轮次口径：按用户可见的输出计，重生成不计入轮次**，
 * > 只计入成本与质量指标。
 * >
 * > 不这样定的话，同一份数据在成本表和北极星指标里含义不同——
 * > 成本要算全部尝试，而「平均会话轮次」要算用户真正看到了几次回答。
 * > 一个字段两种含义，两张报表永远对不上，且**两边看起来都是对的**。
 *
 * 拆成两条可以各自被断言的话：
 *
 * 1. **重生成不进轮次。** 这一条前端天然做对了——重生成发生在服务端一次
 *    `/explain` 里面，前端压根看不见。但**它必须有断言**，否则哪天有人
 *    按 `regenerations` 去累加轮次，没有任何东西会红。
 * 2. **失败不进轮次。** 这一条前端**很容易做错**：最自然的写法是
 *    「发一次请求就 +1」。而一次 503（服务端没配 key）、404（课程不对）、
 *    或者 CORS 被挡，**用户一个字都没看到** —— 那不是一轮讲解。
 *    按请求计数会让「平均会话轮次」把失败算进分子，
 *    而那个数只会**偏高**，看起来像产品更粘。
 *
 * ## `session_id` 的占位口径与它的代价
 *
 * **一次页面加载 = 一个 session**（PRD §3.4.5 的切分规则至今未定）。
 * 由服务端页面生成后当 prop 传给客户端组件——客户端组件的渲染在 SSR 与
 * hydrate 各跑一次，渲染期取随机数两侧会分叉（React #418，阶段 3 踩过一次）。
 *
 * ⚠️ **这个占位口径有一个方向明确的偏差，如实登记**：
 * 用户刷新页面 → 新 session，轮次从 1 重新开始。
 * 于是**「平均会话轮次」被系统性地压低**——一个用户问了 10 轮、
 * 中间刷新过 3 次，会被记成 4 个会话。
 *
 * 偏差方向是**单向**的（只会低估，不会高估），这一点让它比一个方向不明的
 * 偏差好处理：内测第 1 周的数据可以当作「下界」来读，而不是当作估计值。
 * **触发条件**：那个数要用来做决定时（而不只是看趋势），
 * 切分规则必须先定下来——那时要的大概是「同一 `user_id` 在 N 分钟内的
 * 连续轮次算一个会话」，而 N 需要从落盘数据里看，不是现在拍。
 *
 * 决策日期：2026-09-13
 */

/** 一轮的终态。`pending` 是还在等（不流式，30–60 秒）。 */
export type TurnState = "pending" | "answered" | "failed";

export type TurnAnswer = {
  text: string;
  accepted: boolean;
  regenerations: number;
  meanParagraphChars: number;
  detailLevel: string;
  /** 检索后端。`in_memory` = 拿合成课件答的，**必须能被看见**。 */
  backend: string;
  /** 这一轮引用到的出处（文件名 + 页号），供页面点名到页。 */
  sources: { sourceFile: string; pages: number[] }[];
  /** 服务端报的埋点结果（口径 F53：从外面要能自证落没落库）。 */
  events?: unknown;
};

export type TurnFailure = {
  /** HTTP 状态码；网络/CORS 层失败时为 null（那时压根没有状态码）。 */
  status: number | null;
  message: string;
  retryable: boolean;
};

export type Turn = {
  /** 发这一轮时用的 `turn_index`。**failed 的轮次也留着它**，见下。 */
  turnIndex: number;
  question: string;
  state: TurnState;
  answer?: TurnAnswer;
  failure?: TurnFailure;
};

/**
 * 下一轮该用的 `turn_index`。
 *
 * **= 已经产生用户可见输出的轮数 + 1。**
 *
 * 三件事刻意如此：
 *
 * - `failed` **不计入**（口径见模块说明第 2 条）。所以一次失败之后重新提问，
 *   `turn_index` 与失败那次**相同**——它是「第几次让用户看到回答」的序号，
 *   不是「第几次发请求」的序号。
 * - `pending` 不计入：它还没产生输出。
 * - 不看 `regenerations`：重生成在服务端一次请求里面，与轮次无关。
 *
 * ⚠️ 失败的轮次仍然在 `turns` 里保留着它当时用的 `turnIndex`，**只是不参与计数**。
 * 删掉它的话页面上那条失败记录就没了，而用户需要看到「刚才那次没成」。
 */
export function nextTurnIndex(turns: readonly Turn[]): number {
  return turns.filter((t) => t.state === "answered").length + 1;
}

/**
 * 这个会话到目前为止**用户看到了几次回答**。
 *
 * 与 `nextTurnIndex()` 差 1，但语义不同，所以是两个函数：
 * 前者是「下一条事件里写什么」，这个是「现在已经几轮了」。
 * 合成一个会让调用方在某处忘记 ±1，而那种错在数据里是整体偏移一格。
 */
export function answeredCount(turns: readonly Turn[]): number {
  return turns.filter((t) => t.state === "answered").length;
}

/** 有没有一轮还在等。不流式，所以同时只允许一轮在飞。 */
export function hasPending(turns: readonly Turn[]): boolean {
  return turns.some((t) => t.state === "pending");
}

/**
 * 从 `/explain` 的响应体里取出这一轮要展示的东西。
 *
 * **`sources` 按 `source_file` 合并页号**：同一份讲义命中多片时，
 * 「第 3、7、12 页」比三行「第 3 页 / 第 7 页 / 第 12 页」好读，
 * 而合并的前提是 `source_file` 已经是唯一身份
 * （同名文件由 `api/api/retriever.py` 的 `label_documents` 加后缀区分）。
 */
export function answerFromResponse(body: {
  text: string;
  accepted: boolean;
  regenerations: number;
  mean_paragraph_chars: number;
  detail_level: string;
  retrieval?: {
    meta?: { backend?: string };
    slices?: { source_file: string; pages: number[] }[];
  };
  events?: unknown;
}): TurnAnswer {
  const byFile = new Map<string, Set<number>>();
  for (const s of body.retrieval?.slices ?? []) {
    const set = byFile.get(s.source_file) ?? new Set<number>();
    for (const p of s.pages ?? []) set.add(p);
    byFile.set(s.source_file, set);
  }
  return {
    text: body.text,
    accepted: body.accepted,
    regenerations: body.regenerations,
    meanParagraphChars: body.mean_paragraph_chars,
    detailLevel: body.detail_level,
    backend: body.retrieval?.meta?.backend ?? "unknown",
    sources: [...byFile.entries()].map(([sourceFile, pages]) => ({
      sourceFile,
      pages: [...pages].sort((a, b) => a - b),
    })),
    events: body.events,
  };
}

/**
 * 这一轮的回答是不是**真的来自用户问的那门课**。
 *
 * ⚠️ 这一条不是多余的谨慎，是 2026-09-12 真实发生过的那个失败的护栏：
 * 线上跑着一个旧构建，它**静默丢掉** `course_id` 并用合成课件作答，
 * 返回 **200**，正文在讲布隆过滤器（口径 F52）。
 * 「有没有报错」分不出这件事，只有「读的是哪个对象」能（F17 的同一形态）。
 *
 * 页面据此显示一条**醒目的**提示——用合成课件答的内容看起来完全正常，
 * 而它与用户的课件无关。
 */
export function isFromRequestedCourse(answer: TurnAnswer): boolean {
  return answer.backend === "supabase";
}
