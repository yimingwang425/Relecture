-- F2 · 混合检索的词法一路 + 每份文档的索引口径 + 图片页转写记录
-- 日期：2026-10-07　口径见 f1_samples/README.md 的 F97 / F99 / F100
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⚠️ **部署顺序：本文件与 20261007100100 必须先在 SQL Editor 跑完，再部署构建。**
--    反了的话：新构建摄入时写 `slice_lexicon` → 404（表不存在）→ 整次上传失败；
--    检索调 `match_slices_indexed` → 404 → 讲解失败。**与以往「埋点写不进去但主链路
--    照常 200」不同，这一次主链路会红**——检索与摄入都依赖这两张新对象。
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 本文件**不依赖 pgvector**，全部在本地（PGlite）验：`verify_rls_local.mjs` 的
-- 「混合检索 · 词法一路」一节。向量那一半在 20261007100100（本地跳过，真库验）。

-- ---------------------------------------------------------------- documents 加两列

-- **第六个口径版本列**：这份文档的检索索引按哪个口径建的（嵌入文本 + 定位语 + 分词）。
--
-- 为什么必须是文档级的、而不是全库一个常量：新口径（带定位语）只对**新上传**的文档
-- 一步到位；已有文档要靠 `api/scripts/reindex_course.py` 逐份重建。重建完成之前，
-- 一门课里两种口径的文档并存——检索要按**每份文档自己的口径**去取它的向量与词条，
-- 否则老文档会因为「新口径下没有行」整份检索不到，而那看起来和「这份课件没讲」一模一样。
--
-- 默认值 'embed-text-1.0' = 本文件之前的唯一口径，所以已有行**不需要回填**：
-- 它们的向量确实是按那个口径建的（slice_embeddings.embed_ruleset 也都是它）。
alter table public.documents
    add column if not exists index_ruleset text not null default 'embed-text-1.0';

-- 哪些页是**模型看图转写**出来的（口径 F100）。`[{"index": 33, "chars": 412}]`。
--
-- ⚠️ **不改 `verdict` / `unusable_pages`**：那两列是解析器的结论（P08：只报事实），
-- 转写救回了一页不等于解析器读出了它。改它们就是让一个字段说谎（同 F69）。
-- 检索侧据本列把这些页从「读不了的页」里去掉；整份 `unusable` 的扫描件
-- 只要本列非空就仍可入库（`api/api/ingest.py`）。
alter table public.documents
    add column if not exists vision_pages jsonb not null default '[]'::jsonb;

do $$ begin
  alter table public.documents
    add constraint documents_vision_pages_is_array check (jsonb_typeof(vision_pages) = 'array');
exception when duplicate_object then null; end $$;


-- ---------------------------------------------------------------- slice_lexicon

-- 与 slice_embeddings **同一个形状、同一个理由**分表：词条是「切片 + 口径」的函数，
-- 口径一变就整份重算，而切片本身不变。主键带 `ruleset`，新口径天然是新行；
-- **不给 update**——改一行等于让词条与它声称的口径不符。
create table if not exists public.slice_lexicon (
    document_id  uuid not null,
    slice_id     text not null,
    owner        uuid not null references auth.users (id) on delete cascade,
    ruleset      text not null,
    -- 模型写的「定位语」（口径 F99）。**允许为空**：那次调用失败或没配 key 时就没有，
    -- 而那时嵌入文本也不带前缀——存下来是为了让「这一片的嵌入文本是什么」可复原。
    context      text,
    -- `retrieval.lexical.lexical_text()` 的产出：空格拼起来的词条。
    -- **分词在 Python 侧做**，数据库只按空格切（'simple'），不做任何语言处理——
    -- 两侧各做一份分词的话，哪天一侧改了规则，召回会静静地掉到零。
    lexical_text text not null,
    lexical      tsvector generated always as (to_tsvector('simple', lexical_text)) stored,
    created_at   timestamptz not null default now(),
    primary key (document_id, slice_id, ruleset),
    foreign key (document_id, slice_id)
        references public.slices (document_id, slice_id) on delete cascade
);

create index if not exists slice_lexicon_lexical_idx on public.slice_lexicon using gin (lexical);

alter table public.slice_lexicon enable row level security;
revoke all on public.slice_lexicon from anon, authenticated;
grant select, insert, delete on public.slice_lexicon to authenticated;

drop policy if exists slice_lexicon_owner on public.slice_lexicon;
create policy slice_lexicon_owner on public.slice_lexicon
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- ⚠️ 与 `f2_slice_document_same_owner` 同一个洞：with check 只问「这一行的 owner 是不是我」，
-- **不问「我引用的那一片是不是我的」**。外键检查不走 RLS，所以没有这个触发器的话，
-- 我可以给别人的切片挂一行我的词条——那行对他不可见，但它让「词条属于谁」与
-- 「切片属于谁」可以不一致，而 cascade 与清理都假设两者一致。
create or replace function public.f2_lexicon_slice_same_owner()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  slice_owner uuid;
begin
  select owner into slice_owner from public.slices
   where document_id = new.document_id and slice_id = new.slice_id;
  if slice_owner is null then
    raise exception '切片 %#% 不存在', new.document_id, new.slice_id using errcode = '23503';
  end if;
  if slice_owner is distinct from new.owner then
    raise exception '词条的 owner 与切片的 owner 不一致' using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists slice_lexicon_owner_check on public.slice_lexicon;
create trigger slice_lexicon_owner_check
  before insert on public.slice_lexicon
  for each row execute function public.f2_lexicon_slice_same_owner();


-- ---------------------------------------------------------------- 词法检索函数

-- 作用域是**一门课**（口径 F47），且只取每份文档**自己那个口径**的词条
-- （`l.ruleset = d.index_ruleset`）——老文档在新口径下没有词条，于是这一路对它们
-- 召回为空，向量那一路照常（见 20261007100100）。**不混用口径**。
--
-- `p_query` 是 `retrieval.lexical.tsquery()` 的产出（`'a' | 'b'`）。词条只含
-- `[a-z0-9]` 与 CJK 字符，不存在 tsquery 语法注入；空串由调用方跳过，不发到这里。
--
-- 排序用 `ts_rank_cd`（覆盖度）。**它不是 BM25**——Postgres 没有内置 BM25，
-- 文档与提交信息里都不写「BM25」。并列按 (document_id, slice_id) 定序（R09）。
create or replace function public.match_slices_lexical(
    p_course_id uuid,
    p_query     text,
    p_limit     int default 20
)
returns table (document_id uuid, slice_id text, rank real)
language sql stable security invoker
as $$
    select l.document_id, l.slice_id, ts_rank_cd(l.lexical, q) as rank
    from public.slice_lexicon l
    join public.documents d on d.id = l.document_id and l.ruleset = d.index_ruleset
    cross join to_tsquery('simple', p_query) q
    where d.course_id = p_course_id
      and d.owner     = auth.uid()
      and l.owner     = auth.uid()
      and l.lexical @@ q
    order by rank desc, l.document_id, l.slice_id
    limit p_limit;
$$;

comment on function public.match_slices_lexical(uuid, text, int) is
  'F2 混合检索的词法一路（口径 F97）。作用域一门课，只取每份文档自己口径（documents.index_ruleset）'
  '的词条。排序是 ts_rank_cd，不是 BM25。分词在 Python 侧（retrieval/lexical.py），数据库只按空格切。';

comment on table public.slice_lexicon is
  'F2 词法索引（口径 F97 / F99）。一行 = 一片在某个口径下的词条与定位语。'
  '与 slice_embeddings 同形：主键带 ruleset，新口径是新行，不给 update。';
