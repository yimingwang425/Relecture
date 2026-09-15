"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { createClient } from "@/lib/supabase/client";

/**
 * 登录。入口在欢迎页（`/welcome`）；这一页只放登录卡片。
 *
 * **打开就能看全整张卡片**：卡片在视口里垂直居中，上面只有一个小号字标，
 * 不再有标题区把它挤到首屏以下（2026-09-15 用户反馈：要下滑才看全，视觉阻力大）。
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
    <main className="relative flex min-h-dvh flex-col overflow-hidden">
      <div aria-hidden className="band absolute inset-x-0 bottom-0 h-[44dvh]" />

      <header className="relative z-10 flex justify-center px-[var(--page-margin)] pt-6 sm:pt-8">
        <Link href="/welcome" className="wordmark-sm enter-drop" aria-label="Relecture，回到欢迎页">
          Relecture
        </Link>
      </header>

      <div className="relative z-10 flex flex-1 items-center justify-center px-[var(--page-margin)] py-6">
        <form onSubmit={submit} className="card card--raised enter w-full max-w-[400px] p-2">
          <div className="px-3 pt-3 pb-4">
            <p className="t-eyebrow"># 登录</p>
            <p className="t-title mt-1">欢迎回来</p>
          </div>

          <div className="panel flex flex-col gap-4 p-4">
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

            <button type="submit" disabled={busy} className="btn btn--primary btn--lg w-full">
              {busy ? "登录中…" : "登录"}
            </button>

            <p className="t-caption text-center">
              目前是邀请制，注册即将开放 ·{" "}
              <Link href="/welcome" className="link">
                返回
              </Link>
            </p>
          </div>
        </form>
      </div>
    </main>
  );
}
