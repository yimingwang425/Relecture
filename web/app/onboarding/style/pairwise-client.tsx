"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConfigSummary } from "@/app/components/config-summary";
import { AXIS_LABEL, type StyleConfig,
  type Arm,
} from "@/lib/style/axes";
import { createDwellClock, type DwellClock } from "@/lib/style/dwell";
import {
  createEventQueue,
  onboardingCompleted,
  pairChosen,
  pairShown,
  type EventQueue,
  type EventQueueState,
  type StyleEvent,
} from "@/lib/style/events";
import { TOTAL_STEPS, configFromPicks, type Picks } from "@/lib/style/flow";
import { stepAtWithPriors } from "@/lib/style/taste";
import { Markdown } from "@/lib/style/markdown";
import type { PairView } from "@/lib/style/pairs";
import { createClient } from "@/lib/supabase/client";

/**
 * 6 次成对比较的交互。
 *
 * ## 两个刻意的设计
 *
 * 1. **不显示档位标签（口径 F31）。** 比较时两栏只写「讲法 A / 讲法 B」，不写 `P0` / `讲到推导`。
 *    显示标签的话，用户会按标签的字面意思挑（谁不想要「讲到推导」？），
 *    那次点击就不再是对**输出**的偏好——而 F01 要的归因正是「他选的是这一维上的差异」。
 *    档位在**收尾屏**亮出来。
 *
 *    ⚠️ **F31 的适用范围到比较页为止。** `/settings/style` 必须显示可理解的标签——
 *    用户在那里是直接改配置，没有两个样例可比，标签是唯一的信息来源；
 *    把设置页也做成盲选是把这条口径用过头。
 *
 * 2. **走完 6 步才写库，中途不落盘。** 每步都写会产生半截配置：
 *    详细程度已定、深度还没问，而这行配置已经在驱动讲解了。
 *    一次写入还让「onboarding 完成率」有个干净的定义——`onboarded_at` 非空即完成。
 *
 * 3. **左右位置每步随机**，并记下哪一档在左（`leftVariant`）。
 *
 *    位置偏好在二选一里是已知的强效应。位置固定时，一个惯于选左边的用户会稳定产出
 *    一份**系统性偏斜**的配置——而这份配置看起来完全正常，任何指标都不会红。
 *    这与 F23 里「P0/P1 分不开时那次点击更像噪声而非偏好」是同一个担心，
 *    只是这次偏差**有方向**，比噪声更糟。
 *
 *    **它不能事后补**：位置未随机化的那批数据，事后无法从中分离位置效应，
 *    而第一批内测用户恰好是最需要理解的那批。所以必须早于任何真实用户。
 *
 *    **随机发生在服务端**（`page.tsx` 抽好当 prop 传进来），组件内不许有随机：
 *    客户端组件的渲染在 SSR 与 hydrate 各跑一次，渲染期取随机数会让两次分叉
 *    （React #418），用户看到／点到的是哪一版就不确定了。
 *
 * 4. **一步一个事件**（口径 F37，阶段 4 接线）。每步渲染发 `style_pair_shown`、
 *    点选发 `style_pair_chosen`，两条都带 `left_variant`。
 *
 *    只记最终结果的代价是两样东西一起丢：**弃流点**（onboarding 完成率是 A5
 *    预登记的三个指标之一，只知道完成率不知道卡在哪等于没法改）与**位置效应**
 *    （没有逐步的 `left_variant`，上面第 3 条那次随机化就白做了）。
 *
 *    停留时长只采集不判读（口径 F38）：`dwell_ms` + `hidden_ms` 两个数分开记，
 *    **代码里没有任何毫秒阈值常量**，判读标准等数据落盘后单独立。
 *
 *    代价：**「同一串选择重放出同一串比较」不再成立**。比较的**内容**仍然确定
 *    （`stepAt` 只看已选的轴），随机的只是左右呈现；一次会话内每步的位置在
 *    首次渲染时定死，重渲染不会跳动。这个代价是知情接受的。
 */
