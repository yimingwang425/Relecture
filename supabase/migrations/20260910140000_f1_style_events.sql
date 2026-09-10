-- F1 阶段 4 · 埋点事件表 style_events
-- 日期：2026-09-10　口径见 f1_samples/README.md 的 F35 / F36 / F37 / F38
--
-- 沿用 20260907120000_f1_style_config.sql 的写法：显式收权 → 按需授予 → 开 RLS →
-- 一条策略一句理由。本文件同样是**唯一 DDL 出处**，生产（SQL Editor）与本地反向
-- 验证（supabase/verify_rls_local.mjs）执行同一份文本。
--
-- ---------------------------------------------------------------- 一张表，不是五张（口径 F36）
--
-- 五个事件共享 user_id / event_type / arm / session_id / created_at，RLS 策略与
-- cascade 完全一样。拆五张表意味着同一条 RLS 写五遍、cascade 断言写五遍，
-- **而它们必然一起改**——这正是 F13「同一份东西多处出处必然漂」的形态。
--
-- 代价说清楚：`payload` 是 jsonb，**没有 schema 约束**，漏一个字段不会报错。
-- 所以每个 event_type 的必需字段配了一条会红的护栏（下面的
-- `f1_event_required_keys` + `f1_event_payload_ok`）。少了它，jsonb 就是个
-- 什么都能塞的口袋，而「塞漏了」要到分析时才发现，那时数据已经采完了。
--
-- ---------------------------------------------------------------- user_id 存真 uuid（口径 F35）
--
-- PRD §13.0 原文要求事件表存 hashed user_id。**2026-09-10 改掉**：库内存真 uuid、
-- 挂外键到 auth.users、on delete cascade；hash 放到**导出层**（对外导出时才 hash）。
--
-- §13.0 的目的是防止事件数据被关联到个人，而 §12.1「注销即清空」是更强的保障——
-- 数据都没了，hash 与否无所谓。存 hash 却挂不上外键、cascade 失效，等于
-- **拿一个弱保障换掉一个强保障**。F14 刚把「注销即清空」从一条从未被测量过的承诺
-- 变成有断言的，不能在事件表上把它丢掉。
--
-- 连带：PRD §13.0 措辞已改写，记录见 docs/F1/F1进度.md §六。

-- ---------------------------------------------------------------- 必需字段表

-- **五个事件类型的取值域也住在这里**，不另立一条 `check (event_type in (...))`。
-- 两处写同一份清单就是 F13 那个坑的第 N 个入口；这里返回 NULL 即表示「不认识这个
-- event_type」，下面的 `f1_event_payload_ok` 据此判假，未知类型直接被拒。
--
-- 字段清单的依据是任务书 §4.2 那张表。三处值得单独说：
--
--   · `left_variant` 在 shown 与 chosen **两个事件里都必需**。阶段 3 把左右位置
--     随机化了，位置效应要能事后分离，就必须知道「这一步谁在左」——
--     少了它，阶段 3 那次随机化白做（F1进度 §五「已知未控制」里点名的那条）。
--   · `chosen_variant` 存**档位**不存左右。位置随机之后只有档位是稳定的。
--   · `counts_as_config_edit` 与 `config_changed` 分开必需（口径 F06）：
--     「例子没懂」不改配置**且不进「配置修改频率」的分子**，两件事两个字段。
--     只记 config_changed 的话，F06 的后半句在数据里算不出来。
create or replace function public.f1_event_required_keys(event_type text)
returns text[]
language sql
immutable
as $$
  select case event_type
    when 'style_pair_shown'
      then array['step', 'axis', 'pair_id', 'left_variant']
    when 'style_pair_chosen'
      then array['step', 'axis', 'pair_id', 'left_variant', 'chosen_variant', 'dwell_ms', 'hidden_ms']
    when 'style_onboarding_completed'
      then array['config', 'steps_completed', 'total_ms', 'prompt_template_version']
    when 'style_text_mapped'
      then array['call', 'axes_from_text', 'axes_from_default', 'ok', 'prompt_template_version']
    when 'style_config_changed'
      then array['before', 'after', 'trigger', 'config_changed', 'counts_as_config_edit', 'prompt_template_version']
  end;
$$;

comment on function public.f1_event_required_keys(text) is
  'F1 埋点：每个 event_type 的必需 payload 字段（口径 F36）。'
  '返回 NULL 表示未知 event_type —— 事件类型的取值域与本表同一处出处，不另立 CHECK。';

