"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Diagnostics } from "@/app/components/diagnostics";
import { PageHeader } from "@/app/components/page-header";
import { StatusDot, type DotKind } from "@/app/components/status-dot";
import type { Arm } from "@/lib/style/axes";
import { createClient } from "@/lib/supabase/client";
import { copyFor, kindFromStatus, type FailureKind } from "@/lib/upload/failures";

/**
 * 课程列表 + 课件上传（阶段 6 第一批）。
 *
 * ## 这一页**不发埋点事件**，是刻意的（口径 F51）
 *
 * `deck_uploaded` / `deck_upload_failed` 由**服务端**发：它们的字段全部产生在
 * 那一次摄入里（页数、切片数、嵌入数、处理耗时、失败分类），**浏览器一个都不知道**。
 * 让前端发就得先把它们序列化过去再发回来，多一份可以漂的副本，
 * 而且前端能改这些数字。
 *
 * 本组件唯一的埋点责任是**把 `session_id` 带上**（查询参数）。
 * 没带的话服务端一条事件都不发并在日志里点名 —— 它不现编一个，
 * 因为编了会把「平均会话轮次」这个北极星指标静静改成假数。
 *
 * ## 为什么直连产品服务端，而不走 Next 的 route handler
 *
 * **Vercel 的 serverless 请求体上限是 4.5MB**，而上传上限 50MB
 * （测试集里最大的一份正好 4.5MB）。代理一跳会把大课件挡在 Vercel 那一层，
 * 而那个失败与「文件太大」长得一样，却发生在完全不同的地方。
 *
 * 代价是要 CORS。那个服务的 `CORS_ALLOW_ORIGINS` **不配就是空清单**
 * （浏览器一律被挡），不是默认放开 —— 于是配错的表现是这一页当场报错，
 * 一眼看得见。
 *
 * ## 失败**按分类显示**，不压成「上传失败」
 *
 * 八类各有自己的文案与「要不要给重试按钮」，见 `lib/upload/failures.ts`。
 * 压成一句的具体代价写在那个文件里（最要紧的两条：把「还没做 PPTX」说成
 * 「不支持」会让用户去手动转格式；把「已经有这份文件了」说成「失败」
 * 会让他再传一次）。
 *
 * ## 状态挂在 DOM 上（`data-*`），不只在文案里
 *
 * 人工验收要能当场看见，回归工具要能不按中文文案定位（改文案不该让断言
 * 静默变绿）。同阶段 3 把事件队列的 `pending` / `dropped` 暴露在 DOM 上那条。
 *
 * ## 布局（2026-09-15 UI 重做）
 *
 * 左卡选课 / 建课，右卡拖放上传 + 逐份状态行（圆点字形区分排队 / 进行 / 完成 / 失败）。
 * 构建号、`user_id`、臂、`session` 收进页面底部的诊断折叠区。
 */

type Course = { id: string; name: string; created_at: string };

type Item = {
  /** 本次会话内的稳定 key。文件名可能重复，不能当 key。 */
  key: string;
  file: File;
  state: "queued" | "uploading" | "done" | "failed";
  /** 成功时的落库结果。 */
  result?: {
    document_id: string;
    slice_count: number;
    embedded_count: number;
    skipped_pages: number;
    /** **null = 未检查**（口径 F69）：用户上传不做受限页排除。不要把 null 渲染成 0。 */
    restricted_pages_excluded: number | null;
    verdict: string;
  };
  /** 失败时的分类与服务端原文。 */
  failure?: { kind: FailureKind; detail: string; matched: boolean; retryable: boolean };
};

function dotFor(it: Item): DotKind {
  if (it.state === "queued") return "idle";
  if (it.state === "uploading") return "busy";
  if (it.state === "failed") return "bad";
  return it.result?.verdict === "unusable" || (it.result?.skipped_pages ?? 0) > 0 ? "warn" : "ok";
}

