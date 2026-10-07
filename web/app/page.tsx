import type { Metadata } from "next";
import Link from "next/link";
import type { CSSProperties } from "react";
import { redirect } from "next/navigation";

import { ApiWarmup } from "@/app/components/api-warmup";
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
            eyebrow={courses.length > 0 ? `我的课程 · ${courses.length} 门` : "我的课程"}
            title={
              courses.length > 0 ? (
                <>
                  打开一门课，<em>开始提问</em>
                </>
              ) : (
                <>
                  先建<em>一门课</em>
                </>
              )
            }
            lead={
              courses.length > 0
                ? "一门课就是一个项目：它的课件、提问和讲解都在里面。"
                : "一门课就是一个项目。建好之后往里传 PDF 讲义，就可以在里面提问了。"
            }
          />

          <div className="grid items-start gap-x-16 gap-y-14 lg:grid-cols-[minmax(0,1fr)_340px]">
            {courses.length === 0 ? (
              <div className="index enter-late lg:order-1">
                <p className="t-meta py-8" data-field="no-courses">
                  还没有课程。用{API_BASE_URL ? "旁边" : "上面"}的输入框建第一门。
                </p>
              </div>
            ) : (
              <ul className="index stagger" data-field="courses">
                {courses.map((c, i) => (
                  <li key={c.id} style={{ "--i": i } as CSSProperties}>
                    <Link
                      href={`/courses/${c.id}`}
                      data-field="course-card"
                      data-course-id={c.id}
                      className="index__row"
                    >
                      <span className="index__num">{String(i + 1).padStart(2, "0")}</span>
                      <span className="index__name">{c.name}</span>
                      <span className="index__meta">
                        <span className="index__date">建于 {new Date(c.created_at).toLocaleDateString("zh-CN")}</span>
                        <span className="index__arrow" aria-hidden>
                          →
                        </span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}

            {API_BASE_URL ? (
              <section className="enter-late lg:sticky lg:top-8">
                <p className="t-eyebrow mb-4">新建一门课</p>
                {/* 叫醒它，**在用户开始打字之前**。建课是打开这一页之后最可能做的事，
                    而那一次请求正好撞上冷启动（F74）。 */}
                <div className="mb-4 empty:hidden">
                  <ApiWarmup apiBase={API_BASE_URL} />
                </div>
                <NewCourse apiBase={API_BASE_URL} />
                <p className="t-caption mt-4">建好之后在课里上传 PDF 讲义，就能对着它提问。</p>
              </section>
            ) : (
              <p role="alert" data-field="no-api-base" className="notice notice--warn">
                <b>还没配 <code>NEXT_PUBLIC_API_BASE_URL</code>。</b>
                建课、上传与讲解要用产品服务端（<code>api/</code>，部署在 Render）。
                配好之后刷新即可 —— 课程列表不受影响，它直连 Supabase。
              </p>
            )}
          </div>
        </>
      )}

      <Diagnostics
        className="mt-24"
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
