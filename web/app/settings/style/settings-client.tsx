"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  AXES,
  AXIS_IDS,
  AXIS_LABEL,
  ENFORCED,
  VALUE_HINT,
  VALUE_LABEL,
  type StyleConfig,
} from "@/lib/style/axes";
import {
  createEventQueue,
  feedbackChange,
  manualChange,
  type EventQueue,
  type EventQueueState,
  type StyleEvent,
} from "@/lib/style/events";
import { FEEDBACK_KINDS, FEEDBACK_LABEL, applyFeedback, type FeedbackKind } from "@/lib/style/feedback";
import { createClient } from "@/lib/supabase/client";

/**
 * 风格设置：四条轴直接改 + 四个反馈按钮。
 *
 * ## 反馈按钮为什么在这一页
 *
 * 它们的正主是讲解页面（阶段 4 接反馈接口与埋点）。放在这里是为了让 F05–F07
 * **现在就有一条能走通、能被断言的实现**：语义写在 `lib/style/feedback.ts` 的纯函数里，
 * 阶段 4 的接口应当调同一个函数，而不是在服务端把三条映射再写一遍。
 *
 * ## 反馈接口的埋点（阶段 4）
 *
 * **行为一个字没改**（F05–F07 已在阶段 3 验收通过），只在写库之后补了一条
 * `style_config_changed` 事件。事件里的 `config_changed` 与 `counts_as_config_edit`
 * **不在这里判**，由 `lib/style/events.ts` 的 `feedbackChange()` 调同一个
 * `applyFeedback` 得出——在埋点里重新判一遍就是给 F06 开第二个出处，
 * 而两处不一致不会报错：§3.2 的「配置修改频率趋近 0」会因为埋点少写一个分支
 * 而自己达标。
 *
 * **配置没变的那两种情况（F06 例子没懂 / F07 端点）照样发事件。** 它们是这两条
 * 口径唯一的数据痕迹——不发的话，「用户点了但我们决定不改」与「用户没点」
 * 在库里长得一模一样。
 *
 * ## 有未保存改动时反馈按钮禁用
 *
 * 反馈按钮作用在**已保存的配置**上并立即写库。若此时手工草稿还没保存，
 * 两者会互相覆盖——要么静默丢掉用户的手工改动，要么把没打算保存的改动一起写进去。
 * 两种都是静默改写（F10 反对的那件事），所以这里选择**禁用并说明原因**。
 */
