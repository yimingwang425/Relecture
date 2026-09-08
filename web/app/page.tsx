import { redirect } from "next/navigation";

import { LogoutButton } from "@/app/components/logout-button";
import { DEBUG_ROUTES_ENABLED } from "@/lib/env";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const FIELDS = [
  ["detail_level", "详细程度"],
  ["use_examples", "举例"],
  ["structure", "结构"],
  ["depth", "深度"],
] as const;

export default async function HomePage() {
  const supabase = await createClient();

  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/login");

  const { data: rows, error } = await supabase
    .from("style_config")
    .select("*")
    .eq("user_id", auth.user.id);

  // 终态标记。三个分支必须**恰好**给出一个 data-state，供回归工具等待与断言。
  //
  // 之前回归工具是按中文文案做字符串匹配的，两个毛病：
  // ① 页面是流式渲染的，`networkidle` 之后 DOM 可能还没流完，读到中间态会假红；
  // ② 改一次文案，断言会**静默变绿**——它匹配不到「读取失败」，于是「没出错」成立。
  // 挂一个不随文案变化的标记，两个问题一起解决。
  // 与下面 JSX 的条件是同一个表达式写了两遍（TS 的收窄只认字面条件，
  // 用 `state` 做分支会丢掉 `rows` 非空的推断）。两处若漂了，回归工具会红：
  // data-state 说 ok 而四个字段取不到。
  const state = error ? "error" : rows?.length !== 1 ? "bad-row-count" : "ok";

  return (
    <main
      data-state={state}
      className="mx-auto flex min-h-screen max-w-lg flex-col gap-6 p-6 pt-16"
    >
      <header className="flex items-baseline justify-between">
        <h1 className="text-lg font-medium">我的风格配置</h1>
        <LogoutButton />
      </header>

      <dl className="rounded border border-neutral-200 bg-white text-sm">
        <Row label="账号" value={auth.user.email ?? "—"} />
        <Row label="user_id" value={auth.user.id} mono />
      </dl>

      {error ? (
        <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          读取失败：{error.message}（{error.code}）
        </p>
      ) : rows?.length !== 1 ? (
        // 0 行不是「还没配置」，是坏状态：F09 的触发器保证每个用户都有且只有一行。
        // 显示成空白会让这个坏状态看起来像正常的初始态。
        <p role="alert" className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          读到 {rows?.length ?? 0} 行，应为 1 行。每个用户注册时由触发器建行（口径 F09），
          读到 0 行说明触发器没跑或被 RLS 挡住，不是「尚未配置」。
        </p>
      ) : (
        <>
          <dl className="rounded border border-neutral-200 bg-white text-sm">
            {FIELDS.map(([key, label]) => (
              <Row key={key} field={key} label={label} value={String(rows[0][key])} mono />
            ))}
          </dl>

          <dl className="rounded border border-neutral-200 bg-white text-sm">
            <Row field="f1_arm" label="f1_arm（AB 分组）" value={String(rows[0].f1_arm)} mono />
            <Row
              label="onboarded_at"
              value={rows[0].onboarded_at ? String(rows[0].onboarded_at) : "null（未完成 onboarding）"}
              mono
            />
          </dl>

          <p className="text-xs leading-relaxed text-neutral-500">
            四条轴中只有<b>详细程度</b>被 A 类审查执行；举例 / 结构 / 深度仅进 prompt，
            <code>enforced=false</code>（PRD §8.5 未校准前不进判定）。
          </p>
        </>
      )}

      {DEBUG_ROUTES_ENABLED && (
        <p className="text-xs text-neutral-400">
          调试路由已启用：<code>/debug/rls?probe=&lt;uuid&gt;</code>
        </p>
      )}
    </main>
  );
}

function Row({
  label,
  value,
  mono,
  field,
}: {
  label: string;
  value: string;
  mono?: boolean;
  /** 回归工具按它定位，不按中文标签——改文案不该让断言静默变绿。 */
  field?: string;
}) {
  return (
    <div className="flex justify-between gap-4 border-b border-neutral-100 px-4 py-2.5 last:border-0">
      <dt className="shrink-0 text-neutral-500">{label}</dt>
      <dd data-field={field} className={`text-right ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