export function CourseUpload({
  apiBase,
  userId,
  arm,
  sessionId,
  lockedCourse,
}: {
  apiBase: string;
  userId: string;
  arm: Arm;
  sessionId: string;
  /**
   * 项目页（`/courses/[id]`）用：只往这门课传，不渲染课程列表与建课表单，
   * 页头也交给项目页自己出。仍然要拉一次 `/courses`——构建标识（口径 F17）
   * 只有那个响应头带得回来。
   */
  lockedCourse?: { id: string; name: string };
}) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState<string>(lockedCourse?.id ?? "");
  const [newName, setNewName] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  /** 取课程列表 / 建课这一层的错误。**与上传失败分开显示**：处置完全不同。 */
  const [listError, setListError] = useState<string | null>(null);
  /** 线上那个服务在跑哪个构建（口径 F17）。跨源要靠 expose-headers 才读得到。 */
  const [buildCommit, setBuildCommit] = useState<string | null>(null);
  /** 拖着文件悬在上传区上方。只影响样式。 */
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  /** 拿用户自己的 JWT。RLS 由数据库执行，这一跳只是把它转过去。 */
  const token = useCallback(async () => {
    const { data } = await createClient().auth.getSession();
    return data.session?.access_token ?? null;
  }, []);

  const loadCourses = useCallback(async () => {
    setListError(null);
    const jwt = await token();
    if (!jwt) {
      setListError("登录状态已过期，请重新登录。");
      return;
    }
    try {
      const r = await fetch(`${apiBase}/courses`, {
        headers: { Authorization: `Bearer ${jwt}` },
      });
      // 跨源时只有被 expose 的头读得到（服务端配了 `x-build-commit`）。
      setBuildCommit(r.headers.get("x-build-commit"));
      if (!r.ok) {
        setListError(`读课程列表失败（HTTP ${r.status}）。${await r.text()}`);
        return;
      }
      const rows = (await r.json()) as Course[];
      setCourses(rows);
      if (!lockedCourse) setCourseId((prev) => prev || rows[0]?.id || "");
    } catch (e) {
      // fetch 被网络层拒掉时没有状态码 —— **CORS 被挡就长这样**。
      // 点名说出这个可能，否则它读起来像「网络不好」。
      setListError(
        `连不上产品服务端（${apiBase}）。可能是它还没部署，` +
          `或者它的 CORS_ALLOW_ORIGINS 里没有本站来源。原始错误：${String(e)}`,
      );
    }
  }, [apiBase, token, lockedCourse]);

  useEffect(() => {
    void loadCourses();
  }, [loadCourses]);

  async function createCourse() {
    const name = newName.trim();
    if (!name) return;
    const jwt = await token();
    if (!jwt) return setListError("登录状态已过期，请重新登录。");
    const r = await fetch(`${apiBase}/courses`, {
      method: "POST",
      headers: { Authorization: `Bearer ${jwt}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!r.ok) {
      setListError(`建课失败（HTTP ${r.status}）。${await r.text()}`);
      return;
    }
    const row = (await r.json()) as Course;
    setNewName("");
    setCourses((prev) => [row, ...(prev ?? [])]);
    setCourseId(row.id);
  }

  /** 上传一份。**一次一份**：摄入的事务边界就是一份文档（`ingest.py`）。 */
  async function uploadOne(item: Item) {
    setItems((prev) =>
      prev.map((x) => (x.key === item.key ? { ...x, state: "uploading" } : x)),
    );
    const jwt = await token();
    if (!jwt) {
      setItems((prev) =>
        prev.map((x) =>
          x.key === item.key
            ? {
                ...x,
                state: "failed",
                failure: { kind: "unknown", detail: "登录状态已过期，请重新登录。", matched: true, retryable: true },
              }
            : x,
        ),
      );
      return;
    }

    const body = new FormData();
    body.append("file", item.file);

    try {
      const r = await fetch(
        // `session_id` 是这一页唯一的埋点责任（见组件说明）。
        `${apiBase}/courses/${courseId}/documents?session_id=${encodeURIComponent(sessionId)}`,
        { method: "POST", headers: { Authorization: `Bearer ${jwt}` }, body },
      );
      setBuildCommit((prev) => r.headers.get("x-build-commit") ?? prev);

      if (r.ok) {
        const result = await r.json();
        setItems((prev) =>
          prev.map((x) => (x.key === item.key ? { ...x, state: "done", result } : x)),
        );
        return;
      }

      // ---- 失败：**先读响应体里的 `failure_kind`**，状态码只是兜底。
      // 状态码会丢信息（415 与 501 都是「格式家族」，能分开它们正是因为
      // 服务端一个类一个码）。
      let kind: string | null = null;
      let detail = "";
      try {
        const payload = (await r.json()) as { detail?: string; failure_kind?: string };
        detail = payload.detail ?? "";
        kind = payload.failure_kind ?? null;
      } catch {
        detail = await r.text().catch(() => "");
      }
      const resolved = kind ?? kindFromStatus(r.status);
      const copy = copyFor(resolved);
      setItems((prev) =>
        prev.map((x) =>
          x.key === item.key
            ? {
                ...x,
                state: "failed",
                failure: {
                  kind: resolved as FailureKind,
                  detail,
                  matched: copy.matched,
                  retryable: copy.retryable,
                },
              }
            : x,
        ),
      );
    } catch (e) {
      setItems((prev) =>
        prev.map((x) =>
          x.key === item.key
            ? {
                ...x,
                state: "failed",
                failure: {
                  kind: "db_error",
                  detail:
                    `连不上产品服务端。可能是它没部署，或 CORS_ALLOW_ORIGINS 里没有本站来源。` +
                    `原始错误：${String(e)}`,
                  matched: true,
                  retryable: true,
                },
              }
            : x,
        ),
      );
    }
  }

  function enqueue(files: FileList | null) {
    if (!files || !courseId) return;
    const added: Item[] = Array.from(files).map((file, i) => ({
      // 文件名可能重复，所以 key 带一个递增序号 + 时间戳。
      key: `${Date.now()}-${i}-${file.name}`,
      file,
      state: "queued",
    }));
    setItems((prev) => [...prev, ...added]);
    // 串行传。并行会让几份 PDF 同时进 OpenAI 嵌入，而那一步是唯一花钱的一步；
    // 更要紧的是**同一门课的两份同名文件并行时会同时撞唯一索引**，
    // 而那时「先查后提示」查不到任何一份（两边都还没插进去）。
    void (async () => {
      for (const it of added) await uploadOne(it);
    })();
  }

  const busy = items.some((x) => x.state === "uploading");
  const canUpload = !!courseId && !busy;
  const courseName = courses?.find((c) => c.id === courseId)?.name;

  return (
    <section className="flex flex-col" data-testid="course-upload">
      {!lockedCourse && (
        <PageHeader
          eyebrow="课件"
          title="批量上传课件"
          lead="要往哪门课传就选哪门。平时在课程里传更顺手——首页点开一门课，里面就有上传入口。"
        />
      )}

      {listError && (
        <p role="alert" data-field="list-error" className="notice notice--danger mb-6">
          {listError}
        </p>
      )}

      <div
        className={
          lockedCourse
            ? "grid items-start gap-6"
            : "grid items-start gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]"
        }
      >
        {/* ================================================================ 课程 */}
        {!lockedCourse && (
        <div className="card enter p-2">
          <div className="px-3 pt-3 pb-4">
            <p className="t-eyebrow"># 课程</p>
            <h2 className="t-title mt-1">上传到哪门课</h2>
          </div>

          <div className="panel flex flex-col gap-4 p-3">
            {courses === null ? (
              !listError && (
                <div className="flex flex-col gap-2" aria-label="读取中">
                  <div className="skeleton h-12" />
                  <div className="skeleton h-12" />
                </div>
              )
            ) : courses.length === 0 ? (
              <p className="t-meta px-1" data-field="no-courses">
                还没有课程。先建一门，再往里上传课件。
              </p>
            ) : (
              <div
                role="radiogroup"
                aria-label="上传到哪门课"
                data-field="course-select"
                className="flex max-h-[360px] flex-col gap-2 overflow-y-auto p-0.5"
              >
                {courses.map((c) => {
                  const active = c.id === courseId;
                  return (
                    <label
                      key={c.id}
                      data-selected={active}
                      data-course-id={c.id}
                      className="choice flex cursor-pointer items-start gap-3 px-3 py-2.5"
                    >
                      <input
                        type="radio"
                        name="course"
                        className="sr-only"
                        checked={active}
                        onChange={() => setCourseId(c.id)}
                      />
                      <StatusDot kind={active ? "ok" : "idle"} />
                      <span className="min-w-0 flex-1">
                        <span className="row__title block truncate">{c.name}</span>
                        <span className="row__sub block">
                          建于 {new Date(c.created_at).toLocaleDateString("zh-CN")}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}

            <form
              className="flex gap-2 border-t border-[var(--c-border-row)] pt-4"
              onSubmit={(e) => {
                e.preventDefault();
                void createCourse();
              }}
            >
              <input
                data-field="new-course-name"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="新课程名，例如 22.081J 可持续能源"
                aria-label="新课程名"
                className="input flex-1"
              />
              <button
                type="submit"
                data-field="create-course"
                disabled={!newName.trim()}
                className="btn btn--primary shrink-0"
              >
                建课
              </button>
            </form>
          </div>
        </div>
        )}

        {/* ================================================================ 上传 */}
        <div className="card enter-late p-2">
          <div className="flex flex-wrap items-end justify-between gap-3 px-3 pt-3 pb-4">
            <div>
              <p className="t-eyebrow"># 上传</p>
              <h2 className="t-title mt-1">上传课件</h2>
            </div>
            {!lockedCourse && courseName && (
              <span className="chip chip--accent max-w-full truncate">传到：{courseName}</span>
            )}
          </div>

          <div className="panel flex flex-col gap-4 p-4">
            <label
              data-dragging={dragging}
              onDragOver={(e) => {
                e.preventDefault();
                if (canUpload) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (canUpload) enqueue(e.dataTransfer.files);
              }}
              className={`flex flex-col items-center justify-center gap-2 rounded-[12px] border-[1.5px] border-dashed px-6 py-10 text-center transition-colors ${
                !canUpload
                  ? "cursor-not-allowed border-[var(--c-border-strong)] bg-[#fafafa] opacity-70"
                  : dragging
                    ? "cursor-copy border-[var(--c-accent)] bg-[var(--c-accent-wash)]"
                    : "cursor-pointer border-[var(--c-border-strong)] hover:border-[var(--c-accent)] hover:bg-[#fcfeff]"
              }`}
            >
              <input
                ref={fileInput}
                data-field="file-input"
                type="file"
                accept=".pdf"
                multiple
                disabled={!courseId || busy}
                onChange={(e) => {
                  enqueue(e.target.files);
                  // 清空，否则同一个文件连传两次不会触发 change。
                  if (fileInput.current) fileInput.current.value = "";
                }}
                className="sr-only"
              />
              <svg aria-hidden width="36" height="36" viewBox="0 0 36 36" fill="none">
                <rect x="7" y="4" width="22" height="28" rx="4" fill="#f2f2f2" stroke="#d8d8d8" />
                <path d="M18 23V13m0 0-4 4m4-4 4 4" stroke="var(--c-accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="t-ui">
                {!courseId ? "先在左边选一门课" : busy ? "正在处理上一份…" : "把 PDF 拖到这里，或点击选择"}
              </span>
              <span className="t-caption">可以一次选多份，会一份一份处理</span>
            </label>

            <p className="t-caption">
              只能读 .pdf。<b>PPTX 还不能读 —— 那是我们还没做，不是不支持</b>
              （schema 已经定了）。一份课件要先解析、切片，再逐片算向量，
              几十页的讲义通常要几十秒。
            </p>

            {/* ---------------- 每一份的状态 ---------------- */}
            {items.length > 0 && (
              <ul className="flex flex-col gap-2" data-field="upload-items">
                {items.map((it) => (
                  <li
                    key={it.key}
                    data-field="upload-item"
                    data-state={it.state}
                    data-failure-kind={it.failure?.kind ?? ""}
                    className="row"
                  >
                    <StatusDot kind={dotFor(it)} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="row__title truncate">{it.file.name}</span>
                        <span className="t-caption mono shrink-0">{Math.round(it.file.size / 1024)} KB</span>
                      </div>

                      {it.state === "queued" && <p className="row__sub">排队中…</p>}
                      {it.state === "uploading" && (
                        <p className="row__sub">正在解析、切片、算向量…（几十秒，别关页面）</p>
                      )}

                      {it.state === "done" && it.result && (
                        <p className="row__sub text-[var(--c-ok)]" data-field="upload-ok">
                          已入库：{it.result.slice_count} 个切片 · {it.result.embedded_count} 条向量
                          {it.result.skipped_pages > 0 && (
                            <>
                              {" · "}
                              <span className="text-[var(--c-warn)]">{it.result.skipped_pages} 页读不出文字</span>
                            </>
                          )}
                          {it.result.verdict === "unusable" && (
                            <>
                              {" · "}
                              <span className="text-[var(--c-warn)]">整份判为不可用（可能是扫描件）</span>
                            </>
                          )}
                        </p>
                      )}

                      {it.state === "failed" && it.failure && (
                        <div className="mt-1" data-field="upload-failure">
                          {/* 分类文案：说清发生了什么 + 该做什么。**不写「请重试」这类
                              无信息量的提示**（PRD §10）。 */}
                          <p className="text-[14px] font-[var(--fw-medium)] text-[var(--c-danger)]">
                            {copyFor(it.failure.kind).title}
                          </p>
                          <p className="row__sub">{copyFor(it.failure.kind).action}</p>
                          {it.failure.detail && (
                            // 服务端原文只给这一次请求的用户看。**它不进事件**
                            // （解析器谈论的是用户上传的文件内容，PRD §12.2 / 口径 F32）。
                            <p className="mono mt-1 text-[11px] leading-relaxed break-all text-[#9a9a9a]">
                              {it.failure.detail}
                            </p>
                          )}
                          {!it.failure.matched && (
                            <p className="mt-1 text-[11.5px] text-[var(--c-warn)]" data-field="unknown-kind">
                              ⚠️ 这个失败分类前端不认识（<code>{it.failure.kind}</code>）——
                              后端可能加了一类而前端没跟上。
                            </p>
                          )}
                          {it.failure.retryable && (
                            <button
                              data-field="retry"
                              onClick={() => void uploadOne({ ...it, state: "queued" })}
                              className="btn btn--ghost btn--sm mt-2"
                            >
                              重试
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <Diagnostics
        className="mt-10"
        items={[
          ["服务端", apiBase],
          ["构建", buildCommit ? buildCommit.slice(0, 7) : "读不到构建标识", "api-build"],
          ["user_id", userId],
          ["arm", arm],
          ["session", sessionId.slice(0, 8)],
        ]}
      />
    </section>
  );
}
