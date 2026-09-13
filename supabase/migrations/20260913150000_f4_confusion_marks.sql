-- F4 · 跨会话记忆：「这部分没看懂」的持久化 + 讲解轮 slice_ids 的非空护栏
-- 日期：2026-09-13　口径见 f1_samples/README.md 的 F54–F66
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⓵ `confusion_marks` —— 一次「没看懂」点击 = 一行
-- ═══════════════════════════════════════════════════════════════════════════
--
-- **锚点是切片，不是概念**（口径 F54）。两个消费方（讲解 prompt 注入、
-- 护栏指标「重复提问率」）都在检索的下游，手里只有检索的输出，
-- 而检索按切片进出。用概念做锚点要在写入侧与读取侧各加一次映射，
-- **每一次都会漏，而漏掉的那部分与「用户没标记过」长得一模一样**。
--
-- **表名刻意不叫 `concept_marks`**（口径 F55）：它锚的不是概念。
-- 名字会变成一条没人核对的断言 —— 半年后有人按名字去写「按概念聚合」的查询，
-- 而那个聚合永远算不对。
--
-- ---------------------------------------------------------------- slice_ids 为什么是 text[] 而不是 uuid[]
--
-- ⚠️ **F4 方案 §3.1 写的是 `uuid[]`，这里改成 `text[]`，理由是切片的身份本来
-- 就不是一个 uuid**（口径 F66）。
--
-- `slices` 的主键是**两段**：`(document_id uuid, slice_id text)`，
-- 而 `slice_id` 是**文档内局部 id**（`'s001'`）。
-- `20260911100000_f2_corpus.sql` 里那条注释写着「跨文档不唯一」，
-- 2026-09-12 的真库观测坐实了它：**一门课的两份讲义都有 `s001`**。
--
-- 于是 `uuid[]` 只有两条出路，都是坏的：
--   · 存 `document_id` —— 粒度变成「整份讲义」，标记一页会让整份讲义
--     以后每次命中都注入。那就是**恒触发**，而恒触发的限定语最难发现：
--     输出看起来完全正常，只是每一轮都多讲一句。
--   · 只存 `slice_id` —— 它不是 uuid，压根写不进 uuid[]；硬转成 text 又会让
--     A 讲义的 s001 与 B 讲义的 s001 撞成同一个身份 —— **误触发，且不可见**。
--
-- 所以一段字符串键 `'<document_id>:<slice_id>'`，唯一的拼法出处是
-- `api/api/marks.py` 的 `slice_key()`。text[] 上的 `&&` 与 GIN 索引一样用。
--
-- ---------------------------------------------------------------- 不存 course_id（口径 F63）
--
-- 切片经 `documents` 可 join 出课程，而**检索本就按课隔离**（口径 F47：
-- `match_slices` 的作用域是一门课），所以交集判定天然不跨课。
-- 冗余一列的唯一强理由（让 ANN 索引按课裁剪）在 v1 不成立 —— 同接线期决策。
create table if not exists public.confusion_marks (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,

  -- 讲解轮标识，**幂等键**（口径 F65）。由服务端在 `/explain` 里生成并随讲解
  -- 响应返回；一次 `/explain` = 一轮 = 一个 round_id，轮内的重生成不新开一个
  -- （重生成不计入轮次，轮的定义是「用户可见的一次输出」，PRD §13.0）。
  round_id   uuid not null,

  -- 本轮检索命中的**全部**切片，一段字符串键（见上面那节）。
  -- **不许为空**：一条空的 mark 与任何一轮都不会有交集，它是一行死数据，
  -- 而死数据与「标记没生效」在库里长得一模一样。写入侧（`POST /marks`）
  -- 也拒空，两层。
  slice_ids  text[] not null,

  -- 本轮提问原文。**存下来，但 v1 不注入**（口径 F59）：上次的问题与本次未必
  -- 相关，而模型判断不了二者是否相关 —— 要它区分等于要它报告一个它观测不到的量
  -- （同 F3b 的 G02）。存着是为了内测后改主意时不必重跑数据。
  --
  -- ⚠️ 与 `style_events.explain_turn.question` 是同一处张力（PRD §12.2 / 口径 F32：
  -- F1 的对照臂刻意不把描述原文写进事件）。**触发条件与那条相同**：
  -- 对外开放前的隐私政策逐条对齐时，两处摆在一起复核一次。
  question   text not null,

  created_at timestamptz not null default now(),

  -- 幂等（守卫六）：同一轮点两次，表里仍然一行。
  -- 前端**不需要区分**首次与重复 —— 服务端两条路都返回 200。
  constraint confusion_marks_user_round_uniq unique (user_id, round_id),

  -- 空数组在 `array_length(x, 1)` 下是 NULL，不是 0 —— 所以判 `is not null`。
  constraint confusion_marks_slice_ids_nonempty
    check (array_length(slice_ids, 1) is not null)
);

