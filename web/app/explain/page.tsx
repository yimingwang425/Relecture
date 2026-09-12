import { AppNav } from "@/app/components/app-nav";
import { RowStateNotice } from "@/app/components/row-state";
import { API_BASE_URL } from "@/lib/env";
import { newSessionId } from "@/lib/style/events";
import { configOf, loadStyleRow } from "@/lib/style/session";

import { ExplainClient } from "./explain-client";

export const dynamic = "force-dynamic";

/**
 * 讲解页（阶段 6 第二批）。**这是用户第一次能用自己的课件问问题。**
 *
 * ## 为什么这一页要读 `style_config`
 *
 * 两个用途，都不是「渲染配置」：
 *
 * 1. `f1_arm` —— 服务端发的那四个事件要带臂（臂由 `user_id` 确定，口径 F08）。
 * 2. **把当前档位显示出来**。讲解的详略由它决定，而用户在这一页看到的是结果；
 *    看不到「现在是哪一档」的话，「讲得太细了」这种不满没有可操作的出口
 *    （设置页在另一条路径上，口径 F05 的反馈按钮在那里）。
 *
 * ⚠️ 仍然**不显示档位的区间数字**（如「30–120 字/段」）：那些数值只存在于
 * `scan/points.py` 的 `AXIS_POINTS`，抄进 TS 就是 F13 那个坑。
 * 与设置页同一处置——用文字描述档位。
 *
 * ## `sessionId` 在服务端抽，且它是**一次页面加载一个**
 *
 * 客户端组件的渲染在 SSR 与 hydrate 各跑一次，渲染期取随机数两侧会分叉
 * （React #418）。而这一页比别的页更在意这件事：**`session_id` 是「平均会话
 * 轮次」的分组键**，两侧分叉会让同一次会话的轮次落到两个 session 上。
 *
 * 那个占位口径的偏差方向（刷新即新会话 → 系统性压低轮次）如实登记在
 * `lib/explain/turns.ts` 的模块说明里。
 */
export default async function ExplainPage() {
  const result = await loadStyleRow();

  return (
    <main
      // 终态标记。回归工具按它等待与断言，不按中文文案。
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
          讲解要用产品服务端（<code>api/</code>，部署在 Render），这一页在浏览器里直连它。
          配好之后刷新即可 —— 本站其余页面不受影响，它们直连 Supabase。
        </p>
      ) : (
        <ExplainClient
          apiBase={API_BASE_URL}
          sessionId={newSessionId()}
          detailLevel={configOf(result.row).detail_level}
        />
      )}
    </main>
  );
}
