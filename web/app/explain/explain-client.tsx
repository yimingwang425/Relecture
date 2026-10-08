"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Diagnostics } from "@/app/components/diagnostics";
import { LoadingQuotes } from "@/app/components/loading-quotes";
import { StyleNudge } from "@/app/explain/style-nudge";
import { StatusDot } from "@/app/components/status-dot";
import { createClient } from "@/lib/supabase/client";
import {
  answerFromResponse,
  answeredCount,
  gapNotice,
  hasPending,
  isFromRequestedCourse,
  nextTurnIndex,
  type Turn,
} from "@/lib/explain/turns";
import { EMPTY_DRAFT, readExplainStream } from "@/lib/explain/stream";
import { Markdown } from "@/lib/style/markdown";

/**
 * 讲解页的交互。
 *
 * ## 这一页**不构造任何事件**（口径 F51）
 *
 * §13.0 的四个事件（`explain_turn` / `style_review_result` /
 * `regeneration_triggered` / `retrieval_executed`）由**服务端**发：它们的字段
 * 全部产生在那一次 `run_round()` 里（token 四类分列、成本与价目、逐维得分、
 * 检索分数分布），**浏览器一个都不知道**。让前端发就得先序列化过去再发回来，
 * 多一份可以漂的副本，而且前端能改这些数字。
 *
 * 本组件的埋点责任只有两样，且都只是**转述**：
 * `session_id`（服务端页面给的）与 `turn_index`（口径在 `lib/explain/turns.ts`）。
 *
 * F4 的「这部分没看懂」同理：`confusion_mark_created` 由 `POST /marks` 在**服务端**
 * 同一次请求里发（`mark_count_after` 本来就只有服务端知道）。前端只转述
 * `session_id` / `course_id`，**不入队、不另发**——另发就是每次点击两行。
 *
 * ## 等待态：**流式**（2026-10-07，口径 F94）
 *
 * 走 `POST /explain/stream`：检索一完出处先到，正文一段段长出来；
 * 审查判不达标要重写时服务端发 `reset`，这里清掉已显示的那一版并说一句「在重写」。
 * 原来是整段返回，一次 **30–60 秒**全程只有转圈。
 *
 * - **只允许一轮在飞**。两轮同时流的话，会话历史里两轮的先后由服务端各自落库的
 *   时刻决定，可能与提问顺序相反，而 `turn_index` 按「用户看到第几次回答」计。
 * - **计时器仍然在**。正文出来之前（检索 + 首字前的思考）可能有好几秒什么都没有，
 *   没有它，那几秒与一个卡死的请求长得一模一样。
 * - **关掉页面不会丢这一轮**（有会话时）：生成在服务端后台跑完并落库，
 *   刷新会话就能看到。这一句要说出来，否则用户会守着页面不敢动。
 * - **终态只认 `done`**：草稿是边收边拼的，出处、诊断、正文一律取 `done` 里的
 *   响应体（与 `/explain` 逐字段同形）。流在 `done` 之前断了**不算成功**。
 *
 * ## 用合成课件答的必须被看见
 *
 * 2026-09-12 真实发生过：线上跑着一个旧构建，它**静默丢掉** `course_id`
 * 并用合成课件作答，返回 **200**，正文在讲布隆过滤器（口径 F52）。
 * 那种回答**看起来完全正常**。所以这里按 `retrieval.meta.backend` 判，
 * 不是「有没有报错」（F17 的同一形态：要读的是「读的是哪个对象」）。
 *
 * ## 布局（2026-09-15 UI 重做）
 *
 * 桌面两栏：左边吸顶的提问卡，右边一轮一张卡（新的在上）。每轮卡片取参考稿的
 * 「卡片 → 标签页 → 面板」三层：
 *
 * - **讲解**：正文 + 出处（文件与页号，**始终可见**——PRD §6.2 要求溯源）+
 *   「这部分没看懂」。兜底判断非 covered 时，正文上方挂一条琥珀色的行（`gapNotice`）。
 * - **出处**：逐片的标题、页号、检索分数，以及读不了的页。
 * - **诊断**：`round_id` / `slice_keys` / `gap` / `confusion` / 埋点结果。
 *   人工验收要读它们（进度总结 §八 第 7 条：折叠，不删）。
 */

