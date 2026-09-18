-- F1 开场标签（2026-09-19）：把「你希望它怎么给你讲？」换成三块可点的标签。
--
-- ## 为什么换
--
-- 那一问的前提是「用户能内省到自己的讲解偏好并描述出来」，而这个前提在偏好研究里
-- 被直接否定过（Nisbett & Wilson 1977）；更要紧的是它与产品前提自相矛盾——
-- 用户要是能精确描述出想要的讲法，他就不需要这个产品。
-- 设计与理由的正本在 `web/lib/style/taste.ts` 的模块说明，这里不复制。
--
-- ## 这份迁移加什么
--
-- ⓵ `style_config` 三列：`taste_tags` / `tone` / `field`
-- ⓶ 两个事件类型：`style_taste_shown` / `style_taste_chosen`
-- ⓷ `schema_version` → `f1-events-6`
--
-- ⚠️ **部署顺序**：本文件必须先在 SQL Editor 跑完，再让平台部署对应构建。
-- 反了的话新构建写 `tone` 会被 42703 拒掉，而**主链路照常返回 200**（埋点/写库失败
-- 不打断讲解，口径 F42）——这个错不会以「服务挂了」的样子出现。

-- ---------------------------------------------------------------- ⓵ style_config

-- 原始选择。**存它是为了事后能问「哪些标签被点得最多」**，那是改标签表的唯一依据。
-- null = 这个用户没走过标签页（老用户、或直接从设置页改的）。空数组 = 走过但一个没选，
-- 两者含义不同，所以**不给默认值**（同 F50：null 有含义的字段不许被默认值抹平）。
alter table public.style_config add column if not exists taste_tags text[];

-- 生效的语气。**它是配置，不是记录**：初值由标签算出来，之后用户可以在设置页直接改，
-- 与四条轴同一处置。默认 'neutral' 与现有样例库的生成条件一致（样例都是中性语气生成的）。
alter table public.style_config add column if not exists tone text not null default 'neutral';

-- 领域。决定成对比较用哪个领域的题目。null = 没选过。
alter table public.style_config add column if not exists field text;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'style_config_tone_check'
  ) then
    alter table public.style_config
      add constraint style_config_tone_check check (tone in ('spoken', 'neutral', 'academic'));
  end if;
end $$;

comment on column public.style_config.taste_tags is
  '开场标签页里**原始**点选的 tag_id（web/lib/style/taste.ts 的 BOARDS）。'
  'null = 没走过标签页；空数组 = 走过但没选。两者含义不同，不给默认值。'
  '⚠️ 由它算出来的「先验」**不入库**：先验是纯函数的输出（resolveTaste），'
  '存一份就是第二处出处，改了标签表两边会漂而不报错。';

comment on column public.style_config.tone is
  '生效的语气档：spoken / neutral / academic。初值由标签多数票算出（平票回落 neutral），'
  '之后用户可直接改——与四条轴同一处置。默认 neutral 与样例库的生成条件一致。';

comment on column public.style_config.field is
  '用户主学领域，决定成对比较用哪个领域的题目。null = 没选过（样例回落到默认题目）。';

-- ---------------------------------------------------------------- ⓶ 两个新事件
--
-- 分开两条而不是一条的理由与 `style_pair_shown` / `style_pair_chosen` 相同：
-- 只记最终结果的话，**弃流点**就没了——标签页有三块，用户在第几块走掉是要能读出来的。
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

    -- ---- 2026-09-19 开场标签的两个 ----
    -- `board` / `tag_ids`：这一块展示了哪些标签（标签表会改，事后要能按当时那一版读）。
    when 'style_taste_shown'
      then array['board', 'tag_ids']
    -- `field` **刻意不进清单**：它可能是 null（没选），而 null 有含义（口径 F50）。
    -- `priors` 是算出来的，进事件是为了事后能核「先验有没有真的改变提问顺序」。
    when 'style_taste_chosen'
      then array['tags', 'tone', 'priors', 'total_ms']

    -- ---- F3 接线阶段 6 的四个讲解事件（原样；gap_* 刻意不进）----
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

    -- ---- 阶段 6 第一批：上传页的两个 ----
    when 'deck_uploaded'
      then array['filename', 'source_format', 'page_count', 'usable_page_count',
                 'verdict', 'slice_count', 'embedded_count', 'skipped_pages',
                 'duration_ms']
    when 'deck_upload_failed'
      then array['failure_kind', 'filename', 'bytes']

    -- ---- F4（原样）----
    when 'confusion_mark_created'
      then array['round_id', 'slice_ids', 'mark_count_after']
  end;
