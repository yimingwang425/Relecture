import { AppNav } from "@/app/components/app-nav";
import { RowStateNotice } from "@/app/components/row-state";
import { API_BASE_URL } from "@/lib/env";
import { newSessionId } from "@/lib/style/events";
import { loadStyleRow } from "@/lib/style/session";

import { CourseUpload } from "./upload-client";

export const dynamic = "force-dynamic";

/**
 * 课程与课件上传（阶段 6 第一批）。
 *
 * ## 为什么这一页要读 `style_config`
 *
 * 它不需要风格配置本身 —— 需要的是 `f1_arm`：那一页发的埋点事件
 * （`deck_uploaded` / `deck_upload_failed`，由**服务端**发）要带臂，
 * 而臂由 `user_id` 确定（口径 F08）。
 *
 * 复用 `loadStyleRow()` 的第二个理由是它的三分：`ok` / `error` / `bad-row-count`。
 * **读到 0 行是坏状态不是空状态**（口径 F09）——这一页照样要把它显示出来，
 * 而不是渲染一个看起来正常的空列表。
 *
 * ## `sessionId` 在服务端抽
 *
 * 客户端组件的渲染在 SSR 与 hydrate 各跑一次，渲染期取随机数两侧会分叉
 * （React #418，阶段 3 在 `makeFlips` 上踩过一次）。
 * 「一次页面加载 = 一个 session」是**占位口径**，PRD §3.4.5 的切分规则未定。
 *
 * ## ⚠️ `API_BASE_URL` 缺失在这里点名，不在 `lib/env.ts` 抛
 *
 * 用 `required()` 会让**整站因为一个还没部署的服务起不来**——而
 * `/`、`/onboarding/*`、`/settings/*` 直连 Supabase，与那个服务无关。
 * 那是把「一个功能不可用」放大成「全站不可用」。
 */
export default async function CoursesPage() {
  const result = await loadStyleRow();

  return (
    <main
      // 终态标记。回归工具按它等待与断言，**不按中文文案**——
      // 改一次文案不该让断言静默变绿（同 app/page.tsx 那条）。
      data-state={result.state === "ok" && !API_BASE_URL ? "no-api-base" : result.state}
      className="mx-auto w-full max-w-[880px] px-5 py-8"
    >
      <AppNav email={result.email} />

      {result.state !== "ok" ? (
        <div className="mt-10">
          <RowStateNotice result={result} />
        </div>
      ) : !API_BASE_URL ? (
        <p
          role="alert"
          data-field="no-api-base"
          className="mt-10 rounded border border-amber-200 bg-amber-50 p-4 text-sm leading-relaxed text-amber-900"
        >
          <b>还没配 <code>NEXT_PUBLIC_API_BASE_URL</code>。</b>
          上传与讲解要用产品服务端（<code>api/</code>，部署在 Render），
          这一页在浏览器里直连它。配好之后刷新即可 —— 本站其余页面不受影响，
          它们直连 Supabase。
          <br />
          <span className="text-amber-700">
            连带：那个服务要把本站来源列进 <code>CORS_ALLOW_ORIGINS</code>。
            它不配就是空清单（浏览器一律被挡），不是默认放开。
          </span>
        </p>
      ) : (
        <CourseUpload
          apiBase={API_BASE_URL}
          userId={result.userId}
          arm={result.row.f1_arm}
          sessionId={newSessionId()}
        />
      )}
    </main>
  );
}
