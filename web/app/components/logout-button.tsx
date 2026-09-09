"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createClient } from "@/lib/supabase/client";

/**
 * `className` 可覆盖：黑色胶囊导航里要白字，`/` 的浅色头部里要深字。
 * 默认值保持阶段 1.5 的样子，避免改这个组件顺手改掉已经验收过的页面。
 */
export function LogoutButton({
  className = "text-sm text-neutral-500 underline underline-offset-4 hover:text-neutral-900 disabled:opacity-50",
}: {
  className?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await createClient().auth.signOut();
        router.replace("/login");
        router.refresh();
      }}
      className={className}
    >
      退出登录
    </button>
  );
}
