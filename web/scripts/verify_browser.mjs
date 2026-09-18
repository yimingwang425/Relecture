/**
 * 浏览器回归工具（阶段 1.5）。
 *
 * **本次验收的结论以人工走的那一遍为准**——这个脚本的用途是把那一遍固化下来，
 * 供以后每次改动重跑，不是替代它。真 Chromium、真网络、真部署域名，不是替身；
 * 但它看不见你能看见的东西（渲染是否错乱、文案是否说得通）。
 *
 *   node scripts/verify_browser.mjs --base https://xxx.vercel.app
 *   node scripts/verify_browser.mjs --base https://xxx.vercel.app --debug-routes
 *
 * `--debug-routes` 表示该部署**应当**有 /debug/rls（preview 臂）。不给则断言 404。
 * 两条断言是同一个开关的两面，必须都跑过——只跑「生产 404」那一条的话，
 * 页面本身编译不过也会是 404（通则七 #5 的形态）。
 *
 * 账号从 supabase/.env 读，与 verify_rls_prod.py 同一份，不另配。
 */

import { chromium } from "playwright";

import { scanText } from "../lib/secret-scan.mjs";
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const SHOTS = join(WEB, ".playwright-shots");

// ---------------------------------------------------------------- 参数与账号

const argv = process.argv.slice(2);
const BASE = (argv[argv.indexOf("--base") + 1] ?? "").replace(/\/$/, "");
const EXPECT_DEBUG = argv.includes("--debug-routes");

/**
 * 期望的 commit，**必须显式传入，不取本地 HEAD**。
 *
 * 默认取本地 HEAD 会让检查在「本地领先于部署」时静默通过——而那正是 2026-09-08
 * 那次的情形：分支最后一个 commit 构建失败，别名停在上一个部署，本地 HEAD 却已经
 * 是失败的那个。一个在事故当时会绿的检查，就是没有检查（同 F15 的理由）。
 */
const EXPECT_COMMIT = argv[argv.indexOf("--commit") + 1] ?? "";

if (!BASE.startsWith("http") || !argv.includes("--commit") || !/^[0-9a-f]{7,40}$/.test(EXPECT_COMMIT)) {
  console.error(
    "用法：node scripts/verify_browser.mjs --base https://… --commit <sha> [--debug-routes]\n" +
      "\n--commit 必须显式给出：从 GitHub 上那个部署对应的 commit 取，不要用本地 HEAD。",
  );
  process.exit(2);
}

function readSupabaseEnv() {
  const text = readFileSync(join(dirname(WEB), "supabase/.env"), "utf8");
  const env = {};
  for (const line of text.split("\n")) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2].trim();
  }
  return env;
}
const ENV = readSupabaseEnv();
const ACCOUNTS = {
  A: { email: ENV.F1_VERIFY_EMAIL_A, password: ENV.F1_VERIFY_PASSWORD },
  B: { email: ENV.F1_VERIFY_EMAIL_B, password: ENV.F1_VERIFY_PASSWORD },
};

let passed = 0;
let failed = 0;
function check(cond, name, okDetail = "", badDetail = "") {
  const detail = cond ? okDetail : badDetail;
  if (cond) passed++;
  else failed++;
  console.log(`  ${cond ? "\x1b[32m✓\x1b[0m" : "\x1b[31m✗\x1b[0m"} ${name}${detail ? "  " + detail : ""}`);
}

/** 抓这一页发出的所有请求，供「带的是不是 publishable key」那条断言用。 */
function recordRequests(page, sink) {
  page.on("request", (req) => {
    sink.push({
      url: req.url(),
      method: req.method(),
      headers: req.headers(),
      body: req.postData() ?? "",
    });
  });
}

/**
 * 登录并**等到页面进入终态**。
 *
 * `networkidle` 是不够的：页面是流式渲染的，网络安静时 DOM 可能还没流完，
 * 读到的是「header 已在、四个字段还没到」的中间态——2026-09-08 就这么假红了 6 条。
 * 页面在三个分支上各挂了一个 `data-state`，等它出现才是真的到位。
 */
async function login(page, who) {
  const { email, password } = ACCOUNTS[who];
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(|onboarding.*|settings.*)$/, { timeout: 20000 });
  await page.waitForSelector("main[data-state]", { timeout: 20000 });
}

