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
  /** 这一轮的标识（口径 F65，服务端生成）。`null` = 服务端构建比这一页旧。 */
  roundId: string | null;
  /** 本轮命中切片的身份键 `document_id:slice_id`（口径 F66）。**原样带回，前端不拼。** */
  sliceKeys: string[];
  /** 本轮有没有因为历史「没看懂」标记而注入限定语。`null` = 服务端没给。 */
  confusion: Confusion | null;
  /**
   * 逐片的出处（出处面板用）。与 `sources` 同源：`sources` 是按文件合并页号后的版本，
   * 这里保留每一片的标题与检索分数。**缺了就是 null，不编**。
   */
  slices: { sourceFile: string; pages: number[]; title: string | null; score: number | null }[];
  /** 本轮涉及的文档里读不了的页（`retrieval.skipped_pages_in_scope`）。 */
  skippedPages: { sourceFile: string; index: number; kind: string }[];
  /** 兜底判断（F3b，内测前-P1 接进生产）。`null` = 服务端没给（构建旧于 `84b5624`）。 */
  gap: GapInfo | null;
};

export type GapVerdict = "covered" | "partial" | "insufficient";

export type GapInfo = {
  /** `null` = 没判出结果（失败 / 超时 / 输出不合法）。**不等于 covered。** */
  verdict: GapVerdict | null;
  /** `"model"` / `"error"` / `"null"`。读 verdict 之前先读它（口径 F73 同一条纪律）。 */
  implementation: string;
  reason: string | null;
  error: string | null;
  /** G03a 规则通道点名的跳过页（页号）。 */
  hintPages: number[];
};

export type Confusion = {
  injected: boolean;
  note: string | null;
  markedRounds: number;
  matchedSliceKeys: string[];
  skippedReason: string | null;
};

/**
 * 「这部分没看懂」按钮的状态。**没有 `created` 这一维**：重复提交返回
 * `created: false`，而服务端刻意不返回 409，就是为了前端少一个它不关心的分支。
 */
export type MarkState = {
  status: "sending" | "marked" | "failed";
  message?: string;
  /** `POST /marks` 响应里的埋点结果（事件由服务端发，见 `submitMark`）。 */
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
  /** 这一轮**发出时**问的哪门课。标记时带它，而不是下拉框此刻选着的那门。 */
  courseId?: string;
  state: TurnState;
  answer?: TurnAnswer;
  failure?: TurnFailure;
  /** 「这部分没看懂」。**只活在本次页面加载里**：不做跨刷新回显（口径 F60）。 */
  mark?: MarkState;
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
    slices?: { source_file: string; pages: number[]; title?: string | null; score?: number | null }[];
    skipped_pages_in_scope?: { source_file: string; index: number; kind: string }[];
  };
  events?: unknown;
  round_id?: string;
  slice_keys?: string[];
  confusion?: {
    injected?: boolean;
    note?: string | null;
    marked_rounds?: number;
    matched_slice_keys?: string[];
    skipped_reason?: string | null;
  };
  gap?: {
    gap_verdict?: string | null;
    gap_implementation?: string;
    gap_reason?: string | null;
    gap_error?: string | null;
    gap_skipped_hint_pages?: number[];
  };
}): TurnAnswer {
  const c = body.confusion;
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
    // **缺了就是 null / 空，不编**：编一个 round_id 会被服务端 422，
    // 而编出来的 slice_keys 会写进一条永远不相交的死 mark。
    roundId: body.round_id ?? null,
    sliceKeys: body.slice_keys ?? [],
    confusion: c
      ? {
          injected: c.injected === true,
          note: c.note ?? null,
          markedRounds: c.marked_rounds ?? 0,
          matchedSliceKeys: c.matched_slice_keys ?? [],
          skippedReason: c.skipped_reason ?? null,
        }
      : null,
    slices: (body.retrieval?.slices ?? []).map((s) => ({
      sourceFile: s.source_file,
      pages: [...(s.pages ?? [])].sort((a, b) => a - b),
      title: s.title ?? null,
      score: typeof s.score === "number" ? s.score : null,
    })),
    skippedPages: (body.retrieval?.skipped_pages_in_scope ?? []).map((p) => ({
      sourceFile: p.source_file,
      index: p.index,
      kind: p.kind,
    })),
    gap: body.gap
      ? {
          // 认不出的取值当 null，**不就近解释**（G01 同一条：猜错比不显示更危险）。
          verdict: (["covered", "partial", "insufficient"] as const).find((v) => v === body.gap?.gap_verdict) ?? null,
          implementation: body.gap.gap_implementation ?? "unknown",
          reason: body.gap.gap_reason ?? null,
          error: body.gap.gap_error ?? null,
          hintPages: body.gap.gap_skipped_hint_pages ?? [],
        }
      : null,
  };
}

