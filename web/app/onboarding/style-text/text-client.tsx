"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConfigSummary } from "@/app/components/config-summary";
import { AXIS_IDS, AXIS_LABEL, VALUE_LABEL, type StyleConfig } from "@/lib/style/axes";
import {
  createEventQueue,
  onboardingCompleted,
  type EventQueue,
  type EventQueueState,
} from "@/lib/style/events";
import { createClient } from "@/lib/supabase/client";

/**
 * 文字描述 → 确认 → 写库。三屏。
 *
 * ## 为什么有一屏只读确认（2026-09-09 拍板）
 *
 * 直接写库少一屏，但用户不知道自己的描述被理解成了什么，也看不到哪几条轴回落了默认值。
 * 出问题时归因不到是「描述没写清」还是「模型分错了」——而 A5 要读的正是这条臂的效果。
 *
 * 确认屏**只读**：改配置去 `/settings/style`。两条臂因此形状一致
 * （采集 → 写入 → 之后可改），A5 的三个预登记指标才可比。
 *
 * ## 回落默认值必须显式标出（同口径 F07 的原则）
 *
 * 描述里没提到的轴用默认值，这件事**要说出来**。静默补默认值会让
 * 「用户选的」和「没提到所以用默认」在库里长得一模一样，之后没人分得开。
 *
 * ## 埋点分两处写，不是疏漏（阶段 4）
 *
 * - `style_text_mapped` 由 **route handler 写**：`call` 只有服务端知道是真的，
 *   而且模型跑偏、限流这些失败分支根本走不到这里。
 * - `style_onboarding_completed` 由**本组件写**：它的触发条件是「配置那一行真的变了」，
 *   而写库发生在客户端（与主臂同一条路径）。
 *
 * **对照臂也必须发完成事件。** onboarding 完成率是 A5 预登记的三个指标之一，
 * 按臂分组读；这条臂不发，那条曲线就只有一半。
 * `steps_completed` 在这条臂上恒为 1（一段描述），与主臂的 1–6 **含义不同、不可跨臂比**。
 */

const MAX_CHARS = 800;

type AxisOutcome = { value: string; source: "text" | "default"; reason: string };

/**
 * 这次调用用了哪个模型与参数（出处是 `lib/style/text-arm.ts`）。
 *
 * 客户端拿到它只为在 DOM 上暴露 `data-call-model` / `data-call-effort` 供核对；
 * **`style_text_mapped` 事件是服务端写的**，不由这份拷贝回填——
 * 让被记录方自己报告自己用了什么，记录就没有独立性可言。
 */
type CallInfo = { model: string; effort: string; thinking: string; max_tokens: number };

type Mapped = { config: StyleConfig; axes: Record<string, AxisOutcome>; call?: CallInfo };

