#!/usr/bin/env node
/**
 * F1 · 邀请制建号（口径 F40）。**v1 不做自助注册页。**
 *
 *     node scripts/invite_user.mjs --email a@b.com --password '……' --note '内测第一批'
 *
 * ## 为什么是邀请制而不是注册页
 *
 * `/login` 只有登录没有注册入口，这是内测的硬阻塞——没有用户就收不到任何埋点数据。
 * 30–50 人规模下邀请制比注册页省得多：不用做邮箱验证流程、找回密码、防滥用。
 * 那三样都是要长期维护的东西，而它们在这个规模上换不到任何东西。
 *
 * ## 为什么不直接往 auth.users 插 SQL 行
 *
 * 那会绕过 Supabase 自己的密码哈希与字段初始化（`encrypted_password` 的算法、
 * `confirmation_token`、`aud`、`instance_id` 等一整套），属于绕开人家的机制自己造一套。
 * 造出来的行**看起来是对的**——它在表里，能被查到——但登录会失败，或者更糟：
 * 在某次 Supabase 升级后以别的形态失败。
 *
 * 所以这里走 **Admin API 的 `POST /auth/v1/admin/users`**，
 * 也就是 supabase-js 的 `auth.admin.createUser` 打的那个端点、
 * Dashboard 上「Add user」按的那个按钮。
 *
 * 用 REST 而不是引 supabase-js：本脚本住在仓库根的 `scripts/` 下（它是运维工具，
 * 不是 web 应用的一部分），为它引一条 npm 依赖树不划算——同 `verify_rls_prod.py`
 * 的零依赖约定。**换的是依赖，不是机制**：打的仍然是 Supabase 自己那条路径。
 *
 * ## service_role key 只在本地（口径 F40）
 *
 * 它**绕过全部 RLS**。进了 Vercel 环境变量等于把整个数据库开放出去——
 * 而且不会有任何报错，一切看起来正常。
 *
 * 三道措施：
 *   1. key 只从本地 `scripts/.env` 读（`.env*` 已被根 .gitignore 挡在仓库外）；
 *   2. `web/scripts/verify_build_guards.mjs` 把一个真 key 形态的值注进 `next build`
 *      的环境再扫产物，仍须 0 命中（沿用 F16 的形态）；
 *   3. 本脚本**从不打印 key**，也不把它写进任何文件。
 *
 * ## 记录谁被邀请了
 *
 * 每次成功建号追加一行到 `docs/F1/invited_users.md`。**刻意不建表**：
 * 建表就要管 RLS、管 cascade、管它与事件表的一致性，而这是一份 30 行的运维记录，
 * 不是产品数据。它也不该随注销一起消失——「我们邀请过谁」是运维事实。
 */

import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { classifySecret } from "../web/lib/public-env-guard.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(HERE);
const LEDGER = join(ROOT, "docs", "F1", "invited_users.md");

// ---------------------------------------------------------------- env

function loadEnv() {
  const env = { ...process.env };
  const dotenv = join(HERE, ".env");
  if (existsSync(dotenv)) {
    for (const line of readFileSync(dotenv, "utf8").split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#") || !t.includes("=")) continue;
      const i = t.indexOf("=");
      const k = t.slice(0, i).trim();
      if (env[k] === undefined) env[k] = t.slice(i + 1).trim();
    }
  }
  return env;
}

const ENV = loadEnv();
const URL_ = (ENV.SUPABASE_URL ?? "").replace(/\/$/, "");
const SECRET = ENV.SUPABASE_SECRET_KEY ?? "";

function die(msg) {
  console.error(`\n\x1b[31m${msg}\x1b[0m\n`);
  process.exit(1);
}

if (!URL_ || !SECRET) {
  die(
    "缺少 SUPABASE_URL 或 SUPABASE_SECRET_KEY。\n" +
      "  cp scripts/.env.example scripts/.env && $EDITOR scripts/.env\n" +
      "  secret key 在 Supabase → Project Settings → API keys。**只放本地，不进 Vercel。**",
  );
}

