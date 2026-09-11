-- F2 · 语料：课程 / 文档 / 切片
-- 日期：2026-09-11　口径见 f1_samples/README.md 的 F44 / F47
--
-- ---------------------------------------------------------------- 为什么拆成两个文件
--
-- 本文件与 `20260911100100_f2_embeddings.sql` 是同一批交付、同一次执行，
-- 但**刻意分开**，分界线是「本地验不验得了」：
--
--   · 本文件不含任何 pgvector 的东西 → `supabase/verify_rls_local.mjs` 能在
--     PGlite 上把它**全部**跑一遍（RLS / cascade / 唯一索引 / 作用域，含反向控制）；
--   · 另一个文件要 `create extension vector`，PGlite 的 contrib 里没有 vector，
--     **本地一条都验不了**，只能对真库验。
--
-- 糊在一个文件里的话，「哪部分被验过」就没有硬边界了——那正是 F34 要分开记的
-- 「机制」与「存量」的同一条：**两种证据不得互相顶替**。
-- 本地绿数与待真库验证的条数**分两行报，不报合计**。
--
-- ⚠️ 不给替身做一个假的 `<=>` 来凑绿。假的排序语义会让召回类断言全部失去意义，
-- 而那正是 `InMemoryRetriever` 自己文档里写着「不得据此出召回结论」的原因。

-- ---------------------------------------------------------------- 课程

-- 口径 F47：**课程是实体，检索的作用域是一门课，不是一份文档。**
-- 一门课有多份讲义是常态；按 document 过滤会让「同一门课的第二份讲义」检索不到，
-- 而那不是边缘情况。§13.0 的 `course_id` / `deck_id` 从这里取
-- （`deck_id` = `documents.id`）。
create table if not exists public.courses (
    id         uuid primary key default gen_random_uuid(),
    owner      uuid not null references auth.users (id) on delete cascade,
    name       text not null check (length(btrim(name)) > 0),
    created_at timestamptz not null default now()
);

comment on table public.courses is
  'F2 课程。检索的作用域（口径 F47）。'
  '⚠️ **风格配置不挂在这里**（口径 F44）：style_config 一人一行，不按课分——'
  '学生对「讲得细不细」的偏好不因课程而变，且按课分会让 onboarding 从一次变 N 次，'
  '而 onboarding 完成率是 A5 的预登记指标之一（F43），分母一变前后不可比。';

create index if not exists courses_owner_idx on public.courses (owner);

-- ---------------------------------------------------------------- 文档

create table if not exists public.documents (
    id                uuid primary key default gen_random_uuid(),
    owner             uuid not null references auth.users (id) on delete cascade,
    -- 口径 F47。**只放在这里**，不往 slices / slice_embeddings 冗余：
    -- 冗余的唯一强理由是让 ANN 索引能按课裁剪，而 v1 不建 ANN（同 F47）——
    -- 那个理由随它一起没了，剩下的只是「同一件事两处出处」的代价。
    -- 连带好处：传错课时挪文档只是本表一条 UPDATE。
    course_id         uuid not null references public.courses (id) on delete cascade,
    filename          text not null,
    source_format     text not null check (source_format in ('pdf', 'pptx')),
    content_sha256    text not null,
    page_count        int  not null,
    usable_page_count int  not null,
    unusable_pages    jsonb not null default '[]'::jsonb,
    verdict           text not null check (verdict in ('usable', 'unusable')),
    -- parse-service 的 ParserInfo（service_version / pymupdf / python_pptx /
    -- scan_ruleset_version / adjacency_ruleset_version）。它本身是个结构，进 jsonb 合理。
    parser            jsonb not null,

    -- ---- 口径版本列（5 个，显式列不塞 jsonb）----
    --
    -- 缺了它们的后果与 `style_events.schema_version` 缺失是同一类：
    -- 口径一改，新旧行**再也分不开**，而合并会得到一个看起来正常的错数。
    --
    -- **不塞 jsonb** 的理由是 F36 那条教训：jsonb 没有 schema，缺字段不报错；
    -- 而你要问的是「哪些文档还在旧口径上」——那是个索引列的问题，不是翻 json 的问题。
    parse_schema_version  text not null,   -- ParseResult.schema_version
    slice_schema_version  text not null,   -- SliceSet.schema_version
    slicer_version        text not null,   -- manifest.slicer_version
    slice_ruleset_version text not null,   -- manifest.slice_ruleset_version
    -- ⚠️ **这一列最容易被漏掉，而它会改变切片结果。**
    -- `slice_document(parsed, split_threshold_chars=None)` 的注释写着
    -- 「值由 2b 注入，取决于 embedding 供应商的上下文窗口」。
    -- 当前恒为 NULL（不拆）且本语料 0 触发，所以今天不落库看不出问题；
    -- 一旦 F2 摄入按 embedding 窗口注入一个值，新旧切片就再也分不开了。
    -- NULL 有含义（= 不拆），所以**允许为空**，不给默认值。
    split_threshold_chars int,

    created_at        timestamptz not null default now()
);