-- 「必需」判成三件事，都不报错地漏掉过：
--   1. 键不存在        （payload -> k 为 SQL NULL）
--   2. 键在、值是 JSON null（前端拿到 undefined 序列化成 null 的常见形态）
--   3. 键在、值是空字符串   （变量名拼错时最常见的形态）
-- 三种在 jsonb 里长得都不一样，但对分析来说都是「这个字段没有」。
create or replace function public.f1_event_payload_ok(event_type text, payload jsonb)
returns boolean
language plpgsql
immutable
as $$
declare
  keys text[];
  k    text;
  v    jsonb;
begin
  keys := public.f1_event_required_keys(event_type);
  if keys is null then
    return false;  -- 未知 event_type
  end if;
  if payload is null or jsonb_typeof(payload) <> 'object' then
    return false;
  end if;
  foreach k in array keys loop
    v := payload -> k;
    if v is null or jsonb_typeof(v) = 'null' then
      return false;
    end if;
    if jsonb_typeof(v) = 'string' and length(v #>> '{}') = 0 then
      return false;
    end if;
  end loop;
  return true;
end;
$$;

comment on function public.f1_event_payload_ok(text, jsonb) is
  'F1 埋点：payload 必需字段的护栏（口径 F36）。缺键 / JSON null / 空字符串都判假。';

-- ---------------------------------------------------------------- 表

create table if not exists public.style_events (
  event_id    uuid primary key default gen_random_uuid(),
  user_id     uuid        not null references auth.users (id) on delete cascade,
  event_type  text        not null,
  arm         text        not null,
  session_id  uuid        not null,
  payload     jsonb       not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),

  -- PRD §13.0：**`schema_version` 必须埋。** 口径一变历史数据就不可与新数据合并，
  -- 而没有版本字段就无法区分它们——合并会得到一个看起来正常的错数。
  --
  -- 值由**数据库的 default 给**，客户端一个字都不发。这不是省事：
  -- 让客户端发就等于把版本号变成两处出处（TS 一份、迁移一份），
  -- 而那两份漂开的表现恰好是「版本号说没变，其实变了」——比没有版本号更糟。
  -- 改 `f1_event_required_keys` 的那次迁移顺手 `alter column ... set default` 到下一版；
  -- 已有的行保留它们当时那个值，这正是要的。
  schema_version text not null default 'f1-events-1',

  -- 口径 F36：payload 的必需字段。**这一条兼做 event_type 的取值域校验**
  -- （未知类型 → required_keys 返回 NULL → 本函数返回 false）。
  constraint style_events_payload_check
    check (public.f1_event_payload_ok(event_type, payload)),

  -- arm 不信任客户端。它由 user_id 确定（口径 F08，f1_assign_arm 是 immutable 的
  -- 纯函数），所以这里直接算一遍比对——**用户改不了自己在事件里的臂**。
  -- 少了它，任何人都能往事件里写 `arm: "text"` 把 AB 分组的读数搅浑，
  -- 而 A5 的三个预登记指标全部按臂分组。
  constraint style_events_arm_matches_assignment
    check (arm = public.f1_assign_arm(user_id))
);

comment on table public.style_events is
  'F1 埋点事件，一行一次事件（口径 F36：一张表 + event_type 列 + payload jsonb）。'
  'user_id 是**真 uuid**，随账号注销级联删除（口径 F35 / PRD §12.1）；'
  'hash 只发生在对外导出时。客户端权限：**只能 insert 自己的事件**，'
  '不能 select / update / delete —— 读事件是分析行为，走 service_role。';

comment on column public.style_events.created_at is
  '**数据库服务器时间**（insert 时刻），不是客户端时间。PRD §13.0 要求写明是哪一种。'
  '它与「事件真正发生的时刻」差一个网络往返；步内时长不靠它，靠 payload 里的 '
  'dwell_ms / hidden_ms（客户端 performance.now() 差值，口径 F38）。';

comment on column public.style_events.schema_version is
  'payload 的口径版本（PRD §13.0）。由本列的 default 给，客户端不发——'
  '让客户端发就是第二处出处，而漂开的表现是「版本号说没变其实变了」。';

