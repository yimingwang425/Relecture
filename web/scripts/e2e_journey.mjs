#!/usr/bin/env node
/**
 * 端到端用户动线测试：**一个真浏览器，按真用户的路线走一遍**，每一步都断言（2026-10-08）。
 *
 *   # 本地 / CI：假后端，免费，约 1 分钟。自己起 mock-server 与 next dev，跑完关掉。
 *   node scripts/e2e_journey.mjs --target mock
 *
 *   # 生产：真部署、真账号、真模型（要花钱，见下）。账号用批量建号脚本产出的那份 CSV。
 *   node scripts/e2e_journey.mjs --target prod --commit <sha> \
 *       --accounts ../scripts/invited/2026-10-08-....csv [--concurrency 2] [--limit 4] --yes
 *
 * ## 每个账号走的路线（与人工验收同一条）
 *
 *  1. 欢迎页 → 登录页 → 登录。新账号必须被送去开场页；老账号去首页。
 *  2. 开场页：标题、四张讲法卡片、没选时「下一步」不可点；选一张（第 i 个账号选第 i%4 张，
 *     四个账号就把四种讲法都走过）→ 首页。**查库**：语气与四条轴等于那张卡片、onboarded_at 有值。
 *  3. 首页建课 → 直接落进新课。
 *  4. 传一份 PDF → 引导三步走完 →「去提问」。
 *  5. 第一个问题：流式回答 → 答完；出处里有刚传的文件；没有「不是用你的课件答的」那条红字。
 *  6. 追问「没懂」：第二轮答完。
 *  7. 调整讲法：挪一格 → **查库**确认真的挪了 →「按新讲法重讲」→ 第三轮答完，问题带重讲前缀。
 *  8. 刷新页面：会话在侧栏里、标题是第一个问题、三轮都还在（**查库**：messages 6 行）。
 *  9. 设置页：四种讲法与语气一项都在，语气的选中项等于库里那一行。
 * 10. 退出登录 → 回到欢迎页。
 * 11. （生产、≥2 个账号）A 打开 B 的课程链接 → 404（越权不能长得像正常空页）。
 *
 * ## 判据分两类，**分开报，不合计**（口径 F34）
 *
 * - **功能**：上面每一步。任何一条红，退出码非 0。
 * - **观测**：检索有没有命中评测集的金标页、每一步花了多久。它们是真模型的质量与速度，
 *   **不是**这个脚本能判对错的东西——红了不让测试失败，只进报告。
 *
 * ## 花多少钱（只在 prod）
 *
 * 每个账号：一次上传（15 页的 11.201 讲义：嵌入 + 定位语）+ 三次讲解（约 $0.015 一次）。
 * 合计量级 $0.05–0.1 / 账号。所以 prod 要显式加 `--yes`。
 *
 * ## 课件为什么是 11.201
 *
 * 同 `ingest_real.py`：14.129 有第三方保留权利的页，15.316 是扫描件；22.081J 39 页，传得慢。
 * 11.201 15 页、49 KB，评测集里有它的单页金标题（Q022，金标第 14 页）。
 *
 * ## 不做清理
 *
 * `courses` 没有 delete 策略（用户在产品里删不了课），脚本也就不该有一条用户没有的路。
 * 测试账号本身在 Dashboard 删掉时，它的课、课件、会话随 cascade 一起清空。
 */

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const ROOT = dirname(WEB);
const PDF = join(ROOT, "实验脚本", "测试集", "MIT11_201F07_slides2.pdf");
const PDF_NAME = "MIT11_201F07_slides2.pdf";
/** 评测集 Q022：单页金标。用它是因为金标唯一，「命中没有」不需要解释。 */
const QUESTION = "「按流程一步步来定方案」这套做法，具体分哪几步？";
const GOLD_PAGE = 14;
const FOLLOW_UP = "没懂，能说得再简单一点吗？";
/** 与 `app/explain/explain-client.tsx` 的 REDO_PREFIX 同一句。改了那边，这里会红在第 7 步。 */
const REDO_PREFIX = "按我刚调整的讲法，重新讲一遍：";
/** 与 `lib/style/taste.ts` 的 PRESETS 同序。只用 id；配置从页面上点出来、再从库里读回来比。 */
const PRESETS = [
  { id: "preset_beginner", tone: "spoken", detail_level: "P2" },
  { id: "preset_brief", tone: "neutral", detail_level: "P3" },
  { id: "preset_thorough", tone: "neutral", detail_level: "P0" },
  { id: "preset_rigorous", tone: "academic", detail_level: "P0" },
];
const DETAIL_LADDER = ["P0", "P2", "P3"];