export function TextOnboarding({
  userId,
  arm,
  sessionId,
  alreadyOnboarded,
}: {
  userId: string;
  arm: "pairwise" | "text";
  /**
   * 一次页面加载一个，**由服务端生成**（见 `page.tsx`）。
   * 这里只负责原样带给 route handler —— 事件由服务端写（`call` 与失败分支都在那边）。
   */
  sessionId: string;
  alreadyOnboarded: boolean;
}) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [mapped, setMapped] = useState<Mapped | null>(null);
  const [saved, setSaved] = useState<StyleConfig | null>(null);
  /** 保存时那次调用的模型与参数，留到收尾屏——阶段 4 的埋点从这里取。 */
  const [savedCall, setSavedCall] = useState<CallInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 埋点队列的状态。**不打断流程，但也不静默。** */
  const [eventState, setEventState] = useState<EventQueueState>({
    pending: 0,
    dropped: 0,
    stalled: false,
    lastError: null,
  });

  const [frozenSessionId] = useState(sessionId);

  /**
   * 事件队列，**每个组件实例一个**，且**只在 effect / 事件处理里取用**。
   *
   * 不在渲染里建：`createClient()` 与 `sessionStorage` 都是浏览器的东西，
   * 而客户端组件的渲染在 SSR 时也会跑一次（同 React #418 的那条教训）。
   * 不重新建：重建会把内存里还没发出去的那几条丢掉，而那正是这次要修的东西。
   */
  const queueRef = useRef<EventQueue | null>(null);
  function eventQueue(): EventQueue {
    if (queueRef.current === null) {
      queueRef.current = createEventQueue(createClient(), {
        userId,
        arm,
        sessionId: frozenSessionId,
      });
    }
    return queueRef.current;
  }

  useEffect(() => {
    const q = eventQueue();
    const off = q.subscribe(setEventState);
    setEventState(q.state());
    // 冲一次：上一次页面加载没发完的那几条落在 sessionStorage 里等着。
    void q.flush();
    return off;
    // eventQueue 是本实例内稳定的惰性构造器，不进依赖数组。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  /**
   * 这条臂的总耗时起点。**在 effect 里取，不在渲染里**——渲染在 SSR 与 hydrate
   * 各跑一次，两次取到的是两条不同的时间轴（同 React #418 的形态）。
   */
  const startedAtRef = useRef<number | null>(null);
  useEffect(() => {
    if (startedAtRef.current === null) startedAtRef.current = performance.now();
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const res = await fetch("/api/style/from-text", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ description: text, session_id: sessionId }),
      });
      const body = await res.json();
      if (!res.ok) {
        // 原文照报 + 状态码。压成「出错了，请重试」会把「服务端没配 key」（503）、
        // 「限流」（429）、「模型跑偏」（502）说成同一句话，而它们的处置完全不同。
        setError(`${body?.error ?? "未知错误"}（${res.status}）`);
        return;
      }
      setMapped(body as Mapped);
    } catch {
      setError("请求没有发出去，检查网络后重试");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!mapped) return;
    setBusy(true);
    setError(null);

    // 与主臂同一条写入路径：只发四条轴 + onboarded_at，**不发 f1_arm**（口径 F10）。
    const { data, error: err } = await createClient()
      .from("style_config")
      .update({ ...mapped.config, onboarded_at: new Date().toISOString() })
      .eq("user_id", userId)
      .select("user_id, detail_level, use_examples, structure, depth, onboarded_at");

    if (err) {
      setError(`${err.message}（${err.code ?? "?"}）`);
      setBusy(false);
      return;
    }
    if (!data || data.length !== 1) {
      setError(`更新影响了 ${data?.length ?? 0} 行，应为 1 行。配置没有保存。`);
      setBusy(false);
      return;
    }

    // **落库成功之后**才发（上面刚断言影响 1 行）。`steps_completed` 在这条臂上
    // 恒为 1：一段描述就是全部采集，没有「卡在第几步」可言。
    eventQueue().enqueue(
      onboardingCompleted({
        config: mapped.config,
        stepsCompleted: 1,
        totalMs: Math.round(performance.now() - (startedAtRef.current ?? performance.now())),
      }),
    );

    setSaved(mapped.config);
    setSavedCall(mapped.call ?? null);
    setBusy(false);
    router.refresh();
  }

  if (saved) {
    return (
      <div
        data-state="saved"
        data-call-model={savedCall?.model}
        data-call-effort={savedCall?.effort}
        data-events-pending={eventState.pending}
        data-events-dropped={eventState.dropped}
        className="enter mt-10"
      >
        <h1 className="t-display">配置好了</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          之后每次讲解都会按它来，随时可以改。
        </p>
        <div className="mt-6">
          <ConfigSummary config={saved} />
        </div>
        {(eventState.pending > 0 || eventState.dropped > 0) && (
          <p
            role="status"
            data-event-error
            className="panel mt-6 bg-[var(--c-accent-wash)] p-3 text-sm text-[var(--c-accent-ink)]"
          >
            {eventState.dropped > 0 && (
              <b>有 {eventState.dropped} 条埋点被永久拒绝（请把这句话报给开发）。</b>
            )}
            {eventState.pending > 0 &&
              (eventState.stalled
                ? `还有 ${eventState.pending} 条埋点没写进去，网络恢复后会自动重试。`
                : `还有 ${eventState.pending} 条埋点在写入中。`)}
            <br />
            <span className="t-meta">配置已保存，受影响的只有埋点。</span>
          </p>
        )}
        <div className="mt-6 flex flex-wrap gap-3">
          <Link href="/" className="btn btn--primary no-underline">
            回到首页
          </Link>
          <Link href="/settings/style" className="btn btn--ghost no-underline">
            去改配置
          </Link>
        </div>
      </div>
    );
  }

  if (mapped) {
    return (
      <div
        data-state="confirm"
        data-call-model={mapped.call?.model}
        data-call-effort={mapped.call?.effort}
        className="enter mt-10"
      >
        <h1 className="t-display">我们把你的描述理解成了这样</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          确认之后就按它来。<b>这一屏只读</b>——想单独调某一条，点下面的「就按这个来」之后，
          在「风格设置」里改，那里每条轴都能单独选。
        </p>

        <dl className="card mt-6 divide-y divide-[var(--c-border-row)] p-2">
          {AXIS_IDS.map((axis) => {
            const outcome = mapped.axes[axis];
            const fromDefault = outcome?.source === "default";
            return (
              <div key={axis} data-axis={axis} data-source={outcome?.source} className="p-4">
                <div className="flex items-baseline justify-between gap-4">
                  <dt className="t-heading shrink-0">{AXIS_LABEL[axis]}</dt>
                  <dd className="t-ui text-right" data-value={mapped.config[axis]}>
                    {(VALUE_LABEL[axis] as Record<string, string>)[mapped.config[axis]]}
                  </dd>
                </div>
                <p className="t-meta mt-2 leading-relaxed">
                  {fromDefault ? (
                    <>
                      <b>你的描述里没提到这一条</b>，先用了默认值。
                      {outcome?.reason ? ` ${outcome.reason}` : ""}
                    </>
                  ) : (
                    outcome?.reason
                  )}
                </p>
              </div>
            );
          })}
        </dl>

        {error && (
          <p
            role="alert"
            className="panel mt-5 border-[color:var(--c-danger)]/30 bg-[var(--c-danger-wash)] p-3 text-sm text-[var(--c-danger)]"
          >
            {error}
          </p>
        )}

        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy}
            onClick={confirm}
            data-action="confirm"
          >
            {busy ? "保存中…" : "就按这个来"}
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            disabled={busy}
            onClick={() => {
              setMapped(null);
              setError(null);
            }}
            data-action="redo"
          >
            重新描述
          </button>
        </div>
      </div>
    );
  }

  return (
    <div data-state="input" className="enter mt-10">
      <h1 className="t-display">你希望它怎么给你讲？</h1>
      <p className="t-body mt-3 max-w-[62ch]">
        用自己的话写几句就行。比如「讲细一点，多举例子，先说结论」，
        或者「我赶时间，讲要点就够了，别推导」。
      </p>

      {alreadyOnboarded && (
        <p className="panel mt-5 bg-[var(--c-accent-wash)] p-3 text-sm text-[var(--c-accent-ink)]">
          你之前已经设置过一次。重新描述会<b>覆盖</b>现在的配置；只想改一两条的话，
          去 <Link href="/settings/style" className="underline underline-offset-4">风格设置</Link> 更省事。
        </p>
      )}

      <form onSubmit={submit} className="mt-6">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={MAX_CHARS}
          rows={7}
          required
          data-field="description"
          placeholder="例如：讲得细一些，每个概念配个例子；先给结论再解释；能推导的地方把步骤写出来。"
          className="panel w-full resize-y p-4 text-[15px] leading-[1.8] outline-none"
        />
        <div className="mt-2 flex items-center justify-between">
          <span className="t-meta">
            {text.length} / {MAX_CHARS} 字
          </span>
          <span className="t-meta">没提到的方面会用默认值，并在下一屏标出来</span>
        </div>

        {error && (
          <p
            role="alert"
            className="panel mt-5 border-[color:var(--c-danger)]/30 bg-[var(--c-danger-wash)] p-3 text-sm text-[var(--c-danger)]"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          className="btn btn--primary mt-5"
          disabled={busy || text.trim().length === 0}
          data-action="map"
        >
          {busy ? "理解中…" : "下一步"}
        </button>
      </form>
    </div>
  );
}
