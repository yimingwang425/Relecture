#!/usr/bin/env node
/**
 * F1 · 邀请制建号（口径 F40）。**v1 不做自助注册页。**
 *
 *     node scripts/invite_user.mjs --email a@b.com --password '……' --note '内测第一批'
 *
 *     # 批量（2026-10-08）：一次建 N 个测试账号，密码随机生成
 *     node scripts/invite_user.mjs --count 10 --prefix tester --note '内测第一批'
 *     node scripts/invite_user.mjs --emails 'a@x.com,b@y.com' --note '同学试用'
 *
 * ## 批量模式的密码去哪了
 *
 * 账本（`docs/F1/invited_users.md`，进 git）**仍然不写密码**。批量生成的密码只写进
 * `scripts/invited/<日期>-<时间>.csv`（权限 0600，`scripts/invited/` 在 .gitignore 里），
 * 由你转交给本人。密码只在你本地出现这一次——丢了就在 Dashboard 里重设，不要再建一个号。
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

import { randomInt } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

const note = arg("note") ?? "";
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** 批量上限。邀请制的规模是 30–50 人（F40），一次超过这个数多半是参数打错了。 */
const MAX_BATCH = 50;

/**
 * 要建哪些号：`[{ email, password, generated }]`。三种来源互斥：
 * 单个（--email + --password）、按前缀编号（--count + --prefix）、给定邮箱表（--emails）。
 */
function plan() {
  const count = arg("count");
  const emails = arg("emails");
  if ([arg("email"), count, emails].filter(Boolean).length !== 1) {
    die(
      "用法（三选一）：\n" +
        "  node scripts/invite_user.mjs --email <邮箱> --password <密码> [--note <用途>]\n" +
        "  node scripts/invite_user.mjs --count <N> [--prefix tester] [--domain test.local] [--start 1] [--note <用途>]\n" +
        "  node scripts/invite_user.mjs --emails 'a@x.com,b@y.com' [--note <用途>]\n" +
        "  --note 会连同邮箱与日期一起写进 docs/F1/invited_users.md。",
    );
  }
  if (arg("email")) {
    const password = arg("password");
    if (!password) die("单个建号要给 --password。批量模式才自动生成密码。");
    return [{ email: arg("email"), password, generated: false }];
  }
  let list;
  if (count) {
    const n = Number(count);
    if (!Number.isInteger(n) || n < 1 || n > MAX_BATCH) die(`--count 要是 1–${MAX_BATCH} 的整数，收到 ${count}`);
    const prefix = arg("prefix") ?? "tester";
    // test.local：与账本里已有的验收账号同一个域。`email_confirm: true` 不发信，收不收得到无所谓。
    const domain = arg("domain") ?? "test.local";
    const start = Number(arg("start") ?? "1");
    const width = String(start + n - 1).length < 2 ? 2 : String(start + n - 1).length;
    list = Array.from({ length: n }, (_, i) => `${prefix}${String(start + i).padStart(width, "0")}@${domain}`);
  } else {
    list = emails.split(/[,\s]+/).filter(Boolean);
    if (list.length > MAX_BATCH) die(`一次最多 ${MAX_BATCH} 个，收到 ${list.length} 个`);
  }
  if (new Set(list).size !== list.length) die("邮箱有重复");
  return list.map((email) => ({ email, password: randomPassword(), generated: true }));
}

/**
 * 随机密码：14 位，去掉 0/O/1/l/I 这类转交时容易抄错的字符。
 * 用 `crypto.randomInt`（拒绝采样，无取模偏差），不用 Math.random。
 */
const ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
function randomPassword(len = 14) {
  return Array.from({ length: len }, () => ALPHABET[randomInt(ALPHABET.length)]).join("");
}

const accounts = plan();
for (const a of accounts) {
  if (!EMAIL_RE.test(a.email)) die(`邮箱形态不对：${a.email}`);
  if (a.password.length < 8) die("密码短于 8 位。Supabase 默认下限是 6，这里要求 8——内测账号也是真账号。");
}

// ---------------------------------------------------------------- 建号

/**
 * `email_confirm: true`：**直接标记为已验证**，不发确认邮件。
 *
 * 邀请制的整个意义就在这里——我们已经知道这个人是谁了，再让他走一遍
 * 「收邮件 → 点链接」只是给自己加一条会坏的链路（邮件进垃圾箱、
 * 域名没配 SPF、链接过期），而每一种坏法都要人来处理。
 *
 * 返回 `{ ok, userId }` 或 `{ ok: false, status, detail }`。**不抛、不退出**：
 * 批量时一个失败（比如邮箱已存在）不该让后面的全部不建。
 */
