/**
 * F1 阶段 1 · **反向验证**：把防护逐条拆掉，断言必须变红。
 *
 * 通则七 #4：「把被测逻辑换成平凡实现，断言会不会红？不会红就是空的。」
 * 生产验收（verify_rls_prod.py）只跑正向五步——那五步全绿并不能自己证明
 * 「是 RLS 让它绿的」。本文件补的就是这一句。
 *
 * 破坏性动作只在本地一次性 WASM 实例上做，生产上不 disable RLS（口径 F12）。
 * 执行的 DDL 是 migrations/ 里那份**原文**，不是复制品——复制品漂掉之后，
 * 反向验证证明的就是另一个对象（通则七 #6）。
 *
 *   npm install && npm run verify
 */

import { PGlite } from "@electric-sql/pglite";
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const MIGRATIONS = join(HERE, "migrations");

const UID_A = "11111111-1111-4111-8111-111111111111";
const UID_B = "22222222-2222-4222-8222-222222222222";

/** Supabase 侧已存在、迁移文件依赖的对象。本地必须先立好。 */
const SUPABASE_STUBS = `
  create role anon nologin;
  create role authenticated nologin;
  create schema auth;
  create table auth.users (
    id    uuid primary key default gen_random_uuid(),
    email text unique
  );
  -- 与 Supabase 的 auth.uid() 同形：读 JWT claim。
  create or replace function auth.uid() returns uuid
  language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
  $$;
`;

let passed = 0;
let failed = 0;

function ok(name, detail = "") {
  passed++;
  console.log(`  \x1b[32m✓\x1b[0m ${name}${detail ? "  " + detail : ""}`);
}
function bad(name, detail = "") {
  failed++;
  console.log(`  \x1b[31m✗\x1b[0m ${name}${detail ? "  " + detail : ""}`);
}

/** 以某个用户的身份执行。role + JWT claim 一起切，与 PostgREST 的做法一致。 */
async function asUser(db, uid, fn) {
  await db.exec("reset role;");
  await db.query("select set_config('request.jwt.claim.sub', $1, false)", [uid]);
  await db.exec("set role authenticated;");
  try {
    return await fn();
  } finally {
    await db.exec("reset role;");
    await db.query("select set_config('request.jwt.claim.sub', '', false)");
  }
}

/**
 * 期望语句报错，**并且报的是哪个错**。返回 errcode（没报错则 null）。
 *
 * 只断言「报了错」是空的：本表 user_id 是主键，且触发器保证每个合法用户
 * 都已有行，所以任何冒名 insert 必然也会撞唯一约束。RLS 关掉之后错误码
 * 从 42501 变成 23505，「报了错」照样成立——**断言测的是主键不是 with check**。
 * 实测（2026-09-07，PGlite/PG18）：RLS 开 → 42501；RLS 关 → 23505。
 */
async function errcodeOf(fn) {
  try {
    await fn();
    return null;
  } catch (e) {
    return e.code ?? "unknown";
  }
}

// ---------------------------------------------------------------- 五条断言
//
// 与生产验收 verify_rls_prod.py 的五步一一对应。每条返回 true/false，
// 供正向检查与反向检查复用同一段逻辑——两边各写一份，拆防护时容易只改一边。

const CHECKS = {
  /** 步骤 1 · B 读自己 → 1 行。**这一步是步骤 2 的反向控制**（见 §R3）。 */
  async b_reads_own(db) {
    return await asUser(db, UID_B, async () => {
      const r = await db.query("select * from public.style_config where user_id = $1", [UID_B]);
      return r.rows.length === 1;
    });
  },

  /** 步骤 2 · A 读 B → 0 行。 */
  async a_cannot_read_b(db) {
    return await asUser(db, UID_A, async () => {
      const r = await db.query("select * from public.style_config where user_id = $1", [UID_B]);
      return r.rows.length === 0;
    });
  },

  /** 步骤 3 · A 改 B → 0 行受影响，且 B 的值确实没变。 */
  async a_cannot_update_b(db) {
    const before = (await db.query("select detail_level from public.style_config where user_id=$1", [UID_B])).rows[0];
    const affected = await asUser(db, UID_A, async () => {
      const r = await db.query(
        "update public.style_config set detail_level = 'P3' where user_id = $1 returning user_id", [UID_B]);
      return r.rows.length;
    });
    const after = (await db.query("select detail_level from public.style_config where user_id=$1", [UID_B])).rows[0];
    return affected === 0 && before.detail_level === after.detail_level;
  },

  /**
   * 步骤 4 · A 冒名 insert 一行 user_id = B → **必须是 42501**（RLS with check），
   * 不是 23505（主键冲突）。理由见 errcodeOf 的注释。
   */
  async a_cannot_insert_as_b(db) {
    const code = await asUser(db, UID_A, () =>
      errcodeOf(() =>
        db.query("insert into public.style_config (user_id, f1_arm) values ($1, 'pairwise')", [UID_B])));
    return code === "42501";
  },

  /** 步骤 5 · B 改自己的 f1_arm → 抛 23514（口径 F10：不静默改写）。 */
  async b_cannot_change_own_arm(db) {
    const before = (await db.query("select f1_arm from public.style_config where user_id=$1", [UID_B])).rows[0].f1_arm;
    const other = before === "pairwise" ? "text" : "pairwise";
    const code = await asUser(db, UID_B, () =>
      errcodeOf(() =>
        db.query("update public.style_config set f1_arm = $2 where user_id = $1", [UID_B, other])));
    const after = (await db.query("select f1_arm from public.style_config where user_id=$1", [UID_B])).rows[0].f1_arm;
    return code === "23514" && before === after;
  },
};