async function main() {
  mkdirSync(SHOTS, { recursive: true });
  console.log(`\n部署：${BASE}    调试路由：${EXPECT_DEBUG ? "应当存在（preview）" : "应当 404（生产）"}\n`);

  const browser = await chromium.launch();
  const requests = [];

  // ---------------------------------------------------------- 0 跑的是哪个构建
  console.log("\x1b[1m0 · 这个域名当前服务的是哪个 commit（口径 F17）\x1b[0m");
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const resp = await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
    const served = resp?.headers()["x-build-commit"] ?? null;

    // 拿不到就判失败，不跳过（同 verify_git_identity 的 --allow-offline 那条）：
    // 一个跳过了却仍然退出 0 的检查，与它防的失败是同一类东西。
    check(
      served !== null,
      "部署自报了 x-build-commit",
      served,
      "响应里没有这个头 —— 可能是旧构建（还没有 F17），也可能 headers() 没生效。" +
        "拿不到就判失败：下面所有断言都不知道自己在测哪个构建",
    );

    if (served) {
      const matches = served.startsWith(EXPECT_COMMIT) || EXPECT_COMMIT.startsWith(served);
      check(
        matches,
        "服务的构建就是期望的那个 commit",
        `${served.slice(0, 7)} == ${EXPECT_COMMIT.slice(0, 7)}`,
        `服务的是 ${served.slice(0, 7)}，期望 ${EXPECT_COMMIT.slice(0, 7)} —— ` +
          "别名可能停在上一个成功的部署上（新构建失败时就会这样）。" +
          "此时下面的绿全部指向另一个对象",
      );

      // 反向控制：同一段比对逻辑喂一个必然不同的 sha，必须判为不匹配。
      // 否则「相等」可能只是因为比对恒真。
      const bogus = "0".repeat(40);
      check(
        !(served.startsWith(bogus) || bogus.startsWith(served)),
        "【反向控制】比对一个伪造 sha 时判为不匹配",
        "← 比对是活的，不是恒真",
        "比对恒真，上一条什么也证明不了",
      );
    }
    await ctx.close();
  }

  // ---------------------------------------------------------- 1 未登录
  console.log("\x1b[1m1 · 未登录\x1b[0m");
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const r = await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    // 2026-09-15 起未登录的入口是欢迎页（/welcome），登录表单在 /login。
    check(page.url().endsWith("/welcome"), "访问 / 被弹到 /welcome", page.url(), `停在 ${page.url()}（HTTP ${r?.status()}）`);
    await ctx.close();
  }

  // ---------------------------------------------------------- 2 调试路由开关
  console.log("\n\x1b[1m2 · /debug/rls 的构建期开关\x1b[0m");
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const r = await page.goto(`${BASE}/debug/rls`, { waitUntil: "domcontentloaded" });
    const status = r?.status();
    if (EXPECT_DEBUG) {
      // 未登录会被 redirect 到 /welcome（页面存在），或直接 200。404 才是失败。
      check(status !== 404, "路由存在", `HTTP ${status}`, "404 —— 开关没生效，或这页编译不过");
    } else {
      check(status === 404, "生产域名上返回 404", `HTTP ${status}`, `HTTP ${status} —— 路由被打进了生产产物`);
    }
    await ctx.close();
  }

  // ---------------------------------------------------------- 3 A 登录
  console.log("\n\x1b[1m3 · A 登录后看到自己的配置\x1b[0m");
  // 2026-09-19：首页改成「我的课程」，且没走过 onboarding 的人会被直接送去风格选择。
  // 四条轴的真实取值挂在 /settings/style 的诊断区，所以这一节去那一页读。
  let uidA = null;
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    recordRequests(page, requests);
    await login(page, "A");
    await page.goto(`${BASE}/settings/style`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("main[data-state]", { timeout: 20000 });

    // 按 data-* 定位，不按中文文案。文案匹配会在改一次措辞后**静默变绿**：
    // 匹配不到「读取失败」，于是「没出错」成立。
    const state = await page.getAttribute("main[data-state]", "data-state");
    check(state === "ok", "页面进入 ok 终态", state ?? "",
          `data-state=${state} —— error 是查询失败，bad-row-count 是 F09 的触发器没建行`);

    // `textContent` 而不是 `innerText`：UI 重做后 user_id 收在默认折叠的诊断区里
    // （`app/components/diagnostics.tsx`），`innerText` 不含未渲染的文字，会假红。
    const body = (await page.locator("body").textContent()) ?? "";
    uidA = body.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/)?.[0] ?? null;
    check(!!uidA, "页面显示了 user_id", uidA ?? "", "没找到 uuid");
    check(body.includes(ACCOUNTS.A.email), "显示的账号是 A", ACCOUNTS.A.email);

    const fields = {};
    for (const key of ["detail_level", "use_examples", "structure", "depth", "f1_arm"]) {
      fields[key] = await page.locator(`[data-field="${key}"]`).first().textContent().catch(() => null);
      check(!!fields[key], `渲染了 ${key}`, fields[key] ?? "", "取不到该字段");
    }
    check(/^P[0-3]$/.test(fields.detail_level ?? ""), "detail_level 是合法档位",
          fields.detail_level ?? "", `取值域外：${fields.detail_level}`);
    check(["pairwise", "text"].includes(fields.f1_arm ?? ""), "f1_arm 是合法臂",
          fields.f1_arm ?? "", `取值域外：${fields.f1_arm}`);
    await page.screenshot({ path: join(SHOTS, "settings-A.png"), fullPage: true });
    await ctx.close();
  }

  // ---------------------------------------------------------- 4 请求里带的 key
  console.log("\n\x1b[1m4 · 浏览器发出的请求带的是 publishable key\x1b[0m");
  {
    const supa = requests.filter((r) => r.url.includes(".supabase.co"));
    check(supa.length > 0, "确实有到 Supabase 的请求", `${supa.length} 条`,
          "0 条 —— 下面的断言全是空的（登录可能走了别的路径）");

    const keys = new Set();
    for (const r of supa) {
      const k = r.headers["apikey"] ?? r.headers["Apikey"];
      if (k) keys.add(k);
      const auth = r.headers["authorization"] ?? "";
      if (auth.startsWith("Bearer ") && auth.slice(7).startsWith("sb_")) keys.add(auth.slice(7));
    }
    const allPublishable = keys.size > 0 && [...keys].every((k) => k.startsWith("sb_publishable_"));
    check(allPublishable, "apikey 全部是 sb_publishable_",
          [...keys].map((k) => k.slice(0, 22) + "…").join(" "),
          `发现非 publishable：${[...keys].join(" ")}`);

    // 全站扫一遍，含 URL、请求头、请求体。判据与产物扫描共用同一份
    // （lib/secret-scan.mjs）：**形态匹配，不匹配 `sb_secret_` 这个字符串本身**，
    // 否则护栏源码里的字面量比较会让它永远命中（口径 F16）。
    const leak = requests
      .map((r) => ({ r, hits: scanText(r.url + JSON.stringify(r.headers) + r.body) }))
      .find((x) => x.hits.length > 0);
    check(!leak, "任何请求里都没有 secret 凭据", `扫了 ${requests.length} 条请求`,
          `${leak?.hits[0].kind} 泄露于 ${leak?.r.url}`);
  }

  // ---------------------------------------------------------- 5 跨用户读取
  if (EXPECT_DEBUG) {
    console.log("\n\x1b[1m5 · A 的登录态下读 B 的行\x1b[0m");
    const ctxB = await browser.newContext();
    const pageB = await ctxB.newPage();
    await login(pageB, "B");
    await pageB.goto(`${BASE}/settings/style`, { waitUntil: "domcontentloaded" });
    await pageB.waitForSelector("main[data-state]", { timeout: 20000 });
    const bodyB = (await pageB.locator("body").textContent()) ?? "";
    const uidB = bodyB.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/)?.[0];
    check(!!uidB && uidB !== uidA, "拿到 B 的 user_id 且与 A 不同", uidB ?? "",
          "A 和 B 是同一个用户，整条验收会平凡通过");
    await ctxB.close();

    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await login(page, "A");
    await page.goto(`${BASE}/debug/rls?probe=${uidB}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector('main[data-state="ready"]', { timeout: 20000 });
    await page.screenshot({ path: join(SHOTS, "debug-rls.png"), fullPage: true });

    const self = await page.getAttribute('[data-result="self"]', "data-rows");
    const probe = await page.getAttribute('[data-result="probe"]', "data-rows");
    check(self !== null && probe !== null, "页面同时给出了两个查询的行数",
          `自己 ${self} / probe ${probe}`, `自己 ${self} / probe ${probe}`);
    check(self === "1", "【反向控制】查自己 = 1 行", "",
          `= ${self} —— 下一条的「0 行」因此什么也证明不了`);
    check(probe === "0", "查 B = 0 行", "", `= ${probe} —— RLS 被绕过了`);
    await ctx.close();
  } else {
    console.log("\n\x1b[1m5 · A 的登录态下读 B 的行\x1b[0m");
    console.log("  \x1b[33m—\x1b[0m 跳过：该部署没有 /debug/rls。这一条要在开了调试路由的 preview 上跑。");
  }

  await browser.close();
  console.log(`\n\x1b[1m合计\x1b[0m  绿 ${passed} · 红 ${failed}   截图 ${SHOTS}\n`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