comment on table public.confusion_marks is
  'F4 「这部分没看懂」。一次点击一行（口径 F54：锚到切片，不做概念抽取）。'
  '　⚠️ **表名不叫 concept_marks 是刻意的**（F55）：它锚的不是概念，'
  '按名字去写「按概念聚合」的查询永远算不对。'
  '　⚠️ **slice_ids 是 text[] 不是 uuid[]**（F66）：切片身份是两段 '
  '(document_id, slice_id)，后者是文档内局部 id（一门课两份讲义都有 s001，'
  '2026-09-12 真库观测）。这里存 document_id:slice_id 的拼接键，'
  '唯一拼法出处是 api/api/marks.py 的 slice_key()。'
  '　⚠️ **不存 course_id**（F63）：切片经 documents 可 join 出课程，'
  '而检索本就按课隔离，交集判定天然不跨课。'
  '　⚠️ **不授 delete**（F60）：v1 不做取消标记。误标的代价是「多换一次讲法」，'
  '方向单向可解释。触发条件：内测出现误标反馈时，届时加一条 delete 策略。';

comment on column public.confusion_marks.round_id is
  '讲解轮标识（口径 F65）。服务端在 /explain 里生成并随响应返回，'
  '客户端原样带回来 —— 它同时是幂等键（unique (user_id, round_id)）。'
  '⚠️ 客户端可以编一个，但编出来的只会污染他自己的注入，'
  'RLS 让它够不着别人的行。';

-- 交集查询（`slice_ids && $1`）是这张表**唯一**的读取形态，那就给它 GIN。
-- 默认的 array_ops 就支持 `&&`，不需要额外的 opclass。
create index if not exists confusion_marks_slice_ids_gin
  on public.confusion_marks using gin (slice_ids);

-- 每次注入判定都带 `user_id = auth.uid()`（RLS 加的），行数一多就靠它裁剪。
create index if not exists confusion_marks_user_idx
  on public.confusion_marks (user_id, created_at);

-- ---------------------------------------------------------------- 权限
--
-- 与 style_config / style_events 同一套：**显式收权后再按需授予**，
-- 不依赖 public schema 的默认授权（那份默认里有 delete）。
revoke all on public.confusion_marks from anon, authenticated;

-- **只给 select 与 insert**（口径 F60）。
--   · 没有 update：一条 mark 是一次既成的点击，改它等于改历史（同 style_events）。
--   · 没有 delete：v1 不做取消标记。注销清空走 auth.users 那条 cascade。
-- select **要给**（与 style_events 不同）：注入判定是**以用户身份**做的一次
-- `&&` 查询，服务端不持有 service_role key（口径 F40）。
grant select, insert on public.confusion_marks to authenticated;

-- ---------------------------------------------------------------- RLS
alter table public.confusion_marks enable row level security;

