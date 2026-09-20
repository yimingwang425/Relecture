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

  // 三臂分流（2026-09-19）。`text` 是历史臂，不再分配给新用户，但已有行不回改（F10）。
  if (result.row.f1_arm === "text") redirect("/onboarding/style-text");
  if (result.row.f1_arm === "pairwise") redirect("/onboarding/style");
  // taste 臂：先过标签页；已经选过就直接进比较（刷新 / 中途退出再回来不重来一遍）。
  redirect(result.row.taste_tags === null ? "/onboarding/taste" : "/onboarding/style");
}