type Course = { id: string; name: string; created_at: string };

type Tab = "answer" | "sources" | "diag";

/** 重讲时发出去的问题前缀。用户在会话里看得见它——这一轮是重讲，不是新问题。 */
const REDO_PREFIX = "按我刚调整的讲法，重新讲一遍：";

const TABS: readonly (readonly [Tab, string])[] = [
  ["answer", "讲解"],
  ["sources", "出处"],
  ["diag", "诊断"],
];

const DETAIL_COPY: Record<string, string> = {
  // 与设置页同一条纪律：**用文字描述档位，不写区间数字**（口径 F13）。
  P0: "偏详细",
  P2: "适中",
  P3: "偏精简",
};

/**
 * 跳过页的类型 → 给用户看的话。PRD §6.2：`image_only` 是「内容在图里读不了」，
 * 不是「课件里没提到」。认不出的类型原样显示，不猜。
 */
const SKIP_KIND: Record<string, string> = {
  image_only: "内容在图里",
  empty: "空白页",
};

export function ExplainClient({
  apiBase,
  sessionId,
  detailLevel,
  modelLabel,
  userId,
  conversationId = null,
  initialTurns,
  lockedCourse,
  onFirstQuestion,
  autoAsk,
}: {
  apiBase: string;
  sessionId: string;
  detailLevel: string;
  /** 模型名。**显示在输入框下面**——用户有权知道这段讲解是谁生成的。 */
  modelLabel?: string;
  /** 改风格配置要写自己那一行。给了它才渲染「调整讲法」。 */
  userId?: string;
  /**
   * F5 · 这一轮写进哪个会话。给了它，服务端才会读历史、写消息、自己算轮次。
   * 不给就是一次性问答（`/explain` 那个跨课页面仍然是这样）。
   */
  conversationId?: string | null;
  /** 打开一个已有会话时，先把已经问过的轮次铺上（服务端组件从库里读好传进来）。 */
  initialTurns?: Turn[];
  /**
   * 项目页（`/courses/[id]`）用：这一页只问这门课。
   * 给了它就不渲染课程下拉，`courseId` 也不再被课程列表覆盖——
   * 用户是从这门课点进来的，换课应当是回到列表再进另一门，不是在这里改一个下拉。
   */
  lockedCourse?: { id: string; name: string };
  /**
   * 还没有会话时（课程工作区的空白输入框）：第一个问题**不在这里发**，交给调用方去建会话。
   * 建好之后调用方换成正式会话重挂载这个组件，并把问题经 `autoAsk` 交回来。
   * 返回 false = 没建成，问题填回输入框。
   */
  onFirstQuestion?: (question: string) => Promise<boolean>;
  /** 挂载后立刻发出的问题（只发一次）。见 `onFirstQuestion`。 */
  autoAsk?: string;
}) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState(lockedCourse?.id ?? "");
  const [question, setQuestion] = useState(autoAsk ?? "");
  /** 正在建会话（`onFirstQuestion` 那一步）。期间输入框与发送都停用。 */
  const [starting, setStarting] = useState(false);
  // 打开已有会话时先铺上历史。**key 由调用方按 conversationId 给**，
  // 换会话时整个组件重挂载——用 effect 去同步 props 到 state 的话，
  // 换会话那一瞬间旧会话的轮次还在屏幕上，而输入框已经指向新会话了。
  const [turns, setTurns] = useState<Turn[]>(initialTurns ?? []);
  const [listError, setListError] = useState<string | null>(null);
  const [buildCommit, setBuildCommit] = useState<string | null>(null);
  /** 当前这一轮等了多少秒。**不是阈值判断**，只是让等待可见。 */
  const [elapsed, setElapsed] = useState(0);
  /** 每轮卡片当前在哪个标签页。键是 `turns` 里的原始下标。 */
  const [tabs, setTabs] = useState<Record<number, Tab>>({});

  const pending = hasPending(turns) || starting;

  const token = useCallback(async () => {
    const { data } = await createClient().auth.getSession();
    return data.session?.access_token ?? null;
  }, []);

  useEffect(() => {
    void (async () => {
      const jwt = await token();
      if (!jwt) return setListError("登录状态已过期，请重新登录。");
      try {
        const r = await fetch(`${apiBase}/courses`, {
          headers: { Authorization: `Bearer ${jwt}` },
        });
        setBuildCommit(r.headers.get("x-build-commit"));
        if (!r.ok) return setListError(`读课程列表失败（HTTP ${r.status}）。${await r.text()}`);
        const rows = (await r.json()) as Course[];
        setCourses(rows);
        // 锁定课程时不让列表覆盖选择：那会把用户点进来的这门课换掉。
        if (!lockedCourse) setCourseId((prev) => prev || rows[0]?.id || "");
      } catch (e) {
        // fetch 被网络层拒掉时没有状态码 —— **CORS 被挡就长这样**。
        // 点名说出这个可能，否则它读起来像「网络不好」。
        setListError(
          `连不上产品服务端（${apiBase}）。可能是它还没部署，` +
            `或者它的 CORS_ALLOW_ORIGINS 里没有本站来源。原始错误：${String(e)}`,
        );
      }
    })();
  }, [apiBase, token, lockedCourse]);

  // 等待计时器。只在有一轮在飞时走。归零放在 `ask()` 里发起那一刻，
  // 不在 effect 里同步 setState（react-hooks/set-state-in-effect）。
  useEffect(() => {
    if (!pending) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [pending]);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const turnsRef = useRef<HTMLDivElement>(null);

  // 新的一轮出现就滚到底（聊天式布局：最新的在最下面）。
  //
  // **按 `turns.length` 触发，不按 `pending`**：一轮结束时 pending 从 true 变 false，
  // 而那一刻答案刚渲染出来、正是最该滚的时候；只盯 pending 的话，答案回来后
  // 页面停在提问那一屏，用户以为什么都没发生。
  useEffect(() => {
    const el = turnsRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [turns.length, pending]);

  // 刚建好的会话：把带过来的那个问题发出去。ref 挡住开发模式下 effect 的二次执行——
  // 发两次就是两轮讲解、两份钱。
  const autoAsked = useRef(false);
  useEffect(() => {
    if (!autoAsk || autoAsked.current) return;
    autoAsked.current = true;
    void ask();
    // 只在挂载时跑一次：ask 每次渲染都是新函数，放进依赖会在每次渲染后重发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** `override`：不读输入框，直接发这一句（重讲用）。输入框里正在打的字不动。 */
  async function ask(override?: string) {
    const q = (override ?? question).trim();
    // **只允许一轮在飞**（见组件说明）。
    if (!q || !courseId || pending) return;

    if (onFirstQuestion && !conversationId) {
      setStarting(true);
      const ok = await onFirstQuestion(q);
      // 建成了这个组件会被换掉（调用方按新会话重挂载），不必再动自己的状态。
      if (!ok) setStarting(false);
      return;
    }

    // `turn_index` 由账本算，**不是「发了几次请求」**：失败不计入
    // （用户一个字都没看到，那不是一轮讲解），重生成也不计入（它在服务端一次
    // 请求里面）。口径与理由在 lib/explain/turns.ts。
    const turnIndex = nextTurnIndex(turns);
    setTurns((prev) => [...prev, { turnIndex, question: q, courseId, state: "pending", draft: EMPTY_DRAFT }]);
    setElapsed(0);
    if (override === undefined) setQuestion("");

    const finish = (patch: Partial<Turn>) =>
      setTurns((prev) =>
        prev.map((t, i) => (i === prev.length - 1 ? { ...t, ...patch } : t)),
      );

    const jwt = await token();
    if (!jwt) {
      return finish({
        state: "failed",
        failure: { status: null, message: "登录状态已过期，请重新登录。", retryable: true },
      });
    }

    try {
      const r = await fetch(`${apiBase}/explain/stream`, {
        method: "POST",
        headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          course_id: courseId,
          session_id: sessionId,
          turn_index: turnIndex,
          // 给了会话，服务端会读历史、写消息，并**用它自己算出来的轮次**
          // 覆盖上面这个（见 api/api/main.py 里 `turn_index` 的注释）。
          ...(conversationId ? { conversation_id: conversationId } : {}),
        }),
      });
      setBuildCommit((prev) => r.headers.get("x-build-commit") ?? prev);

      if (!r.ok) {
        let message = "";
        try {
          const body = (await r.json()) as { detail?: unknown };
          message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } catch {
          message = await r.text().catch(() => "");
        }
        return finish({
          state: "failed",
          failure: {
            status: r.status,
            message: message || `HTTP ${r.status}`,
            // 503（服务端没配 model key）与 5xx 重试有意义；
            // 404（不是你的课）/ 422（输入不合法）重试没有意义。
            // 方向与上传那边一致：集合取小。
            retryable: r.status === 503 || r.status === 502 || r.status === 504,
          },
        });
      }

      if (!r.body) {
        return finish({
          state: "failed",
          failure: { status: r.status, message: "服务端返回了 200 但没有正文流。", retryable: true },
        });
      }
      const outcome = await readExplainStream(r.body, (draft) =>
        setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, draft } : t))),
      );
      if (outcome.kind === "done") {
        // **终态只取响应体**，不取草稿（见组件说明）。
        return finish({
          state: "answered",
          draft: undefined,
          answer: answerFromResponse(outcome.response as Parameters<typeof answerFromResponse>[0]),
        });
      }
      finish({
        state: "failed",
        draft: undefined,
        failure:
          outcome.kind === "error"
            ? {
                status: outcome.status,
                message: outcome.detail,
                retryable: outcome.status === null || outcome.status >= 500,
              }
            : {
                status: null,
                message:
                  `${outcome.detail}。` +
                  (conversationId ? "服务端多半已经讲完并存进了这个会话，刷新一下就能看到。" : ""),
                retryable: true,
              },
      });
    } catch (e) {
      finish({
        state: "failed",
        failure: {
          status: null,
          message:
            `连不上产品服务端。可能是它没部署，或 CORS_ALLOW_ORIGINS 里没有本站来源。` +
            `原始错误：${String(e)}`,
          retryable: true,
        },
      });
    }
  }

  /**
   * 「按新讲法重讲上一个问题」（口径 F107）。在这个会话里**再问一次**，问题前面写明是重讲——
   * 服务端读到的是刚改好的配置，模型也知道这不是一个新问题。
   * 上一个问题本身就是一次重讲时，剥掉前缀再套，免得越套越长。
   */
  const lastAnswered = [...turns].reverse().find((t) => t.state === "answered");
  function redo() {
    if (!lastAnswered) return;
    const original = lastAnswered.question.startsWith(REDO_PREFIX)
      ? lastAnswered.question.slice(REDO_PREFIX.length)
      : lastAnswered.question;
    void ask(`${REDO_PREFIX}${original}`);
  }

  const composer = (
    <div className="composer" data-field="composer">
      {listError && (
        <p role="alert" data-field="list-error" className="notice notice--danger mb-3 text-[13px]">
          {listError}
        </p>
      )}

      {courses !== null && courses.length === 0 && !lockedCourse ? (
        <div className="flex flex-col items-start gap-3" data-field="no-courses">
          <p className="t-body">
            还没有课件。讲解只用你上传的材料，不靠通用知识补全——先传一份再来问。
          </p>
          <Link href="/" className="btn btn--primary btn--sm no-underline">
            去我的课程
          </Link>
        </div>
      ) : (
        <>
          {!lockedCourse && courses !== null && (
            <label className="mb-2 block">
              <span className="field-label">问哪门课</span>
              <select
                data-field="course-select"
                value={courseId}
                onChange={(e) => setCourseId(e.target.value)}
                disabled={pending}
                className="input"
              >
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="composer__box">
            <textarea
              ref={inputRef}
              data-field="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                // Enter 发送、Shift+Enter 换行（和 Claude 一致）。
                // ⌘/Ctrl+Enter 仍然留着——改键位不该把旧习惯打掉。
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void ask();
                } else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  void ask();
                }
              }}
              disabled={pending}
              rows={2}
              maxLength={500}
              placeholder={
                starting ? "正在建对话…" : pending ? "正在讲解，等这一轮回来再问…" : "问这门课的任何问题"
              }
              className="composer__input"
            />
            <div className="composer__foot">
              <span className="t-caption">
                {/* 用的是哪个模型，**说出来**。 */}
                {modelLabel ? <span className="mono max-sm:hidden">{modelLabel}</span> : null}
                {modelLabel ? <span className="max-sm:hidden"> · </span> : null}
                <span data-field="detail-level">
                  {DETAIL_COPY[detailLevel] ?? detailLevel}
                </span>
                {/* 能改配置时旁边就有「调整讲法」按钮；只有不能改的时候才给去设置页的链接。
                    原来两个都在，同一行里出现两次「调整讲法」，一个跳页、一个弹窗。 */}
                {!userId && (
                  <>
                    {" · "}
                    <Link href="/settings/style" className="link">
                      调整讲法
                    </Link>
                  </>
                )}
              </span>
              <span className="flex items-center gap-3">
                {userId && (
                  <StyleNudge
                    userId={userId}
                    sessionId={sessionId}
                    // 有答过的一轮、且没有一轮在飞时才能重讲。
                    onRedo={lastAnswered && !pending ? redo : undefined}
                  />
                )}
                <span className="t-caption kbd-count max-sm:hidden">{question.length} / 500</span>
                <button
                  data-field="ask"
                  onClick={() => void ask()}
                  disabled={!question.trim() || !courseId || pending}
                  className="btn btn--primary btn--sm"
                >
                  {pending ? "讲解中…" : "发送"}
                </button>
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );

  return (
    <section className="chat" data-testid="explain">
      <div ref={turnsRef} className="chat__scroll">
        {/* ⚠️ **不放「还没有提问」那张卡**（2026-09-20 去掉）。
            它占满首屏、每次新建会话都再来一遍，而它说的三件事
            （问得具体些、只用你的课件、片段里没有会直说）第一次之后就是噪声。
            空会话现在就是空的——输入框自己会说该做什么。 */}

        <ol className="chat__turns" data-field="turns">
            {turns.map((t, index) => {
              const tab = tabs[index] ?? "answer";
              const sliceCount = t.answer?.slices.length ?? 0;
              const notice = t.answer ? gapNotice(t.answer) : null;
              return (
                <li
                  key={index}
                  data-field="turn"
                  data-state={t.state}
                  data-turn-index={t.turnIndex}
                  data-backend={t.answer?.backend ?? ""}
                  data-round-id={t.answer?.roundId ?? ""}
                  data-confusion-injected={String(t.answer?.confusion?.injected ?? "")}
                  className="turn enter"
                >
                  {/* 用户那句话。**不再用「第 N 轮」当标题**：聊天式布局里
                      轮次编号是内部口径，用户读的是自己问了什么。编号仍然在
                      data-turn-index 上，回归工具按它断言。 */}
                  <p className="bubble" data-field="turn-question">{t.question}</p>

                  {t.state === "pending" && t.draft && t.draft.text && (
                    <div className="tabpanel p-4 sm:p-6" data-field="answer-streaming" aria-live="polite">
                      {t.draft.rewriting > 0 && (
                        <p className="notice notice--warn mb-3 text-[13px]" data-field="rewriting">
                          上一版没达到你设定的讲法要求，正在重写（第 {t.draft.rewriting} 次）…
                        </p>
                      )}
                      <div className="prose-answer">
                        <Markdown text={t.draft.text} />
                      </div>
                      {t.draft.sources.length > 0 && (
                        <p className="t-caption mt-4">
                          出处：
                          {t.draft.sources.map((s) => `${s.sourceFile} 第 ${s.pages.join("、")} 页`).join("；")}
                        </p>
                      )}
                      <p className="t-caption mt-2 flex items-center gap-2" data-field="elapsed">
                        <span aria-hidden className="spinner" />
                        生成中 · 已 <b className="kbd-count">{elapsed}</b> 秒
                      </p>
                    </div>
                  )}

                  {t.state === "pending" && !t.draft?.text && (
                    <div className="answer-wait" aria-live="polite">
                      {/* 小尺寸、贴左上角（2026-09-20）。原来是整块面板 + 四条骨架，
                          在聊天式布局里那等于每问一句就插进来一屏占位。
                          等待要看得见，但它不该比回答还大。 */}
                      <LoadingQuotes className="quote-stage--sm" />
                      {/* 走着的计时器。**不能省**：没有它，一个 40 秒的等待与一个
                          卡死的请求在页面上长得一模一样。它不是阈值判断，
                          只是让等待可见——所以只报秒数，不报「是不是太久了」。 */}
                      <p className="t-caption mt-2 flex items-center gap-2" data-field="elapsed">
                        <span aria-hidden className="spinner" />
                        {t.draft?.rewriting
                          ? "上一版没达到讲法要求，正在重写"
                          : t.draft?.stage === "searching"
                            ? (t.draft.activity ?? "正在翻课件")
                            : t.draft?.stage === "generating"
                              ? "正在组织讲解"
                              : "正在检索课件"}
                        {" · "}已等 <b className="kbd-count">{elapsed}</b> 秒
                        {conversationId ? " · 关掉页面也不会丢，会存进这个会话" : ""}
                      </p>
                    </div>
                  )}

                  {t.state === "failed" && t.failure && (
                    <div className="panel p-4 sm:p-5" data-field="turn-failure">
                      <div className="flex items-start gap-3">
                        <StatusDot kind="bad" />
                        <div className="min-w-0 flex-1">
                          <p className="row__title">
                            这一轮没成{t.failure.status ? `（HTTP ${t.failure.status}）` : ""}。
                          </p>
                          <p className="mono mt-1 text-[11.5px] leading-relaxed break-all text-[var(--c-muted-text)]">
                            {t.failure.message}
                          </p>
                          {/* 失败不占轮次 —— 说清楚，否则用户会以为自己「用掉」了一轮。 */}
                          <p className="t-caption mt-2">这一次没有产生回答，所以它不计入会话轮次。</p>
                          {t.failure.retryable && (
                            <button
                              data-field="turn-retry"
                              onClick={() => {
                                setQuestion(t.question);
                                inputRef.current?.focus();
                              }}
                              className="btn btn--ghost btn--sm mt-3"
                            >
                              重新填回这个问题
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {t.state === "answered" && t.answer && (
                    <>
                      <div role="tablist" aria-label={`第 ${t.turnIndex} 轮的内容`} className="tabs">
                        {TABS.map(([id, label]) => (
                          <button
                            key={id}
                            type="button"
                            role="tab"
                            aria-selected={tab === id}
                            data-tab={id}
                            className="tab"
                            onClick={() => setTabs((prev) => ({ ...prev, [index]: id }))}
                          >
                            {label}
                            {id === "sources" && <span className="tab__count kbd-count">{sliceCount}</span>}
                          </button>
                        ))}
                      </div>

                      <div role="tabpanel" className="tabpanel p-4 sm:p-6">
                        {/* ---------------------------------------------- 讲解 */}
                        {tab === "answer" && (
                          <>
                            {/* ⚠️ 用合成课件答的**必须被看见**：那种回答看起来完全正常，
                                而它与用户的课件无关（口径 F52 那次真实失败）。 */}
                            {!isFromRequestedCourse(t.answer) && (
                              <p role="alert" data-field="wrong-backend" className="notice notice--danger mb-4 text-[13px]">
                                ⚠️ <b>这一段不是用你的课件答的。</b>
                                服务端报的检索后端是 <code>{t.answer.backend}</code>，
                                而不是 <code>supabase</code> —— 说明它没有用这门课的材料
                                （最可能是线上构建比这一页旧）。
                                <b>别把这段内容当成课件里的说法。</b>
                              </p>
                            )}

                            {/* F3b 兜底判断：判了且不是 covered 才挂。措辞守 G02——
                                说「片段里」，不说「课件里没有」。 */}
                            {gapNotice(t.answer) && notice && (
                              <div className="row mb-4" data-field="gap-notice" data-gap-verdict={notice.verdict}>
                                <StatusDot kind="warn" />
                                <div className="min-w-0 flex-1">
                                  <p className="row__title">
                                    {notice.verdict === "insufficient"
                                      ? "检索到的课件片段不足以回答这个问题"
                                      : "检索到的课件片段只覆盖了这个问题的一部分"}
                                  </p>
                                  <p className="row__sub">
                                    讲解里会说明哪些地方片段没有涉及——那部分不是课件的说法。
                                    {notice.pages.length > 0 &&
                                      ` 另外第 ${notice.pages.join("、")} 页的内容读不出文字（多半在图里），讲解没有用到。`}
                                  </p>
                                </div>
                              </div>
                            )}

                            {t.answer.confusion?.injected && (
                              <div className="row mb-4" data-field="confusion-injected">
                                <StatusDot kind="info" />
                                <p className="row__title">你之前标过这几页没看懂，这次换了个切入点讲。</p>
                              </div>
                            )}

                            <div data-field="answer" className="prose-answer">
                              <Markdown text={t.answer.text} />
                            </div>

                            {/* 落库失败不打断讲解（F42），但**不能静默**：不说的话用户刷新后才发现对话没了。 */}
                            {t.answer.persistFailed && (
                              <p role="alert" data-field="persist-failed" className="notice notice--warn mt-4 text-[13px]">
                                这一轮没能存进会话，刷新页面后会看不到它（讲解本身不受影响）。
                                <span className="mono mt-1 block text-[11.5px] break-all opacity-80">{t.answer.persistFailed}</span>
                              </p>
                            )}

                            {/* 出处：点名到页，**始终可见**。PRD 要求讲解能溯源到页号。 */}
                            {t.answer.sources.length > 0 ? (
                              <div className="mt-6 flex flex-wrap items-center gap-2" data-field="sources">
                                <span className="t-caption mr-1">出处</span>
                                {t.answer.sources.map((s) => (
                                  <span key={s.sourceFile} className="chip h-auto min-h-7 max-w-full flex-wrap py-1">
                                    <span className="truncate" title={s.sourceFile}>{s.sourceFile}</span>
                                    <span className="text-[var(--c-muted-text)]">第 {s.pages.join("、")} 页</span>
                                  </span>
                                ))}
                                <button
                                  type="button"
                                  className="t-caption link cursor-pointer bg-transparent"
                                  onClick={() => setTabs((prev) => ({ ...prev, [index]: "sources" }))}
                                >
                                  看这几片原文的标题与分数
                                </button>
                              </div>
                            ) : (
                              t.answer.roundId && (
                                // 路由判成寒暄（口径 F110）时根本没去查课件——说「没有检索到」是错话，
                                // 听起来像检索失败了。
                                <p className="t-caption mt-6" data-field="no-sources">
                                  {t.answer.agent?.route === "chat"
                                    ? "这句不需要查课件，所以没有出处。"
                                    : "这一轮没有检索到课件片段，所以没有出处。"}
                                </p>
                              )
                            )}

                            <p className="t-caption mt-2" data-field="turn-meta">
                              {t.answer.regenerations > 0 && (
                                // 重生成**不计入轮次**（§13.0），显示它是为了让
                                // 「审查链路在工作」这件事可见。
                                <>重生成 {t.answer.regenerations} 次（审查判不达标后改写，不占轮次）</>
                              )}
                              {!t.answer.accepted && (
                                <>
                                  {t.answer.regenerations > 0 && " · "}
                                  <span className="text-[var(--c-warn)]">重生成触顶仍未达标，按原样输出</span>
                                </>
                              )}
                            </p>

                          </>
                        )}

                        {/* ---------------------------------------------- 出处 */}
                        {tab === "sources" && (
                          <div data-field="slices">
                            {t.answer.slices.length === 0 ? (
                              <p className="t-meta">
                                {t.answer.agent?.route === "chat" ? "这句不需要查课件。" : "这一轮没有检索到课件片段。"}
                              </p>
                            ) : (
                              <>
                                <p className="t-meta mb-3">讲解用到的课件片段，按检索时的顺序。</p>
                                <ol className="flex flex-col gap-2">
                                  {t.answer.slices.map((s, j) => (
                                    <li key={`${s.sourceFile}-${j}`} className="row">
                                      <span className="chip kbd-count h-6 shrink-0 px-2">{j + 1}</span>
                                      <div className="min-w-0 flex-1">
                                        <p className="row__title">{s.title ?? "（这一片没有标题）"}</p>
                                        <p className="row__sub">
                                          <span className="mono">{s.sourceFile}</span> · 第 {s.pages.join("、")} 页
                                          {s.score !== null && ` · 相似度 ${s.score.toFixed(3)}`}
                                        </p>
                                      </div>
                                    </li>
                                  ))}
                                </ol>
                              </>
                            )}
                            {t.answer.skippedPages.length > 0 && (
                              <div className="mt-5">
                                <p className="t-meta mb-2">这些页读不出文字，讲解没有用到它们：</p>
                                <p className="t-caption mono">
                                  {t.answer.skippedPages
                                    .map((p) => `${p.sourceFile} 第 ${p.index} 页（${SKIP_KIND[p.kind] ?? p.kind}）`)
                                    .join("；")}
                                </p>
                              </div>
                            )}
                          </div>
                        )}

                        {/* ---------------------------------------------- 诊断 */}
                        {tab === "diag" && (
                          <dl className="diag-grid mt-0" data-field="turn-diagnostics">
                            {(
                              [
                                ["round_id", t.answer.roundId ?? "null（服务端没给）"],
                                ["slice_keys", t.answer.sliceKeys.join(", ") || "（空）"],
                                ["backend", t.answer.backend],
                                ["detail_level", t.answer.detailLevel],
                                ["平均段落", `${t.answer.meanParagraphChars.toFixed(0)} 字`],
                                ["重生成", `${t.answer.regenerations}（accepted=${String(t.answer.accepted)}）`],
                                ["gap", t.answer.gap ? JSON.stringify(t.answer.gap) : "null（服务端没给）"],
                                ["agent", t.answer.agent ? JSON.stringify(t.answer.agent) : "null（服务端没给）"],
                                ["confusion", t.answer.confusion ? JSON.stringify(t.answer.confusion) : "null"],
                                ["埋点结果", t.answer.events ? JSON.stringify(t.answer.events) : "—"],
                              ] as const
                            ).map(([k, v]) => (
                              <div key={k} className="contents">
                                <dt>{k}</dt>
                                <dd>{v}</dd>
                              </div>
                            ))}
                          </dl>
                        )}
                      </div>
                    </>
                  )}
                </li>
              );
            })}
        </ol>
      </div>

      {composer}

      {/* 验收要读的那几个数。**折叠，不删**（进度总结 §八 第 7 条）。 */}
      <details className="diagnostics mx-auto w-full max-w-[var(--chat-w)] px-1">
        <summary>
          这个会话已答{" "}
          <b className="kbd-count" data-field="answered-count">{answeredCount(turns)}</b> 轮
        </summary>
        <Diagnostics
          className="mt-3"
          items={[
            ["服务端", apiBase],
            ["构建", buildCommit ? buildCommit.slice(0, 7) : "读不到构建标识", "api-build"],
            ["session", sessionId.slice(0, 8)],
            ["conversation", conversationId ? conversationId.slice(0, 8) : "无（这一轮不落库）"],
          ]}
        />
      </details>
    </section>
  );
}
