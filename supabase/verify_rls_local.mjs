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
  -- 模拟 Supabase 注册时写 auth.users 的那个角色。**对 style_config 没有任何权限**——
  -- 这正是「触发器靠 security definer 而不是靠调用者权限」的检验条件。
  create role auth_admin_sim nologin;
  create schema auth;
  create table auth.users (
    id    uuid primary key default gen_random_uuid(),
    email text unique
  );
  -- auth_admin_sim 要能进 auth schema 才谈得上「往 auth.users 插行」。
  -- 这是环境搭建，不是断言的一部分——它对 public.style_config 仍然一无所有。
  grant usage on schema auth to auth_admin_sim;
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
/** 同 errcodeOf，但把整个错误交出去——有的断言要看文本，不只看码。 */
async function errorOf(fn) {
  try {
    await fn();
    return null;
  } catch (e) {
    return e;
  }
}

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
   * 步骤 4 · A 冒名 insert 一行 user_id = B → **必须是 42501**，不是 23505（主键冲突）。
   *
   * ⚠️ **2026-09-10 起这条断言测的对象换了。** 迁移 `20260910090000` 收回了
   * `authenticated` 的 insert 授权并删掉了 insert 策略，所以现在拦住它的是
   * **权限层**，不再是 RLS 的 with check。两者的 SQLSTATE **都是 42501**——
   * 只断错误码的话，这条断言会在「对象已经换了」的情况下静默保持绿色
   * （与 F11 当初那个「报了错就算过」是同一个形态，只是换了一层）。
   *
   * 所以这里同时看错误文本：权限层报 `permission denied`，RLS 层报
   * `row-level security`。断言要求**当前必须是权限层**，把「防护从哪一层来的」
   * 也钉住。真要哪天把 grant 加回来，这条会红并指出它退回了 RLS 层。
   */
  async a_cannot_insert_as_b(db) {
    const err = await errorOf(() =>
      asUser(db, UID_A, () =>
        db.query("insert into public.style_config (user_id, f1_arm) values ($1, 'pairwise')", [UID_B])));
    return err?.code === "42501" && /permission denied/i.test(err?.message ?? "");
  },

  /**
   * 步骤 4.5 · **「拒绝」的那条允许控制**（口径 F11）。
   *
   * 上一条断言「客户端插不进去」。**只有它的话，把注册一起打死也会全绿**——
   * 收回授权、删掉策略、再顺手让触发器失效，客户端照样插不进去，而没有任何
   * 断言会红，直到第一个新用户注册后发现自己没有配置行（F09 说的那个坏状态）。
   *
   * 这条补的就是那一半：用一个**对 style_config 毫无权限**的角色
   * （模拟 Supabase 的 supabase_auth_admin）往 auth.users 插行，
   * style_config 必须仍然多出一行——因为 `handle_new_user_style_config` 是
   * **security definer**，以函数所有者身份执行，不看调用者权限。
   *
   * 反向控制是 R7：把 security definer 换成 security invoker，这条必须变红。
   */
  async trigger_inserts_for_unprivileged_role(db) {
    const uid = "55555555-5555-4555-8555-555555555555";
    await db.exec("reset role;");
    // 先确认这个角色确实没有 insert 权限，否则下面「它也能建行」就没有信息量。
    const priv = (await db.query(
      "select has_table_privilege('auth_admin_sim', 'public.style_config', 'insert') p")).rows[0].p;
    if (priv) return false;

    await db.exec("grant insert on auth.users to auth_admin_sim;");
    await db.exec("set role auth_admin_sim;");
    try {
      await db.query("insert into auth.users (id, email) values ($1, 'authadmin@t.local')", [uid]);
    } catch {
      return false;
    } finally {
      await db.exec("reset role;");
    }
    const rows = await db.query("select 1 from public.style_config where user_id = $1", [uid]);
    return rows.rows.length === 1;
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

/**
 * 本地**验不了**的迁移。分界线是「要不要 pgvector」。
 *
 * PGlite 的 contrib 里没有 vector（实测 0.5.8：34 个扩展，没有它），所以
 * `20260911100100_f2_embeddings.sql` 在本地一条都跑不了。
 *
 * **跳过是吵着跳过的**：下面 `migrationFiles()` 会断言这些文件确实存在——
 * 改名之后这个跳过会**抛错**，而不是静默变成「没这回事」。
 * 主流程另有一条显眼的提示行，且本地绿数与待真库验证的条数**分两行报，不报合计**。
 *
 * ⚠️ 刻意不给本地造一个假的 `<=>` 来让它「跑得起来」：假的排序语义会让召回类
 * 断言全部失去意义，而那正是 `InMemoryRetriever` 文档里那句话的同一条。
 */
const NOT_VERIFIABLE_LOCALLY = ["20260911100100_f2_embeddings.sql"];

function allMigrationFiles() {
  const files = readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort();
  if (files.length === 0) throw new Error("migrations/ 下没有 .sql——没有被测对象");
  return files;
}

function migrationFiles() {
  const all = allMigrationFiles();
  for (const skipped of NOT_VERIFIABLE_LOCALLY) {
    if (!all.includes(skipped)) {
      throw new Error(
        `跳过清单里的 ${skipped} 在 migrations/ 下不存在——` +
          "它被改名或删掉了，而这个跳过正在静默地什么都不做。先修跳过清单。",
      );
    }
  }
  return all.filter((f) => !NOT_VERIFIABLE_LOCALLY.includes(f));
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
  console.log("\n本地执行的迁移：" + migrationFiles().join(", "));
  console.log(
    "\x1b[33m本地跳过（需 pgvector，只能对真库验）：" +
      NOT_VERIFIABLE_LOCALLY.join(", ") +
      "\x1b[0m\n  \x1b[2m→ 验证走 supabase/verify_corpus_prod.py。" +
      "本地绿数**不含**它的任何一条。\x1b[0m",
  );

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

  // ============================================================ P1 收紧
  //
  // 迁移 20260909130000 把 detail_level 的 CHECK 收紧到 P0/P2/P3，并在收紧前
  // **拒绝静默改写**：存量若有 P1 就抛异常让整条迁移回滚，因为「P1 该移到 P0 还是 P2」
  // 是产品决策。
  //
  // 生产上这个异常大概率永远不触发（没有写入路径会产生 P1），
  // 那正是通则七 #5 的形态——**一条永远不触发的守卫，和一条被删掉的守卫，表现完全一样**。
  // 所以在这里构造它唯一会做事的场景，把两面都测一遍。
  console.log("\n\x1b[1mP1 收紧 · 迁移守卫的两面\x1b[0m");
  {
    const files = migrationFiles();
    const first = files[0];
    const p1Mig = files.find((f) => f.includes("detail_level_drop_p1"));
    if (!p1Mig) throw new Error("找不到 P1 收紧迁移 —— 这一节已失效");

    // ---- 拒绝：存量有 P1 → 抛异常，且那一行原样留着
    {
      const db = await PGlite.create();
      await db.exec(SUPABASE_STUBS);
      await db.exec(readFileSync(join(MIGRATIONS, first), "utf8")); // 这一版 CHECK 还允许 P1
      await db.query("insert into auth.users (id, email) values ($1, 'a@t.local')", [UID_A]);
      await db.query("update public.style_config set detail_level='P1' where user_id=$1", [UID_A]);

      const err = await errorOf(() => db.exec(readFileSync(join(MIGRATIONS, p1Mig), "utf8")));
      const row = (await db.query("select detail_level from public.style_config where user_id=$1", [UID_A])).rows[0];

      err !== null && /P1/.test(err.message ?? "") && row.detail_level === "P1"
        ? ok("存量有 P1 → 迁移抛异常，且那一行未被改写", `errcode=${err.code} 仍是 ${row.detail_level}`)
        : bad("守卫没拦住", `err=${err?.message ?? "无"} 行=${row?.detail_level}`);
      await db.close();
    }

    // ---- 允许（反向控制）：存量无 P1 → 迁移正常通过，且 CHECK 此后拒绝 P1
    //
    // 少了这条，上一条在「迁移永远抛异常」的实现下同样绿。
    {
      const db = await freshDb(); // 全部迁移，存量无 P1
      const n = (await db.query(
        "select count(*)::int c from public.style_config where detail_level='P1'")).rows[0].c;
      const code = await errcodeOf(() =>
        db.query("update public.style_config set detail_level='P1' where user_id=$1", [UID_A]));

      n === 0 && code === "23514"
        ? ok("存量无 P1 → 迁移通过，且此后写 P1 被 CHECK 拒", `P1 存量 ${n} 行，写 P1 → ${code}`)
        : bad("收紧后的状态不对", `P1 存量 ${n} 行，写 P1 → ${code}`);

      // 反向控制的反向控制：P2 必须写得进去，否则上一条可能只是「什么都写不进去」。
      const okCode = await errcodeOf(() =>
        db.query("update public.style_config set detail_level='P2' where user_id=$1", [UID_A]));
      okCode === null
        ? ok("同一条件下写 P2 成功", "← 上一条拒的是 P1，不是「所有写入都失败」")
        : bad("连 P2 都写不进去", `errcode=${okCode}`);
      await db.close();
    }
  }

  // ============================================================ 反向
  console.log("\n\x1b[1m反向 · 拆掉防护后，对应断言必须变红\x1b[0m");

  const reversals = [
    {
      id: "R1",
      what: "关掉 RLS",
      sql: "alter table public.style_config disable row level security;",
      // ⚠️ 2026-09-10 起 `a_cannot_insert_as_b` **不在这张表里**了。
      // 迁移 20260910090000 收回了 insert 授权，所以关掉 RLS 之后插入仍然被
      // **权限层**拒绝——那条断言不会变红，而它不变红是**对的**，不是空转。
      // 把它留在这里会让 R1 红，然后有人「顺手修好」它（多半是弱化断言）。
      // 它现在的反向控制是 R8（把两层都加回来，插入必须成功）。
      mustFail: ["a_cannot_read_b", "a_cannot_update_b"],
      note: "insert 那条已改由权限层承担，反向控制见 R8",
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
    if (rev.note && rev.mustFail) console.log(`  \x1b[2m${rev.note}\x1b[0m`);
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

  // R7 · 触发器的 security definer 是承重的吗？
  //
  // 上面那条「无权限角色插 auth.users → 仍然建行」若在 security invoker 下**也绿**，
  // 说明它测的不是 security definer，而是本地实例恰好权限宽松。
  console.log("\n  \x1b[1mR7 · 触发器函数从 security definer 改成 security invoker\x1b[0m");
  {
    const db = await PGlite.create();
    await db.exec(SUPABASE_STUBS);
    // 与生产同一份迁移文本，只把那一个词换掉——分节标记若变了这里抛错，不静默跳过。
    const files = migrationFiles();
    let replaced = 0;
    for (const f of files) {
      const src = readFileSync(join(MIGRATIONS, f), "utf8");
      const weakened = src.replace(/security definer/gi, (m) => (replaced++, "security invoker"));
      await db.exec(weakened);
    }
    if (replaced === 0) throw new Error("迁移里找不到 security definer —— 这条反向验证已失效");
    await db.query("insert into auth.users (id, email) values ($1, 'a@t.local'), ($2, 'b@t.local')", [UID_A, UID_B]);

    const stillGreen = await CHECKS.trigger_inserts_for_unprivileged_role(db);
    stillGreen
      ? bad("trigger_inserts_for_unprivileged_role 应变红，却仍是绿的", "← 那条断言没测到 security definer")
      : ok("security invoker 下建行失败", `替换了 ${replaced} 处 ← security definer 是承重的`);
    await db.close();
  }

  // R8 · 「客户端插不进去」的**允许控制**（口径 F11）。
  //
  // 把 20260910090000 收走的两样东西都加回来（权限 + 一条宽松的 insert 策略），
  // 插入必须成功。不加这条，「插不进去」可能只是因为表根本不可写、或那一行已存在——
  // 与 F11 当初那个「A 读 B 返回空」在 B 无行时也成立，是同一个形态。
  console.log("\n  \x1b[1mR8 · 把 insert 授权与策略都加回来（允许控制）\x1b[0m");
  {
    const db = await freshDb();
    // 先腾空 A 那一行，否则插入会撞主键，测出来的又是主键不是权限。
    await db.exec("reset role;");
    await db.query("delete from public.style_config where user_id = $1", [UID_A]);
    await db.exec(`grant insert on public.style_config to authenticated;
                   create policy style_config_insert_own on public.style_config
                     for insert to authenticated with check (user_id = auth.uid());`);

    const err = await errorOf(() =>
      asUser(db, UID_A, () =>
        db.query("insert into public.style_config (user_id, f1_arm) values ($1, 'pairwise')", [UID_A])));
    const back = (await db.query("select 1 from public.style_config where user_id=$1", [UID_A])).rows.length;

    err === null && back === 1
      ? ok("加回权限与策略后，A 能插自己那一行", "← 上面的「插不进去」确实是被那两样东西拦的")
      : bad("加回来仍插不进去", `err=${err?.code} ${err?.message ?? ""}`);
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


  // ============================================================ style_events
  //
  // 阶段 4 的事件表。四组断言，各防一件事：
  //
  //   1. **RLS 与权限**：只能 insert 自己的，读/改/删一律不行；
  //   2. **payload 必需字段**（口径 F36）：jsonb 没有 schema，漏字段不会报错，
  //      所以每个 event_type 的必需字段各缺一次都要判红；
  //   3. **arm 不可伪造**：arm 由 user_id 确定（F08），客户端改不了自己的臂；
  //   4. **cascade**（口径 F14 / PRD §12.1「注销即清空」）：删账号 → 事件行也没了。
  //
  // 第 4 组是本阶段唯一会让 §12.1 在事件表上重新变成「一条没被测量过的承诺」的地方。
  console.log("\n\x1b[1mstyle_events · 埋点表（阶段 4）\x1b[0m");

  /** 五个事件各一份**齐全**的 payload。缺字段的用例从它们身上逐个删键造出来。 */
  const FULL_PAYLOADS = {
    style_pair_shown: { step: 1, axis: "detail_level", pair_id: "detail_p0_p3", left_variant: "P0" },
    style_pair_chosen: {
      step: 1, axis: "detail_level", pair_id: "detail_p0_p3",
      left_variant: "P0", chosen_variant: "P3", dwell_ms: 1234, hidden_ms: 0,
    },
    style_onboarding_completed: {
      config: { detail_level: "P0", use_examples: "more", structure: "conclusion_first", depth: "why" },
      steps_completed: 6, total_ms: 42000, prompt_template_version: "b1",
    },
    style_text_mapped: {
      call: { model: "claude-sonnet-5", effort: "low", thinking: "disabled", max_tokens: 1500 },
      axes_from_text: ["detail_level"], axes_from_default: ["depth"], ok: true,
      prompt_template_version: "b1",
    },
    style_config_changed: {
      before: { detail_level: "P0" }, after: { detail_level: "P2" },
      trigger: "feedback:too_verbose", config_changed: true, counts_as_config_edit: true,
      prompt_template_version: "b1",
    },
  };

  const SESSION = "99999999-9999-4999-8999-999999999999";

  /** 以某个用户的身份写一条事件。返回错误对象（成功则 null）。 */
  async function putEvent(db, uid, { type, arm, payload, asUid = uid }) {
    const realArm = arm ?? (await db.query("select public.f1_assign_arm($1) a", [uid])).rows[0].a;
    return await errorOf(() =>
      asUser(db, asUid, () =>
        db.query(
          `insert into public.style_events (user_id, event_type, arm, session_id, payload)
           values ($1, $2, $3, $4, $5::jsonb)`,
          [uid, type, realArm, SESSION, JSON.stringify(payload)],
        )));
  }

  {
    const db = await freshDb();

    // ---- 允许控制先跑：五个事件都写得进去。
    // 少了它，下面所有「被拒」在「整张表根本写不进去」的情况下也全绿。
    let allOk = true;
    let firstErr = "";
    for (const [type, payload] of Object.entries(FULL_PAYLOADS)) {
      const err = await putEvent(db, UID_A, { type, payload });
      if (err) { allOk = false; firstErr = `${type}: ${err.code} ${err.message}`; }
    }
    const n = (await db.query("select count(*)::int c from public.style_events")).rows[0].c;
    allOk && n === 5
      ? ok("五个事件都能写进去", `${n} 行 ← 下面每一条「被拒」的允许控制`)
      : bad("齐全的事件写不进去", firstErr || `行数 ${n}`);

    // ---- payload 必需字段：每个 event_type 的每个必需字段，各缺一次都要判红
    for (const [type, payload] of Object.entries(FULL_PAYLOADS)) {
      const keys = Object.keys(payload);
      const missed = [];
      for (const k of keys) {
        const partial = { ...payload };
        delete partial[k];
        const err = await putEvent(db, UID_A, { type, payload: partial });
        if (err?.code !== "23514") missed.push(`${k}(${err?.code ?? "未报错"})`);
      }
      missed.length === 0
        ? ok(`${type} 缺任一必需字段都判红`, `${keys.length}/${keys.length} 个字段`)
        : bad(`${type} 有字段缺了也放行`, missed.join(" "));
    }

    // 键在、值是 null / 空字符串，同样算缺——这两种是前端拼错时最常见的形态，
    // 而它们在 jsonb 里与「字段有值」长得完全不一样，却对分析同样无用。
    const nullErr = await putEvent(db, UID_A, {
      type: "style_pair_shown",
      payload: { ...FULL_PAYLOADS.style_pair_shown, left_variant: null },
    });
    nullErr?.code === "23514"
      ? ok("left_variant 是 JSON null 也判红", "← 前端拿到 undefined 序列化成 null 的常见形态")
      : bad("JSON null 被放行", String(nullErr?.code));

    const emptyErr = await putEvent(db, UID_A, {
      type: "style_pair_shown",
      payload: { ...FULL_PAYLOADS.style_pair_shown, left_variant: "" },
    });
    emptyErr?.code === "23514"
      ? ok("left_variant 是空字符串也判红", "← 变量名拼错时最常见的形态")
      : bad("空字符串被放行", String(emptyErr?.code));

    // ---- 未知 event_type。取值域与必需字段表是**同一处出处**，不另立 CHECK。
    const unknownErr = await putEvent(db, UID_A, { type: "style_bogus", payload: { a: 1 } });
    unknownErr?.code === "23514"
      ? ok("未知 event_type 被拒", "← 取值域由 f1_event_required_keys 一处给出")
      : bad("未知 event_type 被放行", String(unknownErr?.code));

    // ---- arm 不可伪造。A5 的三个预登记指标全部按臂分组，
    // 谁都能往事件里写 arm 的话，那三个读数当场被搅浑且没有任何东西会红。
    const realArm = (await db.query("select public.f1_assign_arm($1) a", [UID_A])).rows[0].a;
    const forgedErr = await putEvent(db, UID_A, {
      type: "style_pair_shown",
      arm: realArm === "pairwise" ? "text" : "pairwise",
      payload: FULL_PAYLOADS.style_pair_shown,
    });
    forgedErr?.code === "23514"
      ? ok("伪造 arm 被拒", `${realArm} 之外的值写不进去 ← arm = f1_assign_arm(user_id)`)
      : bad("伪造 arm 被放行", String(forgedErr?.code));

    // ---- 冒名：A 写 user_id = B 的事件。挡它的是 RLS 的 with check（insert 有授权）。
    // 与 style_config 那条不同层，所以错误文本也不同——**两条都断文本**，
    // 免得哪天层换了而断言静默保持绿色。
    const impersonateErr = await errorOf(() =>
      asUser(db, UID_A, () =>
        db.query(
          `insert into public.style_events (user_id, event_type, arm, session_id, payload)
           values ($1, 'style_pair_shown', public.f1_assign_arm($1), $2, $3::jsonb)`,
          [UID_B, SESSION, JSON.stringify(FULL_PAYLOADS.style_pair_shown)],
        )));
    impersonateErr?.code === "42501" && /row-level security/i.test(impersonateErr?.message ?? "")
      ? ok("A 写不了 B 的事件", "42501 且拦它的是 RLS 层（insert 有授权，策略承重）")
      : bad("冒名写入没被 RLS 拦", `${impersonateErr?.code} ${impersonateErr?.message ?? "未报错"}`);

    // ---- 读 / 改 / 删：三个动作**权限层就没给**，不是靠策略挡的。
    for (const [what, sql] of [
      ["select", "select * from public.style_events"],
      ["update", "update public.style_events set payload = '{}'::jsonb"],
      ["delete", "delete from public.style_events"],
    ]) {
      const err = await errorOf(() => asUser(db, UID_A, () => db.query(sql)));
      err?.code === "42501" && /permission denied/i.test(err?.message ?? "")
        ? ok(`用户 ${what} 事件被拒`, "42501 权限层 ← 读事件是分析行为，走 service_role")
        : bad(`用户 ${what} 事件没被拒`, `${err?.code} ${err?.message ?? "未报错"}`);
    }

    await db.close();
  }

  // ---- cascade（口径 F14 / PRD §12.1）
  //
  // 事件表同样**刻意不给 delete 策略**，清空完全依赖 auth.users 的 on delete cascade。
  // 少了这条断言，「注销即清空」在事件表上又变成一条没被测量过的承诺。
  console.log("\n  \x1b[1m注销 · style_events 的 on delete cascade\x1b[0m");
  {
    const db = await freshDb();
    await putEvent(db, UID_A, { type: "style_pair_shown", payload: FULL_PAYLOADS.style_pair_shown });
    await putEvent(db, UID_A, { type: "style_pair_chosen", payload: FULL_PAYLOADS.style_pair_chosen });
    await putEvent(db, UID_B, { type: "style_pair_shown", payload: FULL_PAYLOADS.style_pair_shown });

    const before = (await db.query("select count(*)::int c from public.style_events")).rows[0].c;
    // 前提先验一遍：A 真的有事件。没有的话下面的「删完就没了」是空转的
    // （与「A 读 B 返回空」在 B 无行时也成立同形，通则七 #5）。
    const aBefore = (await db.query("select count(*)::int c from public.style_events where user_id=$1", [UID_A])).rows[0].c;
    aBefore === 2 ? ok("删之前 A 有 2 条事件", `总共 ${before} 条 ← 下一条的前提`) : bad("前提不成立", `A 有 ${aBefore} 条`);

    await db.query("delete from auth.users where id = $1", [UID_A]);
    const aAfter = (await db.query("select count(*)::int c from public.style_events where user_id=$1", [UID_A])).rows[0].c;
    const bAfter = (await db.query("select count(*)::int c from public.style_events where user_id=$1", [UID_B])).rows[0].c;

    aAfter === 0
      ? ok("删 auth.users 的行 → 该用户的事件随之消失", `${aBefore} → 0`)
      : bad("cascade 没有触发", `仍有 ${aAfter} 条`);
    bAfter === 1 ? ok("只清掉被注销的那个用户", "B 的事件仍在") : bad("误删了别的用户的事件", `B 剩 ${bAfter} 条`);
    await db.close();
  }

  // ---- R9 · cascade 的反向验证
  console.log("\n  \x1b[1mR9 · style_events 的外键从 cascade 改成 no action\x1b[0m");
  {
    const db = await freshDb();
    await db.exec(`alter table public.style_events
                     drop constraint style_events_user_id_fkey,
                     add constraint style_events_user_id_fkey
                       foreign key (user_id) references auth.users (id);`);
    await putEvent(db, UID_A, { type: "style_pair_shown", payload: FULL_PAYLOADS.style_pair_shown });
    const code = await errcodeOf(() => db.query("delete from auth.users where id=$1", [UID_A]));
    const still = (await db.query("select count(*)::int c from public.style_events where user_id=$1", [UID_A])).rows[0].c;
    code !== null || still > 0
      ? ok("无 cascade 时注销清不掉事件", `errcode=${code} 残留 ${still} 条 ← cascade 是承重的`)
      : bad("去掉 cascade 后事件仍被清掉", "← 说明清空的不是 cascade，上面那条断言测错了对象");
    await db.close();
  }

  // ---- R10 · payload 护栏的反向验证
  //
  // 「缺字段被拒」若在**没有那条 CHECK** 的情况下也绿，说明拒它的是别的东西。
  console.log("\n  \x1b[1mR10 · 去掉 payload 必需字段的 CHECK\x1b[0m");
  {
    const db = await freshDb();
    await db.exec("alter table public.style_events drop constraint style_events_payload_check;");
    const err = await putEvent(db, UID_A, {
      type: "style_pair_shown",
      payload: { step: 1, axis: "detail_level", pair_id: "detail_p0_p3" }, // 缺 left_variant
    });
    err === null
      ? ok("去掉 CHECK 后缺 left_variant 的事件写得进去", "← 拒它的确实是那条 CHECK，不是别的东西")
      : bad("去掉 CHECK 后仍被拒", `${err.code} ${err.message} ← 上面那组断言测错了对象`);
    await db.close();
  }

  // ---- R11 · 「读不到」的允许控制
  //
  // 把授权与策略都加回来，读必须成功。不加这条，「用户读不到事件」可能只是因为
  // 表里根本没有行、或者整表不可用——与 F11 当初那个「A 读 B 返回空」同形。
  console.log("\n  \x1b[1mR11 · 把 select 授权与策略加回来（允许控制）\x1b[0m");
  {
    const db = await freshDb();
    await putEvent(db, UID_A, { type: "style_pair_shown", payload: FULL_PAYLOADS.style_pair_shown });
    await db.exec(`grant select on public.style_events to authenticated;
                   create policy style_events_select_own on public.style_events
                     for select to authenticated using (user_id = auth.uid());`);
    const rowsA = await asUser(db, UID_A, async () =>
      (await db.query("select count(*)::int c from public.style_events")).rows[0].c);
    const rowsB = await asUser(db, UID_B, async () =>
      (await db.query("select count(*)::int c from public.style_events")).rows[0].c);
    rowsA === 1 && rowsB === 0
      ? ok("加回授权与策略后 A 读得到自己的 1 条、B 读到 0 条", "← 上面的「读不到」确实是被那两样东西拦的")
      : bad("加回来仍读不到", `A ${rowsA} 条 / B ${rowsB} 条`);
    await db.close();
  }


  // ============================================================ F2 语料
  //
  // courses / documents / slices。**不含向量**——那部分本地验不了，见文件头。
  //
  // 五组：①允许控制先立 ②跨用户 ③owner 一致性（RLS 挡不住的那一类）
  // ④唯一索引的两面 ⑤cascade 两级 + 控制项。
  console.log("\n\x1b[1mF2 语料 · courses / documents / slices\x1b[0m");

  const VERSIONS = {
    parse_schema_version: "1.0",
    slice_schema_version: "1.0",
    slicer_version: "0.1.0",
    slice_ruleset_version: "slice-1.0",
  };

  /** 以 uid 的身份建一门课，返回 course_id。 */
  async function mkCourse(db, uid, name) {
    return await asUser(db, uid, async () => {
      const r = await db.query(
        "insert into public.courses (owner, name) values ($1, $2) returning id",
        [uid, name],
      );
      return r.rows[0].id;
    });
  }

  /** 以 uid 的身份往 courseId 里放一份文档。`asUid` 用于冒名尝试。 */
  async function mkDoc(db, uid, courseId, sha, { owner = uid, asUid = uid, split = null } = {}) {
    return await errorOf(() =>
      asUser(db, asUid, () =>
        db.query(
          `insert into public.documents
             (owner, course_id, filename, source_format, content_sha256,
              page_count, usable_page_count, verdict, parser,
              parse_schema_version, slice_schema_version, slicer_version,
              slice_ruleset_version, split_threshold_chars)
           values ($1,$2,'lec.pdf','pdf',$3, 10, 9, 'usable', '{"service_version":"0.1.0"}'::jsonb,
                   $4,$5,$6,$7,$8)`,
          [owner, courseId, sha, VERSIONS.parse_schema_version, VERSIONS.slice_schema_version,
           VERSIONS.slicer_version, VERSIONS.slice_ruleset_version, split],
        )));
  }

  async function docId(db, courseId, sha) {
    const r = await db.query(
      "select id from public.documents where course_id=$1 and content_sha256=$2", [courseId, sha]);
    return r.rows[0]?.id ?? null;
  }

  async function mkSlice(db, uid, documentId, sliceId, { owner = uid } = {}) {
    return await errorOf(() =>
      asUser(db, uid, () =>
        db.query(
          `insert into public.slices
             (document_id, slice_id, owner, ordinal, page_indices, text, char_count, units)
           values ($1,$2,$3,1,'{1}','片段正文',5,'[]'::jsonb)`,
          [documentId, sliceId, owner],
        )));
  }

  {
    const db = await freshDb();

    // ---- ① 允许控制先立。没有它，下面所有「被拒」都不携带信息。
    const courseA = await mkCourse(db, UID_A, "22.081J");
    const errDoc = await mkDoc(db, UID_A, courseA, "sha-aaa");
    const dA = await docId(db, courseA, "sha-aaa");
    const errSlice = await mkSlice(db, UID_A, dA, "s001");
    errDoc === null && dA !== null && errSlice === null
      ? ok("A 能建课、放文档、写切片", `course ${String(courseA).slice(0, 8)} → doc ${String(dA).slice(0, 8)}`)
      : bad("正常路径就走不通", `doc=${errDoc?.message} slice=${errSlice?.message}`);

    // ---- ② 跨用户：B 看不见 A 的任何东西
    const seen = await asUser(db, UID_B, async () => ({
      courses: (await db.query("select count(*)::int c from public.courses")).rows[0].c,
      docs: (await db.query("select count(*)::int c from public.documents")).rows[0].c,
      slices: (await db.query("select count(*)::int c from public.slices")).rows[0].c,
    }));
    seen.courses === 0 && seen.docs === 0 && seen.slices === 0
      ? ok("B 读不到 A 的课/文档/切片", "三张表都是 0 行")
      : bad("跨用户读到了东西", JSON.stringify(seen));

    const selfSeen = await asUser(db, UID_A, async () =>
      (await db.query("select count(*)::int c from public.courses")).rows[0].c);
    selfSeen === 1
      ? ok("而 A 读得到自己的", "← 上一条的允许控制：否则可能整表不可读")
      : bad("A 读不到自己的课", `${selfSeen} 行`);

    // ---- ③ owner 一致性：**RLS 挡不住的那一类**
    //
    // documents 的 with check 只问「这一行的 owner 是不是我」，
    // 不问「我引用的那门课是不是我的」。所以 B 可以把自己的文档挂进 A 的课——
    // 而那样 A 一检索就会召回 B 的内容。靠触发器补。
    const courseB = await mkCourse(db, UID_B, "14.129");
    const cross = await mkDoc(db, UID_B, courseA, "sha-bbb");   // B 的文档 → A 的课
    cross?.code === "42501"
      ? ok("B 把文档挂进 A 的课 → 被拒", "42501 ← RLS 挡不住这个，靠 owner 一致性触发器")
      : bad("跨 owner 挂课没被拦", `${cross?.code} ${cross?.message ?? "未报错"}`);

    const okOwn = await mkDoc(db, UID_B, courseB, "sha-bbb");
    okOwn === null
      ? ok("而 B 挂进自己的课成功", "← 上一条的允许控制")
      : bad("B 挂自己的课也失败", okOwn.message);

    // 切片同理：切片的 owner 必须等于它所属文档的 owner。
    const crossSlice = await mkSlice(db, UID_B, dA, "s001");
    crossSlice !== null
      ? ok("B 往 A 的文档里写切片 → 被拒", `${crossSlice.code}`)
      : bad("跨 owner 写切片没被拦");

    // ---- ④ 唯一索引的两面（2026-09-11 拍板：带 course_id）
    const dup = await mkDoc(db, UID_A, courseA, "sha-aaa");     // 同课同 sha
    dup?.code === "23505"
      ? ok("同一课里传同一份文件 → 23505", "⚠️ 这是**硬失败不是提示**，摄入路径必须翻译它")
      : bad("同课重复上传没被拦", `${dup?.code}`);

    const courseA2 = await mkCourse(db, UID_A, "6.006");
    const reuse = await mkDoc(db, UID_A, courseA2, "sha-aaa");  // 换一门课，同 sha
    reuse === null
      ? ok("同一份文件挂到另一门课 → 成功", "← 唯一索引带 course_id 的全部意义（跨学期复用）")
      : bad("跨课复用被拦了", `${reuse?.code} ${reuse?.message}`);

    // ---- 版本列：少一个就写不进去
    const missingVersion = await errorOf(() =>
      asUser(db, UID_A, () =>
        db.query(
          `insert into public.documents
             (owner, course_id, filename, source_format, content_sha256, page_count,
              usable_page_count, verdict, parser, parse_schema_version,
              slice_schema_version, slicer_version)
           values ($1,$2,'x.pdf','pdf','sha-zzz',1,1,'usable','{}'::jsonb,'1.0','1.0','0.1.0')`,
          [UID_A, courseA])));
    missingVersion?.code === "23502"
      ? ok("缺 slice_ruleset_version → 23502", "← 口径版本列是 not null，漏了写不进去")
      : bad("缺版本列却写进去了", `${missingVersion?.code}`);

    // split_threshold_chars 允许 NULL —— **NULL 有含义**（= 不拆），不是「忘了填」。
    const nullSplit = await db.query(
      "select split_threshold_chars from public.documents where content_sha256='sha-aaa' limit 1");
    nullSplit.rows[0].split_threshold_chars === null
      ? ok("split_threshold_chars 可为 NULL", "← NULL = 不拆，是取值不是缺失")
      : bad("split_threshold_chars 不该有默认值");

    // slices 没给 update 授权：切片是解析的产物，改它会让库与文档不一致且无人发现。
    const updSlice = await errorOf(() =>
      asUser(db, UID_A, () => db.query("update public.slices set text='改了' where slice_id='s001'")));
    updSlice?.code === "42501"
      ? ok("用户改不了切片", "42501 权限层 ← 要变就重切")
      : bad("切片可被改写", `${updSlice?.code}`);

    await db.close();
  }

  // ---- ⑤ cascade 两级 + 控制项
  console.log("\n  \x1b[1m注销与删课 · cascade 两级\x1b[0m");
  {
    const db = await freshDb();
    const cA = await mkCourse(db, UID_A, "22.081J");
    const cA2 = await mkCourse(db, UID_A, "6.006");
    const cB = await mkCourse(db, UID_B, "14.129");
    await mkDoc(db, UID_A, cA, "s1");
    await mkDoc(db, UID_A, cA2, "s2");
    await mkDoc(db, UID_B, cB, "s3");
    await mkSlice(db, UID_A, await docId(db, cA, "s1"), "s001");
    await mkSlice(db, UID_A, await docId(db, cA2, "s2"), "s001");
    await mkSlice(db, UID_B, await docId(db, cB, "s3"), "s001");

    const count = async (t, where = "") =>
      (await db.query(`select count(*)::int c from public.${t} ${where}`)).rows[0].c;

    (await count("slices")) === 3
      ? ok("前提：三份文档各一个切片", "← 少了它，下面的「删完就没了」是空转的")
      : bad("前提不成立", `${await count("slices")} 个切片`);

    // 删一门课 → 该课的文档与切片没了，**别的课不受影响**
    await db.exec("reset role;");
    await db.query("delete from public.courses where id = $1", [cA]);
    const afterCourse = {
      docs: await count("documents"),
      slices: await count("slices"),
      aOther: await count("documents", `where course_id = '${cA2}'`),
    };
    afterCourse.docs === 2 && afterCourse.slices === 2 && afterCourse.aOther === 1
      ? ok("删一门课 → 它的文档与切片一起没了", `文档 3→2，切片 3→2`)
      : bad("删课的 cascade 不对", JSON.stringify(afterCourse));

    // 注销 → 该用户的一切没了，**别人的不受影响**
    await db.query("delete from auth.users where id = $1", [UID_A]);
    const afterUser = {
      aCourses: await count("courses", `where owner = '${UID_A}'`),
      aDocs: await count("documents", `where owner = '${UID_A}'`),
      aSlices: await count("slices", `where owner = '${UID_A}'`),
      bDocs: await count("documents", `where owner = '${UID_B}'`),
      bSlices: await count("slices", `where owner = '${UID_B}'`),
    };
    afterUser.aCourses === 0 && afterUser.aDocs === 0 && afterUser.aSlices === 0
      ? ok("注销 → 该用户的课/文档/切片全部消失", "PRD §12.1「注销即清空」")
      : bad("注销没清干净", JSON.stringify(afterUser));
    afterUser.bDocs === 1 && afterUser.bSlices === 1
      ? ok("而 B 的一条没动", "← 控制项：少了它，「整表被清空」也会让上一条绿")
      : bad("误删了别的用户", JSON.stringify(afterUser));

    await db.close();
  }

  // ---- R12 · owner 一致性触发器是承重的吗
  console.log("\n  \x1b[1mR12 · 去掉 documents 的 owner 一致性触发器\x1b[0m");
  {
    const db = await freshDb();
    const cA = await mkCourse(db, UID_A, "22.081J");
    await db.exec("drop trigger documents_course_owner_check on public.documents;");
    const cross = await mkDoc(db, UID_B, cA, "sha-x");
    cross === null
      ? ok("去掉触发器后，B 真的能把文档挂进 A 的课", "← 那条断言确实是它在挡，不是 RLS")
      : bad("去掉之后仍被拦", `${cross.code} ${cross.message} ← 上面那条测错了对象`);
    await db.close();
  }

  // ---- R13 · 唯一索引带 course_id 是承重的吗
  console.log("\n  \x1b[1mR13 · 把唯一索引换回 (owner, sha)\x1b[0m");
  {
    const db = await freshDb();
    await db.exec(`drop index public.documents_owner_course_sha_idx;
                   create unique index documents_owner_sha_idx
                     on public.documents (owner, content_sha256);`);
    const cA = await mkCourse(db, UID_A, "22.081J");
    const cA2 = await mkCourse(db, UID_A, "6.006");
    await mkDoc(db, UID_A, cA, "sha-same");
    const reuse = await mkDoc(db, UID_A, cA2, "sha-same");
    reuse?.code === "23505"
      ? ok("换回旧索引后，跨课复用同一份文件被拦", "← 带 course_id 确实在承重，不是装饰")
      : bad("换回旧索引也没拦住", `${reuse?.code} ← 上面那条「跨课复用成功」测错了对象`);
    await db.close();
  }

  console.log(`\n\x1b[1m本地\x1b[0m  绿 ${passed} · 红 ${failed}`);
  console.log(
    `\x1b[1m待真库验证\x1b[0m  ${NOT_VERIFIABLE_LOCALLY.join(", ")} ` +
      `的全部行为（slice_embeddings 的 RLS/cascade、match_slices 的作用域与排序语义、` +
      `vector(1024) 经 JSON 传参）\n` +
      `  \x1b[2m两行分开报，不合计——两种证据不得互相顶替（同 F34）。\x1b[0m\n`,
  );
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(1); });
