/**
 * UI 预览截图：登录后的每一页 × 桌面 / 手机，外加几个交互中间态。
 *
 *   node scripts/ui-preview/shoot.mjs <输出目录> [只截哪几张，逗号分隔]
 *
 * 前提：`mock-server.mjs` 在 :4010，`next dev` 在 :3000 且指向它（见同目录 README）。
 * 登录不走表单：直接写 @supabase/ssr 认的那个 cookie（`sb-localhost-auth-token`，
 * `base64-` + base64url(会话 JSON)），与真实登录后浏览器里的形状一致。
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "@playwright/test";

const OUT = process.argv[2] ?? "/tmp/ui-shots";
const ONLY = process.argv[3]?.split(",") ?? null;
const BASE = process.env.PREVIEW_BASE ?? "http://localhost:3000";
const EXEC = process.env.PW_CHROMIUM ?? "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
mkdirSync(OUT, { recursive: true });

const USER_ID = "7a0d1f5e-2c4b-4e8a-9f31-6b2d8c1e4a90";
const b64url = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const exp = Math.floor(Date.now() / 1000) + 30 * 86400;
const token = `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ sub: USER_ID, email: "lin.xiao@example.com", role: "authenticated", aud: "authenticated", exp })}.c2ln`;
const session = {
  access_token: token, token_type: "bearer", expires_in: 30 * 86400, expires_at: exp, refresh_token: "preview-refresh",
  user: { id: USER_ID, aud: "authenticated", role: "authenticated", email: "lin.xiao@example.com", app_metadata: {}, user_metadata: {} },
};
const COOKIE = { name: "sb-localhost-auth-token", value: `base64-${b64url(session)}`, domain: "localhost", path: "/" };

const VIEWPORTS = {
  desk: { width: 1440, height: 900 },
  mob: { width: 390, height: 844 },
  // 平板竖屏：侧栏抽屉的断点（900px）就落在它与桌面之间。`PREVIEW_TABLET=1` 才截。
  ...(process.env.PREVIEW_TABLET ? { tab: { width: 820, height: 1180 } } : {}),
};

/** [名字, 是否登录, 动作]。动作拿到 page，自己导航、自己等。 */
const SHOTS = [
  ["welcome", false, async (p) => { await p.goto(`${BASE}/welcome`); await p.waitForTimeout(2600); }],
  ["login", false, async (p) => { await p.goto(`${BASE}/login`); await p.waitForTimeout(1200); }],
  ["home", true, async (p) => { await p.goto(`${BASE}/`); await p.waitForTimeout(1600); }],
  ["onboarding", true, async (p) => { await p.goto(`${BASE}/onboarding/taste`); await p.waitForTimeout(1400); }],
  ["workspace", true, async (p) => { await seenTour(p); await p.goto(`${BASE}/courses/c-energy`); await p.waitForTimeout(1800); }],
  ["tour", true, async (p) => { await p.goto(`${BASE}/courses/c-energy`); await p.waitForTimeout(2200); }],
  ["streaming", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    await p.waitForSelector('[data-field="question"]');
    await p.fill('[data-field="question"]', "铀矿挖出来为什么不能直接当核燃料？");
    await p.click('[data-field="ask"]');
    await p.waitForTimeout(3600);
  }],
  ["answered", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    await p.waitForSelector('[data-field="question"]');
    await p.fill('[data-field="question"]', "铀矿挖出来为什么不能直接当核燃料？");
    await p.click('[data-field="ask"]');
    await p.waitForSelector('[data-field="turn"][data-state="answered"]:last-child', { timeout: 30000 });
    await p.waitForTimeout(900);
  }],
  ["documents", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    // 窄屏上「我的课件」在折起来的那一块里，先展开
    if (p.viewportSize().width < 900) await p.click(".ws-side__toggle");
    await p.click('[data-field="open-documents"]');
    await p.waitForTimeout(700);
  }],
  ["adjust", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    await p.click('[data-field="style-adjust-entry"]');
    await p.waitForTimeout(700);
  }],
  ["drawer", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    if (p.viewportSize().width < 900) await p.click(".ws-side__toggle");
    await p.waitForTimeout(700);
  }],
  // 动线：首页建课 → 直接落进新课（不是停在首页列表里）
  ["new-course", true, async (p) => {
    await p.goto(`${BASE}/`);
    await p.fill('[data-field="new-course-name"]', "6.006 算法导论");
    await p.click('[data-field="create-course"]');
    await p.waitForURL(/\/courses\/c-/, { timeout: 15000 });
    await p.waitForTimeout(1200);
  }],
  // 动线：「新对话」不建行，直接给输入框；第一个问题发出去才建会话并自动发出
  ["first-ask", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    if (p.viewportSize().width < 900) await p.click(".ws-side__toggle");
    await p.click('[data-field="new-conversation"]');
    await p.waitForSelector('[data-field="question"]');
    await p.fill('[data-field="question"]', "核电站的废热比火电站多吗？");
    await p.click('[data-field="ask"]');
    await p.waitForSelector('[data-field="turn"][data-state="answered"]', { timeout: 30000 });
    await p.waitForTimeout(800);
  }],
  // 动线：调整讲法 → 应用 → 按新讲法重讲上一个问题（在这个会话里多出一轮）
  ["adjust-redo", true, async (p) => {
    await seenTour(p);
    await p.goto(`${BASE}/courses/c-energy`);
    await p.waitForSelector('[data-field="turn"][data-state="answered"]');
    const before = await p.locator('[data-field="turn"]').count();
    await p.click('[data-field="style-adjust-entry"]');
    await p.click('[data-adjust="shorter"]');
    await p.click('[data-adjust="more_examples"]');
    await p.click('[data-field="adjust-apply"]');
    await p.waitForSelector('[data-field="adjust-redo"]', { timeout: 15000 });
    await p.click('[data-field="adjust-redo"]');
    await p.waitForFunction((n) => document.querySelectorAll('[data-field="turn"][data-state="answered"]').length > n - 0 &&
      document.querySelectorAll('[data-field="turn"]').length > n, before, { timeout: 30000 });
    await p.waitForSelector('[data-field="turn"]:last-child[data-state="answered"]', { timeout: 30000 });
    await p.waitForTimeout(800);
  }],
  ["empty-course", true, async (p) => { await seenTour(p); await p.goto(`${BASE}/courses/c-newbie`); await p.waitForTimeout(1200); }],
  ["settings", true, async (p) => { await p.goto(`${BASE}/settings`); await p.waitForTimeout(1200); }],
  ["style", true, async (p) => { await p.goto(`${BASE}/settings/style`); await p.waitForTimeout(1400); }],
];

