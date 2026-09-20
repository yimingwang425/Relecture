import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { RowStateNotice } from "@/app/components/row-state";
import { API_BASE_URL } from "@/lib/env";
import { newSessionId } from "@/lib/style/events";
import { configOf, loadStyleRow } from "@/lib/style/session";
import { createClient } from "@/lib/supabase/server";

import { Workspace } from "./workspace-client";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "课程" };

/**
 * 一门课 = 一个项目。**打开它就在里面提问**，这一页是产品的主界面。
 *
 * 这一页自己只做三件事：读行、判坏状态、把数据交给工作区。交互全在
 * `workspace-client.tsx`（侧栏 + 聊天 + 课件弹窗）。
 *
 * ## 课程 / 课件 / 会话都从 Supabase 直读，不经产品服务端
 *
 * 它们都是 `owner = auth.uid()` 的 RLS，服务端组件拿用户自己的会话读自己的行即可。
 * 多绕一跳只会把这些列表也绑在那个免费层服务的冷启动上（F74），而它们与讲解无关。
 * 讲解与上传仍然直连产品服务端（浏览器 → Render）。
 *
 * ## 课程不存在时 404，不显示空页
 *
 * 读不到这一行有两种可能：id 不对，或它是别人的课（RLS 挡住）。两种都不该渲染
 * 一个「这门课还没有课件」的正常页面——那会让越权访问看起来像正常的空状态。
 *
 * ## ⚠️ 读失败与「真的没有」必须分开
 *
 * 2026-09-19 就这么错过一次：列名写错，PostgREST 报 42703 被吞掉，页面渲染成
 * 「这门课还没有课件」。所以这里把 `error` 显式判出来。
 */
export default async function CoursePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await loadStyleRow();

  if (result.state !== "ok") {
    return (
      <main data-state={result.state} className="shell">
        <div className="mt-12">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  const supabase = await createClient();
  const { data: course } = await supabase
    .from("courses")
    .select("id, name")
    .eq("id", id)
    .maybeSingle();
  if (!course) notFound();

  const [{ data: docs, error: docsError }, { data: convs, error: convsError }] = await Promise.all([
    supabase
      .from("documents")
      .select("id, filename, display_name, page_count, verdict")
      .eq("course_id", id)
      .order("created_at", { ascending: false }),
    supabase
      .from("conversations")
      .select("id, title, updated_at")
      .eq("course_id", id)
      .order("updated_at", { ascending: false }),
  ]);

  const readError = docsError ?? convsError;

  if (!API_BASE_URL) {
    return (
      <main data-state="no-api-base" data-course-id={course.id} className="shell">
        <p role="alert" data-field="no-api-base" className="notice notice--warn mt-12">
          <b>还没配 <code>NEXT_PUBLIC_API_BASE_URL</code>。</b>
          提问与上传要用产品服务端（<code>api/</code>，部署在 Render），这一页在浏览器里直连它。
          配好之后刷新即可 —— 课程与课件列表不受影响，它们直连 Supabase。
        </p>
      </main>
    );
  }

  return (
    <main data-state="ok" data-course-id={course.id}>
      {readError && (
        <p role="alert" className="notice notice--danger m-4" data-field="documents-error">
          读这门课的课件或对话失败：{readError.message}
          {readError.code ? `（${readError.code}）` : ""}。
          <b>下面的空列表不可信。</b>
        </p>
      )}
      <Workspace
        apiBase={API_BASE_URL}
        userId={result.userId}
        arm={result.row.f1_arm}
        /* `sessionId` 在**服务端**抽：客户端组件的渲染在 SSR 与 hydrate 各跑一次，
           渲染期取随机数两侧会分叉（React #418）。 */
        sessionId={newSessionId()}
        detailLevel={configOf(result.row).detail_level}
        modelLabel={EXPLAIN_MODEL_LABEL}
        course={{ id: course.id, name: course.name }}
        initialConversations={convs ?? []}
        initialDocuments={docs ?? []}
      />
    </main>
  );
}

/**
 * 输入框下面显示的模型名。
 *
 * ⚠️ **这是一份抄件，抄的是 `explain/explain/llm.py` 里 `AnthropicModel.name` 的默认值。**
 * 抄件会漂——写这一行的时候我当场漂了一次（写成了 "Claude Sonnet 4.5"，
 * 而默认值是 `claude-sonnet-5`）。所以它**不是靠小心维护的**：
 * `verify:logic` 有一条从 `llm.py` 原文解析出默认值再逐字比对的断言。
 *
 * 它只是一行给人看的标签，不参与任何判断；这一轮真正用了哪个模型，
 * 服务端在 `explain_turn` 事件里按实际调用记（`model_reported`）。
 *
 * 不从服务端拿的理由：为了一行标签让这一页去 ping 那个免费层服务，等于把
 * 「输入框什么时候能用」绑在一次冷启动上（F74），而用户此刻只是想打字。
 */
const EXPLAIN_MODEL_LABEL = "claude-sonnet-5";
