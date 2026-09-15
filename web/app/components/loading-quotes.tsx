"use client";

import { useEffect, useState } from "react";

/**
 * 等待时轮播的几句话。讲解一轮要等几十秒（不流式），这段时间里给眼睛一个落点。
 *
 * - **按顺序轮换，不随机**：客户端组件里不许有随机源（SSR 与 hydrate 各跑一次会分叉，
 *   `verify:logic` 五节有断言）。从第一句开始，每次等待都一样，这没有关系。
 * - 它**不替代计时器**：计时器回答「是不是卡住了」，这几句话只是陪着等。
 * - 换句的间隔只在这里定义一次，CSS 动画时长从同一个常量取，两边不会各走各的。
 * - 系统开了「减少动态效果」时，淡入淡出被 globals.css 关掉，只剩换字。
 */
const QUOTES = [
  "难懂的往往不是知识，\n而是讲法。",
  "好的讲解，\n是刚好能让你听懂的那一种。",
  "同样的内容，\n换个讲法，就通了。",
  "慢一点没关系，\n弄懂了才算数。",
  "卡住的地方，\n往往只差一个切入点。",
];

const INTERVAL_MS = 4200;

export function LoadingQuotes({ className = "" }: { className?: string }) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setIndex((i) => (i + 1) % QUOTES.length), INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <p className={`quote-stage ${className}`} aria-hidden>
      {/* key 变化 → 重新挂载 → CSS 动画从头播一遍 */}
      <span key={index} className="quote-line" style={{ animationDuration: `${INTERVAL_MS}ms` }}>
        {QUOTES[index]}
      </span>
    </p>
  );
}
