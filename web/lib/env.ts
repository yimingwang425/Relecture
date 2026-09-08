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

/** 调试路由的构建期开关。Vercel 上不配即为关，生产构建里那个路由不存在。 */
export const DEBUG_ROUTES_ENABLED =
  process.env.NEXT_PUBLIC_ENABLE_DEBUG_ROUTES === "1";
