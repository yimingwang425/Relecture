import { redirect } from "next/navigation";

import { AppNav } from "@/app/components/app-nav";
import { RowStateNotice } from "@/app/components/row-state";
import { SAMPLE_QUESTION, loadPairs } from "@/lib/style/pairs";
import { loadStyleRow } from "@/lib/style/session";

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
      <main data-state={result.state} className="mx-auto w-full max-w-[880px] px-5 py-8">
        <AppNav email={result.email} />
        <div className="mt-10">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  // AB 分组的完整性：对照臂的用户不该看到成对比较，否则 A5 的三个读数按臂分组就没意义了。
  if (result.row.f1_arm !== "pairwise") redirect("/onboarding/style-text");

  return (
    <main data-state="ok" data-arm="pairwise" className="mx-auto w-full max-w-[1220px] px-5 py-8">
      <AppNav email={result.email} />
      <PairwiseOnboarding
        userId={result.userId}
        pairs={loadPairs()}
        question={SAMPLE_QUESTION}
        alreadyOnboarded={result.row.onboarded_at !== null}
      />
    </main>
  );
}
