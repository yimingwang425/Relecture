"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Diagnostics } from "@/app/components/diagnostics";
import { LoadingQuotes } from "@/app/components/loading-quotes";
import { StatusDot } from "@/app/components/status-dot";
import { createClient } from "@/lib/supabase/client";
import {
  answerFromResponse,
  answeredCount,
  gapNotice,
  hasPending,
  isFromRequestedCourse,
  markButtonDisabled,
  markUnavailable,
  nextTurnIndex,
  submitMark,
  type Turn,
} from "@/lib/explain/turns";
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
 * ## 等待态是这一页最要紧的交互
 *
 * 一次请求 **30–60 秒**（生成 + 最多 2 次重生成），而且**不流式**
 * （`explain/prompt.py`：`first_token_latency_ms` 恒 null，「不塞假数」）。
 * 所以：
 *
 * - **只允许一轮在飞**。不流式意味着第二次提交不会「插队」，只会让两个几十秒
 *   的请求同时在跑，而先回来的那个先渲染 —— **轮次顺序会与提问顺序相反**，
 *   而 `turn_index` 是按「用户看到第几次回答」计的，那时它就错了。
 * - **有一个走着的计时器**。没有它，一个 40 秒的等待与一个卡死的请求
 *   在页面上长得一模一样。
 * - **明说别关页面**。关掉标签页这一轮就没了（不流式，没有中间结果可续）。
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
  conversationId = null,
  initialTurns,
  lockedCourse,
}: {
  apiBase: string;
  sessionId: string;
  detailLevel: string;
  /** 模型名。**显示在输入框下面**——用户有权知道这段讲解是谁生成的。 */
  modelLabel?: string;
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
}) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState(lockedCourse?.id ?? "");
  const [question, setQuestion] = useState("");
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

  const pending = hasPending(turns);

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

  async function ask() {
    const q = question.trim();
    // **只允许一轮在飞**（见组件说明）。
    if (!q || !courseId || pending) return;

    // `turn_index` 由账本算，**不是「发了几次请求」**：失败不计入
    // （用户一个字都没看到，那不是一轮讲解），重生成也不计入（它在服务端一次
    // 请求里面）。口径与理由在 lib/explain/turns.ts。
    const turnIndex = nextTurnIndex(turns);
    setTurns((prev) => [...prev, { turnIndex, question: q, courseId, state: "pending" }]);
    setElapsed(0);
    setQuestion("");

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
      const r = await fetch(`${apiBase}/explain`, {
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

      finish({ state: "answered", answer: answerFromResponse(await r.json()) });
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
   * 「这部分没看懂」（F4）。按**下标**改那一轮，不是「最后一轮」：
   * 用户完全可能在下一轮还在飞时回头标记上一轮。
   */
  async function markConfused(index: number) {
    const t = turns[index];
    if (!t?.answer || markUnavailable(t.answer) !== null || markButtonDisabled(t.mark)) return;

    const patchMark = (mark: Turn["mark"]) =>
      setTurns((prev) => prev.map((x, i) => (i === index ? { ...x, mark } : x)));

    patchMark({ status: "sending" });
    const jwt = await token();
    if (!jwt) return patchMark({ status: "failed", message: "登录状态已过期，请重新登录。" });
    patchMark(await submitMark({ fetch: (u, init) => fetch(u, init), apiBase, jwt, turn: t, sessionId }));
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
              placeholder={pending ? "正在讲解，等这一轮回来再问…" : "问这门课的任何问题"}
              className="composer__input"
            />
            <div className="composer__foot">
              <span className="t-caption">
                {/* 用的是哪个模型，**说出来**。 */}
                {modelLabel ? <span className="mono">{modelLabel}</span> : null}
                {modelLabel ? " · " : ""}
                <span data-field="detail-level">
                  {DETAIL_COPY[detailLevel] ?? detailLevel}
                </span>
                {" · "}
                <Link href="/settings/style" className="link">
                  调整讲法
                </Link>
              </span>
              <span className="flex items-center gap-3">
                <span className="t-caption kbd-count">{question.length} / 500</span>
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

                  {t.state === "pending" && (
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
                        已等 <b className="kbd-count">{elapsed}</b> 秒 · 不流式，别关页面
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

                            {/* 出处：点名到页，**始终可见**。PRD 要求讲解能溯源到页号。 */}
                            {t.answer.sources.length > 0 ? (
                              <div className="mt-6 flex flex-wrap items-center gap-2" data-field="sources">
                                <span className="t-caption mr-1">出处</span>
                                {t.answer.sources.map((s) => (
                                  <span key={s.sourceFile} className="chip h-auto min-h-7 max-w-full flex-wrap py-1">
                                    <span className="mono truncate">{s.sourceFile}</span>
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
                                <p className="t-caption mt-6">
                                  这一轮没有检索到课件片段，所以没有出处，也不能标记「没看懂」。
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

                            {/* ---- F4 · 这部分没看懂 ----
                                空检索轮不渲染（POST /marks 对空 slice_ids 回 422）；
                                服务端旧到没有 round_id 时**说出来**，不与空检索一样静默。 */}
                            {markUnavailable(t.answer) === "old-server" && (
                              <p className="notice notice--warn mt-4 text-[13px]" data-field="mark-old-server">
                                服务端没有返回 round_id（构建比这一页旧），这一轮暂时标记不了。
                              </p>
                            )}
                            {markUnavailable(t.answer) === null && (
                              <div className="mt-5 border-t border-[var(--c-border-row)] pt-4" data-field="mark">
                                <div className="flex flex-wrap items-start gap-x-5 gap-y-3">
                                  <button
                                    data-field="mark-confused"
                                    data-mark-state={t.mark?.status ?? "idle"}
                                    onClick={() => void markConfused(index)}
                                    disabled={markButtonDisabled(t.mark)}
                                    className="btn btn--ghost btn--sm shrink-0"
                                  >
                                    {t.mark?.status === "marked"
                                      ? "已记下"
                                      : t.mark?.status === "sending"
                                        ? "记下中…"
                                        : "这部分没看懂"}
                                  </button>
                                  {/* 口径 F56：两个按钮的说明**放在一起**，把「这次这里」与「以后都」说开。
                                      用户点错不会有任何指标变红 —— 配置会被一路改深，而没人知道。 */}
                                  <ul data-field="mark-scope" className="t-caption min-w-[240px] flex-1 list-disc space-y-1 pl-4">
                                    <li>
                                      <b>这部分没看懂</b>：只管<b>这次命中的这几页</b>。不改你的风格配置；
                                      以后再讲到这几页时，换一个切入点重讲。
                                    </li>
                                    <li>
                                      <b>讲太深</b>（在
                                      <a href="/settings/style" className="link">风格设置</a>
                                      里）：<b>以后所有讲解</b>都往浅讲一格，改的是配置。
                                    </li>
                                  </ul>
                                </div>
                                {t.mark?.status === "failed" && (
                                  <p role="alert" data-field="mark-failure" className="notice notice--danger mono mt-3 text-[12px]">
                                    {t.mark.message}
                                  </p>
                                )}
                              </div>
                            )}
                          </>
                        )}

                        {/* ---------------------------------------------- 出处 */}
                        {tab === "sources" && (
                          <div data-field="slices">
                            {t.answer.slices.length === 0 ? (
                              <p className="t-meta">这一轮没有检索到课件片段。</p>
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
