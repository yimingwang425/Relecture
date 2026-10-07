"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { ADJUSTMENTS, applyAdjustments, toggleAdjustment } from "@/lib/style/adjust";
import { AXIS_IDS, type StyleConfig } from "@/lib/style/axes";
import { TONES, type Tone } from "@/lib/style/taste";
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
 * ## 2026-09-22：压暗整屏那一版去掉了
 *
 * 原来第一次拿到讲解之后会压暗背景弹一条提示。**太突兀**——用户刚读完一段讲解，
 * 屏幕突然暗下来，第一反应是「出什么事了」，而且那一刻注意力正在正文上，
 * 挡住它只会被急着关掉。
 *
 * 「这个功能在哪」改由进页面时的三步引导讲（`app/components/tour.tsx`），
 * 这里只留一个**常驻在输入框旁边**的入口——用户想改的时候它一直在。
 *
 * ## 两条路都通向同一份配置
 *
 * 按钮（点几个）与一句话（说出来）写的是同一行 `style_config`。
 * 先写按钮、再让服务端读着**刚写完的那一行**去映射那句话——
 * 反过来的话，那句话会以旧配置为基线，用户刚点的按钮当场被盖掉。
 *
 * ## 2026-10-07：按钮换成相对调整，改完可以当场重讲（口径 F107）
 *
 * 原来的按钮是开场那两块标签（「口语」「大白话」「先给结论」……），点了之后
 * 按多数票重算语气、标签整条覆盖轴——用户想的是「比现在短一点」，拿到的是「被重置」。
 * 现在每个按钮只把一样东西往一个方向挪一格（`lib/style/adjust.ts`）。
 *
 * 改完之前只说「下一次提问开始生效」，用户看不到改了之后是什么样。现在多一个
 * 「按新讲法重讲上一个问题」：调用方给了 `onRedo` 才出现，点了就在这个会话里再问一次。
 */

export function StyleNudge({
  userId,
  sessionId,
  onRedo,
}: {
  userId: string;
  sessionId: string;
  /** 「按新讲法重讲上一个问题」。没有可重讲的问题时不传，按钮就不出现。 */
  onRedo?: () => void;
}) {
  const [, setOpen] = useState(false);
  const dialogRef = useRef<HTMLDialogElement>(null);

  return (
    <>
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
        onRedo={onRedo}
        onClose={() => setOpen(false)}
      />
    </>
  );
}

function AdjustDialog({
  ref,
  userId,
  sessionId,
  onRedo,
  onClose,
}: {
  ref: React.RefObject<HTMLDialogElement | null>;
  userId: string;
  sessionId: string;
  onRedo?: () => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const router = useRouter();

  async function apply() {
    if (busy) return;
    if (picked.length === 0 && !text.trim()) {
      return setError("还没选也没说——点一两个，或者直接写一句。");
    }
    setBusy(true);
    setError(null);
    setDone(null);

    const supabase = createClient();
    let edgeNote = "";

    // ---- 1. 按钮。先读**现在的**配置，在它上面挪；没点到的原样写回。
    if (picked.length > 0) {
      const { data: rows, error: re } = await supabase
        .from("style_config")
        .select("detail_level, use_examples, structure, depth, tone")
        .eq("user_id", userId);
      if (re || !rows || rows.length !== 1) {
        setBusy(false);
        return setError(
          re ? `读当前讲法失败：${re.message}（${re.code ?? "?"}）` : `读到 ${rows?.length ?? 0} 行配置，应为 1 行。`,
        );
      }
      const row = rows[0] as StyleConfig & { tone: string };
      const current = {
        config: Object.fromEntries(AXIS_IDS.map((a) => [a, row[a]])) as StyleConfig,
        tone: ((TONES as readonly string[]).includes(row.tone) ? row.tone : "neutral") as Tone,
      };
      const next = applyAdjustments(current, picked);
      const { error: e } = await supabase
        .from("style_config")
        .update({ ...next.config, tone: next.tone })
        .eq("user_id", userId);
      if (e) {
        setBusy(false);
        return setError(`保存失败：${e.message}（${e.code ?? "?"}）`);
      }
      // 到头了的那几项**说出来**（口径 F07）：不说的话用户会以为没保存成功。
      if (next.atEdge.length > 0) edgeNote = `「${next.atEdge.join("」「")}」已经到头了，那几项没有变。`;
    }

    // ---- 2. 那句话。**在按钮写完之后**：服务端读的是库里那一行当基线，
    // 顺序反了的话这句话会以旧配置为基线，把刚点的按钮盖掉。
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

    setBusy(false);
    setPicked([]);
    setText("");
    // **说清楚它从什么时候开始生效。** 不说的话用户会回头看上一段讲解，
    // 发现没变，以为没保存成功。
    setDone(`已经改好了，之后的讲解按新的讲法。${edgeNote}`);
    // 输入框下面那行「适中 / 简洁」是服务端组件读好传下来的，不刷新就一直显示旧档。
    // refresh 只重跑服务端组件，聊天里的轮次（客户端状态）不受影响。
    router.refresh();
  }

  return (
    <dialog
      ref={ref}
      className="dialog"
      data-field="style-adjust"
      onClose={() => {
        // 关掉就回到初始状态：下次打开是一张新的，不是上一次的「已经改好了」。
        setDone(null);
        setError(null);
        onClose();
      }}
    >
      <div className="dialog__head">
        <h2 className="t-title text-[17px]">调整讲法</h2>
        <form method="dialog">
          <button className="btn btn--ghost btn--sm">关闭</button>
        </form>
      </div>
      <div className="dialog__body">
        <p className="field-label">和刚才比</p>
        <div className="mt-2 mb-5 grid grid-cols-2 gap-2">
          {ADJUSTMENTS.map((a) => (
            <button
              key={a.id}
              type="button"
              className="choice px-3 py-2 text-[14px]"
              data-field="adjust-tag"
              data-adjust={a.id}
              data-selected={picked.includes(a.id)}
              aria-pressed={picked.includes(a.id)}
              onClick={() => setPicked((prev) => toggleAdjustment(prev, a.id))}
            >
              {a.label}
            </button>
          ))}
        </div>

        <section className="mb-4">
          <p className="field-label">或者直接说一句</p>
          <textarea
            className="input mt-2 min-h-[72px]"
            data-field="adjust-text"
            maxLength={800}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="比如：公式少一点，多用生活里的例子。"
          />
          <p className="t-caption mt-1">没提到的部分保持原样，不会被一起改掉。</p>
        </section>

        {error && (
          <p role="alert" className="notice notice--danger text-[13px]" data-field="adjust-error">
            {error}
          </p>
        )}
        {done && (
          <div className="notice notice--info text-[13px]" data-field="adjust-done">
            <p>{done}</p>
            {onRedo && (
              <button
                type="button"
                className="btn btn--primary btn--sm mt-2"
                data-field="adjust-redo"
                onClick={() => {
                  // **先关再发**，不靠 `<form method="dialog">` 关：发出去的那一刻有一轮在飞，
                  // 调用方会把 `onRedo` 撤掉，按钮在表单提交之前就被移出 DOM——弹窗于是不关
                  // （2026-10-07 预览实测）。
                  ref.current?.close();
                  onRedo();
                }}
              >
                按新讲法重讲上一个问题
              </button>
            )}
          </div>
        )}

        {!done && (
          <button
            type="button"
            className="btn btn--primary mt-2"
            data-field="adjust-apply"
            disabled={busy}
            onClick={() => void apply()}
          >
            {busy ? "正在调整…" : "应用"}
          </button>
        )}
      </div>
    </dialog>
  );
}
