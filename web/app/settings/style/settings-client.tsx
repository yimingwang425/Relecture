"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { PageHeader } from "@/app/components/page-header";
import {
  AXES,
  AXIS_IDS,
  AXIS_LABEL,
  ENFORCED,
  VALUE_HINT,
  VALUE_LABEL,
  type StyleConfig,
  type Arm,
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
import { PRESETS, TONES, TONE_HINT, TONE_LABEL, presetMatching, type Tone } from "@/lib/style/taste";

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
 *
 * ## 布局（2026-09-15 UI 重做）
 *
 * 桌面两栏：左边四条轴（卡片 → 面板 → 每轴一组选项），右边吸顶的反馈卡。
 * 保存 / 反馈的结果提示放在两栏上方——两边的操作都在那里出结果，
 * 放在任何一栏里，点另一栏的按钮时提示就落在视线之外。
 */
export function StyleSettings({
  userId,
  initialConfig,
  initialTone,
  onboarded,
  arm,
  sessionId,
}: {
  userId: string;
  initialConfig: StyleConfig;
  /** 语气（2026-10-07 起设置页也能改，口径 F107）。库里认不出的值按默认档显示。 */
  initialTone: string;
  onboarded: boolean;
  arm: Arm;
  /** 一次页面加载一个，**由服务端生成**（见 `page.tsx`）。 */
  sessionId: string;
}) {
  const router = useRouter();
  const [saved, setSaved] = useState<StyleConfig>(initialConfig);
  const [draft, setDraft] = useState<StyleConfig>(initialConfig);
  const startTone: Tone = (TONES as readonly string[]).includes(initialTone) ? (initialTone as Tone) : "neutral";
  const [savedTone, setSavedTone] = useState<Tone>(startTone);
  const [draftTone, setDraftTone] = useState<Tone>(startTone);
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

  const dirty = AXIS_IDS.some((axis) => draft[axis] !== saved[axis]) || draftTone !== savedTone;
  /** 草稿恰好是哪一种讲法。调过任何一样就不是任何一种——那时四张卡片都不亮。 */
  const matched = presetMatching(draft, draftTone);

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

  /**
   * ⚠️ 语气**不进** `style_config_changed` 事件：那条事件的 before/after 是四条轴
   * （A5 预登记的「配置修改频率」按它算），语气是 2026-09-19 才加的维度，
   * 塞进去会让新旧两批事件的 after 形状不一样。语气变没变看 `style_config.tone` 本身。
   */
  async function write(config: StyleConfig, message: string, event: StyleEvent, tone: Tone = savedTone) {
    setBusy(true);
    setError(null);
    setNotice(null);

    // 只发四条轴。**不发 f1_arm**（口径 F10：改它抛 23514），也不动 onboarded_at——
    // 那是「完成过 onboarding」的时间戳，不是「最后修改时间」，改配置不该覆盖它。
    const { data, error: err } = await createClient()
      .from("style_config")
      .update({ ...config, tone })
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
    setSavedTone(tone);
    setDraftTone(tone);
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
    <div data-events-pending={eventState.pending} data-events-dropped={eventState.dropped}>
      <PageHeader
        eyebrow="讲解风格"
        title="按你读得进去的方式讲"
        lead="讲解按这四条来。改完保存即刻生效，下一次讲解就用新的。"
      />

      {!onboarded && (
        <p className="notice notice--info mb-6">
          你还没走过一遍风格设置引导。现在这份是默认配置——
          <Link
            href={arm === "text" ? "/onboarding/style-text" : "/onboarding"}
            className="link font-[var(--fw-medium)]"
          >
            去走一遍
          </Link>
          会更贴合你的偏好。
        </p>
      )}

      {(notice || error) && (
        <div className="mb-6 flex flex-col gap-3">
          {notice && (
            <p role="status" data-notice className="notice notice--info">
              {notice}
            </p>
          )}
          {error && (
            <p role="alert" className="notice notice--danger">
              {error}
            </p>
          )}
        </div>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,360px)]">
        {/* ================================================================ 四条轴 */}
        <section className="card enter p-2">
          <div className="px-3 pt-3 pb-4">
            <p className="t-eyebrow"># 讲法</p>
            <h2 className="t-title mt-1">选一种，或者逐项改</h2>
          </div>

          {/* 四种讲法：点一下把下面五项一起换掉（还要按「保存」）。逐项改过之后就不再是任何一种。 */}
          <div className="grid gap-2 px-1 pb-3 sm:grid-cols-2" data-field="presets">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                data-preset={p.id}
                data-selected={matched?.id === p.id}
                aria-pressed={matched?.id === p.id}
                disabled={busy}
                onClick={() => {
                  setDraft({ ...p.config });
                  setDraftTone(p.tone);
                }}
                className="choice cursor-pointer p-3 text-left"
              >
                <span className="t-ui block">{p.name}</span>
                <span className="t-caption mt-1 block">{p.summary}</span>
              </button>
            ))}
          </div>

          <div className="panel divide-y divide-[var(--c-border-row)]">
            <fieldset data-axis="tone" className="p-4 sm:p-5">
              <legend className="contents">
                <span className="t-heading mb-3 flex flex-wrap items-center gap-2">
                  语气
                  <span className="t-caption font-[var(--fw-regular)]">只作倾向，不做判定</span>
                </span>
              </legend>
              <div className="grid gap-2 sm:grid-cols-3">
                {TONES.map((t) => (
                  <button
                    key={t}
                    type="button"
                    data-value={t}
                    data-selected={draftTone === t}
                    aria-pressed={draftTone === t}
                    disabled={busy}
                    onClick={() => setDraftTone(t)}
                    className="choice cursor-pointer p-3 text-left"
                  >
                    <span className="t-ui block">{TONE_LABEL[t]}</span>
                    <span className="t-caption mt-1 block">{TONE_HINT[t]}</span>
                  </button>
                ))}
              </div>
            </fieldset>
            {AXIS_IDS.map((axis) => (
              <fieldset key={axis} data-axis={axis} className="p-4 sm:p-5">
                <legend className="contents">
                  <span className="t-heading mb-3 flex flex-wrap items-center gap-2">
                    {AXIS_LABEL[axis]}
                    {ENFORCED[axis] ? (
                      <span className="chip chip--accent h-6 px-2 text-[11.5px]">会被自动审查</span>
                    ) : (
                      <span className="t-caption font-[var(--fw-regular)]">只作倾向，不做判定</span>
                    )}
                  </span>
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
                        className="choice cursor-pointer p-3 text-left"
                      >
                        <span className="t-ui block">
                          {(VALUE_LABEL[axis] as Record<string, string>)[value]}
                        </span>
                        <span className="t-caption mt-1 block">
                          {(VALUE_HINT[axis] as Record<string, string>)[value]}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </fieldset>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3 px-3 pt-4 pb-3">
            <button
              type="button"
              className="btn btn--primary"
              disabled={!dirty || busy}
              onClick={() => void write(draft, "已保存。", manualChange(saved, draft), draftTone)}
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
                setDraftTone(savedTone);
                setNotice(null);
              }}
              data-action="revert"
            >
              撤销改动
            </button>
            <span className="t-meta">{dirty ? "有未保存的改动" : "没有未保存的改动"}</span>
          </div>
        </section>

        {/* ================================================================ 反馈 */}
        <aside className="card enter-late p-2 lg:sticky lg:top-[84px]">
          <div className="px-3 pt-3 pb-4">
            <p className="t-eyebrow"># 反馈</p>
            <h2 className="t-title mt-1">读完一段讲解之后</h2>
            <p className="t-meta mt-1">
              点一下就按下面的规则调整配置 —— 除「例子没懂」外，改的都是<b>以后所有讲解</b>。
            </p>
          </div>

          <div className="panel p-4">
            <div className="grid grid-cols-2 gap-2">
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
              <p className="notice notice--warn mt-3 text-[13px]">
                有未保存的改动，反馈按钮先停用——它作用在已保存的配置上，两边同时改会互相覆盖。
                先保存或撤销。
              </p>
            )}

            <ul className="t-caption mt-4 list-disc space-y-1.5 pl-4">
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
            </ul>

            <p className="notice notice--info mt-4 text-[13px]">
              只是<b>某一段</b>没看懂、不想以后都讲浅？别点「讲太深」——
              直接在那个对话里<b>追问</b>（比如「没懂，能简单点吗」）：会换个角度、带例子重讲那一点，不改配置。
            </p>
          </div>
        </aside>
      </div>

      {/* 埋点失败不影响配置本身，但要说出来——「还在重试」「等下次」「永久拒绝」
          三种的处置完全不同，所以分开说，不合成一句。 */}
      {(eventState.pending > 0 || eventState.dropped > 0) && (
        <p role="status" data-event-error className="notice notice--info mt-6">
          {eventState.dropped > 0 && (
            <b>有 {eventState.dropped} 条埋点被永久拒绝（不是网络问题，请把这句话报给开发）。</b>
          )}
          {eventState.pending > 0 &&
            (eventState.stalled
              ? `还有 ${eventState.pending} 条埋点没写进去，网络恢复后会自动重试。`
              : `还有 ${eventState.pending} 条埋点在写入中。`)}
          {eventState.lastError ? ` 最后一次的错误：${eventState.lastError}` : ""}
          <br />
          <span className="opacity-80">你的配置不受影响。</span>
        </p>
      )}
    </div>
  );
}
