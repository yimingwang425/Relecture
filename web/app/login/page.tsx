"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Wordmark } from "@/app/components/wordmark";
import { createClient } from "@/lib/supabase/client";

/**
 * 登录。入口在欢迎页（`/welcome`）；这一页只放登录卡片。
 *
 * **打开就能看全整张表单**：表单在视口里垂直居中，上面只有一个小号字标
 * （2026-09-15 用户反馈：要下滑才看全，视觉阻力大）。2026-10-07 改成左右两栏：
 * 宽屏左边一块墨色底放一句话，右边是表单；窄屏只剩表单，首屏仍然看得全。
 *
 * 没有注册入口：账号目前是邀请制，自助注册是进度总结 §七 第 2 项。
 *
 * `input[type=email]` / `input[type=password]` / `button[type=submit]` 是回归工具
 * （`scripts/verify_browser.mjs`）的定位点，改样式不改它们。
 */
export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);

    const { error } = await createClient().auth.signInWithPassword({ email, password });

    if (error) {
      // 原文照报，不改写成「登录失败，请重试」。改写等于把
      // 「密码错」「邮箱未验证」「项目配错」压成同一句话，而它们的处置完全不同。
      setError(`${error.message}（${error.status ?? "?"}）`);
      setBusy(false);
      return;
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="auth">
      {/* 左半边（只在宽屏）：墨色底上一句话。窄屏上它不出现——手机上第一屏只给表单。 */}
      <aside className="auth__aside" aria-hidden>
        <span className="wordmark-sm">
          <Wordmark />
        </span>
        <p className="auth__quote enter">
          难懂的往往不是知识，
          <br />
          而是<em>讲法</em>。
        </p>
        <p className="t-caption text-[rgb(251_249_244/0.55)]">把你的课件，按你听得懂的方式重新讲一遍。</p>
      </aside>

      <div className="auth__main">
        <header className="flex items-center justify-between">
          <Link href="/welcome" className="wordmark-sm enter-drop" aria-label="Relecture，回到欢迎页">
            <Wordmark />
          </Link>
          <Link href="/welcome" className="link t-caption">
            ← 返回
          </Link>
        </header>

        <div className="flex flex-1 items-center justify-center py-10">
          <form onSubmit={submit} className="enter w-full max-w-[400px]">
            <p className="t-eyebrow">登录</p>
            <h1 className="t-display mt-4 text-[clamp(34px,4vw,48px)]">欢迎回来</h1>
            <p className="t-meta mt-3">用邀请邮件里的账号登录。</p>

            <div className="mt-10 flex flex-col gap-5">
              <label>
                <span className="field-label">邮箱</span>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="username"
                  className="input"
                />
              </label>
              <label>
                <span className="field-label">密码</span>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  className="input"
                />
              </label>

              {error && (
                <p role="alert" className="notice notice--danger">
                  {error}
                </p>
              )}

              <button type="submit" disabled={busy} className="btn btn--primary btn--lg mt-2 w-full">
                {busy ? "登录中…" : "登录"}
                {!busy && (
                  <span className="btn__arrow" aria-hidden>
                    →
                  </span>
                )}
              </button>

              <p className="t-caption text-center">目前是邀请制，注册即将开放</p>
            </div>
          </form>
        </div>
      </div>
    </main>
  );
}
