"use client";

import { useRef, useState, useSyncExternalStore } from "react";

/**
 * 上手引导（2026-09-22）。**替掉了「压暗整屏 + 弹一条提示」那一版。**
 *
 * ## 为什么换
 *
 * 压暗太突兀：用户刚读完一段讲解，屏幕突然暗下来，第一反应是「出什么事了」，
 * 而不是「哦，这里可以调」。而且那条提示出现在**答案刚到**的那一刻——
 * 注意力正在正文上，挡住它只会被急着关掉。
 *
 * 现在：进提问页时弹一次，三步讲清「调整讲法在哪、两种用法各是什么」。
 * 用户看完就知道那个按钮一直在输入框旁边，之后想改随时点。
 *
 * ## 动图是 div 画的，不是 GIF
 *
 * 三张录屏 GIF 大概 1MB，而且**改一次 UI 就全部过期**——没人会记得回来重录，
 * 于是引导会慢慢变成在教一个已经不存在的界面。用 div 画的假界面跟着设计 token
 * 走，颜色、圆角、边框改了它自己就跟上。
 *
 * 代价如实登记：它是**示意**，不是截图，细节与真界面不完全一致。
 * 这是刻意的取舍——示意图不会过期，截图会。
 *
 * ## 只弹一次，记在这台设备上
 *
 * 同风格提示那条：localStorage 可能丢（隐私模式、清数据），丢了就再弹一次，
 * 代价是一次多余的引导，不是一次错误的配置。
 * 读它用 `useSyncExternalStore` 而不是在 effect 里 setState（级联渲染）。
 */

const SEEN_KEY = "relecture.tour.seen";

type Step = {
  title: string;
  lead: string;
  /** 放大的落点与倍数。`--cx/--cy` 是鼠标落点，没有鼠标的那几步不给。 */
  vars: React.CSSProperties;
  mock: React.ReactNode;
};