// 拿的必须是 secret key。给 publishable key 的话 Admin API 会 401，
// 但报出来的是「未授权」——那句话不会告诉任何人「你给错了 key 的种类」。
// 判定复用 web 那份 classifySecret，**不在这里重写一遍**（同 F13）：
// 「什么算 secret key」这个问题只该有一个答案。
if (classifySecret(SECRET) === null) {
  die(
    "SUPABASE_SECRET_KEY 看起来不是一个 secret key（既不是 sb_secret_ 开头，也不是 role=service_role 的 JWT）。\n" +
      "  用 publishable key 调 Admin API 会以「未授权」失败，而那句话不会告诉你是 key 给错了种类。",
  );
}

// ---------------------------------------------------------------- 参数

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const email = arg("email");
const password = arg("password");
const note = arg("note") ?? "";

if (!email || !password) {
  die(
    "用法：node scripts/invite_user.mjs --email <邮箱> --password <密码> [--note <用途>]\n" +
      "  --note 会连同邮箱与日期一起写进 docs/F1/invited_users.md。",
  );
}
if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) die(`邮箱形态不对：${email}`);
if (password.length < 8) die("密码短于 8 位。Supabase 默认下限是 6，这里要求 8——内测账号也是真账号。");

// ---------------------------------------------------------------- 建号

/**
 * `email_confirm: true`：**直接标记为已验证**，不发确认邮件。
 *
 * 邀请制的整个意义就在这里——我们已经知道这个人是谁了，再让他走一遍
 * 「收邮件 → 点链接」只是给自己加一条会坏的链路（邮件进垃圾箱、
 * 域名没配 SPF、链接过期），而每一种坏法都要人来处理。
 */
const res = await fetch(`${URL_}/auth/v1/admin/users`, {
  method: "POST",
  headers: {
    apikey: SECRET,
    Authorization: `Bearer ${SECRET}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ email, password, email_confirm: true }),
});

const raw = await res.text();
let body;
try {
  body = JSON.parse(raw);
} catch {
  body = raw;
}

if (!res.ok) {
  // 原文照报 + 状态码。压成「建号失败」会把「邮箱已存在」（422）、
  // 「key 无效」（401）、「密码太弱」（422 但 msg 不同）说成同一句话。
  // **不打印 key**——它在请求头里，不在这段输出里。
  die(
    `建号失败（HTTP ${res.status}）：${typeof body === "string" ? body : JSON.stringify(body)}\n` +
      "  422 + user_already_exists → 这个邮箱已经有账号了；\n" +
      "  401 → key 不对或不是 service_role；\n" +
      "  其余 → 原文见上。",
  );
}

const userId = body?.id ?? "(未返回 id)";

// ---------------------------------------------------------------- 记录
//
// 建号成功之后才写。反过来先写的话，账本里会有一批实际不存在的账号——
// 而账本的用途正是「谁有权限进来」，那种错误方向恰好是最坏的那个。

const date = new Date().toISOString().slice(0, 10);
if (!existsSync(LEDGER)) {
  die(`账本不存在：${LEDGER}\n  账号已经建出来了（${userId}），请手工补一行。`);
}
appendFileSync(LEDGER, `| ${email} | ${date} | ${note || "—"} | \`${userId}\` |\n`, "utf8");

console.log(`\n\x1b[32m✓\x1b[0m 已建号：${email}`);
console.log(`  user_id  ${userId}`);
console.log(`  已追加一行到 docs/F1/invited_users.md`);
console.log(`\n  这个用户的 style_config 行由 auth.users 的 after-insert 触发器建（口径 F09）。`);
console.log(`  核对一下：select user_id, f1_arm, onboarded_at from style_config where user_id = '${userId}';`);
console.log(`  为 0 行说明触发器没跑——那是坏状态，用户会进不了 onboarding。\n`);