export function PairwiseOnboarding({
  userId,
  arm,
  sessionId,
  pairs,
  question,
  flips,
  alreadyOnboarded,
  priors,
}: {
  userId: string;
  arm: Arm;
  /** 一次页面加载一个，**由服务端生成**（见 `page.tsx`）。 */
  sessionId: string;
  pairs: Record<string, PairView>;
  question: string;
  /**
   * 每步的左右顺序，**由服务端抽好传进来**（见 `page.tsx` 的注释）。
   *
   * 这个组件里**不许有随机**：客户端组件的渲染在 SSR 与 hydrate 各跑一次，
   * 渲染期取随机数必然让两次结果分叉 —— React 报 #418，而且用户看到／点到的
   * 是哪一版不确定，`leftVariant` 就可能记的不是他实际比较的那一版。
   */
  flips: (0 | 1)[];
  alreadyOnboarded: boolean;
  /**
   * 开场标签算出来的先验（`taste.resolveTaste`）。对照臂是空对象。
   *
   * **先验只决定「先问哪一对」，不决定任何一条轴的取值**——轴仍然由这 6 次
   * 选择定。拿先验去跳过比较等于让「他说的」直接变成配置，而那正是不可信的那一半
   * （理由与证据在 `lib/style/taste.ts`）。
   *
   * 空先验时这套排法与旧的固定顺序**逐步相同**（`verify:logic` 十六节断言），
   * 所以对照臂的体验一个字节都没变，A5 两臂仍然可比。
   */
  priors: Partial<Record<string, string>>;
}) {
  const router = useRouter();
  const [picks, setPicks] = useState<Picks>({});
  const [stepIndex, setStepIndex] = useState(1);
  /** 选中的是 `pair.samples` 里的下标，**不是左右位置**——位置会随机，档位不会。 */
  const [selected, setSelected] = useState<0 | 1 | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<StyleConfig | null>(null);

  /**
   * 把服务端传来的顺序**冻在 state 里**。
   *
   * 直接用 prop 也对，但 `commit()` 之后会调 `router.refresh()`，那会让服务端
   * 重新渲染并抽一份新的 `flips`。冻住之后，一次会话内的左右顺序不会因为任何
   * 刷新而变——同一步重渲染（选中、报错、失焦）时位置也不跳。
   */
  const [frozenFlips] = useState<(0 | 1)[]>(flips);

  /**
   * 每一步「谁在左」的记录。阶段 4 的 `style_pair_shown` / `style_pair_chosen`
   * 事件**必须带上 `left_variant`**——没有它，位置效应事后无法从数据里分离出来。
   */
  const [shownLog, setShownLog] = useState<
    { step: number; pairId: string; leftVariant: string; chosenVariant: string }[]
  >([]);
  /** 埋点队列的状态。**不打断 onboarding，但也不静默。** */
  const [eventState, setEventState] = useState<EventQueueState>({
    pending: 0,
    dropped: 0,
    stalled: false,
    lastError: null,
  });

  const [frozenSessionId] = useState(sessionId);

  // 步骤计算放在早退之前：hooks 必须无条件执行，而下面的 effect 要用到它们。
  const step = stepAtWithPriors(stepIndex, picks, priors as Partial<Record<never, string>>);
  const pair = pairs[step.pairId];
  const isLast = stepIndex === TOTAL_STEPS;

  // 左右呈现顺序：flip=0 → samples[0] 在左；flip=1 → samples[1] 在左。
  const order: [0 | 1, 0 | 1] = frozenFlips[stepIndex - 1] === 0 ? [0, 1] : [1, 0];
  const leftVariant = pair.samples[order[0]].variantLabel;

  /**
   * 停留时长的表。**在 effect 里创建，不在渲染里**——`createDwellClock()` 一造出来
   * 就取一次 `performance.now()`，而渲染在 SSR 与 hydrate 各跑一次，
   * 那两次取到的是两条不同的时间轴（同 #418 的形态，只是这次不显示在页面上）。
   */
  const clockRef = useRef<DwellClock | null>(null);
  /** 整段 onboarding 的起点，用于 `style_onboarding_completed` 的总耗时。 */
  const startedAtRef = useRef<number | null>(null);
  /** 已发过 `style_pair_shown` 的步。effect 在 dev 的 StrictMode 下会跑两次。 */
  const shownSentRef = useRef<Set<number>>(new Set());

  /**
   * 事件队列。**每个组件实例一个**，靠 ref 保住——重新建一个会把内存里
   * 还没发出去的那几条丢掉，而那正是这次要修的东西。
   */
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

  // 订阅队列状态 + 挂载时冲一次：上一次页面加载没发完的那几条落在 sessionStorage 里。
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

  // 页面可见性：切走不扣 dwell_ms，但累加 hidden_ms（口径 F38）。
  // 只记一个总数的话，「读了 40 秒」和「挂着 40 秒」永久混在一起。
  useEffect(() => {
    function onVisibility() {
      if (document.visibilityState === "hidden") clockRef.current?.markHidden();
      else clockRef.current?.markVisible();
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // 每进入一步：计时归零 + 发 `style_pair_shown`。
  // 落在 effect 里而不是渲染里，因为「渲染完成」才是 dwell 的起点定义。
  useEffect(() => {
    if (saved) return;
    if (clockRef.current === null) {
      clockRef.current = createDwellClock();
      startedAtRef.current = performance.now();
    } else {
      clockRef.current.reset();
    }
    if (shownSentRef.current.has(stepIndex)) return;
    shownSentRef.current.add(stepIndex);
    void send(
      pairShown({ step: stepIndex, axis: step.axis, pairId: step.pairId, leftVariant }),
    );
    // leftVariant / step 由 stepIndex 与已冻结的 flips 唯一决定，故只依赖 stepIndex。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stepIndex, saved]);

  if (saved) return <DoneScreen config={saved} log={shownLog} eventState={eventState} />;

  async function commit(config: StyleConfig) {
    setBusy(true);
    setError(null);

    // 只发四条轴 + onboarded_at。**不发 f1_arm**——PostgREST 的 PATCH 只带 body 里的列，
    // 不出现的列 new = old，不触发 F10 的守卫。发了它（哪怕值没变）是在给自己找 23514。
    const { data, error: err } = await createClient()
      .from("style_config")
      .update({ ...config, onboarded_at: new Date().toISOString() })
      .eq("user_id", userId)
      .select("user_id, detail_level, use_examples, structure, depth, onboarded_at");

    if (err) {
      setError(`${err.message}（${err.code ?? "?"}）`);
      setBusy(false);
      return;
    }
    // 断言**库里那一行真的变了**，而不是「update 没报错」：RLS 挡住时
    // PostgREST 返回的是 0 行更新且**不报错**，那种「成功」正是要防的。
    if (!data || data.length !== 1) {
      setError(`更新影响了 ${data?.length ?? 0} 行，应为 1 行。配置没有保存。`);
      setBusy(false);
      return;
    }

    // **落库成功之后**才发完成事件——不是「点了完成」。上面那段刚断言过
    // 库里那一行真的变了（影响 1 行），事件挂在这个断言后面才有意义。
    send(
      onboardingCompleted({
        config,
        stepsCompleted: TOTAL_STEPS,
        totalMs: Math.round(performance.now() - (startedAtRef.current ?? performance.now())),
      }),
    );

    setSaved(config);
    setBusy(false);
    router.refresh();
  }

  function next() {
    if (selected === null) return;
    // 记的是**档位**，不是左右位置——位置随机之后，只有这个还稳定。
    const chosen = pair.samples[selected].variantLabel;
    const nextPicks: Picks = { ...picks, [step.axis]: chosen };

    // 读数在改 state 之前取：setStepIndex 之后 leftVariant / step 都会变成下一步的。
    const { dwell_ms, hidden_ms } = clockRef.current?.read() ?? { dwell_ms: 0, hidden_ms: 0 };
    void send(
      pairChosen({
        step: stepIndex,
        axis: step.axis,
        pairId: step.pairId,
        leftVariant,
        chosenVariant: chosen,
        dwellMs: dwell_ms,
        hiddenMs: hidden_ms,
      }),
    );

    setPicks(nextPicks);
    setSelected(null);
    setShownLog((log) => [
      ...log,
      { step: stepIndex, pairId: step.pairId, leftVariant, chosenVariant: chosen },
    ]);

    if (isLast) void commit(configFromPicks(nextPicks));
    else setStepIndex(stepIndex + 1);
  }

  return (
    <div
      className="enter"
      data-events-pending={eventState.pending}
      data-events-dropped={eventState.dropped}
    >
      <header className="mt-10 mb-6 sm:mt-14">
        <p className="t-eyebrow mb-3 flex flex-wrap items-center gap-2" data-step={stepIndex}>
          <span className="chip kbd-count">
            第 {stepIndex} / {TOTAL_STEPS} 步
          </span>
          <span>
            {stepIndex < TOTAL_STEPS ? `选完还剩 ${TOTAL_STEPS - stepIndex} 步` : "最后一步"}
            {" · 这一步在比较"}
            <b className="text-[var(--c-ink-soft)]">{AXIS_LABEL[step.axis]}</b>
          </span>
        </p>
        <h1 className="t-display enter-wipe">哪一段讲解你更想读？</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          两段回答的是同一个提问：「{question}」。它们的差别只在一个维度上，其余都一样。
          <b>只按读起来的感觉选</b>，不用去猜差在哪。
        </p>
      </header>

      {alreadyOnboarded && (
        <p className="notice notice--info mb-6">
          你之前已经完成过一次。走完这 6 步会<b>覆盖</b>现在的配置；只想改一两条的话，
          去 <Link href="/settings/style" className="link font-[var(--fw-medium)]">风格设置</Link> 更省事。
        </p>
      )}

      <Progress current={stepIndex} />

      {/*
        `role="radiogroup"` + 每栏一个隐藏 radio，而不是两个大 <button>：
        正文有一两千字，包进 button 会被读屏当成**一整条按钮标签**念出来，
        而且长文本落在 button 里不便选中复制。radio 承担「选哪个」，
        正文就是普通可选中的正文。

        `data-left-variant` 供人工/回归核对**这一步谁在左**——位置是随机的，
        没有这个属性就没法在页面上确认随机化真的在发生。
      */}
      <div
        role="radiogroup"
        aria-label={`第 ${stepIndex} 步：两段讲解，选一段`}
        data-left-variant={leftVariant}
        className="mt-6 grid items-start gap-5 lg:grid-cols-2"
      >
        {order.map((sampleIndex, position) => {
          const sample = pair.samples[sampleIndex];
          const letter = position === 0 ? "A" : "B";
          const active = selected === sampleIndex;
          return (
            <label
              key={sample.sampleId}
              data-choice={letter.toLowerCase()}
              data-selected={active}
              className="choice flex cursor-pointer flex-col p-5 sm:p-6"
            >
              <input
                type="radio"
                name={`step-${stepIndex}`}
                className="sr-only"
                checked={active}
                onChange={() => setSelected(sampleIndex)}
              />
              <span className="t-heading mb-4 flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-[var(--fw-bold)]"
                  style={{
                    background: active ? "var(--c-accent)" : "var(--c-border-row)",
                    color: active ? "#fff" : "var(--c-ink-soft)",
                  }}
                >
                  {letter}
                </span>
                讲法 {letter}
              </span>
              <div className="t-body t-sample">
                <Markdown text={sample.text} />
              </div>
            </label>
          );
        })}
      </div>

      {/* 发现 C（2026-09-09 拍板：接受现状 + 文案解释）。
          16 个样例里 12 个以「片段里没有涉及××」收尾，4 个点到没喂过的第 3/6 页。
          两次机制假设（加长课件 / 去前向引用）都被证伪，杠杆不在素材上；
          样例是模型在生产 prompt 形状下真实生成的（口径 F18/F19），**不截尾、不重写**，
          所以这里用一句话解释它，而不是把它藏起来。 */}
      <p className="t-meta mt-5 max-w-[78ch] leading-relaxed">
        这两段是模型在一份<b>示例课件</b>的同一批片段上真实生成的，不是手写的范文。片段是节选，
        所以结尾常出现「这部分没有涉及××，需要补充材料」，偶尔还会点到没给出的页码——
        那是讲解助手在如实说明它手里有什么，<b>与你自己的课件无关</b>。比较时看讲法就好。
      </p>

      {error && (
        <p
          role="alert"
          className="notice notice--danger mt-5"
        >
          {error}
        </p>
      )}

      {/* 埋点失败**不打断 onboarding**（用户的配置比我们的数据重要），
          但也不静默。三个数各有含义，别合成一个：
          `pending` 还在队列里（会继续重试）· `stalled` 本轮重试用尽（等下次）·
          `dropped` **永久失败已放弃**（只可能是内容或权限问题，是真 bug）。 */}
      <EventNotice state={eventState} />

      {/* 操作条吸底：两栏各一两千字，读完一栏再滚到页底找按钮是这一页最累的一步。 */}
      <div className="sticky bottom-4 z-20 mt-8">
        <div className="panel card--raised flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
          <button
            type="button"
            className="btn btn--primary"
            disabled={selected === null || busy}
            onClick={next}
            data-action={isLast ? "finish" : "next"}
          >
            {busy ? "保存中…" : isLast ? "完成，保存配置" : "下一步"}
          </button>
          <span className="t-meta">{selected === null ? "先选一段" : "可以改选，选定后继续"}</span>
          <span className="t-caption kbd-count ml-auto hidden sm:inline">
            {stepIndex} / {TOTAL_STEPS}
          </span>
        </div>
      </div>
    </div>
  );
}

function Progress({ current }: { current: number }) {
  return (
    <div className="flex gap-1.5" aria-hidden>
      {Array.from({ length: TOTAL_STEPS }, (_, i) => (
        <span
          key={i}
          className="h-1 flex-1 rounded-full"
          style={{ background: i < current ? "var(--c-accent)" : "var(--c-border-row)" }}
        />
      ))}
    </div>
  );
}

function DoneScreen({
  config,
  log,
  eventState,
}: {
  config: StyleConfig;
  /** 6 步的呈现与选择记录。`data-left-variants` 供人工核对随机化真的在发生。 */
  log: { step: number; pairId: string; leftVariant: string; chosenVariant: string }[];
  eventState: EventQueueState;
}) {
  return (
    <div
      data-state="saved"
      data-left-variants={log.map((l) => l.leftVariant).join(",")}
      data-events-pending={eventState.pending}
      data-events-dropped={eventState.dropped}
      className="enter mx-auto max-w-[720px]"
    >
      <header className="mt-10 mb-6 sm:mt-14">
        <p className="t-eyebrow mb-3">
          {TOTAL_STEPS} / {TOTAL_STEPS} 步 · 完成
        </p>
        <h1 className="t-display enter-wipe">配置好了</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          下面是这 6 次选择得到的风格。之后每次讲解都会按它来，随时可以改。
        </p>
      </header>

      <div className="card p-2">
        <div className="px-3 pt-3 pb-4">
          <p className="t-eyebrow"># 我的讲解风格</p>
        </div>
        <div className="tabpanel rounded-[10px] p-3">
          <ConfigSummary config={config} />
        </div>
      </div>

      <EventNotice state={eventState} className="mt-6" />

      <div className="mt-6 flex flex-wrap gap-3">
        <Link href="/" className="btn btn--primary no-underline">
          去我的课程
        </Link>
        <Link href="/settings/style" className="btn btn--ghost no-underline">
          去改配置
        </Link>
      </div>
    </div>
  );
}

/**
 * 埋点队列的状态条。**三个数分开说，不合成一句。**
 *
 * `pending`（还在重试）、`stalled`（本轮重试用尽，等下次页面加载）、
 * `dropped`（永久失败已放弃）三种的处置完全不同：前两个等一等就好，
 * 第三个是**我们构造了一条坏事件**，是真 bug。合成「埋点出问题了」一句的话，
 * 最该被看见的那种反而最不显眼。
 */
function EventNotice({ state, className = "mt-5" }: { state: EventQueueState; className?: string }) {
  if (state.pending === 0 && state.dropped === 0) return null;
  return (
    <p
      role="status"
      data-event-error
      className={`notice notice--info ${className}`}
    >
      {state.dropped > 0 && (
        <b>有 {state.dropped} 条埋点被永久拒绝（不是网络问题，请把这句话报给开发）。</b>
      )}
      {state.pending > 0 &&
        (state.stalled
          ? `还有 ${state.pending} 条埋点没写进去，网络恢复后会自动重试。`
          : `还有 ${state.pending} 条埋点在写入中。`)}
      {state.lastError ? ` 最后一次的错误：${state.lastError}` : ""}
      <br />
      <span className="t-meta">你的配置不受影响，可以继续。</span>
    </p>
  );
}