// ---------------------------------------------------------------- F3b · 兜底判断的提示

export type GapNotice = { verdict: "partial" | "insufficient"; pages: number[] };

/**
 * 这一轮要不要在讲解上方挂一条「片段可能不够」的提示。
 *
 * **只在「判了，且判的不是 covered」时出现**，两个方向都是真实的失败形态：
 *
 * - **恒触发**：每轮都挂一条提示，页面看起来完全正常，只是每轮多一句——
 *   用户很快学会无视它，真正不够的那一轮反而没人看（进度总结 §五「最难发现」）。
 * - **判断失败时也提示**：失败（`verdict = null`，`implementation = "error"`）不是
 *   「片段不够」，是「没判」。没有判断就没有依据，与讲解侧不注入限定语是同一处置
 *   （`api/tests/test_gap_wiring.py` 守卫 4）。
 *
 * 措辞由页面给，但必须守 G02：能说「**片段里**没有」，不能说「课件里没有」。
 */
export function gapNotice(answer: TurnAnswer): GapNotice | null {
  const verdict = answer.gap?.verdict;
  if (verdict !== "partial" && verdict !== "insufficient") return null;
  return { verdict, pages: answer.gap?.hintPages ?? [] };
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

// ---------------------------------------------------------------- F4 · 这部分没看懂

/**
 * 这一轮能不能标记。三种结果，页面各自处置：
 *
 * - `null`：能。
 * - `"empty-retrieval"`：本轮没有切片身份（空检索 / 走替身检索）。**不渲染按钮**——
 *   `POST /marks` 对空 `slice_ids` 回 422，一条空 mark 是死数据。
 *   ⚠️ 代价：用户想标却没得标，而这不单独埋点（`retrieval_executed` 的
 *   `slice_count` 已经算得出空检索轮占比，见 F4-P2 人工验收清单的观察项）。
 * - `"old-server"`：响应里没有 `round_id`。**服务端比这一页旧**，要响亮地说出来，
 *   不能与「空检索」一样静默不显示——那样它长得和「这一轮本来就不能标」一模一样。
 */
export function markUnavailable(answer: TurnAnswer): null | "empty-retrieval" | "old-server" {
  if (!answer.roundId) return "old-server";
  if (answer.sliceKeys.length === 0) return "empty-retrieval";
  return null;
}

/**
 * `POST /marks` 的请求体。字段必须是服务端 `MarkRequest` 认得的
 * （`extra="forbid"`，多一个就 422 —— `verify:logic` 从 `api/api/main.py` 原文读回来比对）。
 *
 * **`session_id` 与 `course_id` 必须带。** `confusion_mark_created` 由服务端在
 * 同一次请求里发（`_record_mark_event`），**缺 `session_id` 它就跳过不发**
 * （服务端不现编 session）；`course_id` 进事件那一行的列，供按课分组。
 * 前端不自己再发一条：发了就是每次点击两行。
 */
export function markRequestBody(turn: Turn, sessionId: string) {
  return {
    round_id: turn.answer?.roundId ?? "",
    // 原样带回 `/explain` 给的那一份，**不在前端重拼**（口径 F66：唯一拼法在服务端）。
    slice_ids: turn.answer?.sliceKeys ?? [],
    question: turn.question,
    session_id: sessionId,
    course_id: turn.courseId ?? null,
  };
}

/** `fetch` 的最小面。抽成类型是为了能在断言里喂一个假的。 */
export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<{ ok: boolean; status: number; json(): Promise<unknown>; text(): Promise<string> }>;

/**
 * 点一次「这部分没看懂」。**永不抛**，返回按钮的下一个状态。
 *
 * - 2xx → `marked`。**不读 `created`**：重复提交（`created: false`）与首次成功
 *   在 UI 上必须一模一样，幂等是服务端的事。
 * - 非 2xx / 网络层失败 → `failed`，带一句原因，按钮恢复可点。**不静默吞掉。**
 */
export async function submitMark(args: {
  fetch: FetchLike;
  apiBase: string;
  jwt: string;
  turn: Turn;
  sessionId: string;
}): Promise<MarkState> {
  try {
    const r = await args.fetch(`${args.apiBase}/marks`, {
      method: "POST",
      headers: { Authorization: `Bearer ${args.jwt}`, "Content-Type": "application/json" },
      body: JSON.stringify(markRequestBody(args.turn, args.sessionId)),
    });
    if (!r.ok) {
      const detail = await r.text().catch(() => "");
      return { status: "failed", message: `没记上（HTTP ${r.status}）。${detail}`.trim() };
    }
    const body = (await r.json()) as { events?: unknown };
    return { status: "marked", events: body.events };
  } catch (e) {
    return { status: "failed", message: `没记上：连不上产品服务端。原始错误：${String(e)}` };
  }
}

/** 成功之后本轮内不可再点；在飞时也不可再点。失败 → 可点。 */
export function markButtonDisabled(mark: MarkState | undefined): boolean {
  return mark?.status === "sending" || mark?.status === "marked";
}

/**
 * 库里的 `messages` 行 → 页面上的轮次（F5，2026-09-20）。
 *
 * ## 为什么这段映射住在这里
 *
 * `answerFromResponse()` 就在上面：一轮讲解长什么样，**只有一处出处**。
 * 写在页面组件里的话，重新打开会话看到的那一轮与刚问完看到的那一轮
 * 会慢慢长成两个样子，而没有任何东西会红——两边都「显示正常」。
 *
 * ## 配不上的行**丢掉，不编**
 *
 * 一问一答共用同一个 `turn_index`（服务端落库时就是这么写的）。
 * 只有 user 没有 assistant 的那种，意味着那一轮的回答没写进去
 * （写库失败，口径 F42：不打断讲解）。这里**不给它造一个空回答**——
 * 造出来的是一轮「讲解为空」的记录，看起来像模型什么都没说。
 */
export function turnsFromMessages(
  rows: {
    role: string;
    content: string;
    turn_index: number;
    /** **列，不是 meta 里的字段**（迁移里它是 messages.round_id）。 */
    round_id?: string | null;
    meta?: unknown;
  }[],
): Turn[] {
  const byTurn = new Map<
    number,
    { q?: string; a?: string; roundId?: string | null; meta?: Record<string, unknown> }
  >();
  for (const r of rows) {
    const slot = byTurn.get(r.turn_index) ?? {};
    if (r.role === "user") slot.q = r.content;
    else {
      slot.a = r.content;
      slot.roundId = r.round_id ?? null;
      slot.meta = (r.meta ?? {}) as Record<string, unknown>;
    }
    byTurn.set(r.turn_index, slot);
  }

  return [...byTurn.entries()]
    .sort((x, y) => x[0] - y[0])
    .filter(([, v]) => v.q !== undefined && v.a !== undefined)
    .map(([turnIndex, v]) => {
      const m = v.meta ?? {};
      return {
        turnIndex,
        question: v.q!,
        state: "answered" as const,
        answer: answerFromResponse({
          text: v.a!,
          // 这几个是 meta 里存的。**存的时候少一个，这里就会读到 undefined**，
          // 所以给的是「读不到」的值而不是一个看起来正常的默认数：
          // `accepted: true` / `regenerations: 0` 会让一轮其实重生成过两次的讲解
          // 在诊断里显示成一次过。
          accepted: m.accepted as boolean,
          regenerations: m.regenerations as number,
          mean_paragraph_chars: m.mean_paragraph_chars as number,
          detail_level: m.detail_level as string,
          retrieval: m.retrieval as never,
          round_id: v.roundId ?? undefined,
          slice_keys: m.slice_keys as string[] | undefined,
          confusion: m.confusion as never,
          gap: m.gap as never,
        }),
      };
    });
}
