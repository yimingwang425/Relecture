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

import type { StreamDraft } from "./stream";

/** 一轮的终态。`pending` 是还在等（流式：正文在 `draft` 里一段段长出来）。 */
export type TurnState = "pending" | "answered" | "failed";

export type TurnAnswer = {
  text: string;
  /**
   * 这一轮**没存进会话**的原因；存好了（或本来就不存）为 null（2026-10-08）。
   *
   * 服务端落库失败不打断讲解（F42），原来只把结果放进响应、页面不看——于是 F5 上线起
   * 会话一条消息都没存进去，屏幕上一切正常，刷新后对话是空的，没有人知道。
   * 现在页面把它说出来：用户至少知道「刷新会丢」。
   */
  persistFailed: string | null;
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
  /** 多智能体链路走了哪条路（口径 F109–F114）。`null` = 服务端没给（构建旧于 agent 链路）。 */
  agent: AgentInfo | null;
  /** 前情笔记（口径 F116–F118）。`null` = 服务端没给（构建旧于压缩 agent，或从历史重建的轮次）。 */
  memory: MemoryInfo | null;
  /**
   * 出处核对（口径 F121–F123）。`null` = 服务端没给（构建旧于核对 agent，或历史轮次没有核对行）。
   * 流式时 `done` 里只是计划（`pending`），结果随 `done` 之后的 `verify` 事件到，由页面补上。
   */
  verification: VerifyInfo | null;
  /**
   * 风格审查 agent 对最终那一版的判断（2026-10-12）。`null` = 这一轮没问审查员
   * （直接回复、服务端没配，或构建旧于审查 agent）。
   */
  styleReview: StyleReviewInfo | null;
};

export type StyleReviewInfo = {
  /** `ok` / `rewrite`；`null` = 审查员没判成（报错、超时、输出读不懂），按通过处理。 */
  verdict: "ok" | "rewrite" | null;
  notes: { aspect: string; comment: string }[];
  /** 每一版各自的结论，按顺序。打回过几次、为什么，从这里看。 */
  attempts: ("ok" | "rewrite" | null)[];
  error: string | null;
};

/** 核对状态。`lost` = 等到流结束也没收到结果（连接断了；服务端多半核完并存了，刷新可见）。 */
export type VerifyStatus = "pending" | "written" | "failed" | "skipped" | "unavailable" | "lost";

export type VerifyClaim = {
  claim: string;
  verdict: "supported" | "wrong_page" | "not_found";
  citedPage: number | null;
  evidencePage: number | null;
  evidenceQuote: string;
};

export type VerifyInfo = {
  status: VerifyStatus;
  counts: { supported: number; wrongPage: number; notFound: number; supplement: number } | null;
  /** 判成不是 `supported` 的事实说法。讲解补充不在这里——它们不核对，不是问题。 */
  problems: VerifyClaim[];
  /** 存没存进库：`false` 时刷新会看不到（迁移没跑，或这一轮没落库）。`null` = 不适用。 */
  persisted: boolean | null;
  reason: string | null;
};

const VERIFY_STATUSES: readonly VerifyStatus[] = ["pending", "written", "failed", "skipped", "unavailable", "lost"];
const FACT_VERDICTS = ["supported", "wrong_page", "not_found"] as const;

/**
 * 服务端的核对载荷 → 页面要的那几样。计划（`{status: "pending"}`）、`verify` 事件里的结果、
 * 库里 `answer_checks.result` 都走这一个函数——**一处出处**（同 `answerFromResponse`）。
 *
 * 认不出的状态当 `null`，**不就近解释**（G01 同一条）：把一个新状态显示成「核对通过」比不显示更糟。
 */
export function verifyFromPayload(raw: unknown): VerifyInfo | null {
  if (!raw || typeof raw !== "object") return null;
  const v = raw as {
    status?: unknown;
    counts?: { supported?: number; wrong_page?: number; not_found?: number; supplement?: number };
    claims?: { claim?: unknown; kind?: unknown; verdict?: unknown; cited_page?: unknown; evidence_page?: unknown; evidence_quote?: unknown }[];
    persisted?: unknown;
    reason?: unknown;
    error?: unknown;
    persist_error?: unknown;
  };
  const status = VERIFY_STATUSES.find((s) => s === v.status);
  if (!status) return null;
  const num = (x: unknown) => (typeof x === "number" && x > 0 ? x : null);
  const problems: VerifyClaim[] = (Array.isArray(v.claims) ? v.claims : []).flatMap((c) => {
    const verdict = FACT_VERDICTS.find((x) => x === c.verdict);
    if (c.kind !== "fact" || !verdict || verdict === "supported") return [];
    return [{
      claim: String(c.claim ?? ""),
      verdict,
      citedPage: num(c.cited_page),
      evidencePage: num(c.evidence_page),
      evidenceQuote: String(c.evidence_quote ?? ""),
    }];
  });
  return {
    status,
    counts:
      status === "written" && v.counts
        ? {
            supported: v.counts.supported ?? 0,
            wrongPage: v.counts.wrong_page ?? 0,
            notFound: v.counts.not_found ?? 0,
            supplement: v.counts.supplement ?? 0,
          }
        : null,
    problems,
    persisted: typeof v.persisted === "boolean" ? v.persisted : null,
    reason: [v.reason, v.error, v.persist_error].find((x): x is string => typeof x === "string" && x !== "") ?? null,
  };
}