export function StyleSettings({
  userId,
  initialConfig,
  onboarded,
  arm,
  sessionId,
}: {
  userId: string;
  initialConfig: StyleConfig;
  onboarded: boolean;
  arm: "pairwise" | "text";
  /** 一次页面加载一个，**由服务端生成**（见 `page.tsx`）。 */
  sessionId: string;
}) {
  const router = useRouter();
  const [saved, setSaved] = useState<StyleConfig>(initialConfig);
  const [draft, setDraft] = useState<StyleConfig>(initialConfig);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /** 埋点队列的状态。**不打断保存，但也不静默。** */
  const [eventState, setEventState] = useState<EventQueueState>({
    pending: 0,
    dropped: 0,
    stalled: false,
    lastError: null,
  });

  const [frozenSessionId] = useState(sessionId);

  const dirty = AXIS_IDS.some((axis) => draft[axis] !== saved[axis]);

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

  function send(event: StyleEvent) {
    eventQueue().enqueue(event);
  }

  // 挂载时冲一次队列。**这一页是那条兜底路径**：用户在 onboarding 末尾
  // 点「回到首页」时若还有事件排在队里，组件卸载后靠 sessionStorage 留着，
  // 下次打开风格设置就会被发出去。
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

  async function write(config: StyleConfig, message: string, event: StyleEvent) {
    setBusy(true);
    setError(null);
    setNotice(null);

    // 只发四条轴。**不发 f1_arm**（口径 F10：改它抛 23514），也不动 onboarded_at——
    // 那是「完成过 onboarding」的时间戳，不是「最后修改时间」，改配置不该覆盖它。
    const { data, error: err } = await createClient()
      .from("style_config")
      .update(config)
      .eq("user_id", userId)
      .select("user_id, detail_level, use_examples, structure, depth");

    if (err) {
      setError(`${err.message}（${err.code ?? "?"}）`);
      setBusy(false);
      return;
    }
    if (!data || data.length !== 1) {
      setError(`更新影响了 ${data?.length ?? 0} 行，应为 1 行。改动没有保存。`);
      setBusy(false);
      return;
    }

    // 事件发在**断言过「库里那一行真的变了」之后**（上面刚断言影响 1 行）。
    // 反过来先发事件的话，事件流里会有一批实际没落库的「配置修改」。
    send(event);

    setSaved(config);
    setDraft(config);
    setNotice(message);
    setBusy(false);
    router.refresh();
  }

  function onFeedback(kind: FeedbackKind) {
    const outcome = applyFeedback(saved, kind);
    // 事件由 events.ts 自己调 applyFeedback 构造，**不把上面这个 outcome 传过去**：
    // 传过去就等于允许「页面按一份判断行动、事件里记另一份」，而两份不一致不报错。
    const event = feedbackChange(saved, kind);

    // F06 /（在端点时）F07：配置不变 → **不写库**，只给那一句说明。
    // 静默吞掉会让用户以为生效了，也会在埋点里多一次假的配置变更。
    //
    // ⚠️ 但**事件照发**：这两条口径的全部数据痕迹就是这条事件
    // （`config_changed: false` + `counts_as_config_edit: false`）。
    if (!outcome.config_changed) {
      setError(null);
      setNotice(outcome.message);
      void send(event);
      return;
    }
    void write(outcome.config, outcome.message, event);
  }

  return (
    <div
      className="enter mt-10"
      data-events-pending={eventState.pending}
      data-events-dropped={eventState.dropped}
    >
      <header className="mb-6">
        <h1 className="t-display">风格设置</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          讲解按这四条来。改完保存即刻生效，下一次讲解就用新的。
        </p>
      </header>

      {!onboarded && (
        <p className="panel mb-6 bg-[var(--c-accent-wash)] p-3 text-sm text-[var(--c-accent-ink)]">
          你还没走过一遍风格设置引导。现在这份是默认配置——
          <Link
            href={arm === "pairwise" ? "/onboarding/style" : "/onboarding/style-text"}
            className="underline underline-offset-4"
          >
            去走一遍
          </Link>
          会更贴合你的偏好。
        </p>
      )}

      <div className="card divide-y divide-[var(--c-border-row)] p-2">
        {AXIS_IDS.map((axis) => (
          <fieldset key={axis} data-axis={axis} className="p-4">
            <legend className="t-heading mb-3 flex items-center gap-2">
              {AXIS_LABEL[axis]}
              {ENFORCED[axis] ? (
                <span className="rounded-full bg-[var(--c-accent-wash)] px-2 py-0.5 text-[11px] font-[var(--fw-medium)] text-[var(--c-accent-ink)]">
                  会被自动审查
                </span>
              ) : (
                <span className="t-meta font-[var(--fw-regular)]">仅进 prompt，不做判定</span>
              )}
            </legend>

            <div className="grid gap-2 sm:grid-cols-3">
              {(AXES[axis] as readonly string[]).map((value) => {
                const active = draft[axis] === value;
                return (
                  <button
                    key={value}
                    type="button"
                    data-value={value}
                    data-selected={active}
                    aria-pressed={active}
                    disabled={busy}
                    onClick={() =>
                      setDraft({ ...draft, [axis]: value } as StyleConfig)
                    }
                    className="choice p-3 text-left"
                  >
                    <span className="t-ui block">
                      {(VALUE_LABEL[axis] as Record<string, string>)[value]}
                    </span>
                    <span className="t-meta mt-1 block leading-snug">
                      {(VALUE_HINT[axis] as Record<string, string>)[value]}
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn btn--primary"
          disabled={!dirty || busy}
          onClick={() => void write(draft, "已保存。", manualChange(saved, draft))}
          data-action="save"
        >
          {busy ? "保存中…" : "保存"}
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          disabled={!dirty || busy}
          onClick={() => {
            setDraft(saved);
            setNotice(null);
          }}
          data-action="revert"
        >
          撤销改动
        </button>
        {!dirty && <span className="t-meta">没有未保存的改动</span>}
      </div>

      {/* ---------------------------------------------------------------- 反馈 */}

      <section className="mt-10">
        <h2 className="t-title">读完一段讲解之后</h2>
        <p className="t-body mt-2 max-w-[62ch]">
          点一下就按下面的规则调整配置 —— 除「例子没懂」外，改的都是<b>以后所有讲解</b>。
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          {FEEDBACK_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              className="btn btn--ghost"
              disabled={busy || dirty}
              onClick={() => onFeedback(kind)}
              data-feedback={kind}
            >
              {FEEDBACK_LABEL[kind]}
            </button>
          ))}
        </div>

        {dirty && (
          <p className="t-meta mt-3">
            有未保存的改动，反馈按钮先停用——它作用在已保存的配置上，两边同时改会互相覆盖。
            先保存或撤销。
          </p>
        )}

        <ul className="t-meta mt-4 max-w-[70ch] list-disc space-y-1 pl-5 leading-relaxed">
          <li>
            <b>太啰嗦</b>：详细程度往简洁方向移一格。
          </li>
          <li>
            <b>讲太深 / 讲太浅</b>：深度往浅 / 深方向移一格。
          </li>
          <li>
            <b>例子没懂</b>：<b>不改配置</b>，只把这一段按当前配置重新讲一遍。
          </li>
          <li>已经在最边上那一格时，配置保持不变，并会告诉你为什么没变。</li>
          <li>
            只是<b>某一段的这几页</b>没看懂、不想以后都讲浅？别点「讲太深」——
            去讲解页那一轮下面点<b>「这部分没看懂」</b>：不改配置，只在下次讲到这几页时换个切入点（口径 F56）。
          </li>
        </ul>
      </section>

      {notice && (
        <p
          role="status"
          data-notice
          className="panel mt-6 bg-[var(--c-accent-wash)] p-3 text-sm text-[var(--c-accent-ink)]"
        >
          {notice}
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="panel mt-6 border-[color:var(--c-danger)]/30 bg-[var(--c-danger-wash)] p-3 text-sm text-[var(--c-danger)]"
        >
          {error}
        </p>
      )}

      {/* 埋点失败不影响配置本身，但要说出来——「还在重试」「等下次」「永久拒绝」
          三种的处置完全不同，所以分开说，不合成一句。 */}
      {(eventState.pending > 0 || eventState.dropped > 0) && (
        <p
          role="status"
          data-event-error
          className="panel mt-6 bg-[var(--c-accent-wash)] p-3 text-sm text-[var(--c-accent-ink)]"
        >
          {eventState.dropped > 0 && (
            <b>有 {eventState.dropped} 条埋点被永久拒绝（不是网络问题，请把这句话报给开发）。</b>
          )}
          {eventState.pending > 0 &&
            (eventState.stalled
              ? `还有 ${eventState.pending} 条埋点没写进去，网络恢复后会自动重试。`
              : `还有 ${eventState.pending} 条埋点在写入中。`)}
          {eventState.lastError ? ` 最后一次的错误：${eventState.lastError}` : ""}
          <br />
          <span className="t-meta">你的配置不受影响。</span>
        </p>
      )}
    </div>
  );
}
