-- F5 · 讲解的出处核对结果（2026-10-10，口径 F121–F123）
--
-- ## 为什么要一张新表
--
-- 出处核对 agent（`explain/explain/verify.py`）在讲解返回**之后**跑（十几到几十秒，学生不等它），
-- 把讲解里的事实性说法逐条拿去和课件对。结果要落库：学生重新打开这个会话时，
-- 每一轮下面的「出处核对」要原样重现，而那时核对早就跑完了。
--
-- 不塞进 `messages.meta`：`messages` 不给 update（改一条已经说出口的回答没有合法场景），
-- 而核对结果是在那一行写进去**之后**才有的。放开 update 等于让整张消息表可改。
--
-- ## 一轮一行
--
-- `round_id` 唯一：一次 `/explain` = 一轮 = 至多一次核对。核对不重试——
-- 失败了就是失败了，那一行 `status = 'failed'`，原因与花掉的钱在 `result` / `meta` 里
-- （不记的话，失败那几次调用的钱从单位经济里消失，同 `conversation_memories`）。
-- 不需要核的轮（寒暄 / 非学习 / 危机，没有课件片段）**不写行**：没有行 = 没核，
-- 与「核了、全部有依据」分得开。
--
-- ## 挂得上才写得进
--
-- 触发器除了 owner 一致，还要求 `round_id` 是**这个会话里一条讲解消息的那一轮**。
-- 少了它，一行核对可以挂在一个不存在的轮上，读的一侧按 round_id 拼回去时静默对不上。
--
-- ## ⚠️ 部署顺序：本迁移先于构建
--
-- 构建先上、迁移没跑：核对照常跑、照常通过 `verify` 事件推给页面，只是**写不进库**——
-- 结果里 `persisted = false` 并带原因。刷新页面后核对结果消失。**主链路不会红**。

create table if not exists public.answer_checks (
    id              uuid primary key default gen_random_uuid(),
    owner           uuid not null references auth.users (id) on delete cascade,
    conversation_id uuid not null references public.conversations (id) on delete cascade,
    round_id        uuid not null,
    status          text not null check (status in ('written', 'failed')),
    -- 逐条判定与计数（`VerifyRun.to_dict()`）。页面读 counts 与 claims。
    result          jsonb not null default '{}'::jsonb,
    -- 核对过程之外的：模型、prompt 版本、触发它的轮次。
    meta            jsonb not null default '{}'::jsonb,
    created_at      timestamptz not null default now(),
    constraint answer_checks_round_once unique (round_id),
    constraint answer_checks_result_is_object check (jsonb_typeof(result) = 'object'),
    constraint answer_checks_meta_is_object check (jsonb_typeof(meta) = 'object')
);

comment on table public.answer_checks is
  'F5 讲解的出处核对结果（出处核对 agent 写，口径 F121–F123）。一轮一行，只追加。'
  '由产品服务端带用户自己的 JWT 写（同 messages）。';

create index if not exists answer_checks_conversation
    on public.answer_checks (conversation_id, created_at);

-- ---------------------------------------------------------------- RLS

alter table public.answer_checks enable row level security;

revoke all on public.answer_checks from anon, authenticated;
-- update 不给：一轮一次核对，写完不改。delete 要给：PRD §12.1 的一键删除。
grant select, insert, delete on public.answer_checks to authenticated;

drop policy if exists answer_checks_owner on public.answer_checks;
create policy answer_checks_owner on public.answer_checks
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- ---------------------------------------------------------------- 跨 owner 与挂靠护栏

create or replace function public.f5_answer_check_round_exists()
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
    raise exception '核对的 owner 与会话的 owner 不一致：% vs %', new.owner, conv_owner
      using errcode = '42501';
  end if;
  if not exists (
    select 1 from public.messages
     where conversation_id = new.conversation_id and round_id = new.round_id and role = 'assistant'
  ) then
    raise exception '会话 % 里没有 round_id 为 % 的讲解', new.conversation_id, new.round_id
      using errcode = '23503';
  end if;
  return new;
end;
$$;

drop trigger if exists answer_checks_round_check on public.answer_checks;
create trigger answer_checks_round_check
  before insert or update on public.answer_checks
  for each row execute function public.f5_answer_check_round_exists();
