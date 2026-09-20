"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import type { Arm } from "@/lib/style/axes";
import {
  createEventQueue,
  tasteChosen,
  tasteShown,
  type EventQueue,
  type EventQueueState,
  type StyleEvent,
} from "@/lib/style/events";
import { BOARDS, configFromTaste, resolveTaste, priorsAreValid } from "@/lib/style/taste";
import { createClient } from "@/lib/supabase/client";

/**
 * 三块标签的交互。
 *
 * ## 限制选择数量不是为了省事
 *
 * 允许无限多选时人会「什么都想要」，结果是不产生区分度——这是评分量表相对于
 * 强制取舍（MaxDiff / 最优最劣量表）的已知缺陷。每块 `max` 由 `taste.ts` 给，
 * 达到上限后其余标签置灰**并说明原因**，不是静默失效。
 *
 * ## 「展示」事件按**真的进入视口**发
 *
 * 三块在同一页上，挂载时全发的话，`style_taste_shown` 只能告诉我们「这一页被打开过」，
 * 而我们要问的是「用户在第几块走掉」。所以用 IntersectionObserver，每块只发一次。
 *
 * ## 写库成功之后才跳下一页
 *
 * 跳了再写的话，写失败的表现是「下一页的比较顺序莫名其妙」——那时已经没人知道
 * 是标签没存上（同 F42 的处置方向：不打断，但也不静默）。
 */
