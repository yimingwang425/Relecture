/**
 * 护栏：**任何 `NEXT_PUBLIC_*` 变量都不许装着 secret key。**
 *
 * 写成不依赖 Next / React 的纯 JS，是为了让**构建期**（next.config.ts）与
 * **运行期**（lib/env.ts）用同一份判定。两处各写一份就是漂的入口，
 * 而漂掉之后先失效的通常是没人再看的那一份。
 *
 * ## 为什么构建期那条才是主的
 *
 * 运行期检查发现问题时，`NEXT_PUBLIC_` 前缀的值**已经被 Next 内联进 bundle 了**——
 * 它在第一个访客的浏览器里，泄露已经发生。构建期失败才拦得住。
 * 运行期那条保留，管的是另一件事：变量缺失 / 配错项目。
 *
 * ## 光看字面量抓不到旧版 service_role key
 *
 * 新版 secret key 是 `sb_secret_...`，字面量可见。**旧版（legacy）service key 是
 * 一个 JWT**——`service_role` 这个词在 base64 载荷里，明文里一个字都看不到：
 *
 *     eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.xxx
 *                                          ^ 解开才是 {"role":"service_role"}
 *
 * 所以只 `includes("service_role")` 会放它过去，而它恰恰是危害最大的那个。
 * 必须解 JWT 载荷看 `role`。
 */

/** 值本身是否是一个不该公开的凭据。返回原因字符串，或 null。 */
export function classifySecret(value) {
  if (typeof value !== "string") return null;
  const v = value.trim();
  if (v === "") return null;

  if (v.startsWith("sb_secret_")) {
    return "以 sb_secret_ 开头 —— Supabase secret key";
  }
  if (v.includes("service_role")) {
    return "字面量里含 service_role";
  }
  if (jwtRole(v) === "service_role") {
    return "是一个 role=service_role 的 JWT —— 旧版 service key，明文里看不出来";
  }
  return null;
}

/** 解 JWT 载荷取 role。不是 JWT、解不开、没有 role 一律返回 null。 */
function jwtRole(value) {
  const parts = value.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(Buffer.from(b64, "base64").toString("utf8"));
    return typeof payload?.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

/**
 * 扫一份环境变量，返回所有违规项。
 *
 * **只看 `NEXT_PUBLIC_` 前缀**：非该前缀的变量留在服务端，不进 bundle，
 * 在那里放 secret key 是正常用法（阶段 3 的模型 API key 就该在那儿）。
 * 把它们一起拦掉会让这条护栏在阶段 3 变成障碍，然后被关掉——
 * 一条会被关掉的护栏等于没有护栏。
 */
export function findSecretsInPublicEnv(env) {
  const findings = [];
  for (const [key, value] of Object.entries(env ?? {})) {
    if (!key.startsWith("NEXT_PUBLIC_")) continue;
    const reason = classifySecret(value);
    if (reason) findings.push({ key, reason });
  }
  return findings;
}

/** 构建期入口：有违规就抛，让 `next build` 直接失败。 */
export function assertNoSecretsInPublicEnv(env) {
  const findings = findSecretsInPublicEnv(env);
  if (findings.length === 0) return;
  const lines = findings.map((f) => `  · ${f.key}：${f.reason}`).join("\n");
  throw new Error(
    "\n\n构建中止 —— NEXT_PUBLIC_ 变量里发现了 secret 凭据：\n" +
      lines +
      "\n\nNEXT_PUBLIC_ 前缀的值会被内联进浏览器 bundle。构建一旦通过，" +
      "它就在每个访客的浏览器里了。\n" +
      "前端只该拿 publishable key（sb_publishable_...）；" +
      "secret key 放在不带该前缀的服务端变量里。\n" +
      "见 PRD §12.1 与任务书约束 2。\n",
  );
}