/**
 * 核对结果下面那一行字。**措辞守口径 F122**：
 *
 * - 「有依据」= 核对员找到了课件原文的摘录，**代码确认了摘录真的在那一页**；
 *   摘录支不支持这条说法是模型的判断——所以说「找到依据」，不说「已证实」。
 * - 「课件里没找到」只在评测达标时显示（`VERIFY_SHOW_NOT_FOUND`，见 `explain/runs/verify_eval_*.md`）。
 * - 讲解补充（举例、类比）**不核对**，单独说一句，不算问题。
 *
 * 返回 null = 这一轮不显示任何核对信息（不需要核、没有核对模型）。
 */
export function verifySummary(v: VerifyInfo | null): { kind: "pending" | "ok" | "warn" | "muted"; text: string } | null {
  if (!v) return null;
  switch (v.status) {
    case "pending":
      return { kind: "pending", text: "正在核对出处…" };
    case "lost":
      return { kind: "muted", text: "出处核对的结果没收到（连接断了），刷新后可能能看到。" };
    case "failed":
      return { kind: "muted", text: "这一轮的出处核对没有完成，讲解本身不受影响。" };
    case "skipped":
    case "unavailable":
      return null;
    case "written": {
      const c = v.counts ?? { supported: 0, wrongPage: 0, notFound: 0, supplement: 0 };
      const parts = [`${c.supported} 条找到课件依据`];
      if (c.wrongPage > 0) parts.push(`${c.wrongPage} 条页码可能不对`);
      if (c.notFound > 0) parts.push(VERIFY_SHOW_NOT_FOUND ? `${c.notFound} 条课件里没找到` : `${c.notFound} 条没有核对上`);
      if (c.supplement > 0) parts.push(`讲解补充 ${c.supplement} 条（举例、类比，不核对）`);
      return { kind: c.wrongPage + c.notFound > 0 ? "warn" : "ok", text: `出处核对：${parts.join(" · ")}` };
    }
  }
}

/**
 * 「课件里没找到」这句话能不能说（口径 F123）。注入错误评测达标（三类检出率都 ≥ 70%、逐条误报率 ≤ 10%）
 * 才是 true；不达标降级为「没有核对上」——它只说核对员没对上，不断言课件里没有。
 *
 * 2026-10-10：Sonnet 在主集前一半（9/9 · 9/11 · 13/13）与留出集（14/16 · 14/18 · 32/33，误报 5.3%）
 * 上都达标（`explain/runs/verify_eval_results.md`）→ true。被判「没找到」的误报复核下来都是讲解在课件之外
 * 正确地多说了一步——对它们说「课件里没找到」本身是真话。
 */
export const VERIFY_SHOW_NOT_FOUND = true;

export type MemoryInfo = {
  /** 这一轮用的笔记覆盖到第几轮；`null` = 没有笔记。 */
  throughTurn: number | null;
  /** 这一轮之后要不要整理：`not_needed` / `pending` / `written` / `failed` / `unavailable` / `skipped`。 */
  compaction: string;
  /** 读笔记失败的原因（例如迁移没跑）。讲解照常，但要看得见（F42）。 */
  loadError: string | null;
};

/**
 * 路由直接回复、不进讲解的几类（口径 F110 / F119）。这些轮**根本没去查课件**，
 * 也不按讲法审查——「没有检索到」「重生成触顶」这类提示对它们都是错话。
 */
export const DIRECT_ROUTES: readonly string[] = ["chat", "off_topic", "crisis"];

export function isDirectRoute(route: string | null | undefined): boolean {
  return route != null && DIRECT_ROUTES.includes(route);
}

export type AgentInfo = {
  /** `"agent"` / `"pipeline"` / `"fallback"`。`fallback` 时 `fallbackError` 说明原因。 */
  mode: string;
  /** 路由的判断：`course` / `web` / `chat` / `off_topic` / `crisis`；`null` = 这一轮没有路由器。 */
  route: string | null;
  /** 讲解 agent 调了哪些工具，按先后。 */
  tools: string[];
  fallbackError: string | null;
  /** 联网轮（口径 F120）正文引用了的网页；不是联网轮为 `null`。 */
  web: { searches: number; sources: { url: string; title: string }[] } | null;
};

/** 只认 http(s) 链接。别的协议（`javascript:` 之类）一律不当链接渲染——网页标题与地址都来自外部。 */
export function safeHttpUrl(url: unknown): string | null {
  if (typeof url !== "string") return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}

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
  /**
   * 流式进行中的草稿（口径 F94）。**只在 pending 时有意义**：终态的正文、出处、
   * 诊断全部取自 `done` 事件的响应体（`answerFromResponse`），不取草稿——
   * 草稿是边收边拼的，响应体才是服务端认定的那一版。
   */
  draft?: StreamDraft;
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