-- ---------------------------------------------------------------- §13.0 通用字段里刻意不建的三列
--
-- PRD §13.0 的通用字段还列了 `course_id`/`deck_id`、`app_version`、`client_type`。
-- 本表**不建**，理由各不相同，都不是「忘了」：
--
--   · `course_id` / `deck_id` —— F1 的五个事件全部发生在 onboarding 与设置页，
--     **与任何课件无关**（样例是自写合成课件，口径 F02）。一个恒 null 的列和
--     没有这个列，在数据里等价；差别是恒 null 的列会让读的人以为「可以按课分组」，
--     然后得到一张全是空值的表。**触发条件**：事件开始涉及具体课件时（F2）。
--   · `client_type` —— v1 只有 web 一个客户端。同上，恒 'web' 不携带信息。
--     **触发条件**：出现第二个客户端。
--   · `app_version` —— 这一条**有价值但没做**，如实登记：它要把
--     `VERCEL_GIT_COMMIT_SHA` 从服务端一路传到三个页面的客户端组件。
--     当前「线上是哪个构建」由响应头 `x-build-commit` 回答（口径 F17）。
--     **触发条件**：内测期间发生多次部署，需要按构建切分事件时。
--
-- 登记在这里而不是只在脑子里，是因为「没建」与「忘了建」在表结构上长得一模一样。

comment on column public.style_events.session_id is
  '**一次页面加载 = 一个 session**，由服务端页面生成后作为 prop 传给客户端组件。'
  '这是**占位口径**：PRD §3.4.5 的会话切分规则至今未定，内测第 1 周数据落盘后再立。'
  '在服务端生成是为了避开 React #418（客户端组件 SSR 与 hydrate 各渲染一次，'
  '渲染期取随机数两侧会分叉），与阶段 3 的 left_variant 同一条处置。';

create index if not exists style_events_user_created_idx
  on public.style_events (user_id, created_at);

-- ---------------------------------------------------------------- 权限

-- 与 style_config 同一套做法：显式收权后再按需授予，不依赖 public schema 的默认授权。
revoke all on public.style_events from anon, authenticated;

-- **只给 insert。** 读事件是分析行为，走 service_role，不给客户端：
-- 给了 select，任何登录用户都能把自己的行为流拉下来（无害），但更重要的是
-- 它会让「事件表能不能被前端读」变成一个要长期维护的答案，而我们不需要这个能力。
grant insert on public.style_events to authenticated;

-- ---------------------------------------------------------------- RLS

alter table public.style_events enable row level security;

-- 只有 insert 一条策略。**没有 select / update / delete 策略**：
--   · 没有 select：RLS 开着且无 select 策略 → 任何 select 返回 0 行；
--   · 没有 update：事件是既成事实，改事件等于改历史；
--   · 没有 delete：注销走 auth.users 的 on delete cascade（同 style_config，口径 F14）。
-- 权限层已经收走了这三个动作，策略层再空一次——两层，任一层被误改还有另一层
-- （与 20260910090000 收回 style_config 的 insert 是同一个做法）。
drop policy if exists style_events_insert_own on public.style_events;
create policy style_events_insert_own on public.style_events
  for insert to authenticated
  with check (user_id = auth.uid());

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：2026-09-10　执行结果：成功，未回滚
--     执行时表的状态：**新建空表**（本迁移就是它的第一次创建）
--
-- ⚠️ 按口径 F34，执行成功只证明 **DDL 能执行**。表建好那一刻必然是空的，
-- 所以「表里没有坏事件」在那一刻**不携带信息**。
--
-- ---------------------------------------------------------------- 之后发生的事（2026-09-11）
--
-- **与阶段 3 那两条迁移不同：这张表在生产上真的跑过数据。** 阶段 4 人工验收
-- 用两个邀请账号走完了全部五组，所以下面这几件事是**生产观测**，不是本地推断：
--
--   · 三条护栏各拦了一次真请求：缺 `left_variant` → 23514（`style_events_payload_check`）；
--     伪造 arm → 23514（`style_events_arm_matches_assignment`）；
--     客户端 select → 42501 permission denied（**不是空数组**）。
--     每条都配了允许控制：补上字段后 201。
--   · **cascade 在生产上被观测到**（PRD §12.1 在事件表上的第一次真实观测）：
--     删掉账号 a5 → 它的 13 条事件与配置行一起归 0，而账号 a2 的 19 条**一条没动**。
--     ⚠️ 后半句是前半句的控制项——少了它，「整表被清空」也会让前半句为绿。
--   · 写入失败的补偿（口径 F42）也在生产上验过：断网时 `kind:"transient"`、
--     attempt 1→5 递增、`pending=1`；恢复网络后 `pending=0` 且那条事件最终落库。
--
-- **验收账号已按先例全部删除，两张表回到空表**（测试行会同时污染 AB 分组计数与
-- onboarding 完成率的分母，而它们看起来和真实用户一模一样）。所以：
--
--   **机制已在生产被观测；存量从此刻起重新回到「不携带信息」**（口径 F34）。
--   两者各记各的，不互相顶替——「护栏拦过一次真请求」不等于「以后表里不会有坏行」。