-- 两条策略，动作分开写而不是 `for all`：`for all` 会连同 update / delete
-- 一起给出策略，而那两个动作我们**刻意不要**。权限层已经收走了它们，
-- 策略层再空一次 —— 两层，任一层被误改另一层还在
-- （同 20260910090000 收回 style_config insert 的做法）。
drop policy if exists confusion_marks_select_own on public.confusion_marks;
create policy confusion_marks_select_own on public.confusion_marks
  for select to authenticated
  using (user_id = auth.uid());

drop policy if exists confusion_marks_insert_own on public.confusion_marks;
create policy confusion_marks_insert_own on public.confusion_marks
  for insert to authenticated
  with check (user_id = auth.uid());

-- ---------------------------------------------------------------- 让这张表自己说得出自己的形状
--
-- 口径 F53：**验收要确认的东西，系统必须能自己答出来。**
--
-- 「迁移到底应用了没有」在真库上此前只有一条路：去 SQL Editor 手查
-- `pg_indexes` / `pg_policy` / `pg_constraint`。而 2026-09-13 的人工验收刚证明
-- 手查会查错地方，然后把「查错了」读成「功能没做」。
--
-- 服务端**不持有 service_role key**（口径 F40），PostgREST 又读不了 pg_catalog，
-- 所以验收脚本自己答不了这个问题——除非库里有一个它够得着的出口。这就是它。
--
-- ⚠️ **只读、只谈这一张表、返回一组写死的事实。** 它不接参数（接了就成了一个
-- 「以 definer 身份查任意目录」的入口）；返回的是索引名、策略名与动作、
-- 外键的删除规则、授权动作——这些东西本来就写在这个仓库的迁移文件里。
create or replace function public.f4_confusion_marks_shape()
returns jsonb
language sql
stable
security definer
set search_path = public, pg_catalog, pg_temp
as $$
  select jsonb_build_object(
    'table_exists',  to_regclass('public.confusion_marks') is not null,
    'rls_enabled',   (select relrowsecurity from pg_class
                      where oid = 'public.confusion_marks'::regclass),
    'columns',       (select jsonb_object_agg(column_name, data_type)
                      from information_schema.columns
                      where table_schema = 'public' and table_name = 'confusion_marks'),
    'indexes',       (select coalesce(jsonb_object_agg(i.relname, am.amname), '{}'::jsonb)
                      from pg_index x
                      join pg_class i on i.oid = x.indexrelid
                      join pg_am    am on am.oid = i.relam
                      where x.indrelid = 'public.confusion_marks'::regclass),
    'policies',      (select coalesce(jsonb_object_agg(polname, polcmd::text), '{}'::jsonb)
                      from pg_policy
                      where polrelid = 'public.confusion_marks'::regclass),
    'grants',        (select coalesce(jsonb_agg(distinct privilege_type), '[]'::jsonb)
                      from information_schema.role_table_grants
                      where table_schema = 'public' and table_name = 'confusion_marks'
                        and grantee = 'authenticated'),
    -- 'c' = cascade。这是「注销即清空」在这张表上的那一半（PRD §12.1 / 口径 F35）。
    'user_fk_delete_rule',
                     (select confdeltype::text from pg_constraint
                      where conrelid = 'public.confusion_marks'::regclass
                        and contype = 'f'
                        and confrelid = 'auth.users'::regclass
                      limit 1),
    'constraints',   (select coalesce(jsonb_object_agg(conname, contype::text), '{}'::jsonb)
                      from pg_constraint
                      where conrelid = 'public.confusion_marks'::regclass
                        and contype in ('c', 'u')),
    -- 事件那一侧：本轮新增的两样东西在不在。
    'event_types_known', (select count(*)::int from unnest(array[
                            'style_pair_shown', 'style_pair_chosen',
                            'style_onboarding_completed', 'style_text_mapped',
                            'style_config_changed', 'explain_turn',
                            'style_review_result', 'regeneration_triggered',
                            'retrieval_executed', 'deck_uploaded',
                            'deck_upload_failed', 'confusion_mark_created']) t
                          where public.f1_event_required_keys(t) is not null),
    'slice_ids_check', (select count(*)::int from pg_constraint
                        where conrelid = 'public.style_events'::regclass
                          and conname = 'style_events_slice_ids_ok')
  );
