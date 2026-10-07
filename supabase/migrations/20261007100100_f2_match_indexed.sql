-- F2 · 向量检索按**每份文档自己的索引口径**取行（混合检索的向量一路）
-- 日期：2026-10-07　口径见 f1_samples/README.md 的 F97 / F99
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⚠️ **本文件在本地验不了**（要 pgvector，PGlite 没有），与 20260911100100 同一处置：
--    `verify_rls_local.mjs` **吵着跳过**它，验证走真库（`verify_retrieval_prod.py`）。
--    **必须排在 20261007100000 之后执行**：它读 `documents.index_ruleset`。
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 与 `match_slices` 的唯一区别：不收 `p_ruleset`，改为 `e.embed_ruleset = d.index_ruleset`。
--
-- 为什么不在 Python 里对每个口径各调一次 `match_slices` 再合并：两次调用各取 top-N，
-- 合并后的 top-N 与「一次取全课 top-N」不是同一个集合（一边的第 N+1 名可能比另一边的
-- 第 1 名还近）。**一次查询、一个排序**，才与评测时的「全表精确余弦」是同一个对象。
--
-- `match_slices` 原样保留：`verify_corpus_prod.py` / `verify_retrieval_prod.py` 的
-- 既有断言调它，那批真库观测是对它做的，不跟着改。

create or replace function public.match_slices_indexed(
    p_course_id uuid,
    p_query     vector(1024),
    p_model     text,
    p_limit     int default 20
)
returns table (document_id uuid, slice_id text, similarity float)
language sql stable security invoker
as $$
    select e.document_id, e.slice_id, 1 - (e.embedding <=> p_query) as similarity
    from public.slice_embeddings e
    join public.documents d on d.id = e.document_id
    where d.course_id     = p_course_id
      and d.owner         = auth.uid()
      and e.owner         = auth.uid()
      and e.model         = p_model
      and e.embed_ruleset = d.index_ruleset
    order by e.embedding <=> p_query, e.document_id, e.slice_id
    limit p_limit;
$$;

comment on function public.match_slices_indexed(uuid, vector, text, int) is
  'F2 混合检索的向量一路（口径 F97）。与 match_slices 相同，但按每份文档自己的 index_ruleset 取向量——'
  '新旧两种口径的文档在一门课里并存时，两边都检索得到，且不混用口径。';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：（待填）　执行结果：（待填）
--
-- 本文件的行为在本地一条都验不了。真库上要观测的三件事（`verify_retrieval_prod.py`）：
--   · 老文档（index_ruleset = 'embed-text-1.0'）照样召回得到；
--   · 新文档只按新口径召回（同一片在新旧两口径下各有一行时，不重复出现）；
--   · 作用域与 RLS 与 match_slices 一致（B 用 A 的 course_id → 0 行）。
