-- F5 · 会话与消息（2026-09-20）
--
-- ## 在这之前，对话只活在浏览器内存里
--
-- `explain-client.tsx` 的 `useState<Turn[]>` 就是全部：刷新一下、换个标签页、
-- 关掉再打开，之前问过什么**一个字都不剩**。而 `/explain` 本身是无状态的
-- （`ExplainRequest.turn_index` 的注释原话：「本服务不持有会话状态」），
-- 所以每一轮的模型都看不到上一轮——`explain/pipeline.py` 里那个
-- `Trajectory.history` 在生产链路上**恒为空**。
--
-- 两个后果，第二个更要紧：
--   1. 用户的追问（「那第二点再展开讲讲」）没有指代对象；
--   2. PRD §3.1 的北极星指标是「平均会话轮次」，而没有会话实体时那个数只能
--      按 `session_id`（= 一次页面加载）算——**刷新一次就断一次**。
--
-- ## 边界：一门课 N 个会话，一个会话 N 轮
--
-- 用户的原话是「每个 week 单独的窗口」。会话挂在**课**上而不是挂在文档上：
-- 检索的作用域本来就是一门课（口径 F47），一个会话跨这门课的所有课件。
--
-- ## 谁写哪张表（两条写入路径，刻意分开）
--
-- | 表 | 谁写 | 为什么 |
-- | --- | --- | --- |
-- | `conversations` | **浏览器**（RLS 直写） | 标题是纯 UI 状态，服务端不产出任何一个字段 |
-- | `messages` | **产品服务端**（带用户自己的 JWT） | `round_id` / `turn_index` / 答案正文只有那一次 run_round 知道 |
--
-- 让浏览器写 `messages` 就等于让它自报「模型说了什么」，而那正是
-- `explain_turn` 事件坚持由服务端发的同一条理由（口径 F51）。

-- ---------------------------------------------------------------- 会话

create table if not exists public.conversations (
    id         uuid primary key default gen_random_uuid(),
    owner      uuid not null references auth.users (id) on delete cascade,
    course_id  uuid not null references public.courses (id) on delete cascade,
    -- 标题可改（用户按 week 命名）。空白标题不允许——侧栏里一行空的没法点也没法认。
    title      text not null check (length(btrim(title)) > 0),
    created_at timestamptz not null default now(),
    -- 侧栏按它倒序。**由 messages 的触发器维护**，不由写入方自己填：
    -- 让调用方填的话，忘了填的那一次会让这个会话永远沉到底部，而没有任何东西会报错。
    updated_at timestamptz not null default now()
);

comment on table public.conversations is
  'F5 一次对话。挂在课程上（口径 F47：检索作用域是一门课）。'
  '标题由用户改，浏览器直写；消息由产品服务端写。';

create index if not exists conversations_course_idx
    on public.conversations (course_id, updated_at desc);

-- ---------------------------------------------------------------- 消息

create table if not exists public.messages (
    id              uuid primary key default gen_random_uuid(),
    owner           uuid not null references auth.users (id) on delete cascade,
    conversation_id uuid not null references public.conversations (id) on delete cascade,
    role            text not null check (role in ('user', 'assistant')),
    content         text not null,
    -- 会话内第几轮。**按用户可见的输出计，重生成不计入**（PRD §13.0，与
    -- `ExplainRequest.turn_index` 同一口径）。一问一答两行**共用同一个** turn_index：
    -- 分开编号的话「平均会话轮次」会当场翻倍，而那是北极星指标。
    turn_index      int not null check (turn_index >= 1),
    -- 这一轮的 round_id（`explain_turn` 事件里的那一个）。
    -- **只有 assistant 行有**，user 行为 null —— null 在这里是事实不是遗漏，
    -- 所以不给默认值、不填空字符串（同口径 F50）。
    round_id        uuid,
    -- 出处片段、跳过的页、兜底判断结果。**结构随讲解链路走，所以进 jsonb**；
    -- 但凡要按它筛或聚合的字段都不该待在这里（F36 那条教训）。
    meta            jsonb not null default '{}'::jsonb,
    created_at      timestamptz not null default now()
);

comment on table public.messages is
  'F5 一条消息。**由产品服务端写**（带用户自己的 JWT，RLS 由数据库执行）：'
  'round_id / turn_index / 答案正文只有那一次 run_round 知道，'
  '让浏览器自报等于让它自报「模型说了什么」（同口径 F51）。';

