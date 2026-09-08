-- F1 · 用户风格配置表 + RLS + AB 分组
-- 日期：2026-09-07　口径见 f1_samples/README.md 的 F08–F14
--
-- 本文件是 style_config 的**唯一 DDL 出处**：生产（Supabase SQL Editor）与
-- 本地反向验证（supabase/verify_rls_local.mjs）执行的是同一份文本。
-- 两边各写一份必然漂，而漂掉的那一份恰好是「验证用的那份」——
-- 那样反向验证证明的就是另一个对象（通则七 #6：观测范围与应用范围错配）。

-- ---------------------------------------------------------------- 表

create table if not exists public.style_config (
  user_id       uuid primary key references auth.users (id) on delete cascade,
  detail_level  text        not null default 'P0',
  use_examples  text        not null default 'more',
  structure     text        not null default 'conclusion_first',
  depth         text        not null default 'why',
  f1_arm        text        not null,
  onboarded_at  timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  constraint style_config_detail_level_check
    check (detail_level in ('P0', 'P1', 'P2', 'P3')),
  constraint style_config_use_examples_check
    check (use_examples in ('more', 'fewer')),
  constraint style_config_structure_check
    check (structure in ('conclusion_first', 'buildup_first')),
  constraint style_config_depth_check
    check (depth in ('what', 'why', 'derivation')),
  constraint style_config_f1_arm_check
    check (f1_arm in ('pairwise', 'text'))
);

-- 刻意不存的列，不要加回来（任务书阶段 1）：
--   chinese_ratio_min / max_quote_chars  —— 恒 0.85 / 恒 100，是常量，存了就会漂
--   config_fingerprint / prompt_profile_id / metric_set_id —— Python 侧现算
--   配置历史表 —— 修改次数从埋点事件反推

comment on table public.style_config is
  'F1 用户风格配置，一人一行。四条轴中仅 detail_level 被 A 类审查执行，'
  '其余三条（use_examples/structure/depth）仅进 prompt，enforced=false（F1方案 §2.2）。';

-- ---------------------------------------------------------------- 权限

-- 显式收权后再按需授予。不依赖 Supabase 的 public schema 默认授权——
-- 默认授权包含 delete，而本表刻意不给 delete（注销走 auth.users 的 cascade）。
revoke all on public.style_config from anon, authenticated;
grant select, insert, update on public.style_config to authenticated;

-- ---------------------------------------------------------------- RLS

alter table public.style_config enable row level security;

drop policy if exists style_config_select_own on public.style_config;
create policy style_config_select_own on public.style_config
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists style_config_insert_own on public.style_config;
create policy style_config_insert_own on public.style_config
  for insert to authenticated
  with check (user_id = auth.uid());

-- update 的 with check 必须写：只写 using 的话，用户能把自己那行的 user_id
-- 改成别人的，等于把别人的配置整行夺走。
drop policy if exists style_config_update_own on public.style_config;
create policy style_config_update_own on public.style_config
  for update to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- 无 delete 策略（任务书阶段 1）：注销走 auth.users 的 on delete cascade。

-- ---------------------------------------------------------------- AB 分组

-- 口径 F08：分组只在数据库里算，一处出处。
--
-- 用 md5 首位十六进制二分，不用 hashtext()——后者是内部函数，PostgreSQL
-- **不承诺跨大版本稳定**。分组函数一旦漂，历史用户的臂归属不可复现，
-- 而 A5 的三个读数全部按臂分组（F1方案 §七）。
create or replace function public.f1_assign_arm(uid uuid)
returns text
language sql
immutable
as $$
  select case
    when substr(md5(uid::text), 1, 1) in ('0','1','2','3','4','5','6','7')
      then 'pairwise'
    else 'text'
  end;
$$;

comment on function public.f1_assign_arm(uuid) is
  'F1 AB 分组（口径 F08）。确定性、不依赖 hashtext 的跨版本稳定性。';

-- 口径 F09：建行失败即注册失败，不吞异常。
-- 没有 style_config 行的用户是坏状态——他进不了 onboarding、拿不到臂，
-- 而这个坏状态如果被 try/except 吞掉，会在第一次讲解时才以别的形态暴露。
create or replace function public.handle_new_user_style_config()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.style_config (user_id, f1_arm)
  values (new.id, public.f1_assign_arm(new.id));
  return new;
end;
$$;

drop trigger if exists on_auth_user_created_style_config on auth.users;
create trigger on_auth_user_created_style_config
  after insert on auth.users
  for each row
  execute function public.handle_new_user_style_config();

-- ---------------------------------------------------------------- 回填

-- **这段现在是空操作，不要当死代码删掉。**
--
-- 2026-09-07 首次执行时 auth.users 为空，它补 0 行。留着防的是另一件事：
-- 上面那个触发器**只对新注册用户生效**。以后任何一次 migration 若要重建触发器、
-- 或改 f1_assign_arm 的分组算法，存量用户都会整批漏掉——而漏掉的表现是
-- 「某些用户进不了 onboarding」，不是报错，要靠用户反馈才发现（通则六：
-- 会静默改变数字的问题比会报错的危险）。
--
-- 必须排在 f1_assign_arm 之后：排在它之前会报函数不存在。
--
-- `on conflict do nothing` 只补缺行，**不改已有行的 f1_arm**——那正是 F08/F10
-- 钉死的东西。若哪天真要重分组，那是一次独立的、需要单独论证的 migration，
-- 不能由一句回填顺手完成。
do $$
declare
  n int;
begin
  insert into public.style_config (user_id, f1_arm)
  select u.id, public.f1_assign_arm(u.id)
  from auth.users u
  on conflict (user_id) do nothing;

  get diagnostics n = row_count;
  raise notice '[F1 回填] 补建 style_config 行数：%  (0 = 无存量缺行，属预期)', n;
end
$$;

-- ---------------------------------------------------------------- 更新护栏

-- 口径 F10：f1_arm 被改就抛异常，**不静默改写回 old 值**。
-- 静默改写会掩盖前端的真 bug，且与 F07「轴端点不静默吞掉」同一条原则。
-- 合法客户端本来就不发这个字段（PostgREST 的 PATCH 只带 body 里的列，
-- 未出现的列 new = old，不触发本检查）。
create or replace function public.style_config_guard_update()
returns trigger
language plpgsql
as $$
begin
  if new.f1_arm is distinct from old.f1_arm then
    raise exception 'f1_arm 不可修改（AB 分组在注册时定死）：% -> %',
      old.f1_arm, new.f1_arm
      using errcode = '23514';
  end if;

  if new.user_id is distinct from old.user_id then
    raise exception 'user_id 不可修改' using errcode = '23514';
  end if;

  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists style_config_before_update on public.style_config;
create trigger style_config_before_update
  before update on public.style_config
  for each row
  execute function public.style_config_guard_update();