$$;

comment on function public.f1_event_required_keys(text) is
  'F1/F3/F4 埋点：每个 event_type 的必需 payload 字段（口径 F36）。'
  '返回 NULL 表示未知 event_type —— 事件类型的取值域与本表同一处出处，不另立 CHECK。'
  '⚠️ null 有含义的字段不在清单里（口径 F50）：all_activated_passed / '
  'first_token_latency_ms / gap_verdict / gap_cost_usd / deck_upload_failed.page_count / '
  'deck_uploaded.restricted_pages_excluded（null = 未检查，F69）/ '
  'style_taste_chosen.field（null = 没选领域）。'
  'f1_event_payload_ok 把 JSON null 判成「没有」，进了清单会让一条完全正确的'
  '事件被拒掉，而被拒的事件是静默消失的。'
  '⚠️ 「键在」不等于「有内容」：空数组通过 f1_event_payload_ok。';

-- ---------------------------------------------------------------- ⓷ schema_version
--
-- 事件类型由 12 个变为 14 个。不抬版本的话，`f1-events-5` 会同时盖住
-- 「有标签页」与「没有标签页」两段时期，而 onboarding 完成率正要按这个分组读。
alter table public.style_events alter column schema_version set default 'f1-events-6';

-- ---------------------------------------------------------------- ⓸ 分臂：新增 'taste'
--
-- A5 的对照臂原本是「用一段话描述你想要的讲法」。**那个臂的前提被否掉了**
-- （用户能描述出偏好 —— 见 taste.ts 的模块说明），所以它被换成标签流程。
--
-- **不改已有行的 f1_arm**（口径 F10，改了抛 23514），也不该改：
-- `f1_arm = 'text'` 的用户**确实**走的是文字描述那一版，把它改写成 'taste'
-- 是在篡改一段真实发生过的历史，而按臂分组的三个预登记指标正要读这段。
-- 所以加第三个取值，只发给**新注册**用户；老行原样留着，`/onboarding` 仍把它们送去旧页面。
--
-- 于是 A5 变成三臂并存的形态：
--   pairwise（只有成对比较，对照）· taste（标签 + 成对比较，2026-09-19 起的新臂）
--   text（历史：一段话描述，不再分配）
alter table public.style_config drop constraint if exists style_config_f1_arm_check;
alter table public.style_config
  add constraint style_config_f1_arm_check check (f1_arm in ('pairwise', 'text', 'taste'));

create or replace function public.f1_assign_arm(uid uuid)
returns text
language sql
immutable
as $$
  select case
    when substr(md5(uid::text), 1, 1) in ('0','1','2','3','4','5','6','7')
      then 'pairwise'
    else 'taste'
  end;
$$;

comment on function public.f1_assign_arm(uuid) is
  'F1 AB 分组（口径 F08）。确定性、不依赖 hashtext 的跨版本稳定性。'
  '2026-09-19：对照臂由 text（一段话描述）换成 taste（标签 + 成对比较）——'
  '前者的前提「用户能描述出自己的偏好」被否掉了。**已有行不回改**（F10）：'
  'text 那批人确实走过那一版，改写它等于篡改 A5 要读的那段历史。';
