import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AppNav } from "@/app/components/app-nav";
import { Diagnostics } from "@/app/components/diagnostics";
import { RowStateNotice } from "@/app/components/row-state";
import { StatusDot } from "@/app/components/status-dot";
import { CourseUpload } from "@/app/courses/upload-client";
import { ExplainClient } from "@/app/explain/explain-client";
import { API_BASE_URL } from "@/lib/env";
import { newSessionId } from "@/lib/style/events";
import { configOf, loadStyleRow } from "@/lib/style/session";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "课程" };

type Document = {
  id: string;
  /** 列名是 `filename`（`20260911100000_f2_corpus.sql`），不是 `source_file`——
   *  后者是检索侧 `ContextSlice` 的字段名。2026-09-19 写错过一次：
   *  PostgREST 报 42703，而页面把失败渲染成了「还没有课件」。 */
  filename: string;
  page_count: number;
  verdict: string;
  created_at: string;
};

/**
 * 一门课 = 一个项目。**打开它就能提问**，这一页是产品的主界面。
 *
 * ## 课程与课件从 Supabase 直读，不经产品服务端
 *
 * `courses` / `documents` 都是 `owner = auth.uid()` 的 RLS（`20260911100000_f2_corpus.sql`），
 * 服务端组件拿用户自己的会话读自己的行即可。多绕一跳产品服务端只会把
 * 「课件列表」也绑在那个免费层服务的冷启动上（F74），而它与讲解无关。
 *
 * 讲解与上传仍然直连产品服务端（浏览器 → Render）：那两件事本来就在那边，
 * 且上传要绕开 Vercel 4.5MB 的请求体上限。
 *
 * ## 课程不存在时 404，不显示空页
 *
 * 读不到这一行有两种可能：id 不对，或它是别人的课（RLS 挡住）。两种都不该渲染一个
 * 「这门课还没有课件」的正常页面——那会让越权访问看起来像正常的空状态。
 */
export default async function CoursePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await loadStyleRow();

  if (result.state !== "ok") {
    return (
      <main data-state={result.state} className="shell">
        <AppNav email={result.email} />
        <div className="mt-12">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  const supabase = await createClient();
  const { data: course } = await supabase
    .from("courses")
    .select("id, name, created_at")
    .eq("id", id)
    .maybeSingle();
  if (!course) notFound();

  const { data: docs, error: docsError } = await supabase
    .from("documents")
    .select("id, filename, page_count, verdict, created_at")
    .eq("course_id", id)
    .order("created_at", { ascending: false });
  // **读失败与「真的没有课件」必须分开**：把前者渲染成后者，用户看到的是一个
  // 正常的空状态，而实际是查询报错（2026-09-19 就这么错过一次，列名写错了）。
  const documents = (docs ?? []) as Document[];

  return (
    <main data-state={API_BASE_URL ? "ok" : "no-api-base"} data-course-id={course.id} className="shell">
      <AppNav email={result.email} />

      <header className="mt-10 mb-8 sm:mt-14">
        <p className="t-eyebrow enter mb-3">
          <Link href="/" className="link">
            我的课程
          </Link>
          {" · "}
          {documents.length > 0 ? `${documents.length} 份课件` : "还没有课件"}
        </p>
        <h1 className="t-display enter-wipe" data-field="course-name">
          {course.name}
        </h1>
      </header>

      {docsError && (
        <p role="alert" className="notice notice--danger mb-6" data-field="documents-error">
          读课件列表失败：{docsError.message}
          {docsError.code ? `（${docsError.code}）` : ""}。下面的「还没有课件」不可信。
        </p>
      )}

      {!API_BASE_URL ? (
        <p role="alert" data-field="no-api-base" className="notice notice--warn">
          <b>还没配 <code>NEXT_PUBLIC_API_BASE_URL</code>。</b>
          提问与上传要用产品服务端（<code>api/</code>，部署在 Render），这一页在浏览器里直连它。
          配好之后刷新即可 —— 课程与课件列表不受影响，它们直连 Supabase。
        </p>
      ) : (
        <div className="flex flex-col gap-10">
          {/* 课件少的时候先让人看到「传课件」；有了课件，提问才是主角。 */}
          {documents.length === 0 ? (
            <>
              <section className="card p-2" data-field="empty-course">
                <div className="px-3 pt-3 pb-4">
                  <p className="t-eyebrow"># 先传一份课件</p>
                  <h2 className="t-title mt-1">这门课还没有材料</h2>
                  <p className="t-meta mt-1">
                    讲解只用你上传的材料，不靠通用知识补全。传一份 PDF 讲义，就可以开始提问。
                  </p>
                </div>
              </section>
              <CourseUpload
                apiBase={API_BASE_URL}
                userId={result.userId}
                arm={result.row.f1_arm}
                sessionId={newSessionId()}
                lockedCourse={{ id: course.id, name: course.name }}
              />
            </>
          ) : (
            <>
              <ExplainClient
                apiBase={API_BASE_URL}
                sessionId={newSessionId()}
                detailLevel={configOf(result.row).detail_level}
                lockedCourse={{ id: course.id, name: course.name }}
              />

              <section>
                <h2 className="t-title mb-3">这门课的课件</h2>
                <ul className="flex flex-col gap-2" data-field="documents">
                  {documents.map((d) => (
                    <li key={d.id} className="row">
                      <StatusDot kind={d.verdict === "unusable" ? "warn" : "ok"} />
                      <div className="min-w-0 flex-1">
                        <p className="row__title truncate">{d.filename}</p>
                        <p className="row__sub">
                          {d.page_count} 页 · 传于 {new Date(d.created_at).toLocaleDateString("zh-CN")}
                          {d.verdict === "unusable" ? " · 整份判为不可用（可能是扫描件）" : ""}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>

                <details className="diagnostics mt-6">
                  <summary>再传一份课件</summary>
                  <div className="mt-4">
                    <CourseUpload
                      apiBase={API_BASE_URL}
                      userId={result.userId}
                      arm={result.row.f1_arm}
                      sessionId={newSessionId()}
                      lockedCourse={{ id: course.id, name: course.name }}
                    />
                  </div>
                </details>
              </section>
            </>
          )}
        </div>
      )}

      <Diagnostics
        className="mt-10"
        items={[
          ["course_id", course.id],
          ["user_id", result.userId],
          ["课件份数", String(documents.length)],
        ]}
      />
    </main>
  );
}