$$;

comment on function public.f4_confusion_marks_shape() is
  'F4 · 让验收脚本自己答出「迁移应用了没有」（口径 F53）。只读、无参数、'
  '只谈 confusion_marks 与本轮那条 style_events CHECK。'
  '存在的理由：服务端不持有 service_role key（F40），PostgREST 读不了 pg_catalog，'
  '于是「表/索引/策略/cascade 在不在」此前只能去 SQL Editor 手查 —— '
  '而 2026-09-13 的人工验收刚证明手查会查错地方，然后读成「功能没做」。';

revoke all on function public.f4_confusion_marks_shape() from public;
grant execute on function public.f4_confusion_marks_shape() to authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
-- ⓶ 新事件 `confusion_mark_created` + 讲解轮 `slice_ids` 的非空护栏
-- ═══════════════════════════════════════════════════════════════════════════
--
-- ---------------------------------------------------------------- 为什么要这条非空护栏
--
-- F4 方案 §二：护栏指标「重复提问率」的分子**不挂在用户点击上**（口径 F62），
-- 它来自「同一会话内同一 slice_id 被重复命中的次数」，
-- 而那个数的**唯一数据来源**是 `retrieval_executed.payload.slice_ids`。
-- 「漏了就再也补不回来」—— 事后没有任何办法重建它。
--
-- `slice_ids` 早就在必需字段清单里（`20260912120000`），但 `f1_event_payload_ok`
-- 只判「键在不在」：**一个空数组 `[]` 完全通过它**（不是 SQL NULL、不是 JSON null、
-- 不是空字符串）。于是「检索命中了 5 片，而事件里的 slice_ids 是空的」
-- 在本条之前没有任何东西会红。
--
-- ⚠️ **空检索是合法状态，不能一律拒。** `build_user_turn` 里那条
-- 「【本轮没有检索到任何课件片段】」就是它 —— 空检索是**显式失败**，
-- 比答非所问安全（PRD §6.2），它更该被采集到。把它一并拒掉就是 F50 那条教训
-- （「最该被采集的那一类进不了库」）的复发。
--
-- 所以判据带一个例外：`slice_count = 0` 时允许空。两者由同一次
-- `build_explain_events()` 一起写出，**同真同假**，所以这个例外不会被拿来绕过。
--
-- ⚠️ 被这条拒掉的事件**不是静默消失的**：这四个讲解事件由服务端发，
-- `/explain` 把 `record_events()` 的结果原样放进响应的 `events` 字段（口径 F42），
-- 失败会带着 23514 出现在那里。这正是它可以做成一条 CHECK、
-- 而上传那两个字段只能留在 Python 侧的差别。
create or replace function public.f4_event_slice_ids_ok(event_type text, payload jsonb)
returns boolean
language plpgsql
immutable
as $$
declare
  ids jsonb;
