/**
 * 两条构建期护栏的验证 —— **每条都跑一次真实的 `next build`**。
 *
 *   npm run verify:guards
 *
 * ## 为什么单元测试不够
 *
 * `findSecretsInPublicEnv` 单测全绿，只证明这个函数会算；
 * 它证明不了 **next.config.ts 真的调了它**。F3a 那次 59 条测试全绿、
 * `AnthropicModel.request_signature` 被批量改名成 `requestsignature` 一条没红，
 * 就是这个形态（通则七 #7：这个对象被哪条断言碰过？）。
 *
 * 所以下面两组断言：便宜的单元用例覆盖判定逻辑的分支，**真构建**覆盖接线。
 */

import { execFileSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { classifySecret, findSecretsInPublicEnv } from "../lib/public-env-guard.mjs";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));

let passed = 0;
let failed = 0;

/** 一条断言。`okDetail` / `badDetail` 分开，是因为失败时要看的通常是构建输出。 */
function check(cond, name, okDetail = "", badDetail = "") {
  const detail = cond ? okDetail : badDetail;
  if (cond) passed++;
  else failed++;
  const mark = cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m";
  console.log(`  ${mark} ${name}${detail ? "  " + detail : ""}`);
}

/** 一个 role=service_role 的 JWT。明文里看不到 service_role 这几个字。 */
const LEGACY_SERVICE_JWT =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  Buffer.from(JSON.stringify({ role: "service_role", iss: "supabase" })).toString("base64url") +
  ".c2ln";
const LEGACY_ANON_JWT =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  Buffer.from(JSON.stringify({ role: "anon", iss: "supabase" })).toString("base64url") +
  ".c2ln";

console.log("\n\x1b[1m一 · 判定逻辑\x1b[0m");
{
  const cases = [
    ["sb_secret_abc123", true, "新版 secret key"],
    [LEGACY_SERVICE_JWT, true, "旧版 service_role JWT —— 明文里没有 service_role 这几个字"],
    ["eyJ.service_role.x", true, "字面量含 service_role"],
    ["sb_publishable_abc123", false, "publishable key"],
    [LEGACY_ANON_JWT, false, "旧版 anon JWT"],
    ["https://x.supabase.co", false, "URL"],
    ["", false, "空值"],
  ];
  for (const [value, shouldCatch, label] of cases) {
    const caught = classifySecret(value) !== null;
    check(
      caught === shouldCatch,
      label,
      shouldCatch ? "→ 拦" : "→ 放行",
      `期望 ${shouldCatch ? "拦" : "放行"}，实际 ${caught ? "拦" : "放行"}`,
    );
  }

  // 确认那个 JWT 用例不是靠字面量绿的——否则它测的是上一条，不是 JWT 解码。
  check(
    !LEGACY_SERVICE_JWT.includes("service_role"),
    "旧版 JWT 用例确实不含明文 service_role",
    "← 它测的是解码路径",
    "← 它测的是字面量分支，JWT 解码从没被碰过",
  );

  const scoped = findSecretsInPublicEnv({
    NEXT_PUBLIC_SUPABASE_URL: "https://x.supabase.co",
    NEXT_PUBLIC_BAD: "sb_secret_x",
    SUPABASE_SECRET_KEY: "sb_secret_x",
  });
  check(
    scoped.length === 1 && scoped[0].key === "NEXT_PUBLIC_BAD",
    "只扫 NEXT_PUBLIC_ 前缀",
    "非该前缀的 secret 留在服务端是正常用法，拦了会让护栏被关掉",
    JSON.stringify(scoped),
  );
}

// ---------------------------------------------------------------- 真构建

const BASE_ENV = {
  ...process.env,
  NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_fake_for_build_only",
  NEXT_PUBLIC_ENABLE_DEBUG_ROUTES: "",
  NEXT_TELEMETRY_DISABLED: "1",
};

function build(extraEnv) {
  rmSync(join(WEB, ".next"), { recursive: true, force: true });
  try {
    const out = execFileSync("npx", ["next", "build"], {
      cwd: WEB,
      env: { ...BASE_ENV, ...extraEnv },
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { failedBuild: false, out };
  } catch (e) {
    return { failedBuild: true, out: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

console.log("\n\x1b[1m二 · 接线（真实 next build）\x1b[0m");

{
  // 反向验证（补充二指定）：故意把 secret key 配进一个 NEXT_PUBLIC_ 变量。
  const r = build({ NEXT_PUBLIC_TEST_KEY: "sb_secret_fake" });
  check(
    r.failedBuild && r.out.includes("NEXT_PUBLIC_TEST_KEY"),
    "NEXT_PUBLIC_TEST_KEY=sb_secret_fake → 构建失败",
    "且报出了是哪个变量",
    r.out.slice(-400),
  );

  const r2 = build({ NEXT_PUBLIC_TEST_KEY: LEGACY_SERVICE_JWT });
  check(
    r2.failedBuild,
    "旧版 service_role JWT → 构建失败",
    "← 明文扫不出来的那种也拦住了",
    "混过了构建",
  );

  const r3 = build({});
  check(!r3.failedBuild, "干净的 env → 构建成功", "← 护栏不是「永远失败」", r3.out.slice(-400));
}

{
  // 调试路由的构建期开关。断言的是**产物里有没有这条路由**，
  // 不是「运行时返回不返回 404」——后者在路由仍被打包时也能绿。
  const off = build({});
  const offHasRoute =
    off.out.includes("/debug/rls") || existsSync(join(WEB, ".next/server/app/debug"));
  check(
    !offHasRoute,
    "不配 NEXT_PUBLIC_ENABLE_DEBUG_ROUTES → 产物里没有 /debug/rls",
    "",
    "关掉了但路由仍在产物里",
  );

  const on = build({ NEXT_PUBLIC_ENABLE_DEBUG_ROUTES: "1" });
  check(
    on.out.includes("/debug/rls"),
    "配成 1 → 产物里有 /debug/rls",
    "← 上一条不是因为这个页面根本编译不过",
    on.out.slice(-400),
  );
}

console.log(`\n\x1b[1m合计\x1b[0m  绿 ${passed} · 红 ${failed}\n`);
process.exit(failed === 0 ? 0 : 1);
