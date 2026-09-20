"use client";

import { useEffect, useRef, useState } from "react";

import { BOARDS, resolveTaste } from "@/lib/style/taste";
import { createClient } from "@/lib/supabase/client";

/**
 * 「调整讲法」（2026-09-20）。**风格矫正从 onboarding 搬到了讲解里。**
 *
 * ## 为什么搬
 *
 * onboarding 原来有 6 组成对比较，用户的判断是：「不论多短，用户都无法从两组
 * 如此密集的文字中获得有效信息，甚至于现在这个信息还不是用户领域。」
 *
 * 这句话指出的不是排版问题，是**时机问题**：开场时用户手上没有任何具体的东西，
 * 只能对着两段陌生领域的文字猜自己喜欢哪个。而他读完**自己课件的一段真实讲解**
 * 之后，「太长了」「这里我想看推导」是当场就有的反应——那才是可信的偏好信号。
 *
 * 所以：开场只留标签（快、无需阅读理解），矫正放在第一次拿到讲解之后。
 *
 * ## 只弹一次，且**记在这台设备上**
 *
 * 弹第二次就成了骚扰。用 localStorage 记——它可能丢（隐私模式、清数据），
 * 丢了就再弹一次，代价是一次多余的提示，**不是一次错误的配置**。
 * 不记进数据库是因为这件事没必要占一次写入，也没必要跨设备一致。
 *
 * ## 两条路都通向同一份配置
 *
 * 标签（点几个）与一句话（说出来）写的是同一行 `style_config`。
 * 先写标签、再让服务端读着**刚写完的那一行**去映射那句话——
 * 反过来的话，那句话会以旧配置为基线，用户刚点的标签当场被盖掉。
 */

const SEEN_KEY = "relecture.style-nudge.seen";