async function seenTour(p) {
  await p.addInitScript(() => {
    try { window.localStorage.setItem("relecture.tour.seen", "1"); } catch {}
  });
}

const browser = await chromium.launch({ executablePath: EXEC });
for (const [name, authed, act] of SHOTS) {
  if (ONLY && !ONLY.includes(name)) continue;
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    const ctx = await browser.newContext({ viewport: size, deviceScaleFactor: vp === "desk" ? 1 : 2, locale: "zh-CN" });
    if (authed) await ctx.addCookies([COOKIE]);
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    try {
      await act(page);
      await page.screenshot({ path: join(OUT, `${name}-${vp}.png`), fullPage: name !== "streaming" && name !== "tour" && name !== "documents" && name !== "adjust" && name !== "drawer" && name !== "first-ask" && name !== "adjust-redo" });
      console.log(`✓ ${name}-${vp}${errors.length ? `  ⚠ ${errors.slice(0, 2).join(" | ").slice(0, 300)}` : ""}`);
    } catch (e) {
      console.log(`✗ ${name}-${vp}  ${String(e).split("\n")[0]}`);
      await page.screenshot({ path: join(OUT, `${name}-${vp}-FAILED.png`) }).catch(() => {});
    }
    await ctx.close();
  }
}
await browser.close();
