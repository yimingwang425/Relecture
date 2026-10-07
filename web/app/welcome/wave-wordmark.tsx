"use client";

import { useEffect, useRef, type CSSProperties } from "react";

/**
 * 欢迎页的文字标志。波带扫过时，九个字母各自抖一下再复原——像一道电波穿过。
 *
 * ## 触发按位置，不按时间
 *
 * 波带的动画时长写在 CSS 里，标志在视口里的高度随屏幕变。按时间算「什么时候扫到」
 * 就得在这里再抄一份时长、再猜一个高度，两边迟早对不上。所以每帧读一次波带的实际位置，
 * 它的亮部越过标志中线的那一帧再触发。
 *
 * ## 「随机」是一张写死的表
 *
 * 客户端组件里不许有随机源（SSR 与 hydrate 各跑一次会分叉，`verify:logic` 五节有断言）。
 * 抖动只播一次、九个字母方向各不相同，一张打乱过的表在眼睛里与随机没有区别。
 * 幅度用 em，跟着字号走：手机上的小标志抖得同样明显，桌面上的大标志不会抖散。
 *
 * ## 不触发的情况
 *
 * - 系统开了「减少动态效果」：波带本身也不动（globals.css 关掉了动画），不抖。
 * - 波带走完仍没越过中线（理论上不会）：兜底 5 秒后停止读位置，不留一个空转的 rAF。
 */
const LETTERS = "Relecture".split("");

/** [水平偏移, 垂直偏移, 旋转角, 延迟]。单位分别是 em、em、deg、ms。 */
const JOLT: readonly (readonly [number, number, number, number])[] = [
  [-0.036, 0.024, -4, 0],
  [0.024, -0.036, 3, 40],
  [-0.018, 0.03, -2, 15],
  [0.036, -0.018, 5, 60],
  [-0.03, 0.036, -3, 25],
  [0.018, -0.03, 4, 50],
  [-0.036, 0.018, -5, 10],
  [0.03, -0.024, 2, 35],
  [-0.024, 0.03, -3, 20],
];

/** 抖动整段（最后一个字母的延迟 + 动画时长）之后摘掉标记，字母回到静止的样式。 */
const SHAKE_CLEAR_MS = 600;
/** 兜底：这么久还没触发就不再读位置。 */
const GIVE_UP_MS = 5000;

export function WaveWordmark({ waveId, className = "" }: { waveId: string; className?: string }) {
  const ref = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const word = ref.current;
    const wave = document.getElementById(waveId);
    if (!word || !wave || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let raf = 0;
    let clear: ReturnType<typeof setTimeout> | undefined;
    const started = performance.now();

    const tick = () => {
      const w = wave.getBoundingClientRect();
      const r = word.getBoundingClientRect();
      // 波带的亮部在它自身高度的约八成处（globals.css `.welcome-wave` 的 82% 色标）。
      const front = w.top + w.height * 0.82;
      if (front >= r.top + r.height / 2) {
        word.dataset.shake = "1";
        word.dataset.shaken = "1";
        clear = setTimeout(() => delete word.dataset.shake, SHAKE_CLEAR_MS);
        return;
      }
      if (performance.now() - started < GIVE_UP_MS) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      if (clear) clearTimeout(clear);
    };
  }, [waveId]);

  return (
    <h1 ref={ref} className={className} aria-label="Relecture">
      {LETTERS.map((ch, i) => (
        <span
          key={i}
          aria-hidden
          className="wordmark-letter"
          style={
            {
              "--jx": `${JOLT[i][0]}em`,
              "--jy": `${JOLT[i][1]}em`,
              "--jr": `${JOLT[i][2]}deg`,
              "--jd": `${JOLT[i][3]}ms`,
            } as CSSProperties
          }
        >
          {ch}
        </span>
      ))}
    </h1>
  );
}