create index if not exists messages_conversation_idx
    on public.messages (conversation_id, created_at);

-- ---------------------------------------------------------------- 课件的展示名

-- 用户上传后要能改成「week1」这种自己认得的名字。
--
-- ⚠️ **不覆盖 `filename`**：那一列是上传时的真实文件名，出处引用与排错都要它
-- （`slices.source_file` 在摄入时就抄了一份，改 filename 不会跟着改）。
-- 所以这里加一列展示名，null = 没改过 = 显示 filename。
-- **null 有含义，不给默认值、不填空串**（同口径 F50）。
alter table public.documents add column if not exists display_name text;

comment on column public.documents.display_name is
  '用户给这份课件起的名字（如 week1）。null = 没改过，显示 filename。'
  '**不覆盖 filename**：出处引用与排错要的是上传时的真实文件名。';

-- ---------------------------------------------------------------- RLS

alter table public.conversations enable row level security;
alter table public.messages      enable row level security;

-- 与 courses / documents 同一套：显式收权后再按需授予，不依赖 public schema 的默认授权。
-- delete **要给**：PRD §12.1 的「一键删除」，用户要能删掉自己的对话。
revoke all on public.conversations, public.messages from anon, authenticated;
grant select, insert, update, delete on public.conversations to authenticated;
-- ⚠️ **数据库分不出「我们的服务端」和「浏览器」，这一点如实登记。**
--
-- 产品服务端写库带的是**用户自己的 JWT**（口径 F40：服务端不持有 service_role key），
-- 所以它在数据库眼里就是 `authenticated`，和浏览器一模一样。
-- 于是上面那张「谁写哪张表」的表**是代码约定，不是数据库护栏**：
-- 给了 insert，浏览器在技术上照样插得进 messages。
--
-- 不为此引入 service_role：那要求服务端持有一把能绕开所有 RLS 的钥匙，
-- 代价远大于这里要防的东西（用户伪造自己会话里的一条消息，只骗得到他自己）。
-- **写下来是为了别把它当成已经挡住了。**
--
-- `update` 不给：改一条已经说出口的回答没有合法场景。
-- `delete` 要给：PRD §12.1 的一键删除，以及用户删掉单条。
grant select, insert, delete on public.messages to authenticated;

drop policy if exists conversations_owner on public.conversations;
create policy conversations_owner on public.conversations
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

drop policy if exists messages_owner on public.messages;
create policy messages_owner on public.messages
    for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

-- ---------------------------------------------------------------- 跨 owner 护栏
--
-- 与 `f2_document_course_same_owner` 同一条理由：RLS 只管「这一行的 owner 是不是我」，
-- **不管「我引用的那一行是不是我的」**。少了它，用户 A 能把自己的会话挂到用户 B 的课下。

create or replace function public.f5_conversation_course_same_owner()
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
    raise exception '会话的 owner 与课程的 owner 不一致：% vs %', new.owner, course_owner
      using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists conversations_course_owner_check on public.conversations;
create trigger conversations_course_owner_check
  before insert or update on public.conversations
  for each row execute function public.f5_conversation_course_same_owner();

create or replace function public.f5_message_conversation_same_owner()
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
    raise exception '消息的 owner 与会话的 owner 不一致：% vs %', new.owner, conv_owner
      using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists messages_conversation_owner_check on public.messages;
create trigger messages_conversation_owner_check
  before insert or update on public.messages
  for each row execute function public.f5_message_conversation_same_owner();

-- ---------------------------------------------------------------- updated_at
--
-- 侧栏按 `updated_at` 倒序（最近说过话的在上面）。**由触发器维护**：
-- 让写入方自己填的话，忘了填的那一次会让这个会话永远沉到底，而没有任何东西会报错——
-- 一个不会报错的遗漏，正是这个项目一路在躲的那种形态。

create or replace function public.f5_touch_conversation()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  update public.conversations
     set updated_at = now()
   where id = new.conversation_id;
  return new;
end;
$$;

drop trigger if exists messages_touch_conversation on public.messages;
create trigger messages_touch_conversation
  after insert on public.messages
  for each row execute function public.f5_touch_conversation();
