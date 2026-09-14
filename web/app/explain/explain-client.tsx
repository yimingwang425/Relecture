"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { createClient } from "@/lib/supabase/client";
import {
  answerFromResponse,
  answeredCount,
  hasPending,
  isFromRequestedCourse,
  markButtonDisabled,
  markUnavailable,
  nextTurnIndex,
  submitMark,
  type Turn,
} from "@/lib/explain/turns";

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
 */

type Course = { id: string; name: string; created_at: string };

const DETAIL_COPY: Record<string, string> = {
  // 与设置页同一条纪律：**用文字描述档位，不写区间数字**（口径 F13）。
  P0: "偏详细",
  P2: "适中",
  P3: "偏精简",
};

export function ExplainClient({
  apiBase,
  sessionId,
  detailLevel,
}: {
  apiBase: string;
  sessionId: string;
  detailLevel: string;
}) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [buildCommit, setBuildCommit] = useState<string | null>(null);
  /** 当前这一轮等了多少秒。**不是阈值判断**，只是让等待可见。 */
  const [elapsed, setElapsed] = useState(0);

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
        setCourseId((prev) => prev || rows[0]?.id || "");
      } catch (e) {
        // fetch 被网络层拒掉时没有状态码 —— **CORS 被挡就长这样**。
        // 点名说出这个可能，否则它读起来像「网络不好」。
        setListError(
          `连不上产品服务端（${apiBase}）。可能是它还没部署，` +
            `或者它的 CORS_ALLOW_ORIGINS 里没有本站来源。原始错误：${String(e)}`,
        );
      }
    })();
  }, [apiBase, token]);

  // 等待计时器。只在有一轮在飞时走。
  useEffect(() => {
    if (!pending) return setElapsed(0);
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [pending]);

  const inputRef = useRef<HTMLTextAreaElement>(null);

  async function ask() {
    const q = question.trim();
    // **只允许一轮在飞**（见组件说明）。
    if (!q || !courseId || pending) return;

    // `turn_index` 由账本算，**不是「发了几次请求」**：失败不计入
    // （用户一个字都没看到，那不是一轮讲解），重生成也不计入（它在服务端一次
    // 请求里面）。口径与理由在 lib/explain/turns.ts。
    const turnIndex = nextTurnIndex(turns);
    setTurns((prev) => [...prev, { turnIndex, question: q, courseId, state: "pending" }]);
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

  return (
    <section className="mt-8 flex flex-col gap-6" data-testid="explain">
      <p className="font-mono text-[11px] text-neutral-400" data-field="api-build">
        服务端 {apiBase}
        {buildCommit ? ` · 构建 ${buildCommit.slice(0, 7)}` : " · 构建标识读不到"}
        {" · session "}
        {sessionId.slice(0, 8)}
        {" · 已 "}
        <span data-field="answered-count">{answeredCount(turns)}</span>
        {" 轮"}
      </p>

      {listError && (
        <p role="alert" data-field="list-error"
           className="rounded border border-red-200 bg-red-50 p-3 text-sm leading-relaxed text-red-700">
          {listError}
        </p>
      )}

      {/* ---------------- 提问 ---------------- */}
      <div className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between gap-3">
          <h1 className="text-lg font-medium">问我关于课件的问题</h1>
          <span className="text-xs text-neutral-500" data-field="detail-level">
            当前讲解详略：<b>{DETAIL_COPY[detailLevel] ?? detailLevel}</b>
            {" · "}
            <a href="/settings/style" className="underline underline-offset-2">
              去调整
            </a>
          </span>
        </div>

        {courses === null ? (
          <p className="text-sm text-neutral-500">读取中…</p>
        ) : courses.length === 0 ? (
          <p className="text-sm text-neutral-500" data-field="no-courses">
            还没有课件。先去{" "}
            <a href="/courses" className="underline underline-offset-2">
              上传课件
            </a>{" "}
            再回来提问 —— 讲解只用你上传的材料，不靠通用知识补全。
          </p>
        ) : (
          <>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-neutral-500">问哪门课</span>
              <select
                data-field="course-select"
                value={courseId}
                onChange={(e) => setCourseId(e.target.value)}
                disabled={pending}
                className="rounded border border-neutral-300 bg-white px-3 py-2 disabled:opacity-50"
              >
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>

            <textarea
              ref={inputRef}
              data-field="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              disabled={pending}
              rows={3}
              maxLength={500}
              placeholder="例如：这门课讲的能源问题主要有哪些？"
              className="rounded border border-neutral-300 px-3 py-2 text-sm disabled:bg-neutral-50"
            />
            <div className="flex items-center gap-3">
              <button
                data-field="ask"
                onClick={() => void ask()}
                disabled={!question.trim() || !courseId || pending}
                className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-40"
              >
                {pending ? "正在讲解…" : "提问"}
              </button>
              {pending && (
                // 走着的计时器。**没有它，一个 40 秒的等待与一个卡死的请求
                // 在页面上长得一模一样。** 它不是阈值判断，只是让等待可见。
                <span className="text-xs text-neutral-500" data-field="elapsed">
                  已等 {elapsed} 秒 —— 生成加最多两次重生成，通常几十秒。
                  <b>别关页面</b>（不流式，关了这一轮就没了）。
                </span>
              )}
            </div>
          </>
        )}
      </div>

      {/* ---------------- 轮次 ---------------- */}
      {turns.length > 0 && (
        <ol className="flex flex-col gap-4" data-field="turns">
          {[...turns].reverse().map((t, i) => (
            <li
              key={turns.length - 1 - i}
              data-field="turn"
              data-state={t.state}
              data-turn-index={t.turnIndex}
              data-backend={t.answer?.backend ?? ""}
              data-round-id={t.answer?.roundId ?? ""}
              data-confusion-injected={String(t.answer?.confusion?.injected ?? "")}
              className="rounded border border-neutral-200 bg-white p-4 text-sm"
            >
              <p className="font-medium text-neutral-800">
                <span className="mr-2 font-mono text-xs text-neutral-400">
                  第 {t.turnIndex} 轮
                </span>
                {t.question}
              </p>

              {t.state === "pending" && (
                <p className="mt-2 text-neutral-500">正在解析你的课件片段并生成讲解…</p>
              )}

              {t.state === "failed" && t.failure && (
                <div className="mt-2" data-field="turn-failure">
                  <p className="text-red-700">
                    这一轮没成{t.failure.status ? `（HTTP ${t.failure.status}）` : ""}。
                  </p>
                  <p className="mt-0.5 font-mono text-[11px] leading-relaxed text-neutral-500">
                    {t.failure.message}
                  </p>
                  {/* 失败不占轮次 —— 说清楚，否则用户会以为自己「用掉」了一轮。 */}
                  <p className="mt-1 text-[11px] text-neutral-400">
                    这一次没有产生回答，所以它不计入会话轮次。
                  </p>
                  {t.failure.retryable && (
                    <button
                      data-field="turn-retry"
                      onClick={() => {
                        setQuestion(t.question);
                        inputRef.current?.focus();
                      }}
                      className="mt-2 rounded border border-neutral-300 px-3 py-1 text-xs"
                    >
                      重新填回这个问题
                    </button>
                  )}
                </div>
              )}

              {t.state === "answered" && t.answer && (
                <div className="mt-3">
                  {/* ⚠️ 用合成课件答的**必须被看见**：那种回答看起来完全正常，
                      而它与用户的课件无关（口径 F52 那次真实失败）。 */}
                  {!isFromRequestedCourse(t.answer) && (
                    <p
                      role="alert"
                      data-field="wrong-backend"
                      className="mb-3 rounded border border-red-300 bg-red-50 p-3 text-xs leading-relaxed text-red-800"
                    >
                      ⚠️ <b>这一段不是用你的课件答的。</b>
                      服务端报的检索后端是 <code>{t.answer.backend}</code>，
                      而不是 <code>supabase</code> —— 说明它没有用这门课的材料
                      （最可能是线上构建比这一页旧）。
                      <b>别把这段内容当成课件里的说法。</b>
                    </p>
                  )}

                  <div
                    data-field="answer"
                    className="whitespace-pre-wrap leading-relaxed text-neutral-900"
                  >
                    {t.answer.text}
                  </div>

                  {/* 出处：点名到页。PRD 要求讲解能溯源到页号。 */}
                  {t.answer.sources.length > 0 && (
                    <p className="mt-3 text-xs text-neutral-500" data-field="sources">
                      出处：
                      {t.answer.sources.map((s, j) => (
                        <span key={s.sourceFile}>
                          {j > 0 && "；"}
                          <span className="font-mono">{s.sourceFile}</span> 第{" "}
                          {s.pages.join("、")} 页
                        </span>
                      ))}
                    </p>
                  )}

                  <p className="mt-1 text-[11px] text-neutral-400" data-field="turn-meta">
                    平均段落 {t.answer.meanParagraphChars.toFixed(0)} 字 · 档位{" "}
                    {t.answer.detailLevel}
                    {t.answer.regenerations > 0 && (
                      <>
                        {" · "}
                        {/* 重生成**不计入轮次**（§13.0），显示它是为了让
                            「审查链路在工作」这件事可见。 */}
                        重生成 {t.answer.regenerations} 次（审查判不达标后改写，不占轮次）
                      </>
                    )}
                    {!t.answer.accepted && (
                      <>
                        {" · "}
                        <span className="text-amber-700">
                          重生成触顶仍未达标，按原样输出
                        </span>
                      </>
                    )}
                    {t.answer.confusion?.injected && (
                      <>
                        {" · "}
                        <span data-field="confusion-injected">
                          你之前标过这几页没看懂，这次换了个切入点讲
                        </span>
                      </>
                    )}
                  </p>

                  {/* ---- F4 · 这部分没看懂 ----
                      空检索轮不渲染（POST /marks 对空 slice_ids 回 422）；
                      服务端旧到没有 round_id 时**说出来**，不与空检索一样静默。 */}
                  {markUnavailable(t.answer) === "old-server" && (
                    <p className="mt-3 text-[11px] text-amber-700" data-field="mark-old-server">
                      服务端没有返回 round_id（构建比这一页旧），这一轮暂时标记不了。
                    </p>
                  )}
                  {markUnavailable(t.answer) === null && (
                    <div className="mt-3 border-t border-neutral-100 pt-3" data-field="mark">
                      <button
                        data-field="mark-confused"
                        data-mark-state={t.mark?.status ?? "idle"}
                        onClick={() => void markConfused(turns.length - 1 - i)}
                        disabled={markButtonDisabled(t.mark)}
                        className="rounded border border-neutral-300 px-3 py-1 text-xs disabled:opacity-50"
                      >
                        {t.mark?.status === "marked"
                          ? "已记下"
                          : t.mark?.status === "sending"
                            ? "记下中…"
                            : "这部分没看懂"}
                      </button>
                      {t.mark?.status === "failed" && (
                        <p role="alert" data-field="mark-failure"
                           className="mt-1 font-mono text-[11px] text-red-700">
                          {t.mark.message}
                        </p>
                      )}
                      {/* 口径 F56：两个按钮的说明**放在一起**，把「这次这里」与「以后都」说开。
                          用户点错不会有任何指标变红 —— 配置会被一路改深，而没人知道。 */}
                      <ul data-field="mark-scope"
                          className="mt-2 list-disc space-y-0.5 pl-5 text-[11px] leading-relaxed text-neutral-500">
                        <li>
                          <b>这部分没看懂</b>：只管<b>这次命中的这几页</b>。不改你的风格配置；
                          以后再讲到这几页时，换一个切入点重讲。
                        </li>
                        <li>
                          <b>讲太深</b>（在
                          <a href="/settings/style" className="underline underline-offset-2">风格设置</a>
                          里）：<b>以后所有讲解</b>都往浅讲一格，改的是配置。
                        </li>
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
