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
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { classifySecret, findSecretsInPublicEnv } from "../lib/public-env-guard.mjs";
import { scanDir, scanText } from "../lib/secret-scan.mjs";

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

// ---------------------------------------------------------------- 产物扫描

console.log("\n\x1b[1m三 · 构建产物里有没有真实凭据（口径 F16）\x1b[0m");

{
  // 判据的两个必要性质。**先验这两条**，否则下面「.next 里 0 命中」什么也不说明。
  const guardSrc = readFileSync(join(WEB, "lib/public-env-guard.mjs"), "utf8");
  check(
    scanText(guardSrc).length === 0,
    "护栏自身源码不命中",
    "← 匹配字符串本身的判据会永远命中这里，从此不可用",
    `命中 ${scanText(guardSrc).length} 处`,
  );

  const fakeKey = "sb_secret_" + "x9K2mQ7pL4vR8tN3wZ6y";
  check(scanText(fakeKey).length === 1, "真 key 形态命中", "sb_secret_ + ≥20 个 key 字符");

  const mkJwt = (role) =>
    "eyJhbGciOiJIUzI1NiJ9." +
    Buffer.from(JSON.stringify({ role })).toString("base64url") +
    ".c2lnc2ln";
  check(scanText(mkJwt("service_role")).length === 1, "service_role JWT 命中", "解载荷判的，不看字面量");
  check(scanText(mkJwt("anon")).length === 0, "anon JWT 不命中", "← 判据不是「凡是 JWT 都算」");

  // 阶段 3 新增的凭据类型：对照臂 route handler 用的模型 API key。
  // 两条性质与上面同构——真 key 形态要命中，源码/示例里的前缀字面量不能命中，
  // 否则这条判据会像当初的 `sb_secret_` 那样被自己的护栏永久钝化。
  const fakeAnthropic = "sk-ant-api03-" + "T7hQ2xM9vB4kL8nR3wZ6";
  check(scanText(fakeAnthropic).length === 1, "模型 API key 形态命中", "sk-ant- + ≥20 个 key 字符");
  check(
    scanText(readFileSync(join(WEB, ".env.example"), "utf8")).length === 0,
    ".env.example 里的 sk-ant-... 占位不命中",
    "← 占位命中的话，「产物 0 命中」就永远读不出信号",
  );
}

{
  // 阶段 3 的新暴露面：对照臂要一个**服务端**模型 API key。
  //
  // 「它不带 NEXT_PUBLIC_ 前缀，所以不会进 bundle」是**推理**，不是测量。
  // 这里把一个真 key 形态的值真的注进构建环境，然后扫产物——
  // 万一哪天有人把它改名成 NEXT_PUBLIC_ANTHROPIC_API_KEY（或在客户端组件里读它），
  // 这条会红。判据的分辨力上面已经验过（真形态命中 / 占位不命中 / canary 能红）。
  const CANARY_KEY = "sk-ant-api03-" + "J4dW8sK1qF6mX2bV9pT5";
  const withKey = build({ ANTHROPIC_API_KEY: CANARY_KEY });
  if (withKey.failedBuild) {
    check(false, "带 ANTHROPIC_API_KEY 的构建", "", withKey.out.slice(-400));
  } else {
    const { filesScanned, hits } = await scanDir(join(WEB, ".next"));
    check(filesScanned > 0, "确实扫到了文件（服务端 key 这一轮）", `${filesScanned} 个`);
    check(
      hits.length === 0,
      "环境里有真实模型 key，产物里仍 0 命中",
      "← 服务端变量没有被内联进任何产物",
      hits.map((h) => `${h.kind} @ ${h.file}`).join("\n      "),
    );
  }
}

