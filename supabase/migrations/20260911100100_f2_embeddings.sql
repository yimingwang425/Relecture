-- F2 · 向量：slice_embeddings + match_slices
-- 日期：2026-09-11　口径见 f1_samples/README.md 的 F47
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⚠️ **本文件的一切在本地都验不了。** 它要 `create extension vector`，
--    而 `verify_rls_local.mjs` 跑的 PGlite 里没有 vector 扩展
--    （实测 0.5.8 的 contrib 有 34 个扩展，没有它）。
--
--    所以 `verify_rls_local.mjs` **显式跳过本文件**，并且是**吵着跳过**的：
--    它会打印一条「本文件未被本地验证」，且断言本文件确实存在——
--    改名之后这个跳过不会静默变成「没这回事」。
--
--    本文件的验证走 `supabase/verify_corpus_prod.py`，对**真库**跑。
--    本地绿数与待真库验证的条数**分两行报，不报合计**——
--    两种证据不得互相顶替（同 F34 分开记「机制」与「存量」）。
--
--    刻意**不**给本地造一个假的 `<=>`：假的排序语义会让召回类断言全部失去意义，
--    而那正是 `InMemoryRetriever` 文档里写着「不得据此出召回结论」的同一条。
-- ═══════════════════════════════════════════════════════════════════════════

create extension if not exists vector;

-- 与 slices 分表：口径版本一变就要重算向量，而切片本身不变。
-- 分开后重建向量不必动 slices，也不会把 embedding_text 的口径混进切片契约。
create table if not exists public.slice_embeddings (
    document_id   uuid not null,
    slice_id      text not null,
    owner         uuid not null references auth.users (id) on delete cascade,
    model         text not null,          -- 如 'text-embedding-3-large@1024'
    embed_ruleset text not null,          -- EMBED_TEXT_RULESET_VERSION
    -- ⚠️ 维度与选型强耦合，建表时定死，改它要迁移已有数据。
    -- 1024 = `retrieval.selection.SELECTED_DIMENSIONS`（显式截断，不是 large 的默认 3072）。
    -- 改选型必须同步改三处：本列、下面 match_slices 的 p_query 参数、以及
    -- `retrieval/selection.py`。`retrieval/tests/test_selection.py` 在不一致时会红。
    embedding     vector(1024) not null,
    created_at    timestamptz not null default now(),
    primary key (document_id, slice_id, model, embed_ruleset),
    foreign key (document_id, slice_id)
        references public.slices (document_id, slice_id) on delete cascade
);

-- ── ANN 索引：**v1 不建**（口径 F47）───────────────────────────────────────
--
-- 理由不是性能，是**观测范围与应用范围错配**（F23 的形态）：
-- `retrieval/README.md` §七 的全部选型证据都是在**精确余弦**上取得的
-- （`retrieval/index.py` 的全表扫描 + 确定性排序）。HNSW 是近似最近邻，有召回损失；
-- 建上它，生产检索就不再是那批数字描述的那个对象。
--
-- 而 v1 不需要：实测每门课 15–46 个切片。
--
-- ⚠️ **重新评估的触发线：单门课的 slice_embeddings 行数超过 5,000。**
--   这是当前实测规模的 100 倍以上（46 → 5,000），**是规模标记，不是量出来的延迟阈值**。
-- 触发后第一件事不是建索引，是先量 ANN 与精确扫描的 top-5 一致率。
--
-- create index if not exists slice_embeddings_hnsw_idx
--     on public.slice_embeddings using hnsw (embedding vector_cosine_ops);

alter table public.slice_embeddings enable row level security;
revoke all on public.slice_embeddings from anon, authenticated;
grant select, insert, delete on public.slice_embeddings to authenticated;
-- 不给 update：向量是切片 + 口径版本的函数，改它等于让向量与它声称的口径不符。
-- 要变就换一行（主键里带 model 与 embed_ruleset，新口径天然是新行）。

drop policy if exists slice_embeddings_owner on public.slice_embeddings;
create policy slice_embeddings_owner on public.slice_embeddings
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- ---------------------------------------------------------------- 检索函数

-- **作用域是一门课，不是一份文档**（口径 F47）。
--
-- ⚠️ **返回里必须有 document_id。** `slice_id` 是**文档内局部 id**（如 's003'），
-- `slices` 的主键是 `(document_id, slice_id)`——一门课只有一份讲义时
-- 只返回 slice_id 不咬人，有两份时 's003' 就有两个。而「一门课多份讲义」
-- 正是本口径要支持的常态，所以上一版的返回形状**在它的目标场景下第一天就是坏的**。
-- 同源确认：`retrieval` 侧的 `IndexedSlice` 用的键一直是 `(source_file, slice_id)` 两段。
--
-- `course_id` 走 join 取，不在 slices/slice_embeddings 冗余一列（见 corpus 那个文件）。
--
-- owner 过滤写在函数里而不只依赖 RLS，是**双保险**：security invoker + RLS 已经够，
-- 但这一条让「忘记开 RLS」不至于直接变成越权。
create or replace function public.match_slices(
    p_course_id uuid,
    p_query     vector(1024),
    p_model     text,
    p_ruleset   text,
    p_limit     int default 5
)
returns table (document_id uuid, slice_id text, similarity float)
language sql stable security invoker
as $$
    select e.document_id, e.slice_id, 1 - (e.embedding <=> p_query) as similarity
    from public.slice_embeddings e
    join public.documents d on d.id = e.document_id
    where d.course_id      = p_course_id
      and d.owner          = auth.uid()
      and e.owner          = auth.uid()
      and e.model          = p_model
      and e.embed_ruleset  = p_ruleset
    order by e.embedding <=> p_query
    limit p_limit;
$$;

comment on function public.match_slices(uuid, vector, text, text, int) is
  'F2 检索。作用域是**一门课**（口径 F47），返回 (document_id, slice_id, similarity)——'
  'slice_id 是文档内局部 id，跨文档不唯一，所以两段都要返回。'
  '⚠️ 经 PostgREST RPC 调用时 p_query 的传参形状需对真库确认一次，'
  '见 supabase/verify_corpus_prod.py 的第 1 步。';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：待填　执行结果：待填
--     vector(1024) 经 JSON 传参是否可行：**待填**（verify_corpus_prod.py 第 1 步）
--
-- ⚠️ 本文件的所有行为在本地一条都没验过（见文件头）。执行后请跑
-- `python3 supabase/verify_corpus_prod.py`，它的第 1 步就是那一发 RPC。
