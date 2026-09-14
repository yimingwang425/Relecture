"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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

export function CourseUpload({
  apiBase,
  userId,
  arm,
  sessionId,
}: {
  apiBase: string;
  userId: string;
  arm: "pairwise" | "text";
  sessionId: string;
}) {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState<string>("");
  const [newName, setNewName] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  /** 取课程列表 / 建课这一层的错误。**与上传失败分开显示**：处置完全不同。 */
  const [listError, setListError] = useState<string | null>(null);
  /** 线上那个服务在跑哪个构建（口径 F17）。跨源要靠 expose-headers 才读得到。 */
  const [buildCommit, setBuildCommit] = useState<string | null>(null);
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
      setCourseId((prev) => prev || rows[0]?.id || "");
    } catch (e) {
      // fetch 被网络层拒掉时没有状态码 —— **CORS 被挡就长这样**。
      // 点名说出这个可能，否则它读起来像「网络不好」。
      setListError(
        `连不上产品服务端（${apiBase}）。可能是它还没部署，` +
          `或者它的 CORS_ALLOW_ORIGINS 里没有本站来源。原始错误：${String(e)}`,
      );
    }
  }, [apiBase, token]);

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

  return (
    <section className="mt-8 flex flex-col gap-6" data-testid="course-upload">
      {/* ---------------- 构建标识（口径 F17）---------------- */}
      <p className="font-mono text-[11px] text-neutral-400" data-field="api-build">
        服务端 {apiBase}
        {buildCommit ? ` · 构建 ${buildCommit.slice(0, 7)}` : " · 构建标识读不到"}
      </p>

      {listError && (
        <p role="alert" data-field="list-error"
           className="rounded border border-red-200 bg-red-50 p-3 text-sm leading-relaxed text-red-700">
          {listError}
        </p>
      )}

      {/* ---------------- 课程 ---------------- */}
      <div className="flex flex-col gap-3">
        <h1 className="text-lg font-medium">我的课程</h1>

        {courses === null ? (
          <p className="text-sm text-neutral-500">读取中…</p>
        ) : courses.length === 0 ? (
          <p className="text-sm text-neutral-500" data-field="no-courses">
            还没有课程。先建一门，再往里上传课件。
          </p>
        ) : (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-neutral-500">上传到哪门课</span>
            <select
              data-field="course-select"
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              className="rounded border border-neutral-300 bg-white px-3 py-2"
            >
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="flex gap-2">
          <input
            data-field="new-course-name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="新课程名，例如 22.081J 可持续能源"
            className="flex-1 rounded border border-neutral-300 px-3 py-2 text-sm"
          />
          <button
            data-field="create-course"
            onClick={() => void createCourse()}
            disabled={!newName.trim()}
            className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-40"
          >
            建课
          </button>
        </div>
      </div>

      {/* ---------------- 上传 ---------------- */}
      <div className="flex flex-col gap-2">
        <h2 className="text-base font-medium">上传课件</h2>
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
          className="text-sm"
        />
        <p className="text-xs leading-relaxed text-neutral-500">
          只能读 .pdf。<b>PPTX 还不能读 —— 那是我们还没做，不是不支持</b>
          （schema 已经定了）。一份课件要先解析、切片，再逐片算向量，
          几十页的讲义通常要几十秒。
        </p>
      </div>

      {/* ---------------- 每一份的状态 ---------------- */}
      {items.length > 0 && (
        <ul className="flex flex-col gap-2" data-field="upload-items">
          {items.map((it) => (
            <li
              key={it.key}
              data-field="upload-item"
              data-state={it.state}
              data-failure-kind={it.failure?.kind ?? ""}
              className="rounded border border-neutral-200 bg-white p-3 text-sm"
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate font-medium">{it.file.name}</span>
                <span className="shrink-0 font-mono text-xs text-neutral-400">
                  {Math.round(it.file.size / 1024)} KB
                </span>
              </div>

              {it.state === "queued" && <p className="mt-1 text-neutral-500">排队中…</p>}
              {it.state === "uploading" && (
                <p className="mt-1 text-neutral-500">
                  正在解析、切片、算向量…（几十秒，别关页面）
                </p>
              )}

              {it.state === "done" && it.result && (
                <p className="mt-1 text-emerald-700" data-field="upload-ok">
                  已入库：{it.result.slice_count} 个切片 ·{" "}
                  {it.result.embedded_count} 条向量
                  {it.result.skipped_pages > 0 && (
                    <>
                      {" · "}
                      <span className="text-amber-700">
                        {it.result.skipped_pages} 页读不出文字
                      </span>
                    </>
                  )}
                  {it.result.verdict === "unusable" && (
                    <>
                      {" · "}
                      <span className="text-amber-700">整份判为不可用（可能是扫描件）</span>
                    </>
                  )}
                </p>
              )}

              {it.state === "failed" && it.failure && (
                <div className="mt-1" data-field="upload-failure">
                  {/* 分类文案：说清发生了什么 + 该做什么。**不写「请重试」这类
                      无信息量的提示**（PRD §10）。 */}
                  <p className="text-red-700">{copyFor(it.failure.kind).title}</p>
                  <p className="mt-0.5 text-neutral-600">{copyFor(it.failure.kind).action}</p>
                  {it.failure.detail && (
                    // 服务端原文只给这一次请求的用户看。**它不进事件**
                    // （解析器谈论的是用户上传的文件内容，PRD §12.2 / 口径 F32）。
                    <p className="mt-1 font-mono text-[11px] leading-relaxed text-neutral-400">
                      {it.failure.detail}
                    </p>
                  )}
                  {!it.failure.matched && (
                    <p className="mt-1 text-[11px] text-amber-700" data-field="unknown-kind">
                      ⚠️ 这个失败分类前端不认识（<code>{it.failure.kind}</code>）——
                      后端可能加了一类而前端没跟上。
                    </p>
                  )}
                  {it.failure.retryable && (
                    <button
                      data-field="retry"
                      onClick={() => void uploadOne({ ...it, state: "queued" })}
                      className="mt-2 rounded border border-neutral-300 px-3 py-1 text-xs"
                    >
                      重试
                    </button>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="text-[11px] text-neutral-400">
        user_id <span className="font-mono">{userId}</span> · arm{" "}
        <span className="font-mono">{arm}</span> · session{" "}
        <span className="font-mono">{sessionId.slice(0, 8)}</span>
      </p>
    </section>
  );
}
