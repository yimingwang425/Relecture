-- AI 课件讲解助手 · F2 2b · Supabase pgvector 表结构与 RLS
--
-- 本次交付**只出 SQL，不接线上库**：schema 定错了以后要迁数据，所以现在定；
-- 接线属 F3。评测走内存精确余弦（见 retrieval/index.py），
-- 理由是 ANN 索引会引入召回损失，把「模型好不好」与「近似索引损失多少」混在一起
-- 就归因不了。
--
-- 三条与 PRD §12.1 直接对应的约束：
--   1. 文档主键是随机 uuid，**不是内容哈希**。按内容哈希做主键等于两个用户上传
--      同一份课件时共用一份存储，违反「不跨用户共享、不建公共库」。
--      content_sha256 只作完整性校验与**同一用户内**的重复上传提示。
--   2. RLS 按 owner 隔离，且**没有任何跨用户查询路径**。
--   3. ON DELETE CASCADE：注销即清空、一键删除必须一次删干净，
--      跨两个存储系统实现会有两处漏点，所以全部放在这里。
--
-- ⚠️ 维度与选型强耦合：vector(n) 的 n 必须在建表时定死，改它要迁移已有数据。
--
-- 当前值 1024 = 选型结论 text-embedding-3-large @ 1024（retrieval/README.md §七）。
-- 该维度是显式截断（Matryoshka），不是模型默认值——large 的默认是 3072。
--
-- 若日后改选型，**这三处必须同步改**，否则接线时直接报维度不匹配：
--   ① slice_embeddings.embedding 的 vector(n)
--   ② match_slices 的 p_query 参数
--   ③ hnsw 索引（n > 2000 时该索引建不出来，须整段删掉并接受精确扫描）
--
-- 选 1024 而非 large 默认的 3072，理由是工程约束不是召回：3072 超过 pgvector
-- 的 ANN 索引上限 2000 维。两者在本次评测上无法区分（差异只出现在单格，
-- 按 README R17 不构成证据），故在无法区分的选项间选保留 ANN 能力的那个。

create extension if not exists vector;

-- ── 文档 ────────────────────────────────────────────────────────────────────
create table if not exists public.documents (
    id                uuid primary key default gen_random_uuid(),
    owner             uuid not null references auth.users (id) on delete cascade,
    filename          text not null,
    source_format     text not null check (source_format in ('pdf', 'pptx')),
    content_sha256    text not null,
    page_count        int  not null,
    usable_page_count int  not null,
    unusable_pages    jsonb not null default '[]'::jsonb,
    verdict           text not null check (verdict in ('usable', 'unusable')),
    parser            jsonb not null,
    created_at        timestamptz not null default now()
);

-- 同一用户内的重复上传提示。**刻意不是全局唯一约束**——
-- 全局唯一会让第二个用户上传同一份课件时失败，那正是跨用户耦合。
create unique index if not exists documents_owner_sha_idx
    on public.documents (owner, content_sha256);

-- ── 切片 ────────────────────────────────────────────────────────────────────
create table if not exists public.slices (
    document_id   uuid not null references public.documents (id) on delete cascade,
    slice_id      text not null,                 -- 文档内局部 id，如 's003'
    owner         uuid not null references auth.users (id) on delete cascade,
    ordinal       int  not null,
    page_indices  int[] not null,                -- 原始页号；跳过的页表现为缺号
    title         text,
    text          text not null,                 -- slice.text：溯源与引用比对用
    char_count    int  not null,
    units         jsonb not null,                -- 块级 bbox / font / image_adjacency
    images        jsonb not null default '[]'::jsonb,
    page_signals  jsonb not null default '[]'::jsonb,
    prev_slice_id text,
    next_slice_id text,
    primary key (document_id, slice_id)
);

