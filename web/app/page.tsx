import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppNav } from "@/app/components/app-nav";
import { ConfigSummary } from "@/app/components/config-summary";
import { Diagnostics } from "@/app/components/diagnostics";
import { DEBUG_ROUTES_ENABLED } from "@/lib/env";
import { configOf, type StyleRow } from "@/lib/style/session";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: { absolute: "Relecture" } };

const RAW_FIELDS = ["detail_level", "use_examples", "structure", "depth"] as const;

export default async function HomePage() {
  const supabase = await createClient();

  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/welcome");

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
    <main data-state={state} className="flex min-h-screen flex-col">
      <div className="px-[var(--page-margin)] pt-4">
        <AppNav email={auth.user.email} />
      </div>

      <section className="mx-auto w-full max-w-[880px] px-[var(--page-margin)] pt-[clamp(48px,10vh,112px)] text-center">
        <h1 className="t-display enter-wipe">
          今天想弄懂
          <br />
          课件里的哪一部分？
        </h1>
        <p className="t-body enter-late mx-auto mt-4 max-w-[48ch] text-balance">
          选一门课，直接问。讲解按你偏好的讲法来，只用你上传的材料，出处标到页。
        </p>
        <div className="enter-late mt-7 flex flex-wrap justify-center gap-3">
          <Link href="/explain" className="btn btn--primary btn--lg" data-field="to-explain">
            开始提问
          </Link>
          <Link href="/courses" className="btn btn--ghost btn--lg" data-field="to-courses">
            上传课件
          </Link>
        </div>
      </section>

      <section className="relative mt-[clamp(40px,7vh,72px)] flex-1">
        <div aria-hidden className="band absolute inset-x-0 top-[88px] bottom-0" />

        <div className="relative z-10 mx-auto w-[min(640px,calc(100%-2*var(--page-margin)))] pb-[clamp(40px,8vh,88px)]">
          {error ? (
            <p role="alert" className="notice notice--danger card--raised">
              读取失败：{error.message}（{error.code}）
            </p>
          ) : rows?.length !== 1 ? (
            // 0 行不是「还没配置」，是坏状态：F09 的触发器保证每个用户都有且只有一行。
            // 显示成空白会让这个坏状态看起来像正常的初始态。
            <p role="alert" className="notice notice--warn card--raised bg-white">
              读到 {rows?.length ?? 0} 行，应为 1 行。每个用户注册时由触发器建行（口径 F09），
              读到 0 行说明触发器没跑或被 RLS 挡住，不是「尚未配置」。
            </p>
          ) : (
            <div className="card card--raised enter-later p-2">
              <div className="flex items-start justify-between gap-4 px-3 pt-3 pb-4">
                <div className="min-w-0">
                  <p className="t-eyebrow"># 我的讲解风格</p>
                  <p className="t-title mt-1">
                    {rows[0].onboarded_at ? "按这四条给你讲" : "还没按你的偏好设置过"}
                  </p>
                  <p className="t-meta mt-1">
                    {rows[0].onboarded_at
                      ? "每次讲解都按它来；读完觉得不对，随时可以调。"
                      : "现在用的是默认配置。走一遍 6 步的风格引导，会更贴合你。"}
                  </p>
                </div>
                <Link href="/settings/style" className="btn btn--ghost btn--sm shrink-0">
                  调整
                </Link>
              </div>

              <div className="tabs" aria-hidden>
                <span className="tab" data-active="true">
                  四条轴
                </span>
              </div>
              <div className="tabpanel p-3">
                <ConfigSummary config={configOf(rows[0] as StyleRow)} showEnforcedNote={false} />
                {!rows[0].onboarded_at && (
                  <div className="notice notice--info mt-3 flex flex-wrap items-center justify-between gap-3">
                    <span>让它先认识一下你想怎么被讲解。</span>
                    <Link href="/onboarding" className="btn btn--primary btn--sm">
                      开始风格引导
                    </Link>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </section>

      <div className="mx-auto w-full max-w-[880px] px-[var(--page-margin)] py-6">
        <Diagnostics
          items={[
            ["账号", auth.user.email ?? "—"],
            ["user_id", auth.user.id, "user-id"],
            ...(rows?.length === 1
              ? ([
                  ...RAW_FIELDS.map((key) => [key, String(rows[0][key]), key] as [string, string, string]),
                  ["f1_arm", String(rows[0].f1_arm), "f1_arm"],
                  [
                    "onboarded_at",
                    rows[0].onboarded_at ? String(rows[0].onboarded_at) : "null（未完成 onboarding）",
                  ],
                ] as [string, string, string?][])
              : []),
            ...(DEBUG_ROUTES_ENABLED
              ? ([["调试路由", "/debug/rls?probe=<uuid>"]] as [string, string][])
              : []),
          ]}
        />
      </div>
    </main>
  );
}