// ---------------------------------------------------------------- 参数

const argv = process.argv.slice(2);
const arg = (k, d) => (argv.includes(`--${k}`) ? argv[argv.indexOf(`--${k}`) + 1] : d);
const TARGET = arg("target", "mock");
const CONCURRENCY = Math.max(1, Number(arg("concurrency", "2")));
const LIMIT = Number(arg("limit", "0"));
const HEADED = argv.includes("--headed");
const EXEC = process.env.PW_CHROMIUM; // 云端容器里浏览器版本对不上时指定；本机一般不用
const STAMP = new Date().toISOString().replace(/[:T.]/g, "-").slice(0, 19);
const OUT = join(WEB, ".e2e-runs", `${TARGET}-${STAMP}`);

function die(msg) {
  console.error(`\n\x1b[31m${msg}\x1b[0m\n`);
  killChildren();
  process.exit(2);
}

function readEnvFile(path) {
  const env = {};
  if (!existsSync(path)) return env;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2].trim();
  }
  return env;
}

/** 批量建号脚本的 CSV：email,password,user_id,note（note 可能带引号）。 */
function readAccountsCsv(path) {
  const lines = readFileSync(path, "utf8").trim().split("\n").slice(1);
  return lines.map((l) => {
    const [email, password] = l.split(",");
    return { email, password };
  });
}

// ---------------------------------------------------------------- 环境

let BASE, API, SUPA_URL, SUPA_KEY, accounts;
const children = [];
function killChildren() {
  for (const p of children) {
    try {
      process.kill(-p.pid, "SIGTERM");
    } catch {}
  }
}

async function waitHttp(url, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      const r = await fetch(url);
      if (r.status < 500) return true;
    } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

async function setupMock() {
  const port = Number(arg("port", "3100"));
  const mockPort = 4010;
  BASE = arg("base", `http://localhost:${port}`);
  API = `http://localhost:${mockPort}/api`;
  SUPA_URL = `http://localhost:${mockPort}`;
  SUPA_KEY = "sb_publishable_preview";
  // 假后端只有一个用户；密码随便。onboarded_at 设为空，开场那一步才有东西可测。
  accounts = [{ email: "lin.xiao@example.com", password: "mock-password" }];

  const up = await waitHttp(`${SUPA_URL}/rest/v1/courses`, 500);
  if (!up) {
    const p = spawn("node", ["scripts/ui-preview/mock-server.mjs"], {
      cwd: WEB, stdio: "ignore", detached: true,
      env: { ...process.env, MOCK_ONBOARDED: "0", MOCK_RETRIEVE_MS: "200", MOCK_FIRST_TOKEN_MS: "200", MOCK_CHAR_MS: "4", MOCK_UPLOAD_MS: "600" },
    });
    children.push(p);
    if (!(await waitHttp(`${SUPA_URL}/rest/v1/courses`, 15000))) die("mock-server 没起来");
  } else {
    console.log("  （复用已在 :4010 的 mock-server——它的种子用户可能已经走过开场）");
  }
  if (!arg("base")) {
    // detached：自成一个进程组，收尾时整组杀掉。只杀 npx 的话，它拉起的 next dev 会留在后台占着端口。
    const p = spawn("npx", ["next", "dev", "-p", String(port)], {
      cwd: WEB, stdio: "ignore", detached: true,
      env: { ...process.env, NEXT_PUBLIC_SUPABASE_URL: SUPA_URL, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: SUPA_KEY, NEXT_PUBLIC_API_BASE_URL: API },
    });
    children.push(p);
    if (!(await waitHttp(`${BASE}/welcome`, 120000))) die("next dev 没起来");
  }
}