-- 口径 F47 + 2026-09-11 拍板：**同一份文件可以挂到不同课程**。
--
-- ⚠️ **这是硬失败，不是「提示」。** 撞上它 PostgREST 返回 23505，插入不成立。
-- 上一版注释把它写成「重复上传提示」——那是意图，不是机制；要「提示」得先查后提示
-- （登记在 f1_samples/README.md §二，阶段 6 做前端时一并处理）。
-- 在那之前，摄入路径**必须显式捕获 23505 并翻译成人话**，
-- 否则用户看到的是一个数据库错误码。
--
-- 为什么带上 course_id：不带的话，「同一份导论讲义用于两个学期」会被数据库拦住，
-- 而用户看不出为什么。代价是同一份内容被 embedding 两次（两份钱 + 同一内容的
-- 第二次出境）——知情接受。同一用户内按 content_sha256 复用向量是合法的
-- （§12.1 禁的是**跨用户**共享），但它会搅复杂 embedding 表的主键，登记不做。
create unique index if not exists documents_owner_course_sha_idx
    on public.documents (owner, course_id, content_sha256);

create index if not exists documents_course_idx on public.documents (course_id);

comment on column public.documents.content_sha256 is
  '文件内容 sha256。用途仅两项：完整性校验，以及**同一用户同一课程内**的重复上传检测。'
  '**不是主键，严禁用作跨用户去重或跨用户缓存的键**（PRD §12.1：不跨用户共享、不建公共库）。';

-- ---------------------------------------------------------------- 切片

create table if not exists public.slices (
    document_id   uuid not null references public.documents (id) on delete cascade,
    -- **文档内局部 id**（如 's003'）。跨文档不唯一——所以主键是两段，
    -- 而 `match_slices` 也必须返回两段（见 embeddings 那个文件）。
    slice_id      text not null,
    owner         uuid not null references auth.users (id) on delete cascade,
    ordinal       int  not null,
    page_indices  int[] not null,
    title         text,
    text          text not null,
    char_count    int  not null,
    units         jsonb not null,
    images        jsonb not null default '[]'::jsonb,
    page_signals  jsonb not null default '[]'::jsonb,
    prev_slice_id text,
    next_slice_id text,
    primary key (document_id, slice_id)
);

create index if not exists slices_document_idx on public.slices (document_id);

-- ---------------------------------------------------------------- RLS

alter table public.courses   enable row level security;
alter table public.documents enable row level security;
alter table public.slices    enable row level security;

-- 与 style_config 同一套：显式收权后再按需授予，不依赖 public schema 的默认授权
-- （默认授权含 delete，而这里的 delete 是**要给**的——用户要能删自己的课件，
-- PRD §12.1「一键删除」。这与 style_config 不同，那张表的清空走注销 cascade）。
revoke all on public.courses, public.documents, public.slices from anon, authenticated;
grant select, insert, update, delete on public.courses   to authenticated;
grant select, insert, update, delete on public.documents to authenticated;
grant select, insert, delete         on public.slices    to authenticated;
-- slices 不给 update：切片是解析的产物，改它等于让库里的切片与文档不一致，
-- 而没有任何东西会发现。要变就重切（删掉整份重新写入）。

-- 每张表一条策略，条件都是 owner = auth.uid()。
-- **没有任何允许跨用户读取的策略**——PRD §12.1「不做跨用户检索」。
drop policy if exists courses_owner on public.courses;
create policy courses_owner on public.courses
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

drop policy if exists documents_owner on public.documents;
create policy documents_owner on public.documents
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

drop policy if exists slices_owner on public.slices;
create policy slices_owner on public.slices
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- ⚠️ `owner` 列与 `course_id` 指向的课程的 owner 必须一致，否则用户 A 能把自己的
-- 文档挂到用户 B 的课程下（with check 只看 documents.owner）。RLS 挡不住这个——
-- 它只管「这一行的 owner 是不是我」，不管「我引用的那门课是不是我的」。
create or replace function public.f2_document_course_same_owner()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  course_owner uuid;
begin
  select owner into course_owner from public.courses where id = new.course_id;
  if course_owner is null then
    raise exception '课程 % 不存在', new.course_id using errcode = '23503';
  end if;
  if course_owner is distinct from new.owner then
    raise exception '文档的 owner 与课程的 owner 不一致：% vs %', new.owner, course_owner
      using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists documents_course_owner_check on public.documents;
create trigger documents_course_owner_check
  before insert or update on public.documents
  for each row execute function public.f2_document_course_same_owner();

-- 同理：切片的 owner 必须等于它所属文档的 owner。
create or replace function public.f2_slice_document_same_owner()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  doc_owner uuid;
begin
  select owner into doc_owner from public.documents where id = new.document_id;
  if doc_owner is null then
    raise exception '文档 % 不存在', new.document_id using errcode = '23503';
  end if;
  if doc_owner is distinct from new.owner then
    raise exception '切片的 owner 与文档的 owner 不一致' using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists slices_document_owner_check on public.slices;
create trigger slices_document_owner_check
  before insert on public.slices
  for each row execute function public.f2_slice_document_same_owner();

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：待填　执行结果：待填
--
-- ⚠️ 口径 F34：执行成功只证明 DDL 能执行。建表那一刻必然是空表，
-- 「表里没有坏行」在那一刻不携带信息。
