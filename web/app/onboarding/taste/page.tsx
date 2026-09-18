import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { RowStateNotice } from "@/app/components/row-state";
import { newSessionId } from "@/lib/style/events";
import { loadStyleRow } from "@/lib/style/session";

import { TasteBoards } from "./taste-client";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "先说说你的口味" };

/**
 * 开场标签页：三块，点选即可。**没有顶栏**——这一页只做一件事。
 *
 * 为什么不再问「你希望它怎么给你讲？」：理由与证据在 `lib/style/taste.ts` 的模块说明，
 * 一句话是**用户要是能描述出想要的讲法，他就不需要这个产品**。
 *
 * 只有 `taste` 臂走这里（`/onboarding` 按臂分流）：`pairwise` 是对照臂，直接进成对比较，
 * A5 要比的正是「标签先验有没有让结果更准」。
 */
export default async function TastePage() {
  const result = await loadStyleRow();

  if (result.state !== "ok") {
    return (
      <main data-state={result.state} className="shell shell--narrow">
        <div className="mt-12">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  // 对照臂不该看到标签页，否则 A5 的三个读数按臂分组就没意义了（同成对页那条）。
  if (result.row.f1_arm === "pairwise") redirect("/onboarding/style");
  if (result.row.f1_arm === "text") redirect("/onboarding/style-text");

  return (
    <main data-state="ok" data-arm="taste" className="shell shell--narrow">
      {/* `sessionId` 在**服务端**抽（React #418：客户端组件 SSR 与 hydrate 各渲染一次）。 */}
      <TasteBoards
        userId={result.userId}
        arm={result.row.f1_arm}
        sessionId={newSessionId()}
        initialTags={result.row.taste_tags ?? []}
      />
    </main>
  );
}
