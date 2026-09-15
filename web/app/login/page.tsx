"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { BrandMark } from "@/app/components/brand-mark";
import { createClient } from "@/lib/supabase/client";

/**
 * 登录。**没有注册入口**：账号目前是邀请制，自助注册是内测前的另一件事
 * （进度总结 §七 第 2 项）。页面上说出来，免得用户找注册按钮。
 *
 * 构图取参考稿：胶囊标志 → 居中两行标题 → 一句说明 → 卡片坐在青色色带上。
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
    <main className="flex min-h-screen flex-col overflow-hidden">
      <div className="px-[var(--page-margin)] pt-4">
        <div className="pill-nav enter-drop">
          <span className="wordmark">
            <BrandMark />
            <span>课件讲解助手</span>
          </span>
        </div>
      </div>

      <section className="mx-auto mt-[clamp(40px,9vh,104px)] w-full max-w-[760px] px-[var(--page-margin)] text-center">
        <h1 className="t-display enter-wipe">
          把课件讲成
          <br />
          你读得进去的样子
        </h1>
        <p className="t-body enter-late mx-auto mt-4 max-w-[46ch] text-balance">
          上传自己的课件，按你偏好的讲法提问。讲解只用你给的材料，读不到的地方会直说。
        </p>
      </section>

      <div className="relative mt-[clamp(28px,6vh,60px)] flex flex-1 flex-col">
        <div aria-hidden className="band absolute inset-x-0 top-[112px] bottom-0" />

        <form
          onSubmit={submit}
          className="card card--raised enter-later relative z-10 mx-auto mb-16 w-[min(420px,calc(100%-2*var(--page-margin)))] p-2"
        >
          <div className="px-3 pt-3 pb-4">
            <p className="t-eyebrow"># 登录</p>
            <p className="t-title mt-1">用受邀账号登录</p>
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

            <p className="t-caption text-center">目前是邀请制，还不能自助注册。</p>
          </div>
        </form>
      </div>
    </main>
  );
}
