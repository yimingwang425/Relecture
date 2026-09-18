import { redirect } from "next/navigation";

import { RowStateNotice } from "@/app/components/row-state";
import { newSessionId } from "@/lib/style/events";
import { TOTAL_STEPS, makeFlips } from "@/lib/style/flow";
import { SAMPLE_QUESTION, loadPairs } from "@/lib/style/pairs";
import { loadStyleRow } from "@/lib/style/session";
import { resolveTaste } from "@/lib/style/taste";

import { PairwiseOnboarding } from "./pairwise-client";

export const dynamic = "force-dynamic";

/**
 * 主臂：6 次固定成对比较。
 *
 * 服务端只做三件事——读行、核对臂、把**投影过的**样例交给客户端组件。
 * `loadPairs()` 在这里（服务端）调用，`pairs.json` 的生产元数据不进浏览器。
 */
export default async function PairwiseOnboardingPage() {
  const result = await loadStyleRow();

  if (result.state !== "ok") {
    return (
      <main data-state={result.state} className="shell">
        <div className="mt-12">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  // AB 分组的完整性：历史的 text 臂不该看到成对比较（他们走的是一段话描述那一版）。
  // pairwise（对照）与 taste（标签 + 比较）都走这一页，区别只在**有没有先验**。
  if (result.row.f1_arm === "text") redirect("/onboarding/style-text");

  return (
    <main data-state="ok" data-arm={result.row.f1_arm} className="shell max-w-[1240px]">
      {/*
        左右顺序**在服务端抽一次**，作为 prop 传下去。

        起初是在客户端组件里 `useState(() => makeFlips(...))` 抽的，那是错的：
        惰性初始化在 **SSR 渲染时跑一次**、**hydrate 时又跑一次**，两次 `Math.random()`
        约有一半概率不一致 —— 于是 React 报 hydration 文本不匹配（#418），
        服务端先绘制的那一版被换掉。

        它不只是控制台里一条报错，而是直接打穿随机化的目的：
        用户看到并点下去的可能是服务端那一版，而 `leftVariant` 记的是客户端那一版，
        「这次点击对应哪个档位在左」就对不上了 —— 位置效应照样分离不出来。

        放在服务端则只抽一次：prop 随 RSC 载荷序列化过去，两侧渲染同一份数据。
        页面是 force-dynamic，所以每次加载仍是新的一次抽取。
      */}
      {/*
        `sessionId` 与 `flips` 同一条处置：**在服务端抽一次**，当 prop 传下去。

        客户端组件的渲染在 SSR 与 hydrate 各跑一次，渲染期取随机数两侧必然分叉
        （React #418）。`session_id` 分叉不会像左右位置那样肉眼可见 ——
        它只是让同一次 onboarding 的 6 步事件挂在两个 session 上，
        而弃流点分析正是按 session 分组的。

        「一次页面加载 = 一个 session」是**占位口径**：PRD §3.4.5 的会话切分规则
        至今未定，内测第 1 周数据落盘后再立（见 f1_samples/README.md §二）。
      */}
      <PairwiseOnboarding
        userId={result.userId}
        priors={resolveTaste(result.row.taste_tags ?? []).priors}
        arm={result.row.f1_arm}
        sessionId={newSessionId()}
        pairs={loadPairs()}
        question={SAMPLE_QUESTION}
        flips={makeFlips(TOTAL_STEPS)}
        alreadyOnboarded={result.row.onboarded_at !== null}
      />
    </main>
  );
}
