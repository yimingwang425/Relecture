"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { BrandMark } from "@/app/components/brand-mark";
import { LogoutButton } from "@/app/components/logout-button";

/**
 * 黑色胶囊导航（参考稿 docs/F1/UI设计.md 的 `.nav`）。
 *
 * 参考稿把它绝对定位在 1290px 舞台上；这里改成吸顶 + 居中限宽，因为页面要滚动。
 * 保留的是形状（黑底、26px 圆角、52px 高、白字）、当前页的白底胶囊（参考稿的 CTA），
 * 以及窄屏下的汉堡菜单（Escape 关闭并把焦点还给按钮、点外面关闭、点链接关闭）。
 *
 * 顺序是使用顺序：**提问在前**——那是用户每天来做的事；课件是一次性的准备；
 * 风格是偶尔调。
 */
const LINKS = [
  { href: "/explain", label: "提问", match: ["/explain"] },
  { href: "/courses", label: "课件", match: ["/courses"] },
  { href: "/settings/style", label: "讲解风格", match: ["/settings", "/onboarding"] },
] as const;

export function AppNav({ email }: { email?: string | null }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const burgerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (!navRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        burgerRef.current?.focus();
      }
    }
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const isCurrent = (match: readonly string[]) =>
    match.some((m) => pathname === m || pathname.startsWith(`${m}/`));

  return (
    <div className="nav-sticky enter-drop">
      <nav ref={navRef} className="pill-nav" aria-label="主导航">
        <Link href="/" className="wordmark" aria-current={pathname === "/" ? "page" : undefined}>
          <BrandMark />
          <span>课件讲解助手</span>
        </Link>

        <div className="nav-desktop">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="nav-link"
              aria-current={isCurrent(l.match) ? "page" : undefined}
            >
              {l.label}
            </Link>
          ))}

          <span className="ml-auto hidden max-w-[200px] truncate px-2 text-[13px] text-white/50 lg:block">
            {email ?? ""}
          </span>
          <LogoutButton className="nav-link disabled:opacity-50" />
        </div>

        <button
          ref={burgerRef}
          type="button"
          className="burger"
          aria-label={open ? "关闭菜单" : "打开菜单"}
          aria-expanded={open}
          aria-controls="nav-menu"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((v) => !v);
          }}
        >
          <span className="burger__bar" />
          <span className="burger__bar" />
        </button>

        <div id="nav-menu" className="navmenu" data-open={open} aria-hidden={!open}>
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="nav-link"
              tabIndex={open ? 0 : -1}
              aria-current={isCurrent(l.match) ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              {l.label}
            </Link>
          ))}
          {email && <span className="truncate px-[14px] pt-2 pb-1 text-[12px] text-white/45">{email}</span>}
          <LogoutButton className="nav-link text-left disabled:opacity-50" />
        </div>
      </nav>
    </div>
  );
}
