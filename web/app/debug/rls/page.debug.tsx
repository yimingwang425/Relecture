import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

/**
 * 验收③的仪器：用**当前登录态**同时查「自己」和「另一个 user_id」。
 *
 * 文件名是 `page.debug.tsx` 而不是 `page.tsx`：只有构建时
 * `NEXT_PUBLIC_ENABLE_DEBUG_ROUTES=1`，`debug.tsx` 才在 next.config.ts 的
 * pageExtensions 里，这个模块才成为一条路由。**生产构建里它不存在**——
 * 不是运行时 404，是根本没被编译进去。断言见 scripts/verify_build_guards.mjs。
 *
 * ## 两个查询必须在同一次渲染里
 *
 * 分两次页面加载去查，「读不到 B」就有第二种解释：会话在两次之间过期了。
 * 并排渲染之后，「查自己 = 1 行」与「查 probe = 0 行」共享同一个 JWT、
 * 同一次请求周期——前者是后者的反向控制，缺了它后者什么也证明不了
 * （通则七 #5，与 supabase/verify_rls_prod.py 的步骤 1 同形）。
 */
export const dynamic = "force-dynamic";

export default async function DebugRlsPage({
  searchParams,
}: {
  searchParams: Promise<{ probe?: string }>;
}) {
  const { probe } = await searchParams;
  const supabase = await createClient();

  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/welcome");

  const self = await supabase.from("style_config").select("*").eq("user_id", auth.user.id);
  const other = probe
    ? await supabase.from("style_config").select("*").eq("user_id", probe)
    : null;

  return (
    <main data-state="ready" className="mx-auto flex min-h-screen max-w-2xl flex-col gap-5 p-6 pt-16">
      <header>
        <h1 className="text-lg font-medium">RLS 探针</h1>
        <p className="mt-1 text-sm text-neutral-500">
          当前登录态：<code className="font-mono text-xs">{auth.user.email}</code> ·{" "}
          <code className="font-mono text-xs">{auth.user.id}</code>
        </p>
      </header>

      <Result
        kind="self"
        title="查自己 —— 反向控制"
        note="这一条必须是 1 行。它若为 0，下面那条的「0 行」就什么也证明不了（可能整表不可读、可能会话已失效）。"
        uid={auth.user.id}
        rows={self.data}
        error={self.error?.message}
      />

      {probe ? (
        <Result
          kind="probe"
          title="查 probe —— 跨用户读取"
          note="必须是 0 行。RLS 策略 style_config_select_own：user_id = auth.uid()。"
          uid={probe}
          rows={other?.data ?? null}
          error={other?.error?.message}
        />
      ) : (
        <p className="rounded border border-neutral-200 bg-white p-4 text-sm text-neutral-500">
          没给 <code>?probe=&lt;uuid&gt;</code>。**没有 probe 时本页不构成任何验收**——
          只显示上面那条控制。
        </p>
      )}
    </main>
  );
}

function Result({
  kind,
  title,
  note,
  uid,
  rows,
  error,
}: {
  /** 回归工具按 data-result 取行数，不解析渲染文本。 */
  kind: "self" | "probe";
  title: string;
  note: string;
  uid: string;
  rows: unknown[] | null;
  error?: string;
}) {
  const n = rows?.length ?? 0;
  return (
    <section
      data-result={kind}
      data-rows={error ? "error" : String(n)}
      className="rounded border border-neutral-200 bg-white p-4"
    >
      <h2 className="text-sm font-medium">{title}</h2>
      <p className="mt-1 font-mono text-xs break-all text-neutral-500">user_id = {uid}</p>
      <p className="mt-3 text-2xl tabular-nums">
        {error ? <span className="text-base text-red-700">错误：{error}</span> : `${n} 行`}
      </p>
      <p className="mt-2 text-xs leading-relaxed text-neutral-500">{note}</p>
      {n > 0 && (
        <pre className="mt-3 overflow-x-auto rounded bg-neutral-50 p-3 text-xs">
          {JSON.stringify(rows, null, 2)}
        </pre>
      )}
    </section>
  );
}
