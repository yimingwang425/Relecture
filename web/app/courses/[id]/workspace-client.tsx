"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { ExplainClient } from "@/app/explain/explain-client";
import { CourseUpload } from "@/app/courses/upload-client";
import { StatusDot } from "@/app/components/status-dot";
import type { Arm } from "@/lib/style/axes";
import { turnsFromMessages, type Turn } from "@/lib/explain/turns";
import { createClient } from "@/lib/supabase/client";

/**
 * 一门课的工作区：**左侧栏 + 右聊天**（2026-09-20 改版）。
 *
 * ## 顶栏去掉了，课件与会话搬进侧栏
 *
 * 原来的黑色胶囊顶栏把「我的课程 / 课件 / 讲解风格」并排摆着，而用户在一门课里
 * 真正做的只有一件事：提问。课件是准备，风格是偶尔调。现在课件在侧栏按钮后面
 * （弹窗，不是一整页），会话列在它下面，风格与退出进 `/settings`。
 *
 * ## 会话的行由**浏览器**建，消息由服务端写
 *
 * 分工与理由写在迁移 `20260920110000_f5_conversations.sql` 里。一句话：
 * 标题是纯 UI 状态，服务端不产出它的任何一个字段；而 `round_id` / `turn_index` /
 * 答案正文只有那一次 `run_round` 知道，让浏览器自报等于让它自报模型说了什么。
 *
 * ## 换会话时**整个聊天组件重挂载**（`key`）
 *
 * 不重挂载就得在 `ExplainClient` 里加一个 effect 把 props 同步进 state，
 * 而那一瞬间屏幕上是旧会话的轮次、输入框却已经指向新会话了——
 * 用户在那一刻发出去的问题会落进另一个会话，而页面看起来完全正常。
 */

type Conversation = { id: string; title: string; updated_at: string };
type Doc = {
  id: string;
  filename: string;
  display_name: string | null;
  page_count: number;
  verdict: string;
};