async function setupProd() {
  if (!argv.includes("--yes")) {
    die("生产模式会真的调模型、传课件、花钱（约 $0.05–0.1 / 账号）。确认后加 --yes。");
  }
  BASE = arg("base", "https://ai-courseware-assistant.vercel.app").replace(/\/$/, "");
  API = arg("api", "https://ai-courseware-assistant.onrender.com").replace(/\/$/, "");
  const env = readEnvFile(join(ROOT, "supabase", ".env"));
  SUPA_URL = arg("supabase-url", env.SUPABASE_URL ?? "").replace(/\/$/, "");
  SUPA_KEY = arg("publishable-key", env.SUPABASE_PUBLISHABLE_KEY ?? "");
  if (!SUPA_URL || !SUPA_KEY) die("缺 SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY（supabase/.env，或 --supabase-url / --publishable-key）");

  const csv = arg("accounts");
  if (csv) accounts = readAccountsCsv(csv);
  else if (env.F1_VERIFY_EMAIL_A && env.F1_VERIFY_PASSWORD) {
    accounts = [env.F1_VERIFY_EMAIL_A, env.F1_VERIFY_EMAIL_B].filter(Boolean).map((email) => ({ email, password: env.F1_VERIFY_PASSWORD }));
    console.log("  （没给 --accounts，用 supabase/.env 里的两个验收账号）");
  } else die("没有账号：给 --accounts <批量建号产出的 csv>");

  // ---- 第 0 步：核对线上跑的是哪一版（口径 F17）。build_commit 不对，后面的结果一条都不能解释。
  const commit = arg("commit");
  if (!commit || !/^[0-9a-f]{7,40}$/.test(commit)) die("生产模式必须给 --commit <sha>（GitHub 上那个提交，不是本地 HEAD）");
  console.log(`  叫醒 ${API}（免费层冷启动可能要 30 秒以上）…`);
  if (!(await waitHttp(`${API}/health`, 180000))) die(`${API}/health 一直不通`);
  const health = await (await fetch(`${API}/health`)).json();
  if (!health.build_commit || !String(health.build_commit).startsWith(commit.slice(0, 7))) {
    die(`线上 api 的 build_commit 是 ${health.build_commit}，不是 ${commit}。等部署完再跑（F17）。`);
  }
  console.log(`  api build_commit = ${health.build_commit} ✓`);
}

// ---------------------------------------------------------------- 用账号自己的会话查库

