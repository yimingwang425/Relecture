import Link from "next/link";

import { LogoutButton } from "@/app/components/logout-button";

/**
 * 黑色胶囊导航（参考稿 docs/F1/UI设计.md 的 `.nav`）。
 *
 * 参考稿把它绝对定位在 1290px 舞台上；这里改成正常流 + 居中限宽，
 * 因为我们的页面要滚动。保留的是形状：黑底、26px 圆角、52px 高、白字。
 */
export function AppNav({ email }: { email?: string | null }) {
  return (
    <nav className="pill-nav mx-auto flex h-[52px] w-full max-w-[880px] items-center gap-4 px-5">
      <Link
        href="/"
        className="shrink-0 text-[17px] tracking-[var(--tr-wordmark)] font-[var(--fw-black)] text-[var(--c-on-dark)] no-underline"
      >
        AI 课件讲解助手
      </Link>

      <span className="ml-auto hidden truncate font-mono text-xs text-white/55 sm:block">
        {email ?? ""}
      </span>

      <LogoutButton className="shrink-0 text-sm text-white/70 underline underline-offset-4 hover:text-white disabled:opacity-50" />
    </nav>
  );
}