-- ── 向量 ────────────────────────────────────────────────────────────────────
-- 与 slices 分表：口径版本一变就要重算向量，而切片本身不变。
-- 分开后重建索引不必动 slices，也不会把 embedding_text 的口径混进切片契约。
create table if not exists public.slice_embeddings (
    document_id     uuid not null,
    slice_id        text not null,
    owner           uuid not null references auth.users (id) on delete cascade,
    model           text not null,               -- 如 'text-embedding-3-large@1024'
    embed_ruleset   text not null,               -- EMBED_TEXT_RULESET_VERSION
    embedding       vector(1024) not null,       -- 见文件头的维度说明
    created_at      timestamptz not null default now(),
    primary key (document_id, slice_id, model, embed_ruleset),
    foreign key (document_id, slice_id)
        references public.slices (document_id, slice_id) on delete cascade
);

-- ── ANN 索引：**v1 不建**（2026-09-11 拍板，口径 F47）─────────────────────────
--
-- 原本这里有一条 `create index ... using hnsw (embedding vector_cosine_ops)`。
-- 现在注掉，理由不是性能，是**观测范围与应用范围错配**（F23 的形态）：
--
--   `retrieval/README.md` §七 的全部选型证据 —— large 优于 small、1024 与 3072
--   不可区分、四个文本变体与三种扩展模式的对照 —— **都是在精确余弦上取得的**
--   （`retrieval/index.py` 的 `VectorIndex.search`，全表扫描 + 确定性排序）。
--   HNSW 是近似最近邻，**有召回损失**。建上它，生产检索就不再是那批数字描述的
--   那个对象了，而 R17 那些「无一致方向」的结论会被悄悄挪用到一个没测过的配置上。
--
-- 而 v1 根本不需要它：实测本语料**每门课 15–46 个切片**，精确扫描的代价可忽略
-- （这也正是 `retrieval/index.py` 文件头选精确扫描的理由，对生产同样成立）。
--
-- ⚠️ **重新评估的触发线：单门课的 `slice_embeddings` 行数超过 5,000。**
--   这个数是**当前实测规模的 100 倍以上**（46 → 5,000），不是量出来的延迟阈值——
--   如实说明，免得日后有人把它当成「测过 5,000 行会变慢」。
--
-- 触发之后**第一件事不是建索引**，是先量一次 ANN 与精确扫描的 top-5 一致率：
-- 不一致到什么程度会翻转 §七 的选型结论，那才是要不要建它的判据。
-- 建之前还要重读一遍本文件头的维度说明（1024 在 ANN 的 2000 维上限内，可建）。
--
-- create index if not exists slice_embeddings_hnsw_idx
--     on public.slice_embeddings using hnsw (embedding vector_cosine_ops);

-- ── RLS ─────────────────────────────────────────────────────────────────────
alter table public.documents        enable row level security;
alter table public.slices           enable row level security;
alter table public.slice_embeddings enable row level security;

-- 每张表一条策略，条件都是 owner = auth.uid()。
-- **没有任何允许跨用户读取的策略**——PRD §12.1「不做跨用户检索」。
create policy documents_owner on public.documents
    for all using (owner = auth.uid()) with check (owner = auth.uid());

create policy slices_owner on public.slices
    for all using (owner = auth.uid()) with check (owner = auth.uid());

create policy slice_embeddings_owner on public.slice_embeddings
    for all using (owner = auth.uid()) with check (owner = auth.uid());

-- 检索函数。owner 过滤写在函数里而不只依赖 RLS，是**双保险**：
-- security invoker + RLS 已经够，但这一条让「忘记开 RLS」不至于直接变成越权。
create or replace function public.match_slices(
    p_document_id uuid,
    p_query       vector(1024),                  -- 须与 slice_embeddings.embedding 同维
    p_model       text,
    p_ruleset     text,
    p_limit       int default 5
)
returns table (slice_id text, similarity float)
language sql stable security invoker
as $$
    select e.slice_id, 1 - (e.embedding <=> p_query) as similarity
    from public.slice_embeddings e
    where e.document_id = p_document_id
      and e.owner       = auth.uid()
      and e.model       = p_model
      and e.embed_ruleset = p_ruleset
    order by e.embedding <=> p_query
    limit p_limit;
$$;
