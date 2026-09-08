import type { NextConfig } from "next";
import { assertNoSecretsInPublicEnv } from "./lib/public-env-guard.mjs";

// 构建期护栏。放在最顶上、任何配置计算之前——它一旦通过，后面所有 NEXT_PUBLIC_
// 的值都会被内联进 bundle，那时再拦已经没有意义了。
assertNoSecretsInPublicEnv(process.env);

// 调试路由的构建期开关（不是运行期的 notFound()）。
//
// 差别是实的：`notFound()` 版本里，路由**仍在构建产物里**，页面代码仍被打包，
// 只是运行时返回 404——防护取决于一个运行期分支没被绕过。这里用 pageExtensions
// 把 `page.debug.tsx` 整个排除在路由解析之外，关掉时它连模块都不会被编译进去。
//
// 断言见 scripts/verify_build_guards.mjs：关掉时构建产物里不得出现该路由。
const debugRoutes = process.env.NEXT_PUBLIC_ENABLE_DEBUG_ROUTES === "1";

const nextConfig: NextConfig = {
  pageExtensions: ["tsx", "ts", "jsx", "js", ...(debugRoutes ? ["debug.tsx"] : [])],
};

export default nextConfig;