begin
  if event_type not in ('retrieval_executed', 'confusion_mark_created') then
    return true;
  end if;

  ids := payload -> 'slice_ids';
  -- 键缺失 / JSON null 不在这里判 —— 那是 f1_event_payload_ok 的活。
  -- 在这里再判一次会让同一个失败被两条 CHECK 拦，而先报哪一条不确定。
  if ids is null or jsonb_typeof(ids) = 'null' then
    return true;
  end if;

  if jsonb_typeof(ids) <> 'array' then
    return false;   -- 不是数组 = 构造处写错了形状，那是硬错误
  end if;

  if jsonb_array_length(ids) > 0 then
    return true;
  end if;

  -- 空数组：只有「本轮真的一片都没检索到」时才合法。
  -- confusion_mark_created 没有这个例外 —— 写入侧拒空 mark（见上面那条 CHECK）。
  --
  -- ⚠️ **用 jsonb 相等比，不用 `(payload ->> 'slice_count')::int`。** 两个理由：
  --   · 那个转换在 slice_count 不是数字时抛 **22P02** —— 一条 CHECK 应当
  --     判假，而不是让 insert 以一个「数据库坏了」的错误码失败；
  --     `add constraint` 校验存量时它甚至会让整条迁移以 22P02 而不是 23514 失败，
  --     而那两个错误码指向完全不同的排查路径。
  --   · 转换会让 JSON 字符串 `"0"` 也算数，而 Python 侧那份镜像
  --     （`events._slice_ids_problem` 的 `payload.get("slice_count") == 0`）
  --     不认字符串。**两份镜像在同一组输入上必须给同一个答案**（口径 F33），
  --     `tests/test_marks.py::test_python_and_sql_agree_on_the_non_empty_rule`
  --     逐条比对的就是这个。
  --
  -- ⚠️ **`is not distinct from`，不是 `=`。** `slice_count` 这个键缺失时
  -- `payload -> 'slice_count'` 是 SQL NULL，而 `NULL = '0'` 求值为 **NULL**，
  -- 函数于是返回 NULL —— 而**结果为 NULL 的 CHECK 是通过的**。
  -- 那会让「空 slice_ids 且没有 slice_count」这一行悄悄写进去，
  -- 正是本条护栏要挡的东西。`is not distinct from` 只出 true/false。
  return event_type = 'retrieval_executed'
     and payload -> 'slice_count' is not distinct from '0'::jsonb;
end;
$$;

comment on function public.f4_event_slice_ids_ok(text, jsonb) is
  '讲解轮事件的 slice_ids 非空护栏（口径 F62 的数据来源）。'
  '与 f1_event_payload_ok 分工：那个管「键在不在」（空数组照样通过），'
  '这个管「有命中就必须有身份」。空检索（slice_count = 0）是例外 —— '
  '它是合法状态且更该被采集，一并拒掉就是 F50 那条教训的复发。';

alter table public.style_events drop constraint if exists style_events_slice_ids_ok;
alter table public.style_events
  add constraint style_events_slice_ids_ok
  check (public.f4_event_slice_ids_ok(event_type, payload));

-- ---------------------------------------------------------------- 必需字段：加第十二个类型
--
-- `confusion_mark_created`（F4 方案 §3.4）。三个字段：
--   · `round_id` —— 与 confusion_marks 那一行对得上，也是「一轮标记几次」的分母；
--   · `slice_ids` —— 标的是哪几片。**与那一行里存的是同一份**；
--   · `mark_count_after` —— 这个用户至此累计多少条。它是「标记是不是越用越少」
--     这个问题的唯一来源；事后从事件表数行数算不出来，因为**事件可能写失败**
--     （口径 F42：写不进去不打断主链路），而 marks 那一行仍在。
--
-- ⚠️ 仍然守 F50：null 有含义的字段不进清单。这三个都不可能是 null ——
-- 服务端在 mark 已经落库之后才发这条事件，三个值那时全部确定。
create or replace function public.f1_event_required_keys(event_type text)
returns text[]
language sql
immutable
as $$
  select case event_type
    -- ---- F1 阶段 4 的五个（原样）----
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

    -- ---- F3 接线阶段 6 的四个讲解事件（原样）----
    when 'explain_turn'
      then array['turn_index', 'question', 'usage', 'cost_usd', 'pricing_rates',
                 'latency_ms', 'accepted', 'regenerations',
                 'style_config_fingerprint', 'metric_set_id']
    when 'style_review_result'
      then array['dimensions', 'counted_dimensions']
    when 'regeneration_triggered'
      then array['attempt_index', 'failed_dimensions', 'final_accepted']
    when 'retrieval_executed'
      then array['top_k_scores', 'slice_ids', 'pages', 'latency_ms',
                 'embed_model', 'dimensions', 'ruleset_versions', 'backend']

    -- ---- 阶段 6 第一批：上传页的两个（原样）----
    when 'deck_uploaded'
      then array['filename', 'source_format', 'page_count', 'usable_page_count',
                 'verdict', 'slice_count', 'embedded_count', 'skipped_pages',
                 'restricted_pages_excluded', 'duration_ms']
    when 'deck_upload_failed'
      then array['failure_kind', 'filename', 'bytes']

    -- ---- F4：第十二个 ----
    when 'confusion_mark_created'
      then array['round_id', 'slice_ids', 'mark_count_after']
  end;