/** 假界面：提问页。 */
function MockChat() {
  return (
    <div className="mk">
      <div className="mk__side">
        <div className="mk__bar" style={{ width: "70%" }} />
        <div className="mk__bar" style={{ width: "52%" }} />
        <div className="mk__bar" style={{ width: "60%" }} />
      </div>
      <div className="mk__main">
        <div className="mk__ans">
          <div className="mk__bubble" />
          <div className="mk__bar" style={{ width: "92%" }} />
          <div className="mk__bar" style={{ width: "86%" }} />
          <div className="mk__bar" style={{ width: "94%" }} />
          <div className="mk__bar" style={{ width: "48%" }} />
        </div>
        <div className="mk__box">
          <div className="mk__bar" style={{ width: "40%" }} />
          <div className="mk__row" style={{ marginTop: 5 }}>
            <span className="mk__chip mk__chip--target">调整讲法</span>
            <span className="mk__chip">发送</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 假界面：调整讲法面板。`focus` 决定哪一块被画重。 */
function MockPanel({ focus }: { focus: "tags" | "text" }) {
  return (
    <div className="mk" style={{ flexDirection: "column", padding: "8px 10px", gap: 6 }}>
      <div className="mk__row">
        <div className="mk__bar" style={{ width: "34%", height: 5 }} />
        <span className="mk__chip">关闭</span>
      </div>

      <div
        className="mk__box"
        style={focus === "tags" ? { borderColor: "var(--c-accent-band)" } : undefined}
      >
        <div className="mk__bar" style={{ width: "26%" }} />
        <div className="mk__tags" style={{ marginTop: 4 }}>
          {["口语", "大白话", "平实", "书面", "教材式"].map((t, i) => (
            <span
              key={t}
              className={`mk__chip${focus === "tags" && (i === 1 || i === 3) ? " mk__chip--pick" : ""}`}
              // 两个标签先后亮起来。**错开是刻意的**：同时亮看起来像页面在闪，
              // 而不是像有人在挑。
              style={focus === "tags" && (i === 1 || i === 3) ? { animationDelay: `${i * 140}ms` } : undefined}
            >
              {t}
            </span>
          ))}
        </div>
        <div className="mk__bar" style={{ width: "22%", marginTop: 6 }} />
        <div className="mk__tags" style={{ marginTop: 4 }}>
          {["先给结论", "要具体例子", "尽量短", "讲透"].map((t) => (
            <span key={t} className="mk__chip">
              {t}
            </span>
          ))}
        </div>
      </div>

      <div
        className="mk__box"
        style={focus === "text" ? { borderColor: "var(--c-accent-band)" } : undefined}
      >
        <div className="mk__bar" style={{ width: "30%" }} />
        <div style={{ marginTop: 5, minHeight: 12 }}>
          {focus === "text" ? <div className="mk__typing" /> : <div className="mk__bar" style={{ width: "55%" }} />}
        </div>
      </div>
    </div>
  );
}

const STEPS: Step[] = [
  {
    title: "讲得不合适？就在输入框旁边改",
    lead: "每段讲解都按你的偏好生成。读着太长、例子太少、想看推导——点输入框旁的「调整讲法」。",
    vars: { "--zs": 2.9, "--zx": "72%", "--zy": "88%", "--cx": "60%", "--cy": "84%" } as React.CSSProperties,
    mock: <MockChat />,
  },
  {
    title: "点几个标签就行",
    lead: "不用想怎么描述。挑几个读着顺眼的，没挑到的部分保持原样，不会被一起改掉。",
    vars: { "--zs": 2.2, "--zx": "40%", "--zy": "38%" } as React.CSSProperties,
    mock: <MockPanel focus="tags" />,
  },
  {
    title: "或者直接说一句",
    lead: "「再短一点」「先说结论」「多举个例子」——写下来就行，我们把它变成设置。",
    vars: { "--zs": 2.4, "--zx": "40%", "--zy": "86%" } as React.CSSProperties,
    mock: <MockPanel focus="text" />,
  },
];

export function Tour() {
  const stored = useSyncExternalStore(
    () => () => {},
    () => {
      try {
        return window.localStorage.getItem(SEEN_KEY) === "1";
      } catch {
        return false;
      }
    },
    () => true, // 服务端当作看过：SSR 不渲染，hydrate 时再按真实值决定
  );
  const [closed, setClosed] = useState(false);
  const [i, setI] = useState(0);
  const dialogRef = useRef<HTMLDialogElement>(null);

  function finish() {
    setClosed(true);
    dialogRef.current?.close();
    try {
      window.localStorage.setItem(SEEN_KEY, "1");
    } catch {
      // 存不进去只意味着下次还会弹一次。
    }
  }

  if (stored || closed) return null;

  const step = STEPS[i];
  const last = i === STEPS.length - 1;

  return (
    <dialog
      ref={(el) => {
        dialogRef.current = el;
        // 挂上就打开。`showModal()` 只能在元素进 DOM 之后调。
        if (el && !el.open) el.showModal();
      }}
      className="dialog"
      data-field="tour"
      data-step={i + 1}
      onCancel={(e) => {
        // Esc 关掉也算看过——否则每次进页面都再弹一次。
        e.preventDefault();
        finish();
      }}
    >
      <div className="dialog__body">
        {/* key：换一步就重挂载，动画从头播一遍。 */}
        <div key={i} className="tour-stage" style={step.vars}>
          <div className="tour-zoom">{step.mock}</div>
          {step.vars["--cx" as keyof React.CSSProperties] !== undefined && (
            <span className="tour-cursor" aria-hidden />
          )}
        </div>

        <h2 className="t-title mt-5 text-[17px]">{step.title}</h2>
        <p className="t-body mt-2">{step.lead}</p>

        <div className="mt-6 flex items-center justify-between gap-4">
          <div className="tour-dots" data-field="tour-dots">
            {STEPS.map((s, k) => (
              <button
                key={s.title}
                type="button"
                className="tour-dot"
                aria-current={k === i}
                aria-label={`第 ${k + 1} 步：${s.title}`}
                onClick={() => setI(k)}
              />
            ))}
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              data-field="tour-prev"
              disabled={i === 0}
              onClick={() => setI((v) => Math.max(0, v - 1))}
            >
              上一步
            </button>
            <button
              type="button"
              className="btn btn--primary btn--sm"
              data-field="tour-next"
              onClick={() => (last ? finish() : setI((v) => v + 1))}
            >
              {last ? "完成" : "下一步"}
            </button>
          </div>
        </div>
      </div>
    </dialog>
  );
}
