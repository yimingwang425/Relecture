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
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const SHOTS = join(WEB, ".playwright-shots");

// ---------------------------------------------------------------- 参数与账号

const argv = process.argv.slice(2);
const BASE = (argv[argv.indexOf("--base") + 1] ?? "").replace(/\/$/, "");
const EXPECT_DEBUG = argv.includes("--debug-routes");
if (!BASE.startsWith("http")) {
  console.error("用法：node scripts/verify_browser.mjs --base https://… [--debug-routes]");
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

async function login(page, who) {
  const { email, password } = ACCOUNTS[who];
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(`${BASE}/`, { timeout: 20000 });
  await page.waitForLoadState("networkidle");
}

async function main() {
  mkdirSync(SHOTS, { recursive: true });
  console.log(`\n部署：${BASE}    调试路由：${EXPECT_DEBUG ? "应当存在（preview）" : "应当 404（生产）"}\n`);

  const browser = await chromium.launch();
  const requests = [];

  // ---------------------------------------------------------- 1 未登录
  console.log("\x1b[1m1 · 未登录\x1b[0m");
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    const r = await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
    check(page.url().endsWith("/login"), "访问 / 被弹到 /login", page.url(), `停在 ${page.url()}（HTTP ${r?.status()}）`);
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
      // 未登录会被 redirect 到 /login（页面存在），或直接 200。404 才是失败。
      check(status !== 404, "路由存在", `HTTP ${status}`, "404 —— 开关没生效，或这页编译不过");
    } else {
      check(status === 404, "生产域名上返回 404", `HTTP ${status}`, `HTTP ${status} —— 路由被打进了生产产物`);
    }
    await ctx.close();
  }

  // ---------------------------------------------------------- 3 A 登录
  console.log("\n\x1b[1m3 · A 登录后看到自己的配置\x1b[0m");
  let uidA = null;
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    recordRequests(page, requests);
    await login(page, "A");
    const body = await page.locator("body").innerText();

    const m = body.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
    uidA = m?.[0] ?? null;
    check(!!uidA, "页面显示了 user_id", uidA ?? "", "没找到 uuid");
    check(body.includes(ACCOUNTS.A.email), "显示的账号是 A", ACCOUNTS.A.email);
    for (const label of ["详细程度", "举例", "结构", "深度", "f1_arm"]) {
      check(body.includes(label), `显示了「${label}」`);
    }
    check(/\bP[0-3]\b/.test(body), "详细程度是合法档位", body.match(/\bP[0-3]\b/)?.[0] ?? "");
    check(!body.includes("应为 1 行"), "没有落到「读到 0 行」的坏状态分支");
    await page.screenshot({ path: join(SHOTS, "home-A.png"), fullPage: true });
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

    // 全站扫一遍，含 URL、请求头、请求体。
    const leak = requests.find(
      (r) =>
        /sb_secret_/.test(r.url + JSON.stringify(r.headers) + r.body) ||
        /"role"\s*:\s*"service_role"/.test(r.body),
    );
    check(!leak, "任何请求里都没有 secret 凭据", `扫了 ${requests.length} 条请求`,
          `泄露于 ${leak?.url}`);
  }

  // ---------------------------------------------------------- 5 跨用户读取
  if (EXPECT_DEBUG) {
    console.log("\n\x1b[1m5 · A 的登录态下读 B 的行\x1b[0m");
    const ctxB = await browser.newContext();
    const pageB = await ctxB.newPage();
    await login(pageB, "B");
    const bodyB = await pageB.locator("body").innerText();
    const uidB = bodyB.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/)?.[0];
    check(!!uidB && uidB !== uidA, "拿到 B 的 user_id 且与 A 不同", uidB ?? "",
          "A 和 B 是同一个用户，整条验收会平凡通过");
    await ctxB.close();

    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await login(page, "A");
    await page.goto(`${BASE}/debug/rls?probe=${uidB}`, { waitUntil: "networkidle" });
    const text = await page.locator("body").innerText();
    await page.screenshot({ path: join(SHOTS, "debug-rls.png"), fullPage: true });

    const counts = [...text.matchAll(/^(\d+) 行$/gm)].map((m) => Number(m[1]));
    check(counts.length === 2, "页面同时给出了两个查询的行数", counts.join(" / "),
          `只解析出 ${counts.length} 个数：${counts.join(",")}`);
    check(counts[0] === 1, "【反向控制】查自己 = 1 行", "",
          `= ${counts[0]} —— 下一条的「0 行」因此什么也证明不了`);
    check(counts[1] === 0, "查 B = 0 行", "", `= ${counts[1]} —— RLS 被绕过了`);
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