export function TasteBoards({
  userId,
  arm,
  sessionId,
  initialTags,
}: {
  userId: string;
  arm: Arm;
  sessionId: string;
  initialTags: string[];
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<string[]>(initialTags);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [eventState, setEventState] = useState<EventQueueState>({
    pending: 0,
    dropped: 0,
    stalled: false,
    lastError: null,
  });

  const [frozenSessionId] = useState(sessionId);
  const startedAtRef = useRef<number | null>(null);
  const shownRef = useRef<Set<string>>(new Set());

  /** 事件队列：每个实例一个，只在 effect / 事件处理里取用（同其余三页）。 */
  const queueRef = useRef<EventQueue | null>(null);
  function eventQueue(): EventQueue {
    if (queueRef.current === null) {
      queueRef.current = createEventQueue(createClient(), { userId, arm, sessionId: frozenSessionId });
    }
    return queueRef.current;
  }
  function send(event: StyleEvent) {
    eventQueue().enqueue(event);
  }

  useEffect(() => {
    const q = eventQueue();
    const off = q.subscribe(setEventState);
    setEventState(q.state());
    void q.flush();
    startedAtRef.current = performance.now();
    return off;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 每块**真的进入视口**时发一次展示事件（见组件说明）。
  useEffect(() => {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = (e.target as HTMLElement).dataset.board;
          if (!e.isIntersecting || !id || shownRef.current.has(id)) continue;
          shownRef.current.add(id);
          const board = BOARDS.find((b) => b.id === id);
          if (board) send(tasteShown({ board: id, tagIds: board.tags.map((t) => t.id) }));
        }
      },
      { threshold: 0.35 },
    );
    for (const el of document.querySelectorAll("[data-board]")) io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const taste = resolveTaste(selected);
  const perBoard = BOARDS.map((b) => ({
    board: b,
    picked: selected.filter((id) => b.tags.some((t) => t.id === id)),
  }));
  const unmet = perBoard.filter(({ board, picked }) => picked.length < board.min);
  const canContinue = unmet.length === 0 && !busy;

  function toggle(boardId: string, tagId: string) {
    const board = BOARDS.find((b) => b.id === boardId);
    if (!board) return;
    setSelected((prev) => {
      if (prev.includes(tagId)) return prev.filter((x) => x !== tagId);
      const mine = prev.filter((id) => board.tags.some((t) => t.id === id));
      // 单选块（领域）：再点一个就换掉，不是被拒绝——拒绝会让人以为点坏了。
      if (board.max === 1) return [...prev.filter((id) => !mine.includes(id)), tagId];
      if (mine.length >= board.max) return prev;
      return [...prev, tagId];
    });
  }

  async function submit() {
    if (!canContinue) return;
    setBusy(true);
    setError(null);

    if (!priorsAreValid(taste.priors)) {
      // 取值域校验在写库之前。落进配置之后再发现，炸的地方离原因很远。
      setError(`先验取值不在取值域里：${JSON.stringify(taste.priors)}。请把这句话报给开发。`);
      setBusy(false);
      return;
    }

    // ---- 2026-09-20：成对比较去掉了，**这一页就是 onboarding 的全部**。
    //
    // 所以这里要写的不再只是「先验」三列，而是真正的四条轴 + `onboarded_at`。
    // 标签没覆盖到的轴回落默认档，并且把回落了哪几条**记进事件**——
    // 回落本身不可怕，看不见的回落才可怕（库里「他选的 P0」和「我们填的 P0」
    // 长得一模一样）。
    //
    // 仍然不碰 `f1_arm`（口径 F10：改它抛 23514）。它现在的含义从「实验臂」
    // 变成「这行配置是怎么来的」，新用户一律 taste（见迁移 20260920120000）。
    const { config, fellBack } = configFromTaste(taste.priors, taste.tone);
    const { data, error: err } = await createClient()
      .from("style_config")
      .update({
        taste_tags: selected,
        tone: config.tone,
        detail_level: config.detail_level,
        use_examples: config.use_examples,
        structure: config.structure,
        depth: config.depth,
        onboarded_at: new Date().toISOString(),
      })
      .eq("user_id", userId)
      .select("user_id, taste_tags, tone, onboarded_at");

    if (err) {
      setError(`${err.message}（${err.code ?? "?"}）`);
      setBusy(false);
      return;
    }
    if (!data || data.length !== 1) {
      // RLS 挡住时 PostgREST 返回 0 行且**不报错**，那种「成功」正是要防的。
      setError(`更新影响了 ${data?.length ?? 0} 行，应为 1 行。选择没有保存。`);
      setBusy(false);
      return;
    }

    send(
      tasteChosen({
        tags: selected,
        tone: taste.tone,
        priors: taste.priors as Record<string, string>,
        totalMs: Math.round(performance.now() - (startedAtRef.current ?? performance.now())),
      }),
    );
    if (fellBack.length > 0) {
      // 标签没定下来的轴。**查得出来的数**，不是一句感觉——
      // 如果它长期是 3 条、4 条，说明这两块标签没在做事。
      console.info(JSON.stringify({ event: "taste_axes_fell_back", axes: fellBack }));
    }

    router.replace("/");
  }

  return (
    <div className="enter pb-28" data-events-pending={eventState.pending} data-events-dropped={eventState.dropped}>
      <header className="mt-10 mb-8 sm:mt-14">
        
        <h1 className="t-display enter-wipe">先说说你的口味</h1>
        <p className="t-body mt-3 max-w-[62ch]">点几个就行。之后在讲解里随时能改——那时你手上有具体的东西可以反应。</p>
      </header>

      <div className="flex flex-col gap-8">
        {perBoard.map(({ board, picked }) => (
          <section key={board.id} data-board={board.id} className="card p-2">
            <div className="flex flex-wrap items-baseline justify-between gap-2 px-3 pt-3 pb-4">
              <div>
                <h2 className="t-title">{board.title}</h2>
                <p className="t-meta mt-1 max-w-[56ch]">{board.lead}</p>
              </div>
              <span className="t-caption kbd-count shrink-0" data-field={`count-${board.id}`}>
                {picked.length} / {board.max}
              </span>
            </div>

            <div className="panel flex flex-wrap gap-2 p-4">
              {board.tags.map((tag) => {
                const active = selected.includes(tag.id);
                const full = !active && board.max > 1 && picked.length >= board.max;
                return (
                  <button
                    key={tag.id}
                    type="button"
                    data-tag={tag.id}
                    data-selected={active}
                    aria-pressed={active}
                    disabled={full || busy}
                    onClick={() => toggle(board.id, tag.id)}
                    className={`chip h-auto cursor-pointer px-4 py-2 text-[14px] transition-colors ${
                      active
                        ? "border-[var(--c-accent)] bg-[var(--c-accent-wash)] text-[var(--c-accent-ink)]"
                        : full
                          ? "opacity-40"
                          : "hover:border-[var(--c-border-strong)]"
                    }`}
                  >
                    {tag.label}
                  </button>
                );
              })}
            </div>

            {board.max > 1 && picked.length >= board.max && (
              <p className="t-caption px-3 py-2" data-field={`full-${board.id}`}>
                最多 {board.max} 个。想换先取消一个。
              </p>
            )}
          </section>
        ))}
      </div>

      {error && (
        <p role="alert" className="notice notice--danger mt-6">
          {error}
        </p>
      )}

      {(eventState.pending > 0 || eventState.dropped > 0) && (
        <p role="status" data-event-error className="notice notice--info mt-6">
          {eventState.dropped > 0 && <b>有 {eventState.dropped} 条埋点被永久拒绝（请把这句话报给开发）。</b>}
          {eventState.pending > 0 && `还有 ${eventState.pending} 条埋点在写入中。`}
          <br />
          <span className="t-caption">你的选择不受影响。</span>
        </p>
      )}

      {/* 操作条吸底：三块要滚，按钮不能只在页底。 */}
      <div className="sticky bottom-4 z-20 mt-8">
        <div className="panel card--raised flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <button
            type="button"
            className="btn btn--primary"
            disabled={!canContinue}
            onClick={() => void submit()}
            data-action="continue"
          >
            {busy ? "保存中…" : "开始用"}
          </button>
          <span className="t-meta" data-field="gate">
            {unmet.length === 0 ? `已选 ${selected.length}` : `还差：${unmet.map(({ board }) => board.title).join(" · ")}`}
          </span>
        </div>
      </div>
    </div>
  );
}