export function Workspace({
  apiBase,
  userId,
  arm,
  sessionId,
  detailLevel,
  modelLabel,
  course,
  initialConversations,
  initialDocuments,
}: {
  apiBase: string;
  userId: string;
  arm: Arm;
  sessionId: string;
  detailLevel: string;
  modelLabel: string;
  course: { id: string; name: string };
  initialConversations: Conversation[];
  initialDocuments: Doc[];
}) {
  const [conversations, setConversations] = useState(initialConversations);
  const [activeId, setActiveId] = useState<string | null>(initialConversations[0]?.id ?? null);
  /**
   * 读到的轮次，**连同它属于哪个会话一起存**。
   *
   * 不存 id 的话就得在 effect 里先同步 `setTurns(null)` 再去读——而那一瞬间
   * 屏幕上还是上一个会话的轮次，只是头部已经换了。存成一对之后，
   * 「这批轮次是不是当前会话的」变成一个纯计算，不需要那次同步 setState
   * （eslint 的 set-state-in-effect 拦的正是它）。
   */
  const [loaded, setLoaded] = useState<{ id: string; turns: Turn[] } | null>(null);
  const turns = loaded?.id === activeId ? loaded.turns : null;
  const [docs, setDocs] = useState(initialDocuments);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  const supabase = useCallback(() => createClient(), []);

  // 打开一个会话就把它已有的轮次读出来。**读失败要说出来**：
  // 悄悄显示成空会话的话，用户会以为之前问过的都没了，然后重问一遍（再花一次钱）。
  useEffect(() => {
    if (!activeId) return;
    const id = activeId;
    let cancelled = false;
    void (async () => {
      const { data, error: e } = await supabase()
        .from("messages")
        .select("role, content, turn_index, round_id, meta")
        .eq("conversation_id", id)
        .order("created_at", { ascending: true });
      if (cancelled) return;
      if (e) {
        // **说出来，不要悄悄显示成空会话。** 显示成空的话用户会以为之前问过的
        // 都没了，然后把同样的问题再问一遍——再花一次钱、再等几十秒。
        // （2026-09-19 在课件列表上踩过同一个形态：42703 被渲染成「还没有课件」。）
        setError(`读这个会话的消息失败：${e.message}（${e.code}）`);
        setLoaded({ id, turns: [] });
        return;
      }
      setLoaded({ id, turns: turnsFromMessages(data ?? []) });
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, supabase]);

  async function newConversation() {
    // 标题先给一个占位，用户随时能改。**不用问题的前几个字当标题**：
    // 建会话发生在提问之前，那时还没有问题；等提问之后再回填的话，
    // 用户已经改过的标题会被覆盖掉。
    const title = `新对话 ${conversations.length + 1}`;
    const { data, error: e } = await supabase()
      .from("conversations")
      .insert({ owner: userId, course_id: course.id, title })
      .select("id, title, updated_at")
      .single();
    if (e || !data) return setError(`新建对话失败：${e?.message ?? "没有返回行"}`);
    setConversations((prev) => [data as Conversation, ...prev]);
    setActiveId(data.id);
    setError(null);
  }

  async function rename(id: string, title: string) {
    const clean = title.trim();
    setRenaming(null);
    if (!clean) return; // 空标题数据库也会拒（CHECK），这里先挡一层免得白跑一趟
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title: clean } : c)));
    const { error: e } = await supabase()
      .from("conversations")
      .update({ title: clean })
      .eq("id", id);
    if (e) setError(`改名字失败：${e.message}`);
  }

  async function renameDoc(id: string, name: string) {
    const clean = name.trim();
    // 清空 = 恢复显示原文件名。**存 null，不存空串**：null 的含义是「没改过」，
    // 空串会让列表里出现一行没有名字的课件（同口径 F50）。
    const value = clean === "" ? null : clean;
    setDocs((prev) => prev.map((d) => (d.id === id ? { ...d, display_name: value } : d)));
    const { error: e } = await supabase()
      .from("documents")
      .update({ display_name: value })
      .eq("id", id);
    if (e) setError(`给课件改名失败：${e.message}`);
  }

  return (
    <div className="ws" data-course-id={course.id}>
      {/* ============================================================ 侧栏 */}
      <aside className="ws-side">
        <div className="flex items-center justify-between gap-2 px-2">
          <Link href="/" className="t-caption link no-underline">
            ← 我的课程
          </Link>
          <Link href="/settings" className="t-caption link no-underline">
            设置
          </Link>
        </div>

        <div className="px-2">
          <p className="t-title text-[17px]" data-field="course-name">
            {course.name}
          </p>
        </div>

        <div>
          <button
            type="button"
            className="side-item"
            data-field="open-documents"
            onClick={() => dialogRef.current?.showModal()}
          >
            <span aria-hidden>📄</span>
            我的课件
            <span className="t-caption ml-auto">{docs.length}</span>
          </button>
          <button type="button" className="side-item" data-field="new-conversation" onClick={() => void newConversation()}>
            <span aria-hidden>＋</span>
            新对话
          </button>
        </div>

        <div className="min-h-0 flex-1">
          <p className="side-label mb-1">对话</p>
          {conversations.length === 0 ? (
            <p className="t-caption px-[10px]">还没有对话。按「新对话」开一个。</p>
          ) : (
            <ul data-field="conversations">
              {conversations.map((c) => (
                <li key={c.id}>
                  {renaming === c.id ? (
                    <input
                      autoFocus
                      defaultValue={c.title}
                      maxLength={80}
                      className="input"
                      data-field="conversation-rename"
                      onBlur={(e) => void rename(c.id, e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                        if (e.key === "Escape") setRenaming(null);
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      className="side-item"
                      data-field="conversation"
                      data-conversation-id={c.id}
                      aria-current={c.id === activeId}
                      onClick={() => setActiveId(c.id)}
                      onDoubleClick={() => setRenaming(c.id)}
                    >
                      <span className="min-w-0 flex-1 truncate">{c.title}</span>
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={`重命名「${c.title}」`}
                        className="t-caption shrink-0 opacity-60 hover:opacity-100"
                        onClick={(e) => {
                          e.stopPropagation();
                          setRenaming(c.id);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            e.stopPropagation();
                            setRenaming(c.id);
                          }
                        }}
                      >
                        改名
                      </span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && (
          <p role="alert" data-field="workspace-error" className="notice notice--danger text-[12.5px]">
            {error}
          </p>
        )}
      </aside>

      {/* ============================================================ 聊天 */}
      <div className="ws-main">
        {docs.length === 0 ? (
          <div className="mx-auto mt-16 max-w-[46rem] px-4">
            <p className="t-title">这门课还没有材料</p>
            <p className="t-body mt-2">
              讲解只用你上传的材料，不靠通用知识补全。先传一份 PDF 讲义。
            </p>
            <button
              type="button"
              className="btn btn--primary mt-5"
              onClick={() => dialogRef.current?.showModal()}
            >
              上传课件
            </button>
          </div>
        ) : activeId === null ? (
          <div className="mx-auto mt-16 max-w-[46rem] px-4">
            <p className="t-title">开一个对话</p>
            <p className="t-body mt-2">
              一个对话一个上下文。按周分开，追问时它才知道「刚才那一点」指的是什么。
            </p>
            <button type="button" className="btn btn--primary mt-5" onClick={() => void newConversation()}>
              新对话
            </button>
          </div>
        ) : turns === null ? (
          <div className="mx-auto mt-16 w-full max-w-[46rem] px-4" aria-label="读取中">
            <div className="skeleton h-5 w-[40%]" />
            <div className="skeleton mt-3 h-5 w-[70%]" />
          </div>
        ) : (
          <ExplainClient
            // 换会话 = 换一棵树。见组件说明最后一节。
            key={activeId}
            apiBase={apiBase}
            sessionId={sessionId}
            detailLevel={detailLevel}
            modelLabel={modelLabel}
            conversationId={activeId}
            initialTurns={turns}
            lockedCourse={course}
          />
        )}
      </div>

      {/* ============================================================ 课件弹窗 */}
      <dialog ref={dialogRef} className="dialog" data-field="documents-dialog">
        <div className="dialog__head">
          <h2 className="t-title text-[17px]">我的课件</h2>
          <form method="dialog">
            <button className="btn btn--ghost btn--sm">关闭</button>
          </form>
        </div>
        <div className="dialog__body">
          {docs.length > 0 && (
            <ul className="mb-6 flex flex-col gap-2" data-field="documents">
              {docs.map((d) => (
                <li key={d.id} className="row">
                  <StatusDot kind={d.verdict === "unusable" ? "warn" : "ok"} />
                  <div className="min-w-0 flex-1">
                    <input
                      defaultValue={d.display_name ?? ""}
                      placeholder={d.filename}
                      maxLength={80}
                      className="input"
                      data-field="document-rename"
                      aria-label={`给「${d.filename}」起个名字`}
                      onBlur={(e) => void renameDoc(d.id, e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") e.currentTarget.blur();
                      }}
                    />
                    <p className="row__sub mt-1">
                      {d.page_count} 页 · <span className="mono">{d.filename}</span>
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <p className="t-caption mb-3">
            起个自己认得的名字，比如 <b>week1</b>。留空就显示原文件名。
            {/* 改的是展示名，不覆盖原文件名——出处引用与排错要的是后者。 */}
            出处引用里仍然显示原文件名。
          </p>

          <CourseUpload
            apiBase={apiBase}
            userId={userId}
            arm={arm}
            sessionId={sessionId}
            lockedCourse={course}
          />
        </div>
      </dialog>
    </div>
  );
}