function migrationFiles() {
  const files = readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort();
  if (files.length === 0) throw new Error("migrations/ 下没有 .sql——没有被测对象");
  return files;
}

async function runMigrations(db) {
  for (const f of migrationFiles()) await db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
}

async function freshDb() {
  const db = await PGlite.create();
  await db.exec(SUPABASE_STUBS);
  await runMigrations(db);
  await db.query("insert into auth.users (id, email) values ($1, 'a@t.local'), ($2, 'b@t.local')", [UID_A, UID_B]);
  return db;
}

async function main() {
  console.log("\n迁移文件：" + migrationFiles().join(", "));

  // ============================================================ 正向
  console.log("\n\x1b[1m正向 · 五条断言应全绿\x1b[0m");
  {
    const db = await freshDb();

    const n = (await db.query("select count(*)::int c from public.style_config")).rows[0].c;
    n === 2
      ? ok("触发器建行", `auth.users 插 2 个 → style_config 2 行`)
      : bad("触发器建行", `期望 2 行，得到 ${n}`);

    const arms = (await db.query(
      "select user_id, f1_arm, f1_arm = public.f1_assign_arm(user_id) as matches from public.style_config order by user_id")).rows;
    arms.every((r) => r.matches)
      ? ok("f1_arm 由 f1_assign_arm 定", arms.map((r) => `${r.user_id.slice(0, 4)}→${r.f1_arm}`).join(" "))
      : bad("f1_arm 由 f1_assign_arm 定");

    // 两个臂都要被断言碰到。测试用的两个 uuid 恰好都落在 pairwise 上，
    // 若只看它们，f1_assign_arm 的 text 分支从没被任何断言执行过（通则七 #7）。
    const dist = (await db.query(
      "select public.f1_assign_arm(gen_random_uuid()) arm from generate_series(1, 400)")).rows
      .reduce((m, r) => ((m[r.arm] = (m[r.arm] || 0) + 1), m), {});
    dist.pairwise > 0 && dist.text > 0
      ? ok("f1_assign_arm 两个分支都可达", `400 个随机 uuid → pairwise ${dist.pairwise} / text ${dist.text}`)
      : bad("f1_assign_arm 有分支不可达", JSON.stringify(dist));

    for (const [name, fn] of Object.entries(CHECKS)) {
      (await fn(db)) ? ok(name) : bad(name);
    }
    await db.close();
  }

  // ============================================================ 回填
  //
  // 迁移里的回填在生产首次执行时补 0 行（auth.users 为空）。**一段永远补 0 行的
  // 语句和一段被删掉的语句，在生产上表现完全一样**——所以它必须在别处被真的执行
  // 过一次，否则没人能说出它到底还灵不灵（通则七 #7：这个对象被哪条断言碰过？）。
  //
  // 这里构造它唯一会做事的场景：存量用户 + 触发器缺失。
  console.log("\n\x1b[1m回填 · 存量用户补行（生产上当前是空操作）\x1b[0m");
  {
    const db = await freshDb();
    const UID_C = "33333333-3333-4333-8333-333333333333";

    // 模拟「触发器还没建 / 刚被重建」的时间窗：此时注册的用户拿不到行。
    await db.exec("drop trigger on_auth_user_created_style_config on auth.users;");
    await db.query("insert into auth.users (id, email) values ($1, 'c@t.local')", [UID_C]);

    const missing = (await db.query("select 1 from public.style_config where user_id=$1", [UID_C])).rows.length;
    missing === 0
      ? ok("存量用户 C 确实缺行", "← 这是下一条断言的前提，缺了它那条就是空的")
      : bad("C 不该有行", `得到 ${missing} 行`);

    const armsBefore = (await db.query(
      "select user_id, f1_arm from public.style_config order by user_id")).rows;

    await runMigrations(db); // 重跑同一份迁移文件——顺带验幂等

    const c = (await db.query("select f1_arm from public.style_config where user_id=$1", [UID_C])).rows;
    c.length === 1 && c[0].f1_arm === (await db.query("select public.f1_assign_arm($1) a", [UID_C])).rows[0].a
      ? ok("重跑迁移后 C 被补上，且臂由 f1_assign_arm 定", `C → ${c[0]?.f1_arm}`)
      : bad("回填没补上 C", JSON.stringify(c));

    const armsAfter = (await db.query(
      "select user_id, f1_arm from public.style_config where user_id in ($1,$2) order by user_id", [UID_A, UID_B])).rows;
    JSON.stringify(armsBefore.filter(r => r.user_id !== UID_C)) === JSON.stringify(armsAfter)
      ? ok("已有用户的 f1_arm 未被回填改动", "← on conflict do nothing，不是 do update")
      : bad("回填改动了已有用户的臂", `${JSON.stringify(armsBefore)} → ${JSON.stringify(armsAfter)}`);

    const total = (await db.query("select count(*)::int c from public.style_config")).rows[0].c;
    total === 3 ? ok("迁移可重复执行，无重复行", `${total} 行`) : bad("重跑后行数不对", `${total}`);
    await db.close();
  }

  // ============================================================ cascade
  //
  // PRD §12.1 承诺「注销即清空」，而本表**刻意不给 delete 策略**——清空完全
  // 依赖 auth.users 的 on delete cascade。那是一条写在合规章节里、却从没被任何
  // 断言碰过的承诺（通则七 #1：凡是写成「已验证」的句子，必须能指出是哪次测量）。
  console.log("\n\x1b[1m注销 · on delete cascade（§12.1「注销即清空」）\x1b[0m");
  {
    const db = await freshDb();
    const before = (await db.query("select count(*)::int c from public.style_config")).rows[0].c;
    await db.query("delete from auth.users where id = $1", [UID_B]);
    const after = (await db.query("select count(*)::int c from public.style_config")).rows[0].c;
    const bGone = (await db.query("select 1 from public.style_config where user_id=$1", [UID_B])).rows.length === 0;
    const aKept = (await db.query("select 1 from public.style_config where user_id=$1", [UID_A])).rows.length === 1;

    bGone && after === before - 1
      ? ok("删 auth.users 的行 → style_config 的行随之消失", `${before} → ${after}`)
      : bad("cascade 没有触发", `${before} → ${after}`);
    aKept ? ok("只清掉被注销的那个用户", "A 的行仍在") : bad("误删了别的用户");
    await db.close();
  }

  // ============================================================ 反向
  console.log("\n\x1b[1m反向 · 拆掉防护后，对应断言必须变红\x1b[0m");

  const reversals = [
    {
      id: "R1",
      what: "关掉 RLS",
      sql: "alter table public.style_config disable row level security;",
      mustFail: ["a_cannot_read_b", "a_cannot_update_b", "a_cannot_insert_as_b"],
    },
    {
      id: "R2",
      what: "去掉 f1_arm 钉死触发器",
      sql: "drop trigger style_config_before_update on public.style_config;",
      mustFail: ["b_cannot_change_own_arm"],
    },
    {
      id: "R4",
      what: "去掉触发器 + update 策略的 with check",
      sql: `drop trigger style_config_before_update on public.style_config;
            drop policy style_config_update_own on public.style_config;
            create policy style_config_update_own on public.style_config
              for update to authenticated using (user_id = auth.uid());`,
      custom: async (db) => {
        // 没有 with check，用户可以把自己那行的 user_id 改成别人的 —— 夺行。
        const moved = await asUser(db, UID_B, async () => {
          await db.query("delete from auth.users where id = $1", [UID_A]).catch(() => {});
          const r = await db.query(
            "update public.style_config set detail_level='P3' where user_id=$1 returning user_id", [UID_B]);
          return r.rows.length;
        });
        return moved === 1; // 能改说明策略还在放行，用于确认拆的位置对
      },
      note: "with check 是承重的：去掉它，update 策略只挡「改别人的行」，挡不住「把自己的行改成别人的」",
    },
  ];

  for (const rev of reversals) {
    const db = await freshDb();
    await db.exec(rev.sql);
    console.log(`\n  \x1b[1m${rev.id} · ${rev.what}\x1b[0m`);
    if (rev.mustFail) {
      for (const name of rev.mustFail) {
        const stillGreen = await CHECKS[name](db);
        stillGreen
          ? bad(`${name} 应变红，却仍是绿的`, "← 这条断言是空的")
          : ok(`${name} 变红`, "← 断言有效");
      }
    }
    if (rev.custom) {
      const r = await rev.custom(db);
      r ? ok("with check 缺失时 update 确实放行", rev.note) : bad("拆解位置不对，未观察到放行");
    }
    await db.close();
  }

  // R6 · cascade 的反向验证：换成 on delete no action，注销必须失败/留残行。
  console.log("\n  \x1b[1mR6 · 外键从 cascade 改成 no action\x1b[0m");
  {
    const db = await freshDb();
    await db.exec(`alter table public.style_config
                     drop constraint style_config_user_id_fkey,
                     add constraint style_config_user_id_fkey
                       foreign key (user_id) references auth.users (id);`);
    const code = await errcodeOf(() => db.query("delete from auth.users where id=$1", [UID_B]));
    const still = (await db.query("select 1 from public.style_config where user_id=$1", [UID_B])).rows.length;
    code !== null || still === 1
      ? ok("无 cascade 时注销清不掉配置", `errcode=${code} 残留 ${still} 行 ← cascade 是承重的`)
      : bad("去掉 cascade 后仍被清掉了", "← 说明清空的不是 cascade，上面那条断言测错了对象");
    await db.close();
  }

  // R5 · 回填的反向验证：把回填语句从迁移文本里剔掉，C 必须补不上。
  console.log("\n  \x1b[1mR5 · 迁移里去掉回填语句\x1b[0m");
  {
    const db = await PGlite.create();
    await db.exec(SUPABASE_STUBS);
    const UID_C = "33333333-3333-4333-8333-333333333333";
    // 与上面同一份文本，只切掉「回填」那一节——切法与注释里的分节标记绑定，
    // 分节标记若被改掉这里会抛错，而不是静默跳过一段不存在的替换。
    const src = readFileSync(join(MIGRATIONS, migrationFiles()[0]), "utf8");
    const cut = src.replace(/-{4,} 回填[\s\S]*?\$\$;\n/, "");
    if (cut === src) throw new Error("没能切掉回填节——分节标记变了，这条反向验证已失效");
    await db.exec(cut);
    await db.query("insert into auth.users (id, email) values ($1, 'c@t.local')", [UID_C]);
    await db.exec("drop trigger on_auth_user_created_style_config on auth.users;");
    await db.query("insert into auth.users (id, email) values ($1, 'd@t.local')",
                   ["44444444-4444-4444-8444-444444444444"]);
    await db.exec(cut); // 重跑（无回填）
    const n = (await db.query("select 1 from public.style_config where user_id=$1",
                              ["44444444-4444-4444-8444-444444444444"])).rows.length;
    n === 0
      ? ok("无回填时存量用户补不上", "← 回填那段确实在做事，不是死代码")
      : bad("去掉回填后仍被补上了", "← 说明补行的不是回填语句，上面那条断言测错了对象");
    await db.close();
  }

  // ============================================================ R3
  // 任务书原文的验收是「A 读 B 的行 → 返回空」。**B 的行不存在时它也返回空。**
  // 这里把它演示出来：关掉 RLS + 删掉 B 的行，那条断言依然绿。
  console.log("\n  \x1b[1mR3 · 演示「A 读 B 返回空」在没有 RLS 时也能是绿的（通则七 #5）\x1b[0m");
  {
    const db = await freshDb();
    await db.exec("alter table public.style_config disable row level security;");
    await db.query("delete from public.style_config where user_id = $1", [UID_B]);

    const vacuous = await CHECKS.a_cannot_read_b(db);
    const control = await CHECKS.b_reads_own(db);

    vacuous
      ? ok("RLS 已关 + B 无行 → 「A 读 B 为空」仍然绿", "← 单靠这一条的验收是空转的")
      : bad("预期该断言在此条件下为绿");
    !control
      ? ok("而步骤 1「B 读自己」在同一条件下是红的", "← 它才是承重的反向控制")
      : bad("步骤 1 应变红");
    await db.close();
  }

  console.log(`\n\x1b[1m合计\x1b[0m  绿 ${passed} · 红 ${failed}\n`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
