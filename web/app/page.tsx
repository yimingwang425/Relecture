import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { BrandLine } from "@/app/components/brand-line";
import { Diagnostics } from "@/app/components/diagnostics";
import { NewCourse } from "@/app/components/new-course";
import { PageHeader } from "@/app/components/page-header";
import { API_BASE_URL, DEBUG_ROUTES_ENABLED } from "@/lib/env";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: { absolute: "Relecture" } };

type Course = { id: string; name: string; created_at: string };

/**
 * 首页 = **我的课程**。一门课就是一个项目：点进去在里面提问。
 *
 * ## 还没设置过风格的人直接送去设置（2026-09-19）
 *
 * 讲解的详略、举例、结构、深度都由那份配置驱动。没走过一遍就先提问，
 * 拿到的是默认档，而用户并不知道这是可调的——F1 的整个前提（按你的偏好讲）
 * 在他眼里不存在。所以 `onboarded_at` 为空时**直接进风格选择**，不给一个可跳过的入口。
 *
 * 走完那一遍会写 `onboarded_at`，此后这一跳不再发生。
 *
 * ## 课程列表从 Supabase 直读
 *
 * `courses` 是 `owner = auth.uid()` 的 RLS。服务端组件读自己的行即可，
 * 不必绕产品服务端——那会把「我有哪些课」绑在免费层的冷启动上（F74），而两件事无关。
 * 建课仍走产品服务端（`NewCourse`）：建课与上传是同一条链路，不另开第二条写入路径。
 */
export default async function HomePage() {
  const supabase = await createClient();

  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) redirect("/welcome");

  const { data: rows, error } = await supabase
    .from("style_config")
    .select("*")
    .eq("user_id", auth.user.id);

  // 终态标记。三个分支必须**恰好**给出一个 data-state，供回归工具等待与断言。
  // 与下面 JSX 的条件是同一个表达式写了两遍（TS 的收窄只认字面条件）。
  const state = error ? "error" : rows?.length !== 1 ? "bad-row-count" : "ok";

  // 第一次进来：先选讲解风格。**放在读到行之后**——读不到行是坏状态（F09），
  // 那种情况要把坏状态显示出来，而不是把人送进 onboarding 转一圈。
  if (state === "ok" && !rows![0].onboarded_at) redirect("/onboarding");

  const { data: courseRows } = await supabase
    .from("courses")
    .select("id, name, created_at")
    .order("created_at", { ascending: false });
  const courses = (courseRows ?? []) as Course[];

  return (
    <main data-state={state} className="shell">
      <BrandLine>
        <Link href="/settings" className="link">
          设置
        </Link>
      </BrandLine>

      {error ? (
        <p role="alert" className="notice notice--danger mt-12">
          读取失败：{error.message}（{error.code}）
        </p>
      ) : rows?.length !== 1 ? (
        // 0 行不是「还没配置」，是坏状态：F09 的触发器保证每个用户都有且只有一行。
        <p role="alert" className="notice notice--warn mt-12">
          读到 {rows?.length ?? 0} 行，应为 1 行。每个用户注册时由触发器建行（口径 F09），
          读到 0 行说明触发器没跑或被 RLS 挡住，不是「尚未配置」。
        </p>
      ) : (
        <>
          <PageHeader
            eyebrow="我的课程"
            title={courses.length > 0 ? "打开一门课，开始提问" : "先建一门课"}
            lead={
              courses.length > 0
                ? "一门课就是一个项目：它的课件、提问和讲解都在里面。"
                : "一门课就是一个项目。建好之后往里传 PDF 讲义，就可以在里面提问了。"
            }
          />

          {API_BASE_URL ? (
            <div className="enter mb-8 max-w-[520px]">
              <NewCourse apiBase={API_BASE_URL} />
            </div>
          ) : (
            <p role="alert" data-field="no-api-base" className="notice notice--warn mb-8">
              <b>还没配 <code>NEXT_PUBLIC_API_BASE_URL</code>。</b>
              建课、上传与讲解要用产品服务端（<code>api/</code>，部署在 Render）。
              配好之后刷新即可 —— 课程列表不受影响，它直连 Supabase。
            </p>
          )}

          {courses.length === 0 ? (
            <p className="t-meta" data-field="no-courses">
              还没有课程。用上面的输入框建第一门。
            </p>
          ) : (
            <ul className="enter-late grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-field="courses">
              {courses.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/courses/${c.id}`}
                    data-field="course-card"
                    data-course-id={c.id}
                    className="card card--raised flex h-full flex-col justify-between gap-6 p-5 no-underline transition-transform hover:-translate-y-0.5"
                  >
                    <span className="t-title block">{c.name}</span>
                    <span className="t-caption">
                      建于 {new Date(c.created_at).toLocaleDateString("zh-CN")} · 进入提问 →
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <Diagnostics
        className="mt-12"
        items={[
          ["账号", auth.user.email ?? "—"],
          ["user_id", auth.user.id, "user-id"],
          ["课程数", String(courses.length)],
          ...(DEBUG_ROUTES_ENABLED
            ? ([["调试路由", "/debug/rls?probe=<uuid>"]] as [string, string][])
            : []),
        ]}
      />
    </main>
  );
}
