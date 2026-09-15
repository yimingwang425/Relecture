import { redirect } from "next/navigation";

import { AppNav } from "@/app/components/app-nav";
import { RowStateNotice } from "@/app/components/row-state";
import { newSessionId } from "@/lib/style/events";
import { loadStyleRow } from "@/lib/style/session";

import { TextOnboarding } from "./text-client";

export const dynamic = "force-dynamic";

/**
 * 对照臂：用户用文字描述期望的讲解风格。
 *
 * A5 比的是「样例选择 vs 文字描述哪个更有效」，所以两条臂只在**采集方式**上不同：
 * 都以「配置已写入 + 之后可改」收尾，都不在 onboarding 里现场生成讲解。
 * 只建两臂、v1 不跑显著性检验——30–50 用户分两臂各 15–25，
 * 任何组间比较在这个量级上都不可能有统计效力。
 */
export default async function TextOnboardingPage() {
  const result = await loadStyleRow();

  if (result.state !== "ok") {
    return (
      <main data-state={result.state} className="shell">
        <AppNav email={result.email} />
        <div className="mt-12">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  if (result.row.f1_arm !== "text") redirect("/onboarding/style");

  return (
    <main data-state="ok" data-arm="text" className="shell shell--narrow">
      <AppNav email={result.email} />
      {/*
        `sessionId` 在**服务端**抽（同主臂）：客户端组件的渲染在 SSR 与 hydrate
        各跑一次，渲染期取随机数两侧会分叉（React #418）。

        这条臂的事件由 **route handler 写**（`call` 只有服务端知道是真的，
        且失败分支根本走不到客户端），所以客户端要做的只是把这个 id **原样带回去**。
      */}
      <TextOnboarding
        userId={result.userId}
        arm={result.row.f1_arm}
        sessionId={newSessionId()}
        alreadyOnboarded={result.row.onboarded_at !== null}
      />
    </main>
  );
}
