import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: { absolute: "Relecture" } };

/**
 * 欢迎页：未登录用户的入口。
 *
 * 只有三样东西：文字标志、一句话、登录 / 注册。入场是一道从上往下扫开的渐变遮罩
 * （`.reveal-down`），标志、说明、按钮再依次上浮——视线被动画从上带到下，落在按钮上。
 *
 * **注册按钮先放着但禁用**：自助注册是进度总结 §七 第 2 项，还没做。
 * 按钮在、说明在，用户就知道「现在不能注册」而不是「找不到注册」。
 *
 * 已登录的人不该看到这一页，直接送回首页。
 */
export default async function WelcomePage() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (data.user) redirect("/");

  return (
    <main className="reveal-down flex min-h-dvh flex-col overflow-hidden">
      <section className="flex flex-1 flex-col items-center justify-center px-[var(--page-margin)] py-12 text-center">
        <h1 className="wordmark-hero welcome-1">Relecture</h1>
        {/* 断行写死在逗号后：交给浏览器的话，「在」会被留在上一行（2026-09-15 截图实测）。 */}
        <p className="t-body welcome-2 mt-5 text-[17px] sm:mt-6">
          选择适合你的讲解方式，
          <br />
          在自己的节奏里弄懂每个问题。
        </p>
        <div className="welcome-3 mt-9 flex flex-wrap justify-center gap-3">
          <Link href="/login" className="btn btn--primary btn--lg min-w-[136px]">
            登录
          </Link>
          <button type="button" disabled className="btn btn--ghost btn--lg min-w-[136px]" aria-describedby="register-note">
            注册
          </button>
        </div>
        <p id="register-note" className="t-caption welcome-3 mt-3">
          注册即将开放
        </p>
      </section>

      <div aria-hidden className="band welcome-band h-[26dvh] min-h-[140px] shrink-0" />
    </main>
  );
}