export function StyleNudge({
  userId,
  sessionId,
  /** 这个会话里已经答出来的轮数。**≥1 才提示**——没看过讲解就没有可反应的东西。 */
  answered,
}: {
  userId: string;
  sessionId: string;
  answered: number;
}) {
  const [seen, setSeen] = useState(true); // 先当作看过，避免 SSR/hydrate 两侧不一致
  const [open, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    // localStorage 只能在浏览器读，而且**可能抛**（隐私模式）。抛了就当没看过。
    try {
      setSeen(window.localStorage.getItem(SEEN_KEY) === "1");
    } catch {
      setSeen(false);
    }
  }, []);

  function dismiss() {
    setSeen(true);
    try {
      window.localStorage.setItem(SEEN_KEY, "1");
    } catch {
      // 存不进去只意味着下次还会提示一次，不影响任何配置。
    }
  }

  const showNudge = answered >= 1 && !seen && !open;

  return (
    <>
      {showNudge && (
        <div className="nudge" data-field="style-nudge">
          {/* 背景压暗，提示保持正常亮度——用户的注意力此刻在刚读完的那段讲解上，
              不压暗的话这条提示会被当成页面的一部分而略过。 */}
          <button
            type="button"
            className="nudge__backdrop"
            aria-label="先不调整"
            onClick={dismiss}
          />
          <div className="nudge__card" role="dialog" aria-label="调整讲解风格">
            <p className="t-title text-[16px]">需要调整讲解风格？</p>
            <p className="t-meta mt-1">刚读完一段，这时候最说得清想要什么。</p>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className="btn btn--primary btn--sm"
                data-field="open-style-adjust"
                onClick={() => {
                  dismiss();
                  setOpen(true);
                  dialogRef.current?.showModal();
                }}
              >
                调整讲法
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={dismiss}>
                先不用
              </button>
            </div>
          </div>
        </div>
      )}

      <button
        type="button"
        className="btn btn--ghost btn--sm"
        data-field="style-adjust-entry"
        onClick={() => {
          setOpen(true);
          dialogRef.current?.showModal();
        }}
      >
        调整讲法
      </button>

      <AdjustDialog
        ref={dialogRef}
        userId={userId}
        sessionId={sessionId}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

function AdjustDialog({
  ref,
  userId,
  sessionId,
  onClose,
}: {
  ref: React.RefObject<HTMLDialogElement | null>;
  userId: string;
  sessionId: string;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  function toggle(tagId: string, boardId: string, max: number) {
    setPicked((prev) => {
      if (prev.includes(tagId)) return prev.filter((x) => x !== tagId);
      const board = BOARDS.find((b) => b.id === boardId)!;
      const ids = new Set(board.tags.map((t) => t.id));
      if (prev.filter((x) => ids.has(x)).length >= max) return prev;
      return [...prev, tagId];
    });
  }

  async function apply() {
    if (busy) return;
    setBusy(true);
    setError(null);
    setDone(null);

    const supabase = createClient();

    // ---- 1. 标签。只写**标签真的定下来的那几条轴**，其余原样不动。
    if (picked.length > 0) {
      const taste = resolveTaste(picked);
      const patch: Record<string, string> = { tone: taste.tone };
      for (const [axis, value] of Object.entries(taste.priors)) {
        if (value) patch[axis] = value;
      }
      const { error: e } = await supabase
        .from("style_config")
        .update(patch)
        .eq("user_id", userId);
      if (e) {
        setBusy(false);
        return setError(`保存标签失败：${e.message}（${e.code ?? "?"}）`);
      }
    }

    // ---- 2. 那句话。**在标签写完之后**：服务端读的是库里那一行当基线，
    // 顺序反了的话这句话会以旧配置为基线，把刚点的标签盖掉。
    if (text.trim()) {
      try {
        const r = await fetch("/api/style/from-text", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: text.trim(), session_id: sessionId }),
        });
        const body = await r.json();
        if (!r.ok) {
          setBusy(false);
          return setError(body?.error ?? `调整失败（HTTP ${r.status}）`);
        }
        // 服务端**只做映射，不写库**（它至今没有写 style_config 的职责）。
        // 写在这里，用的是它返回的那份完整配置——里面已经含了「没提到的轴保持原样」。
        const { error: e } = await supabase
          .from("style_config")
          .update(body.config)
          .eq("user_id", userId);
        if (e) {
          setBusy(false);
          return setError(`保存失败：${e.message}（${e.code ?? "?"}）`);
        }
      } catch (err) {
        setBusy(false);
        return setError(`连不上：${String(err)}`);
      }
    }

    if (picked.length === 0 && !text.trim()) {
      setBusy(false);
      return setError("还没选也没说——选几个标签，或者直接写一句。");
    }

    setBusy(false);
    setPicked([]);
    setText("");
    // **说清楚它从什么时候开始生效。** 不说的话用户会回头看上一段讲解，
    // 发现没变，以为没保存成功。
    setDone("已经改好了。下一次提问开始按新的讲法。");
  }

  return (
    <dialog ref={ref} className="dialog" data-field="style-adjust" onClose={onClose}>
      <div className="dialog__head">
        <h2 className="t-title text-[17px]">调整讲法</h2>
        <form method="dialog">
          <button className="btn btn--ghost btn--sm">关闭</button>
        </form>
      </div>
      <div className="dialog__body">
        {BOARDS.map((board) => (
          <section key={board.id} className="mb-5">
            <p className="field-label">{board.title}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {board.tags.map((tag) => (
                <button
                  key={tag.id}
                  type="button"
                  className="choice"
                  data-field="adjust-tag"
                  aria-pressed={picked.includes(tag.id)}
                  onClick={() => toggle(tag.id, board.id, board.max)}
                >
                  {tag.label}
                </button>
              ))}
            </div>
          </section>
        ))}

        <section className="mb-4">
          <p className="field-label">或者直接说一句</p>
          <textarea
            className="input mt-2 min-h-[72px]"
            data-field="adjust-text"
            maxLength={800}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="输入你想要的讲法，我们来调整。比如：再短一点，先说结论，多举个例子。"
          />
          <p className="t-caption mt-1">没提到的部分保持原样，不会被一起改掉。</p>
        </section>

        {error && (
          <p role="alert" className="notice notice--danger text-[13px]" data-field="adjust-error">
            {error}
          </p>
        )}
        {done && (
          <p className="notice notice--info text-[13px]" data-field="adjust-done">
            {done}
          </p>
        )}

        <button
          type="button"
          className="btn btn--primary mt-2"
          data-field="adjust-apply"
          disabled={busy}
          onClick={() => void apply()}
        >
          {busy ? "正在调整…" : "应用"}
        </button>
      </div>
    </dialog>
  );
}
