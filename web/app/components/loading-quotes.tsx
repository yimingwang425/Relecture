"use client";

import { useEffect, useState } from "react";

/**
 * 等待时轮播的几句话。流式之后只在**第一段正文出来之前**显示（检索 + 首字前的思考，
 * 通常几秒），这段时间里给眼睛一个落点。
 *
 * - **按顺序轮换，不随机**：客户端组件里不许有随机源（SSR 与 hydrate 各跑一次会分叉，
 *   `verify:logic` 五节有断言）。从第一句开始，每次等待都一样，这没有关系。
 * - 它**不替代计时器**：计时器回答「是不是卡住了」，这几句话只是陪着等。
 * - 换句的间隔只在这里定义一次，CSS 动画时长从同一个常量取，两边不会各走各的。
 * - 系统开了「减少动态效果」时，翻滚与淡入淡出都被 globals.css 关掉，只剩换字。
 * - **逐字翻滚**：每个字单独入场（上翻 + 轻微模糊），按字序错开 28ms。
 *   拆字是为了让等待有东西可看；不拆的话一句话淡入淡出，几十秒里只动四五次。
 *   错开 16ms（原 28ms）：一句十几个字的话要 0.2 秒翻完，而不是半秒。
 */
const QUOTES = [
  "难懂的往往不是知识，\n而是讲法。",
  "好的讲解，\n是刚好能让你听懂的那一种。",
  "同样的内容，\n换个讲法，就通了。",
  "慢一点没关系，\n弄懂了才算数。",
  "卡住的地方，\n往往只差一个切入点。",
];

//: 每句停留多久。**2026-09-22 从 4200 调到 2400**：实测下来一句话停四秒多，
//: 等待时看着像卡住了——而这几句话存在的意义恰恰是让等待不像卡住。
//: 下限不是越小越好：低于约 2 秒时最后几个字刚翻完就换句，读不完。
const INTERVAL_MS = 2400;

export function LoadingQuotes({ className = "" }: { className?: string }) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setIndex((i) => (i + 1) % QUOTES.length), INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  const lines = QUOTES[index].split("\n");

  return (
    <p className={`quote-stage ${className}`} aria-hidden>
      {/* key 变化 → 整句重新挂载 → 每个字从头翻滚一遍 */}
      <span key={index} className="quote-line" style={{ animationDuration: `${INTERVAL_MS}ms` }}>
        {lines.map((line, li) => (
          <span key={li} className="quote-row">
            {[...line].map((ch, ci) => (
              <span
                key={ci}
                className="quote-char"
                // 逐字错开。**延迟按字序算，不随机**：客户端组件里不许有随机源
                // （SSR 与 hydrate 会分叉），而且同一句每次翻滚一致，看起来才像设计过的。
                style={{ animationDelay: `${(li * line.length + ci) * 16}ms` }}
              >
                {ch === " " ? "\u00a0" : ch}
              </span>
            ))}
          </span>
        ))}
      </span>
    </p>
  );
}