async function createUser(email, password) {
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
    return { ok: false, status: res.status, detail: typeof body === "string" ? body : JSON.stringify(body) };
  }
  return { ok: true, userId: body?.id ?? "(未返回 id)" };
}

// ---------------------------------------------------------------- 记录
//
// 每个号**建成之后**才写账本那一行。反过来先写的话，账本里会有一批实际不存在的账号——
// 而账本的用途正是「谁有权限进来」，那种错误方向恰好是最坏的那个。

const date = new Date().toISOString().slice(0, 10);
if (!existsSync(LEDGER)) die(`账本不存在：${LEDGER}\n  一个号都还没建。`);

const done = [];
const failed = [];
for (const a of accounts) {
  const r = await createUser(a.email, a.password);
  if (!r.ok) {
    failed.push({ ...a, ...r });
    console.log(`\x1b[31m✗\x1b[0m ${a.email}  HTTP ${r.status}  ${r.detail.slice(0, 160)}`);
    // 401 对每个号都一样（key 不对），继续建只会刷出一屏相同的错。
    if (r.status === 401 || r.status === 403) break;
    continue;
  }
  appendFileSync(LEDGER, `| ${a.email} | ${date} | ${note || "—"} | \`${r.userId}\` |\n`, "utf8");
  done.push({ ...a, userId: r.userId });
  console.log(`\x1b[32m✓\x1b[0m ${a.email}  ${r.userId}`);
}

// ---------------------------------------------------------------- 批量：密码文件

let credFile = null;
const generated = done.filter((a) => a.generated);
if (generated.length > 0) {
  const dir = join(HERE, "invited");
  mkdirSync(dir, { recursive: true });
  // 带毫秒，且 `flag: "wx"`：**绝不覆盖**已有的密码文件。同一秒里连跑两批时，
  // 只到秒的文件名会让第二批把第一批的密码整份盖掉（2026-10-08 假服务端实测）——
  // 那批号已经建了、账本已经写了，密码却没了。
  const stamp = new Date().toISOString().replace(/[:T.]/g, "-").slice(0, 23);
  credFile = join(dir, `${stamp}.csv`);
  // --note 是自由文本，可能带逗号或引号：按 CSV 规则加引号，否则这一行的列会错位。
  const cell = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const csv = ["email,password,user_id,note", ...generated.map((a) => [a.email, a.password, a.userId, note].map(cell).join(","))];
  // 0600：只有你自己能读。这是全仓库唯一一个落盘的明文密码，它不该比 .env 更容易被读到。
  writeFileSync(credFile, csv.join("\n") + "\n", { encoding: "utf8", mode: 0o600, flag: "wx" });
}

// ---------------------------------------------------------------- 核对 style_config（口径 F09）
//
// 每个用户的 style_config 行由 auth.users 的 after-insert 触发器建。为 0 行的用户进不了
// onboarding——那是坏状态，而建号本身会成功，不核对就不会有人知道。
// 用的是同一把 secret key（绕过 RLS），只读，只查刚建的这几个 id。
let missing = [];
if (done.length > 0) {
  const ids = done.map((a) => a.userId).join(",");
  const r = await fetch(`${URL_}/rest/v1/style_config?select=user_id&user_id=in.(${ids})`, {
    headers: { apikey: SECRET, Authorization: `Bearer ${SECRET}` },
  });
  if (r.ok) {
    const have = new Set((await r.json()).map((x) => x.user_id));
    missing = done.filter((a) => !have.has(a.userId));
  } else {
    console.log(`\n\x1b[33m⚠\x1b[0m 没核对成 style_config（HTTP ${r.status}），请手工查：`);
    console.log(`  select user_id, f1_arm, onboarded_at from style_config where user_id in (${done.map((a) => `'${a.userId}'`).join(", ")});`);
    missing = null;
  }
}

console.log(`\n建成 ${done.length} 个 · 失败 ${failed.length} 个 · 未尝试 ${accounts.length - done.length - failed.length} 个`);
if (done.length > 0) console.log(`已追加 ${done.length} 行到 docs/F1/invited_users.md（不含密码）`);
if (credFile) {
  console.log(`\n密码只写在这里（0600，不进 git）：\n  ${credFile}\n`);
  for (const a of generated) console.log(`  ${a.email.padEnd(28)} ${a.password}`);
}
if (missing && missing.length > 0) {
  console.log(`\n\x1b[31m✗ ${missing.length} 个号没有 style_config 行（触发器没跑，口径 F09）：\x1b[0m`);
  for (const a of missing) console.log(`  ${a.email}  ${a.userId}`);
} else if (missing) {
  console.log(`\nstyle_config 行：${done.length}/${done.length} 都在（触发器正常）。`);
}
console.log("");
process.exit(failed.length > 0 || (missing && missing.length > 0) ? 1 : 0);