$$;

comment on function public.f1_event_required_keys(text) is
  'F1/F3/F4 埋点：每个 event_type 的必需 payload 字段（口径 F36）。'
  '返回 NULL 表示未知 event_type —— 事件类型的取值域与本表同一处出处，不另立 CHECK。'
  '⚠️ null 有含义的字段不在清单里（口径 F50）：all_activated_passed / '
  'first_token_latency_ms / gap_verdict / deck_upload_failed.page_count。'
  'f1_event_payload_ok 把 JSON null 判成「没有」，进了清单会让一条完全正确的'
  '事件被拒掉，而被拒的事件是静默消失的。'
  '⚠️ 「键在」不等于「有内容」：空数组通过 f1_event_payload_ok。'
  'slice_ids 的非空由 f4_event_slice_ids_ok 单独管。';

-- ---------------------------------------------------------------- schema_version
--
-- payload 口径又变了（十一个类型 → 十二个，且多了一条 slice_ids 非空护栏），
-- 版本号跟着走。不抬的话 `f1-events-3` 会同时盖住两份口径
-- （§13.0：没有版本字段就无法区分它们，合并会得到一个看起来正常的错数）。
alter table public.style_events alter column schema_version set default 'f1-events-4';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：____________　执行结果：____________
--
-- ⚠️ **这一条迁移会拒掉已有的坏行吗？** `style_events_slice_ids_ok` 会 ——
-- `add constraint ... check` 默认校验存量。生产里若真有一条
-- `retrieval_executed` 命中非空而 `slice_ids` 为 `[]`，**本迁移会失败并回滚**。
-- 那不是麻烦，那正是想要的：它把「护栏指标的来源到底全不全」
-- 从一个查不出答案的问题变成一次明确的执行结果。失败时先看清：
--
--     select event_id, created_at, payload->'slice_count', payload->'slice_ids'
--     from public.style_events
--     where event_type = 'retrieval_executed'
--       and jsonb_typeof(payload->'slice_ids') = 'array'
--       and jsonb_array_length(payload->'slice_ids') = 0
--       and payload->'slice_count' is distinct from '0'::jsonb;
--
-- **跑迁移之前先记数**（同 cascade 那条「删之前先记数」）：不先记的话，
-- 迁移成功之后「没有坏行」与「本来就一行都没有」分不开。两句 SQL 在
-- `docs/F4/F4-P1执行记录.md` §六 ① 里，逐字可粘。
--
-- ⚠️ **部署顺序：本迁移先于那个构建**（仓库根 CLAUDE.md 那条）。
-- 反了的话 `confusion_mark_created` 会因为 `f1_event_required_keys` 还不认识它
-- 而 **23514 全部被拒**（未知 event_type → 返回 NULL → payload_ok 判 false），
-- 而 `POST /marks` **照常返回 200**（埋点失败不打断主链路，口径 F42）。
-- 更糟的一半：`confusion_marks` 表还不存在时，标记本身也写不进去，
-- 而前端看到的是一个 502 —— 两种失败混在一起，先跑迁移就都没有。
--
-- ⚠️ 口径 F34：DDL 执行成功只证明**它能执行**。RLS 真的挡过一次跨用户读、
-- cascade 真的删过一行、幂等真的拦过第二次提交，是另外的证据，各记各的：
--   · 本地机制（含反向控制）—— `supabase/verify_rls_local.mjs` 的
--     「confusion_marks（F4）」一节 + R17 / R18 / R19；
--   · 真库存量 —— `api/scripts/verify_f4_marks.py`。
