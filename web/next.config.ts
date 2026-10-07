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

// 让部署**自报它是哪个 commit 构建出来的**（口径 F17）。
//
// 回归工具此前只接受一个 --base 就开跑，从不核对那个域名当前服务的是哪个构建。
// 2026-09-08 就撞上了：分支上最后一个 commit 的 preview 构建失败，别名仍停在上一个
// 成功的部署上，而报告写的是「preview 19 绿」——**读数是真的，读的对象不是我以为的
// 那个**（通则七 #6）。
//
// 值缺失时**不设这个头**，让检查因为「拿不到」而失败。设成 "unknown" 之类的占位
// 会让比对有东西可比，然后永远不相等或永远相等——两种都不是判据（同 F16）。
const commitSha = process.env.VERCEL_GIT_COMMIT_SHA;

// 2026-09-22：**解析根目录上提那一行已经删掉了。**
//
// 它当初只为一件事存在：`lib/style/pairs.ts` 要 import 仓库根下的
// `f1_samples/pairs.json`（成对比较要读那 16 个样例）。成对比较撤掉之后
// （口径 F83）那个文件删了，这一行也就没有理由了。
//
// 连带去掉了两项代价，记在这里免得以后有人「顺手加回来」：
// ① 解析与文件监视的范围不再是整个仓库（`retrieval/cache/` 有 54M / 1586 个文件）；
// ② **部署不再依赖 Vercel 的 Include files outside the Root Directory**。
//    那是一条隐形前提——关掉它构建会 Module not found，而没人会想到去看那个开关。
const nextConfig: NextConfig = {
  pageExtensions: ["tsx", "ts", "jsx", "js", ...(debugRoutes ? ["debug.tsx"] : [])],

  async headers() {
    if (!commitSha) return [];
    return [
      {
        source: "/:path*",
        headers: [{ key: "x-build-commit", value: commitSha }],
      },
    ];
  },
};

export default nextConfig;
