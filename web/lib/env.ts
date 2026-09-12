import { classifySecret } from "./public-env-guard.mjs";

/**
 * 运行期的环境变量读取与校验。
 *
 * 主护栏在构建期（next.config.ts）——到了运行期，`NEXT_PUBLIC_` 的值早已内联进
 * bundle，再发现就晚了。这里管的是另外两件事：**变量缺失**、**配错项目**。
 * 顺带复查一次凭据类型（同一份 classifySecret，不重写判定）：构建期与运行期的
 * 环境未必是同一份，Vercel 上改了变量而不重新构建就是一例。
 *
 * `process.env.NEXT_PUBLIC_*` 必须**字面写全**，Next 是按字面量做替换的，
 * 解构或动态取键都拿不到值。
 */

const URL_ = process.env.NEXT_PUBLIC_SUPABASE_URL;
const KEY_ = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `缺少环境变量 ${name}。本地看 web/.env.example，Vercel 上看 Settings → Environment Variables。`,
    );
  }
  const reason = classifySecret(value);
  if (reason) {
    throw new Error(`${name} ${reason} —— 前端只能拿 publishable key（约束 2 / PRD §12.1）。`);
  }
  return value;
}

export const SUPABASE_URL = required("NEXT_PUBLIC_SUPABASE_URL", URL_);
export const SUPABASE_PUBLISHABLE_KEY = required(
  "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
  KEY_,
);

/**
 * 产品服务端（`api/` · Render）的地址。**上传页与讲解页在浏览器里直连它。**
 *
 * 为什么不走 route handler 代理：Vercel 的 serverless 请求体上限 **4.5MB**，
 * 而上传上限是 50MB（测试集里最大的一份正好 4.5MB）。代理会把大课件挡在
 * Vercel 那一层，而那个失败与「文件太大」长得一样却发生在别的地方。
 *
 * ⚠️ **它不是必需变量。** 缺它时只有上传页/讲解页不可用，而
 * `/`、`/onboarding/*`、`/settings/*` 全部照常——那几页直连 Supabase，
 * 与这个服务无关。用 `required()` 会让**整站因为一个还没部署的服务起不来**，
 * 那是把「一个功能不可用」放大成「全站不可用」。
 *
 * 所以缺失在**页面上**被点名（见 `app/courses/page.tsx`），不在这里抛。
 * 这与 `ANTHROPIC_API_KEY` 缺失时 `/api/style/from-text` 返回 503 并说明
 * 「是服务端未配置」是同一条处置：**说清楚缺的是什么，不静默失败、也不牵连别人。**
 */
export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/+$/, "");

/** 调试路由的构建期开关。Vercel 上不配即为关，生产构建里那个路由不存在。 */
export const DEBUG_ROUTES_ENABLED =
  process.env.NEXT_PUBLIC_ENABLE_DEBUG_ROUTES === "1";
