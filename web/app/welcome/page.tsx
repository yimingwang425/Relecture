import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

import { WaveWordmark } from "./wave-wordmark";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: { absolute: "Relecture" } };

/**
 * 欢迎页：未登录用户的入口。
 *
 * 只有三样东西：文字标志、一句话、登录 / 注册。
 *
 * ## 入场
 *
 * 标志擦出、说明与按钮上浮；随后一道淡蓝波带从页顶扫到页底，扫过标志时字母各抖一下
 * （`wave-wordmark.tsx`），扫到底部时渐隐落定成静止的样子。时间线定义在 globals.css 的
 * `.welcome-stage`，只有一处。
 *
 * ## 位置
 *
 * 内容**略高于视口中线**（底部多留 10dvh）。正中间看起来会往下坠——视觉中心本来就比几何中心高一点。
 * 渐隐层绝对定位垫在内容下面，不占居中区域的高度。
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
    <main className="welcome-stage relative flex min-h-dvh flex-col overflow-hidden">
      <div
        aria-hidden
        className="welcome-fade welcome-settle absolute inset-x-0 bottom-0 h-[30dvh] min-h-[160px]"
      />
      <div aria-hidden id="welcome-wave" className="welcome-wave" />

      <section className="relative z-10 flex flex-1 flex-col items-center justify-center px-[var(--page-margin)] pt-12 pb-[calc(3rem+10dvh)] text-center">
        <WaveWordmark waveId="welcome-wave" className="wordmark-hero welcome-1" />
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
        {/* 体验账号（2026-10-16）：账号是邀请制，来看项目的人没法自己注册，给一个公开的演示账号。
            `select-all`：点一下整段选中，方便复制。 */}
        <div data-field="demo-account" className="panel welcome-3 mt-7 w-full max-w-[300px] px-4 py-3 text-left">
          <p className="t-caption">体验账号 · 邀请制期间可直接用它登录</p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1">
            <dt className="t-meta">账号</dt>
            <dd className="mono select-all text-[14px] text-[var(--c-ink)]">demo@demo.com</dd>
            <dt className="t-meta">密码</dt>
            <dd className="mono select-all text-[14px] text-[var(--c-ink)]">Ww123456</dd>
          </dl>
        </div>
      </section>
    </main>
  );
}
