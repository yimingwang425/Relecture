-- F5 · 会话的前情笔记（2026-10-09，口径 F116–F118）
--
-- ## 为什么要一张新表
--
-- 上下文压缩 agent（`explain/explain/memory.py`）把一段长对话里最早的若干轮整理成一份笔记，
-- 之后的讲解请求用「笔记 + 最近几轮原文」代替「最近 40 条消息原文」。笔记要落库：
-- 下一轮请求在另一个进程里，它得读得到。
--
-- 不塞进 `messages`：
--   · `messages.role` 只许 user / assistant，笔记两者都不是；放宽 CHECK 等于让前端的会话列表
--     多出一种它不认识的行，而那个列表不会报错，只会把笔记当成一条消息显示出来；
--   · `messages` 不给 update（改一条已经说出口的回答没有合法场景），笔记却要整份替换。
--
-- ## 只追加，不改
--
-- 每次整理写**一行新的**，读取取 `through_turn` 最大的那一行。旧行留着：
-- 哪一次整理折了哪几轮、花了多少、被退回几次，事后都查得到（口径 F53：验收要确认的东西，
-- 系统必须能自己答出来）。所以和 `messages` 一样**不给 update**。
--
-- ## 失败也记一行
--
-- `status = 'failed'` 的行没有笔记、没有 through_turn，只有 meta（原因、步数、花了多少）。
-- 不记的话，失败那几次调用的钱从单位经济里消失，而失败本身也看不见——
-- 讲解照常返回，一切正常，只是笔记永远没写出来（PRD §6.2「它看起来成功了」那一类）。
--
-- ## ⚠️ 部署顺序：本迁移先于构建
--
-- 构建先上、迁移没跑：读笔记查不到表 → 当作没有笔记，讲解照常（原文照旧带上）；
-- 写笔记失败 → 记进日志与响应的 `memory.compaction.error`。**主链路不会红**——
-- 所以这个错不会以「服务挂了」的样子出现，验收第 0 步之后要专门看一眼 `memory` 字段。

create table if not exists public.conversation_memories (
    id              uuid primary key default gen_random_uuid(),
    owner           uuid not null references auth.users (id) on delete cascade,
    conversation_id uuid not null references public.conversations (id) on delete cascade,
    status          text not null check (status in ('written', 'failed')),
    -- 笔记覆盖第 1 轮到第 through_turn 轮。failed 行为 null —— null 在这里是事实（同口径 F50）。
    through_turn    int check (through_turn >= 1),
    -- 笔记本体（`MemoryNote.to_dict()`）。渲染在代码里做，库里只存结构：
    -- 渲染规则改了不用回填，字数上限量的也只有一处。
    memory          jsonb,
    -- 整理过程：候选范围、工具调用、退回次数、用量与成本、模型、prompt 版本（`MemoryRun.to_dict()`）。
    meta            jsonb not null default '{}'::jsonb,
    created_at      timestamptz not null default now(),
    -- written ⇔ 有笔记且有 through_turn。少了它，一行 written 却没有笔记的记录会被读成
    -- 「整理过、什么都不用记」，后面的轮次就此丢掉前情。
    constraint conversation_memories_written_has_note check (
        (status = 'written') = (memory is not null and through_turn is not null)
    ),
    constraint conversation_memories_memory_is_object check (
        memory is null or jsonb_typeof(memory) = 'object'
    )
);

comment on table public.conversation_memories is
  'F5 会话的前情笔记（上下文压缩 agent 写，口径 F116–F118）。只追加：读 through_turn 最大的 written 行。'
  '由产品服务端带用户自己的 JWT 写（同 messages）。';

-- 同一会话同一个 through_turn 只有一份笔记（两次整理撞上时第二次被拒，读的一侧不必挑）。
create unique index if not exists conversation_memories_written_once
    on public.conversation_memories (conversation_id, through_turn)
    where status = 'written';

create index if not exists conversation_memories_latest
    on public.conversation_memories (conversation_id, through_turn desc);

-- ---------------------------------------------------------------- RLS

alter table public.conversation_memories enable row level security;

revoke all on public.conversation_memories from anon, authenticated;
-- update 不给：只追加（见上）。delete 要给：PRD §12.1 的一键删除。
grant select, insert, delete on public.conversation_memories to authenticated;

drop policy if exists conversation_memories_owner on public.conversation_memories;
create policy conversation_memories_owner on public.conversation_memories
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- ---------------------------------------------------------------- 跨 owner 护栏
--
-- 与 `f5_message_conversation_same_owner` 同一条理由：RLS 只管「这一行的 owner 是不是我」，
-- **不管我引用的那个会话是不是我的**。

create or replace function public.f5_memory_conversation_same_owner()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  conv_owner uuid;
begin
  select owner into conv_owner from public.conversations where id = new.conversation_id;
  if conv_owner is null then
    raise exception '会话 % 不存在', new.conversation_id using errcode = '23503';
  end if;
  if conv_owner is distinct from new.owner then
    raise exception '笔记的 owner 与会话的 owner 不一致：% vs %', new.owner, conv_owner
      using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists conversation_memories_owner_check on public.conversation_memories;
create trigger conversation_memories_owner_check
  before insert or update on public.conversation_memories
  for each row execute function public.f5_memory_conversation_same_owner();
