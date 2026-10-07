"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import type { Arm } from "@/lib/style/axes";
import {
  createEventQueue,
  onboardingCompleted,
  tasteChosen,
  tasteShown,
  type EventQueue,
  type EventQueueState,
  type StyleEvent,
} from "@/lib/style/events";
import { PRESETS, SAMPLE_QUESTION, configFromPreset } from "@/lib/style/taste";
import { createClient } from "@/lib/supabase/client";

/**
 * 开场：四种讲法选一种（口径 F107）。卡片与示例的定义在 `lib/style/taste.ts`。
 *
 * ## 写库成功之后才跳下一页
 *
 * 跳了再写的话，写失败的表现是「后面的讲解莫名其妙」——那时已经没人知道
 * 是选择没存上（同 F42 的处置方向：不打断，但也不静默）。
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
  // 走过一遍又回来的人：上次选的那种讲法预先选中。老用户存的是旧标签 id，认不出就不选。
  const [picked, setPicked] = useState<string | null>(
    () => initialTags.find((id) => PRESETS.some((p) => p.id === id)) ?? null,
  );
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

  /** 事件队列：每个实例一个，只在 effect / 事件处理里取用（同设置页）。 */
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
    // 一页、一块，挂载即是看到了。`board` 记成 "preset"，与旧的 "tone" / "stuck" 分得开。
    send(tasteShown({ board: "preset", tagIds: PRESETS.map((p) => p.id) }));
    return off;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit() {
    if (!picked || busy) return;
    setBusy(true);
    setError(null);

    let chosen: ReturnType<typeof configFromPreset>;
    try {
      chosen = configFromPreset(picked);
    } catch (e) {
      setError(`${String(e)}。请把这句话报给开发。`);
      setBusy(false);
      return;
    }
    const { config, tone } = chosen;
    // 一种讲法定死全部四条轴 + 语气，**没有回落默认的轴**（旧版两块标签时有，见 F107）。
    //
    // 仍然不碰 `f1_arm`（口径 F10：改它抛 23514）。它的含义是「这行配置是怎么来的」，
    // 新用户一律 taste（见迁移 20260920120000）。
    const { data, error: err } = await createClient()
      .from("style_config")
      .update({
        taste_tags: [picked],
        tone,
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

    const totalMs = Math.round(performance.now() - (startedAtRef.current ?? performance.now()));
    // `priors` 现在就是整份配置：一种讲法定死全部四条轴（不再有「回落默认」的轴）。
    send(tasteChosen({ tags: [picked], tone, priors: { ...config }, totalMs }));
    // ---- A5 预登记的「onboarding 完成率」。
    //
    // ⚠️ **这条事件断过一次，而且是静默断的**（2026-09-20 到 09-22）：开场换页之后
    // 没有人再发它，完成率的分子恒为 0 而不报错。页面再换时它必须跟着搬过来。
    //
    // `steps_completed` 在不同版本上含义不同（见 `events.ts` 那条注释）。
    // 这一版恒为 1：只有一步、选了才能走。
    send(onboardingCompleted({ config, stepsCompleted: 1, tone, totalMs }));

    router.replace("/");
  }

  return (
    <div className="enter pb-28" data-events-pending={eventState.pending} data-events-dropped={eventState.dropped}>
      <header className="mt-10 mb-8 sm:mt-14">
        <h1 className="t-display enter-wipe">请选择你的讲解偏好</h1>
        <p className="t-meta mt-3">下面四段都在回答同一个问题：{SAMPLE_QUESTION}</p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="讲解偏好">
        {PRESETS.map((p) => {
          const active = picked === p.id;
          return (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={active}
              data-preset={p.id}
              data-selected={active}
              disabled={busy}
              onClick={() => setPicked(p.id)}
              className="choice flex cursor-pointer flex-col gap-3 p-5 text-left"
            >
              <span>
                <span className="t-title block">{p.name}</span>
                <span className="t-meta mt-1 block">{p.summary}</span>
              </span>
              <span className="t-sample block border-l-2 border-[var(--c-border-strong)] pl-3 text-[14.5px] leading-[1.85] text-[var(--c-ink-soft)]">
                {p.sample}
              </span>
            </button>
          );
        })}
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

      {/* 吸底：卡片在手机上要滚，按钮不能只在页底。 */}
      <div className="sticky bottom-4 z-20 mt-8 flex justify-end">
        <button
          type="button"
          className="btn btn--primary btn--lg card--raised"
          disabled={!picked || busy}
          onClick={() => void submit()}
          data-action="continue"
        >
          {busy ? "保存中…" : "下一步"}
        </button>
      </div>
    </div>
  );
}