/**
 * 有没有一轮还在等。**同时只允许一轮在飞**：流式之后仍然如此——两轮同时流，
 * 会话历史里两轮的先后由服务端各自落库的时刻决定，与用户的提问顺序可能相反。
 */
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
  persisted?: { written?: number; attempted?: number; error?: string };
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
  agent?: {
    mode?: string;
    route?: { route?: string } | null;
    tools?: { name?: string }[];
    fallback_error?: string | null;
    web?: { searches?: number; sources?: { url?: string; title?: string }[] } | null;
  } | null;
  memory?: {
    through_turn?: number | null;
    load_error?: string | null;
    compaction?: { status?: string } | null;
  } | null;
  verification?: unknown;
  style_review?: {
    verdict?: string | null;
    notes?: { aspect?: string; comment?: string }[];
    attempts?: { verdict?: string | null }[];
    error?: string | null;
  } | null;
}): TurnAnswer {
  const c = body.confusion;
  const byFile = new Map<string, Set<number>>();
  for (const s of body.retrieval?.slices ?? []) {
    const set = byFile.get(s.source_file) ?? new Set<number>();
    for (const p of s.pages ?? []) set.add(p);
    byFile.set(s.source_file, set);
  }
  const pe = body.persisted;
  return {
    text: body.text,
    persistFailed:
      pe && (pe.attempted ?? 0) > (pe.written ?? 0)
        ? (pe.error ?? `只存进 ${pe.written ?? 0} / ${pe.attempted} 条`)
        : null,
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
    agent: body.agent
      ? {
          mode: body.agent.mode ?? "unknown",
          route: body.agent.route?.route ?? null,
          tools: (body.agent.tools ?? []).map((t) => String(t.name ?? "?")),
          fallbackError: body.agent.fallback_error ?? null,
          web: body.agent.web
            ? {
                searches: body.agent.web.searches ?? 0,
                sources: (body.agent.web.sources ?? []).flatMap((x) => {
                  const url = safeHttpUrl(x.url);
                  return url ? [{ url, title: String(x.title ?? url) }] : [];
                }),
              }
            : null,
        }
      : null,
    memory: body.memory
      ? {
          throughTurn: body.memory.through_turn ?? null,
          compaction: body.memory.compaction?.status ?? "unknown",
          loadError: body.memory.load_error ?? null,
        }
      : null,
    verification: verifyFromPayload(body.verification),
    styleReview: styleReviewFromPayload(body.style_review),
  };
}

const STYLE_VERDICTS = ["ok", "rewrite"] as const;

/** 认不出的结论当 null，**不就近解释**成「通过」（G01 同一条）。 */
function styleVerdict(v: unknown): "ok" | "rewrite" | null {
  return STYLE_VERDICTS.find((x) => x === v) ?? null;
}

/** 风格审查的结果体。缺了就是 null，不编。 */
export function styleReviewFromPayload(
  r: { verdict?: unknown; notes?: unknown; attempts?: unknown; error?: unknown } | null | undefined,
): StyleReviewInfo | null {
  if (!r || typeof r !== "object") return null;
  const notes = Array.isArray(r.notes) ? r.notes : [];
  const attempts = Array.isArray(r.attempts) ? r.attempts : [];
  return {
    verdict: styleVerdict(r.verdict),
    notes: notes.flatMap((n) =>
      n && typeof n === "object"
        ? [{ aspect: String((n as { aspect?: unknown }).aspect ?? ""), comment: String((n as { comment?: unknown }).comment ?? "") }]
        : [],
    ),
    attempts: attempts.map((a) => styleVerdict(a && typeof a === "object" ? (a as { verdict?: unknown }).verdict : null)),
    error: typeof r.error === "string" ? r.error : null,
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

// ---------------------------------------------------------------- F4 · 这部分没看懂（按钮已撤）
//
// 2026-10-07 撤掉了「这部分没看懂」按钮与它的客户端代码（`markUnavailable` /
// `markRequestBody` / `submitMark` / `markButtonDisabled`，口径 F107）：难点由用户
// **追问**说出来，追问时服务端让模型换个角度、更浅白、带例子重讲那一点。
// 已经标过的页仍然生效——服务端照旧读 `confusion_marks`，命中时注入限定语，
// 页面照旧显示「你之前标过这几页没看懂」（`confusion` 字段，见上面的 TurnAnswer）。

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
  /**
   * 这个会话的核对结果（`answer_checks` 行，口径 F121）。按 `round_id` 挂回那一轮。
   * 不给 / 读失败 = 历史轮次都没有核对信息（不是「核对通过」）。
   */
  checks: { round_id: string; status: string; result?: unknown }[] = [],
): Turn[] {
  const checkByRound = new Map(checks.map((c) => [c.round_id, c]));
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
          agent: m.agent as never,
          style_review: m.style_review as never,
          // 库里的 status 只有 written / failed；结果体的形状与 `verify` 事件同一个。
          verification: (() => {
            const c = v.roundId ? checkByRound.get(v.roundId) : undefined;
            return c ? { ...(c.result as object), status: c.status, persisted: undefined } : undefined;
          })(),
        }),
      };
    });
}
