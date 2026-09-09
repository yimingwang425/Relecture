"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConfigSummary } from "@/app/components/config-summary";
import { AXIS_LABEL, type StyleConfig } from "@/lib/style/axes";
import { TOTAL_STEPS, configFromPicks, makeFlips, stepAt, type Picks } from "@/lib/style/flow";
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
 *    代价：**「同一串选择重放出同一串比较」不再成立**。比较的**内容**仍然确定
 *    （`stepAt` 只看已选的轴），随机的只是左右呈现；一次会话内每步的位置在
 *    首次渲染时定死，重渲染不会跳动。这个代价是知情接受的。
 */
export function PairwiseOnboarding({
  userId,
  pairs,
  question,
  alreadyOnboarded,
}: {
  userId: string;
  pairs: Record<string, PairView>;
  question: string;
  alreadyOnboarded: boolean;
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
   * 每步一次抛硬币，决定 `samples[0]` 放左还是放右。
   *
   * 用 `useState` 的惰性初始化一次性抛完 6 次，而不是在渲染里现抛：
   * 渲染里抛会让每次重渲染（选中、报错、失焦）都换一次左右，用户点着点着位置就变了。
   */
  const [flips] = useState<(0 | 1)[]>(() => makeFlips(TOTAL_STEPS));

  /**
   * 每一步「谁在左」的记录。阶段 4 的 `style_pair_shown` / `style_pair_chosen`
   * 事件**必须带上 `left_variant`**——没有它，位置效应事后无法从数据里分离出来。
   */
  const [shownLog, setShownLog] = useState<
    { step: number; pairId: string; leftVariant: string; chosenVariant: string }[]
  >([]);

  if (saved) return <DoneScreen config={saved} log={shownLog} />;

  const step = stepAt(stepIndex, picks);
  const pair = pairs[step.pairId];
  const isLast = stepIndex === TOTAL_STEPS;

  // 左右呈现顺序：flip=0 → samples[0] 在左；flip=1 → samples[1] 在左。
  const order: [0 | 1, 0 | 1] = flips[stepIndex - 1] === 0 ? [0, 1] : [1, 0];
  const leftVariant = pair.samples[order[0]].variantLabel;

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

    setSaved(config);
    setBusy(false);
    router.refresh();
  }

  function next() {
    if (selected === null) return;
    // 记的是**档位**，不是左右位置——位置随机之后，只有这个还稳定。
    const chosen = pair.samples[selected].variantLabel;
    const nextPicks: Picks = { ...picks, [step.axis]: chosen };
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
    <div className="enter mt-10">
      <header className="mb-6">
        <p className="t-meta mb-2" data-step={stepIndex}>
          第 {stepIndex} / {TOTAL_STEPS} 步
          {stepIndex < TOTAL_STEPS ? `（选完还剩 ${TOTAL_STEPS - stepIndex} 步）` : "（最后一步）"}
          {" · 这一步在比较"}
          <b>{AXIS_LABEL[step.axis]}</b>
        </p>
        <h1 className="t-display">哪一段讲解你更想读？</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          两段回答的是同一个提问：「{question}」。它们的差别只在一个维度上，其余都一样。
          <b>只按读起来的感觉选</b>，不用去猜差在哪。
        </p>
      </header>

      {alreadyOnboarded && (
        <p className="panel mb-6 bg-[var(--c-accent-wash)] p-3 text-sm text-[var(--c-accent-ink)]">
          你之前已经完成过一次。走完这 6 步会<b>覆盖</b>现在的配置；只想改一两条的话，
          去 <Link href="/settings/style" className="underline underline-offset-4">风格设置</Link> 更省事。
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
          className="panel mt-5 border-[color:var(--c-danger)]/30 bg-[var(--c-danger-wash)] p-3 text-sm text-[var(--c-danger)]"
        >
          {error}
        </p>
      )}

      <div className="mt-6 flex items-center gap-4">
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
}: {
  config: StyleConfig;
  /** 6 步的呈现与选择记录。阶段 4 的埋点从这里取 `left_variant`。 */
  log: { step: number; pairId: string; leftVariant: string; chosenVariant: string }[];
}) {
  return (
    <div
      data-state="saved"
      data-left-variants={log.map((l) => l.leftVariant).join(",")}
      className="enter mt-10"
    >
      <h1 className="t-display">配置好了</h1>
      <p className="t-body mt-3 max-w-[62ch]">
        下面是这 6 次选择得到的风格。之后每次讲解都会按它来，随时可以改。
      </p>

      <div className="mt-6">
        <ConfigSummary config={config} />
      </div>

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