async function jwtOf(acc) {
  const r = await fetch(`${SUPA_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: SUPA_KEY, "Content-Type": "application/json" },
    body: JSON.stringify({ email: acc.email, password: acc.password }),
  });
  if (!r.ok) throw new Error(`拿不到 ${acc.email} 的会话：HTTP ${r.status} ${await r.text()}`);
  return (await r.json()).access_token;
}

/** 用**用户自己的** token 读——走的是 RLS，与页面看到的是同一个视角。 */
async function rest(jwt, path) {
  // 刚登录拿到的 token 偶尔被 PostgREST 以「JWT issued at future」（PGRST303）拒掉：
  // 签发它的 auth 服务器时钟比数据库那台快了一点点（2026-10-08 生产跑，4 个账号里 1 个）。
  // 等一秒重试，最多 3 次——这是两台服务器的时钟差，不是产品行为，不该让测试红。
  for (let attempt = 1; ; attempt++) {
    const r = await fetch(`${SUPA_URL}/rest/v1/${path}`, { headers: { apikey: SUPA_KEY, Authorization: `Bearer ${jwt}` } });
    if (r.ok) return r.json();
    const body = await r.text();
    if (r.status === 401 && body.includes("PGRST303") && attempt < 4) {
      await new Promise((res) => setTimeout(res, 1000 * attempt));
      continue;
    }
    throw new Error(`GET ${path} → HTTP ${r.status} ${body}`);
  }
}

// ---------------------------------------------------------------- 一个账号走一遍

class Journey {
  constructor(acc, index) {
    this.acc = acc;
    this.i = index;
    this.tag = acc.email.split("@")[0];
    this.steps = [];
    this.courseId = null;
  }

  /** 一步：跑 fn，记时间；抛了就记红并截图，**后面依赖它的步骤不再跑**。 */
  async step(name, fn) {
    const t0 = Date.now();
    try {
      const detail = (await fn()) ?? "";
      this.steps.push({ name, kind: "功能", ok: true, detail, ms: Date.now() - t0 });
      log(this.tag, true, name, detail, Date.now() - t0);
      return true;
    } catch (e) {
      const detail = String(e?.message ?? e).split("\n")[0].slice(0, 240);
      this.steps.push({ name, kind: "功能", ok: false, detail, ms: Date.now() - t0 });
      log(this.tag, false, name, detail, Date.now() - t0);
      await this.page?.screenshot({ path: join(OUT, `${this.tag}-${this.steps.length}.png`), fullPage: true }).catch(() => {});
      throw new StopJourney();
    }
  }

  /** 观测：只记，不判失败。 */
  observe(name, ok, detail) {
    this.steps.push({ name, kind: "观测", ok, detail, ms: 0 });
    log(this.tag, ok ? true : "warn", `［观测］${name}`, detail);
  }

  async run(browser) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "zh-CN" });
    const page = (this.page = await ctx.newPage());
    page.setDefaultTimeout(30000);
    const consoleErrors = [];
    page.on("pageerror", (e) => consoleErrors.push(String(e)));
    try {
      await this.walk(page);
    } catch (e) {
      if (!(e instanceof StopJourney)) {
        this.steps.push({ name: "（脚本自身出错）", kind: "功能", ok: false, detail: String(e), ms: 0 });
        log(this.tag, false, "（脚本自身出错）", String(e));
      }
    }
    if (consoleErrors.length) this.observe("页面没有未捕获的 JS 异常", false, consoleErrors.slice(0, 2).join(" | ").slice(0, 240));
    else this.observe("页面没有未捕获的 JS 异常", true, "");
    await ctx.close();
  }

  async walk(page) {
    const jwt = await jwtOf(this.acc);
    const [before] = await rest(jwt, "style_config?select=onboarded_at");
    const fresh = !before?.onboarded_at;

    // ---- 1. 欢迎页 → 登录
    await this.step("欢迎页 → 登录页 → 登录", async () => {
      await page.goto(`${BASE}/welcome`);
      await page.getByRole("link", { name: "登录" }).click();
      await page.waitForURL(/\/login$/);
      await page.fill('input[type="email"]', this.acc.email);
      await page.fill('input[type="password"]', this.acc.password);
      await page.click('button[type="submit"]');
      // 登录后先到 `/`，首页再按 onboarded_at 服务端重定向——等**最终**落点，不等第一跳。
      await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 });
      if (fresh) {
        await page.waitForURL((u) => u.pathname.startsWith("/onboarding"), { timeout: 30000 })
          .catch(() => { throw new Error(`新账号登录后停在 ${new URL(page.url()).pathname}，应该被送去开场页`); });
      } else {
        await page.waitForLoadState("networkidle");
      }
      const path = new URL(page.url()).pathname;
      return fresh ? `新账号 → ${path}` : `老账号 → ${path}`;
    });

    // ---- 2. 开场：四种讲法选一种
    const preset = PRESETS[this.i % PRESETS.length];
    await this.step(`开场页：选「${preset.id}」→ 首页`, async () => {
      await page.goto(`${BASE}/onboarding/taste`);
      await page.getByRole("heading", { name: "请选择你的讲解偏好" }).waitFor();
      const cards = await page.locator("[data-preset]").count();
      if (cards !== 4) throw new Error(`讲法卡片 ${cards} 张，应为 4`);
      const next = page.locator('[data-action="continue"]');
      // 老账号回来时可能预选了上次那张；新账号必须是不可点。
      if (fresh && (await next.isEnabled())) throw new Error("没选讲法时「下一步」可以点");
      await page.click(`[data-preset="${preset.id}"]`);
      await next.click();
      await page.waitForURL((u) => u.pathname === "/", { timeout: 30000 });
    });
    await this.step("库里的配置等于那张卡片", async () => {
      const [row] = await rest(jwt, "style_config?select=tone,detail_level,taste_tags,onboarded_at");
      const bad = [];
      if (row.tone !== preset.tone) bad.push(`tone=${row.tone}`);
      if (row.detail_level !== preset.detail_level) bad.push(`detail_level=${row.detail_level}`);
      if (!row.onboarded_at) bad.push("onboarded_at 为空");
      if (!(row.taste_tags ?? []).includes(preset.id)) bad.push(`taste_tags=${JSON.stringify(row.taste_tags)}`);
      if (bad.length) throw new Error(bad.join("；"));
      return `tone=${row.tone} · detail=${row.detail_level}`;
    });

    // ---- 3. 建课 → 直接落进新课
    const courseName = `E2E ${STAMP.slice(5, 16)} ${this.tag}`;
    await this.step("首页建课 → 直接进入新课", async () => {
      await page.fill('[data-field="new-course-name"]', courseName);
      await page.click('[data-field="create-course"]');
      await page.waitForURL(/\/courses\/[^/]+$/, { timeout: 90000 });
      this.courseId = new URL(page.url()).pathname.split("/").pop();
      await page.locator('[data-field="course-name"]').filter({ hasText: courseName }).waitFor();
      return this.courseId;
    });

    // ---- 4. 上传 → 引导 → 去提问
    await this.step("上传 PDF（解析 · 切片 · 向量）", async () => {
      await page.getByRole("button", { name: "上传课件" }).click();
      // ⚠️ **传内容，不传路径。** 课件在 `实验脚本/测试集/` 下，路径带中文；Playwright 按路径
      // 塞文件时对非 ASCII 路径**静默失败**——不抛错、不触发 change，页面上什么都没发生
      // （2026-10-08 实测：同一份文件拷到 ASCII 路径就好了）。读成 buffer 与路径无关。
      await page.setInputFiles('[data-field="file-input"]', {
        name: PDF_NAME, mimeType: "application/pdf", buffer: readFileSync(PDF),
      });
      // 成功与失败都等：失败时把页面上的原因报出来，不是干等 5 分钟超时。
      await page.locator('[data-field="upload-ok"], [data-field="upload-failure"]').first().waitFor({ timeout: 300000 });
      if (await page.locator('[data-field="upload-failure"]').count()) {
        throw new Error(`上传失败：${(await page.locator('[data-field="upload-failure"]').innerText()).replace(/\s+/g, " ").slice(0, 160)}`);
      }
      const docs = await rest(jwt, `documents?select=id,filename,page_count&course_id=eq.${this.courseId}`);
      if (docs.length !== 1) throw new Error(`库里这门课有 ${docs.length} 份课件，应为 1`);
      return `${docs[0].filename} · ${docs[0].page_count} 页`;
    });
    await this.step("引导三步走完", async () => {
      const tour = page.locator('[data-field="tour"]');
      await tour.waitFor({ timeout: 10000 });
      for (let k = 0; k < 3; k++) await page.click('[data-field="tour-next"]');
      await tour.waitFor({ state: "detached" });
    });
    await this.step("「去提问」→ 输入框就在眼前", async () => {
      await page.click('[data-field="documents-done"]');
      await page.locator('[data-field="question"]').waitFor();
      // 弹窗的 close 事件是**异步派发**的，焦点在点击之后的下一个任务里才移过去。
      // 立刻读 activeElement 本地能过、CI 上会输（2026-10-08 CI 实测）——等它，最多 3 秒。
      const moved = await page
        .waitForFunction(() => document.activeElement?.getAttribute("data-field") === "question", null, { timeout: 3000 })
        .then(() => true, () => false);
      if (!moved) {
        const focused = await page.evaluate(() => document.activeElement?.getAttribute("data-field"));
        throw new Error(`关掉课件弹窗 3 秒后焦点仍在 ${focused}，不在输入框`);
      }
    });

    // ---- 5. 第一个问题
    await this.ask(page, "第一个问题：流式回答 → 答完", QUESTION, 1);
    await this.step("出处是刚传的课件，没有「不是用你的课件答的」", async () => {
      const turn = page.locator('[data-field="turn"]').nth(0);
      if (await turn.locator('[data-field="wrong-backend"]').count()) throw new Error("出现了「这一段不是用你的课件答的」");
      if ((await turn.getAttribute("data-backend")) !== "supabase") throw new Error(`backend=${await turn.getAttribute("data-backend")}`);
      const sources = await turn.locator('[data-field="sources"]').innerText().catch(() => "");
      if (!sources) throw new Error("这一轮没有出处");
      if (TARGET === "prod" && !sources.includes(PDF_NAME)) throw new Error(`出处里没有 ${PDF_NAME}：${sources.slice(0, 120)}`);
      return sources.replace(/\s+/g, " ").slice(0, 80);
    });
    if (TARGET === "prod") {
      const sources = await page.locator('[data-field="turn"]').nth(0).locator('[data-field="sources"]').innerText();
      const hit = new RegExp(`(^|[^0-9])${GOLD_PAGE}([^0-9]|$)`).test(sources.split(PDF_NAME)[1] ?? "");
      this.observe(`检索命中评测集金标页（Q022 → 第 ${GOLD_PAGE} 页）`, hit, sources.replace(/\s+/g, " ").slice(0, 100));
    }

    // ---- 6. 追问
    await this.ask(page, "追问「没懂」：第二轮答完", FOLLOW_UP, 2);

    // ---- 7. 调整讲法 → 查库 → 重讲
    const [cfgBefore] = await rest(jwt, "style_config?select=detail_level");
    const dir = cfgBefore.detail_level === "P3" ? "longer" : "shorter";
    const expectDetail = DETAIL_LADDER[DETAIL_LADDER.indexOf(cfgBefore.detail_level) + (dir === "shorter" ? 1 : -1)];
    await this.step(`调整讲法：${dir === "shorter" ? "再短一点" : "再细一点"} → 库里真的挪了一格`, async () => {
      await page.click('[data-field="style-adjust-entry"]');
      await page.click(`[data-adjust="${dir}"]`);
      await page.click('[data-field="adjust-apply"]');
      await page.locator('[data-field="adjust-done"]').waitFor({ timeout: 30000 });
      const [after] = await rest(jwt, "style_config?select=detail_level,use_examples,structure,depth,tone");
      if (after.detail_level !== expectDetail) throw new Error(`detail_level ${cfgBefore.detail_level} → ${after.detail_level}，应为 ${expectDetail}`);
      return `${cfgBefore.detail_level} → ${after.detail_level}`;
    });
    await this.ask(page, "「按新讲法重讲」→ 第三轮答完", null, 3);
    await this.step("第三轮的问题带重讲前缀、是原来那个问题", async () => {
      const q = await page.locator('[data-field="turn-question"]').nth(2).innerText();
      if (!q.startsWith(REDO_PREFIX)) throw new Error(`第三轮问题是「${q.slice(0, 40)}」`);
      if (!q.includes(FOLLOW_UP) && !q.includes(QUESTION)) throw new Error("重讲的不是上一个问题");
      return q.slice(0, 50);
    });

    // ---- 8. 刷新：都还在
    // **先查库、再看页面**，分开成两步：刷新后看不到第三轮时，要分得清是「没写进库」
    // （服务端落库失败不打断讲解，F42——屏幕上一切正常，库里少了几行）还是「库里有、页面没渲染出来」。
    // 2026-10-08 第一次生产跑红在这里，而原来那一步把两件事绑在一起，红了说不出是哪一边。
    let convId = null;
    await this.step("库里：一个会话、三问三答 6 条消息", async () => {
      const convs = await rest(jwt, `conversations?select=id,title&course_id=eq.${this.courseId}`);
      if (convs.length !== 1) throw new Error(`这门课有 ${convs.length} 个会话，应为 1（新对话不该建空行）`);
      convId = convs[0].id;
      const msgs = await rest(jwt, `messages?select=role,turn_index&conversation_id=eq.${convId}&order=created_at.asc`);
      const shape = msgs.map((m) => `${m.turn_index}${m.role === "user" ? "问" : "答"}`).join(" ");
      if (msgs.length !== 6) throw new Error(`messages ${msgs.length} 条，应为 6：[${shape}]`);
      return `「${convs[0].title.slice(0, 16)}」· [${shape}]`;
    });
    await this.step("刷新页面：会话在侧栏、三轮都显示", async () => {
      await page.reload();
      const answered = page.locator('[data-field="turn"][data-state="answered"]');
      const ok = await answered.nth(2).waitFor({ timeout: 30000 }).then(() => true, () => false);
      if (!ok) {
        // 红的时候把页面上**实际**有什么说出来，不只报一个超时。
        const n = await answered.count();
        const all = await page.locator('[data-field="turn"]').count();
        const err = await page.locator('[data-field="workspace-error"], [data-field="documents-error"]').allInnerTexts();
        const active = await page.locator('[data-field="conversation"][aria-current="true"]').allInnerTexts();
        throw new Error(`30 秒后页面上 ${all} 轮（已答 ${n}）· 当前会话 ${JSON.stringify(active)}${err.length ? ` · 报错：${err.join(" | ").slice(0, 120)}` : ""}`);
      }
      const title = await page.locator('[data-field="conversation"][aria-current="true"]').innerText();
      if (!title.includes(QUESTION.slice(0, 10))) throw new Error(`当前会话标题是「${title}」，应取第一个问题`);
      return `会话「${title.trim().slice(0, 20)}」`;
    });

    // ---- 9. 设置页
    await this.step("设置页：四种讲法与语气都在，语气选中项与库一致", async () => {
      await page.goto(`${BASE}/settings/style`);
      await page.locator('[data-field="presets"] [data-preset]').nth(3).waitFor();
      const [row] = await rest(jwt, "style_config?select=tone");
      const sel = await page.locator('[data-axis="tone"] [aria-pressed="true"]').getAttribute("data-value");
      if (sel !== row.tone) throw new Error(`页面选中 ${sel}，库里是 ${row.tone}`);
      return `tone=${sel}`;
    });

    // ---- 10. 退出
    await this.step("退出登录 → 欢迎页", async () => {
      await page.goto(`${BASE}/settings`);
      await page.getByRole("button", { name: "退出登录" }).click();
      await page.waitForURL(/\/welcome$/);
    });
  }

  /** 发一个问题（`text` 为 null 表示点「重讲」已经发出去了），等第 n 轮答完。 */
  async ask(page, name, text, n) {
    await this.step(name, async () => {
      const t0 = Date.now();
      if (text !== null) {
        await page.fill('[data-field="question"]', text);
        await page.click('[data-field="ask"]');
      } else {
        await page.click('[data-field="adjust-redo"]');
      }
      // 流式：先看到正文在长（answer-streaming），再等终态。看不到中间态不判失败——
      // 假后端很快，可能一闪而过——但记成观测。
      const sawStream = await page.locator('[data-field="answer-streaming"]').first()
        .waitFor({ timeout: 90000 }).then(() => Date.now() - t0).catch(() => null);
      const turn = page.locator('[data-field="turn"]').nth(n - 1);
      await turn.and(page.locator('[data-state="answered"], [data-state="failed"]')).waitFor({ timeout: 180000 });
      if ((await turn.getAttribute("data-state")) === "failed") {
        throw new Error(`这一轮失败了：${(await turn.locator('[data-field="turn-failure"]').innerText()).slice(0, 160)}`);
      }
      // 落库失败的提示条：讲解照常出来，但这一轮刷新就没了——这是功能红，不是观测。
      if (await turn.locator('[data-field="persist-failed"]').count()) {
        throw new Error(`这一轮没存进会话：${(await turn.locator('[data-field="persist-failed"]').innerText()).replace(/\s+/g, " ").slice(0, 160)}`);
      }
      const answer = await turn.locator('[data-field="answer"]').innerText();
      if (answer.trim().length < 20) throw new Error(`回答只有 ${answer.trim().length} 字`);
      const paras = await turn.locator('[data-field="answer"] p').count();
      this.timing ??= [];
      this.timing.push({ n, firstMs: sawStream, totalMs: Date.now() - t0 });
      return `${answer.trim().length} 字 · ${paras} 段 · 首字 ${sawStream === null ? "—" : (sawStream / 1000).toFixed(1) + "s"} · 全程 ${((Date.now() - t0) / 1000).toFixed(1)}s`;
    });
  }
}

class StopJourney extends Error {}

function log(tag, ok, name, detail = "", ms = 0) {
  const mark = ok === true ? "\x1b[32m✓\x1b[0m" : ok === "warn" ? "\x1b[33m—\x1b[0m" : "\x1b[31m✗\x1b[0m";
  console.log(`  ${mark} [${tag}] ${name}${ms ? `  (${(ms / 1000).toFixed(1)}s)` : ""}${detail ? `  ${detail}` : ""}`);
}

// ---------------------------------------------------------------- 越权（生产，≥2 个账号）

async function crossAccount(browser, a, b) {
  if (!a.courseId || !b.courseId) return;
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const steps = [];
  try {
    await page.goto(`${BASE}/login`);
    await page.fill('input[type="email"]', a.acc.email);
    await page.fill('input[type="password"]', a.acc.password);
    await page.click('button[type="submit"]');
    await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 });
    const r = await page.goto(`${BASE}/courses/${b.courseId}`);
    const ok = r?.status() === 404;
    steps.push({ name: `${a.tag} 打开 ${b.tag} 的课 → 404`, kind: "功能", ok, detail: `HTTP ${r?.status()}`, ms: 0 });
    log(a.tag, ok, `打开 ${b.tag} 的课 → 404`, `HTTP ${r?.status()}`);
  } catch (e) {
    steps.push({ name: `${a.tag} 打开 ${b.tag} 的课 → 404`, kind: "功能", ok: false, detail: String(e).slice(0, 200), ms: 0 });
  }
  await ctx.close();
  a.steps.push(...steps);
}

// ---------------------------------------------------------------- 主流程

async function main() {
  mkdirSync(OUT, { recursive: true });
  console.log(`\n\x1b[1m端到端动线测试 · ${TARGET}\x1b[0m`);
  if (!existsSync(PDF)) die(`找不到测试课件 ${PDF}`);
  if (TARGET === "mock") await setupMock();
  else if (TARGET === "prod") await setupProd();
  else die("--target 只能是 mock 或 prod");
  if (LIMIT > 0) accounts = accounts.slice(0, LIMIT);
  console.log(`  站点 ${BASE} · 账号 ${accounts.length} 个 · 并发 ${CONCURRENCY}\n`);

  const browser = await chromium.launch({ headless: !HEADED, ...(EXEC ? { executablePath: EXEC } : {}) });
  const journeys = accounts.map((a, i) => new Journey(a, i));
  // 并发跑：Render 免费层只有一个实例，并发太高时排队的是讲解，测出来的「慢」不是产品的慢。
  const queue = [...journeys];
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
    while (queue.length) await queue.shift().run(browser);
  }));
  if (TARGET === "prod" && journeys.length >= 2) {
    console.log("");
    for (let k = 0; k < journeys.length; k++) await crossAccount(browser, journeys[k], journeys[(k + 1) % journeys.length]);
  }
  await browser.close();
  report(journeys);
}

function report(journeys) {
  const fn = journeys.flatMap((j) => j.steps.filter((s) => s.kind === "功能"));
  const ob = journeys.flatMap((j) => j.steps.filter((s) => s.kind === "观测"));
  const fnRed = fn.filter((s) => !s.ok).length;
  const obRed = ob.filter((s) => !s.ok).length;
  const lines = [
    `# 端到端动线测试 · ${TARGET} · ${STAMP}`, "",
    `站点 \`${BASE}\` · 账号 ${journeys.length} 个`, "",
    "| | 绿 | 红 |", "| --- | --- | --- |",
    `| 功能 | ${fn.length - fnRed} | ${fnRed} |`,
    `| 观测（不判失败） | ${ob.length - obRed} | ${obRed} |`, "",
  ];
  for (const j of journeys) {
    lines.push(`## ${j.acc.email}`, "", "| | 类 | 步骤 | 耗时 | 说明 |", "| --- | --- | --- | --- | --- |");
    for (const s of j.steps) {
      lines.push(`| ${s.ok ? "✓" : "✗"} | ${s.kind} | ${s.name} | ${s.ms ? (s.ms / 1000).toFixed(1) + "s" : ""} | ${String(s.detail).replace(/\|/g, "\\|")} |`);
    }
    lines.push("");
  }
  writeFileSync(join(OUT, "report.md"), lines.join("\n"));
  console.log(`\n\x1b[1m功能\x1b[0m  绿 ${fn.length - fnRed} · 红 ${fnRed}`);
  console.log(`\x1b[1m观测\x1b[0m  绿 ${ob.length - obRed} · 红 ${obRed}（不判失败）`);
  console.log(`报告与失败截图：${OUT}\n`);
  killChildren();
  process.exit(fnRed > 0 ? 1 : 0);
}

process.on("SIGINT", () => {
  killChildren();
  process.exit(130);
});

main().catch((e) => {
  console.error(e);
  killChildren();
  process.exit(1);
});
