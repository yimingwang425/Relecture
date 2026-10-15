// 词法一路的本地执行器：真的 Postgres（PGlite），跑与生产同一句 SQL。仅评测用，不进生产。
//
// 为什么不在 Python 里仿一个：生产的排序是 `ts_rank_cd`（覆盖密度），不是 BM25，也不是词频——
// 手写一个「差不多」的打分，测出来的就不是生产那一路了。PGlite 是编译到 WASM 的 Postgres，
// `to_tsvector('simple', …)` / `to_tsquery` / `ts_rank_cd` 与线上逐字相同。
//
// 与生产 `match_slices_lexical`（迁移 20261007100000）的差别只有两处，都不影响排序：
//   · 没有 RLS 与 owner 条件——本地只有一个人的一门课；
//   · 没有 `ruleset = d.index_ruleset` 的连接——本地索引只有一种口径（由调用方保证）。
//
// 协议：stdin 一行一个 JSON，stdout 一行一个 JSON。
//   {"op":"load","rows":[{"document_id","slice_id","lexical_text"}]}  → {"ok":true,"n":…}
//   {"op":"query","query":"'a' | 'b'","limit":20}                      → {"rows":[{"document_id","slice_id","rank"}]}
import { PGlite } from "@electric-sql/pglite";
import { createInterface } from "node:readline";

const db = new PGlite();
await db.exec(`
  create table slice_lexicon (
    document_id  uuid not null,
    slice_id     text not null,
    lexical_text text not null,
    lexical      tsvector generated always as (to_tsvector('simple', lexical_text)) stored,
    primary key (document_id, slice_id)
  );
  create index on slice_lexicon using gin (lexical);
`);

const SQL = `
  select l.document_id::text as document_id, l.slice_id, ts_rank_cd(l.lexical, q) as rank
  from slice_lexicon l
  cross join to_tsquery('simple', $1) q
  where l.lexical @@ q
  order by rank desc, l.document_id, l.slice_id
  limit $2`;

const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  if (!line.trim()) continue;
  let out;
  try {
    const msg = JSON.parse(line);
    if (msg.op === "load") {
      await db.exec("delete from slice_lexicon");
      for (const r of msg.rows) {
        await db.query("insert into slice_lexicon (document_id, slice_id, lexical_text) values ($1, $2, $3)",
          [r.document_id, r.slice_id, r.lexical_text]);
      }
      out = { ok: true, n: msg.rows.length };
    } else if (msg.op === "query") {
      const res = await db.query(SQL, [msg.query, msg.limit ?? 20]);
      out = { rows: res.rows };
    } else {
      out = { error: `unknown op ${msg.op}` };
    }
  } catch (e) {
    out = { error: String(e && e.message ? e.message : e) };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
}
// stdin 关了就退出。PGlite 会让事件循环一直挂着，不显式退出的话调用方 wait() 会超时。
await db.close();
process.exit(0);
