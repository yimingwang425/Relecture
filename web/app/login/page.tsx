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
/** 公开的演示账号。邀请制期间写在页面上是刻意的，不是凭据泄漏。 */
const DEMO_EMAIL = "demo@demo.com";
const DEMO_PASSWORD = "Ww123456";

/** 先走 Clipboard API；拿不到（非安全上下文、权限被拒）时退回 execCommand。 */
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

function DemoRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    if (await copyText(value)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }

  return (
    <div className="mt-1.5 flex items-center gap-3">
      <span className="t-meta w-8 shrink-0">{label}</span>
      <span className="mono flex-1 select-all text-[14px] text-[var(--c-ink)]">{value}</span>
      {/* type="button"：这里在 form 里，默认的 submit 会把表单提交出去 */}
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? `已复制${label}` : `复制${label}`}
        title={copied ? "已复制" : "复制"}
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--c-muted-text)] transition-colors hover:bg-[var(--c-surface-card)] hover:text-[var(--c-ink)]"
      >
        {copied ? (
          <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        ) : (
          <svg aria-hidden width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
    </div>
  );
}

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
      <div aria-hidden className="welcome-fade absolute inset-x-0 bottom-0 h-[44dvh]" />

      <header className="relative z-10 flex justify-center px-[var(--page-margin)] pt-6 sm:pt-8">
        <Link href="/welcome" className="wordmark-sm enter-drop" aria-label="Relecture，回到欢迎页">
          Relecture
        </Link>
      </header>

      <div className="relative z-10 flex flex-1 items-center justify-center px-[var(--page-margin)] py-6">
        <form onSubmit={submit} className="card card--raised enter w-full max-w-[400px] p-2">
          <div className="px-3 pt-3 pb-4">
            <p className="t-title">欢迎回来</p>
          </div>

          {/* 体验账号（2026-10-16）：邀请制期间给来看项目的人用。两项各带复制按钮，
              都在这一页，复制一个、粘贴、再复制另一个，不用来回跳页。宽度与下面的表单面板对齐。 */}
          <div data-field="demo-account" className="panel mb-2 px-4 py-3">
            <p className="t-caption">体验账号</p>
            <DemoRow label="账号" value={DEMO_EMAIL} />
            <DemoRow label="密码" value={DEMO_PASSWORD} />
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
