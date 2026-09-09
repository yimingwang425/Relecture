import { redirect } from "next/navigation";

import { AppNav } from "@/app/components/app-nav";
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
      <main data-state={result.state} className="mx-auto w-full max-w-[880px] px-5 py-8">
        <AppNav email={result.email} />
        <div className="mt-10">
          <RowStateNotice result={result} />
        </div>
      </main>
    );
  }

  redirect(result.row.f1_arm === "pairwise" ? "/onboarding/style" : "/onboarding/style-text");
}