{
  // 阶段 4 的新暴露面：邀请制脚本要一个 **service_role key**（口径 F40）。
  //
  // 它绕过全部 RLS —— 进了前端 bundle 等于把整个数据库开放出去，**且不会报错**。
  // 「它住在 scripts/.env、web 从来不读它，所以不会进产物」是**推理**，不是测量。
  // 这里把一个真 key 形态的值真的注进 `next build` 的环境再扫产物。
  //
  // 判据的分辨力上面 §三 已经先验过（sb_secret_ + ≥20 个 key 字符命中、
  // 护栏源码不命中），所以这一条读出来的 0 命中才携带信息。
  const SERVICE_CANARY = "sb_secret_" + "Q8mN3xV7bK2rT5wZ9jL4";
  check(scanText(SERVICE_CANARY).length === 1, "service_role canary 本身可被扫出", "← 先验分辨力");

  const withService = build({ SUPABASE_SECRET_KEY: SERVICE_CANARY });
  if (withService.failedBuild) {
    check(false, "带 SUPABASE_SECRET_KEY 的构建", "", withService.out.slice(-400));
  } else {
    const { filesScanned, hits } = await scanDir(join(WEB, ".next"));
    check(filesScanned > 0, "确实扫到了文件（service_role 这一轮）", `${filesScanned} 个`);
    check(
      hits.length === 0,
      "环境里有真实 service_role key，产物里仍 0 命中",
      "← 它没有被内联进任何产物",
      hits.map((h) => `${h.kind} @ ${h.file}`).join("\n      "),
    );
  }

  // 反向控制：**带 NEXT_PUBLIC_ 前缀就必须让构建直接失败**。
  // 这是最可能出事的那条路——有人为了「前端也要用一下」顺手加个前缀。
  const misnamed = build({ NEXT_PUBLIC_SUPABASE_SECRET_KEY: SERVICE_CANARY });
  check(
    misnamed.failedBuild && misnamed.out.includes("NEXT_PUBLIC_SUPABASE_SECRET_KEY"),
    "NEXT_PUBLIC_SUPABASE_SECRET_KEY=<真 key 形态> → 构建失败并点名变量",
    "← 改名加前缀是最可能出事的那条路",
    misnamed.out.slice(-400),
  );

  // 源码层面：`web/` 里不该有任何地方读这个变量。它是运维脚本的东西。
  // 上面那条真构建管的是「值有没有进产物」，这条管的是「有没有人开始依赖它」——
  // 一旦 app 里出现一处读它的代码，下一步就是有人给它加前缀。
  const readsIt = [];
  (function walk(dir) {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, e.name);
      if (e.isDirectory()) walk(full);
      else if (/\.(tsx?|mjs|js)$/.test(e.name) && readFileSync(full, "utf8").includes("SUPABASE_SECRET_KEY")) {
        readsIt.push(full);
      }
    }
  })(join(WEB, "app"));
  check(readsIt.length === 0, "web/app 里没有任何地方读 SUPABASE_SECRET_KEY", "", readsIt.join(" "));
}

{
  // 干净构建的产物必须 0 命中。**filesScanned 必须 > 0**——目录写错时命中数
  // 同样是 0，那种「0 命中」与「A 读 B 返回空」在 B 无行时也成立同形（通则七 #5）。
  const clean = build({});
  if (clean.failedBuild) {
    check(false, "扫描前的干净构建", "", clean.out.slice(-400));
  } else {
    const { filesScanned, hits } = await scanDir(join(WEB, ".next"));
    check(filesScanned > 0, "确实扫到了文件", `${filesScanned} 个`, "0 个 —— 下一条的「0 命中」是空的");
    check(
      hits.length === 0,
      ".next 里没有真实凭据",
      `扫了 ${filesScanned} 个文件`,
      hits.map((h) => `${h.kind} @ ${h.file}`).join("\n      "),
    );
  }
}

{
  // 反向验证：把一个真 key 形态的字符串**写进源码**并真的构建一次，扫描必须红。
  //
  // 为什么不用「塞进 NEXT_PUBLIC_ 变量」：那条路在 next.config.ts 的护栏处就
  // 构建失败了（见上面「二」），根本产不出产物，扫描无从谈起。**源码里硬编码
  // 正是环境变量护栏看不见的那条路**，也就是产物扫描存在的理由。
  const canaryDir = join(WEB, "app/scan-canary-tmp");
  const canaryValue = "sb_secret_" + Array.from({ length: 24 }, () =>
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"[
      Math.floor(Math.random() * 62)
    ]).join("");

  try {
    mkdirSync(canaryDir, { recursive: true });
    writeFileSync(
      join(canaryDir, "page.tsx"),
      '"use client";\n\n// 反向验证用的临时页面，由 scripts/verify_build_guards.mjs 生成并删除。\n' +
        "export default function Canary() {\n" +
        `  return <p>${canaryValue}</p>;\n` +
        "}\n",
    );

    const r = build({});
    if (r.failedBuild) {
      check(false, "canary 构建应当成功（它不走环境变量那条路）", "", r.out.slice(-400));
    } else {
      const { hits } = await scanDir(join(WEB, ".next"));
      check(
        hits.some((h) => h.kind === "sb_secret"),
        "源码里硬编码的 key → 产物扫描变红",
        `命中 ${hits.length} 处 ← 扫描有效`,
        "没命中 —— 这条扫描是空的",
      );
    }
  } finally {
    rmSync(canaryDir, { recursive: true, force: true });
  }

  // canary 已删干净：留在源码里会让下一次「0 命中」永远红。
  const leftovers = await scanDir(join(WEB, "app"));
  check(leftovers.hits.length === 0, "canary 已从源码清干净", "",
        leftovers.hits.map((h) => h.file).join(" "));
}

console.log(`\n\x1b[1m合计\x1b[0m  绿 ${passed} · 红 ${failed}\n`);
process.exit(failed === 0 ? 0 : 1);
