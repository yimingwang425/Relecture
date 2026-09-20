import { redirect } from "next/navigation";

import { RowStateNotice } from "@/app/components/row-state";
import { loadStyleRow } from "@/lib/style/session";

export const dynamic = "force-dynamic";

/**
 * 按 AB 分组把用户送到对应的臂。
 *
 * 分组读的是数据库里的 `f1_arm`（口径 F08：在 `auth.users` 的触发器里算），
 * **前端不算 hash、不做兜底**——前端算一遍就是「TS 一份 Python 一份必然漂」的复发，
 * 而且用户对自己那行有 update 权限，能改自己的臂，AB 分组当场污染。
 */
export default async function OnboardingEntryPage() {
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

  // ---- 2026-09-20：**不再按臂分流，所有人都走标签页。**
  //
  // 成对比较去掉了（用户的判断：「不论多短，用户都无法从两组如此密集的文字中
  // 获得有效信息，甚至这个信息还不是用户领域」）。于是 onboarding 只剩这一页，
  // 而 `f1_arm` 那一列的含义从「实验臂」变成「这行配置是怎么来的」——
  // 老行不回改（口径 F10），新用户一律 taste（迁移 20260920120000）。
  //
  // **这里不读 f1_arm 了**。读它就还得决定「pairwise 的老用户再进来看什么」，
  // 而那个答案是「和新用户一样」——那就不该有分支。
  redirect("/onboarding/taste");
}
