"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConfigSummary } from "@/app/components/config-summary";
import { AXIS_IDS, AXIS_LABEL, VALUE_LABEL, type StyleConfig } from "@/lib/style/axes";
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
 */

const MAX_CHARS = 800;

type AxisOutcome = { value: string; source: "text" | "default"; reason: string };

/** 这次调用用了哪个模型与参数。阶段 4 的事件字段照抄它（见 `lib/style/text-arm.ts`）。 */
type CallInfo = { model: string; effort: string; thinking: string; max_tokens: number };

type Mapped = { config: StyleConfig; axes: Record<string, AxisOutcome>; call?: CallInfo };

export function TextOnboarding({
  userId,
  alreadyOnboarded,
}: {
  userId: string;
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

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const res = await fetch("/api/style/from-text", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ description: text }),
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
        className="enter mt-10"
      >
        <h1 className="t-display">配置好了</h1>
        <p className="t-body mt-3 max-w-[62ch]">
          之后每次讲解都会按它来，随时可以改。
        </p>
        <div className="mt-6">
          <ConfigSummary config={saved} />
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
          确认之后就按它来。<b>这一屏只读</b>——想调哪一条，保存后去风格设置改，那里每条轴都能单独选。
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
