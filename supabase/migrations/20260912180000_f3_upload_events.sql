-- F1→F3 接线阶段 6 第一批 · 上传页的两个事件 + 一条闭集护栏
-- 日期：2026-09-12　口径见 f1_samples/README.md 的 F36 / F50 / F51 · PRD §13.0 / §13.1
--
-- 两个事件：`deck_uploaded`（成功）与 `deck_upload_failed`（失败）。
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⓵ 触发条件都满足了，两条一起记
-- ═══════════════════════════════════════════════════════════════════════════
--
-- **`deck_upload_failed`**：PRD §13.0「本批不做的事件」那张表里写着
--   触发条件 **「接前端与账号之后」**，理由是「失败分类**依赖前端的上传流程形态**」。
--   阶段 6 做上传页，触发。分类的取值域见 `api/api/upload_failures.py`。
--
-- **`deck_id` 那一列**：`20260912120000_f3_explain_events.sql` 里登记着
--   「**本阶段它恒为 NULL**：四个讲解事件没有一个天然只涉及一份讲义……
--   第一个真正填它的事件是上传侧的（deck_upload_failed / deck_uploaded），
--   而那要等上传页。**触发条件就是上传页**」。
--
--   **本迁移解掉它**：`deck_uploaded` 天然只涉及一份讲义，它填 `deck_id`。
--   ⚠️ 而 `deck_upload_failed` **仍然不填**，且这不是遗漏——
--   失败时**没有 document 行**（要么在建行之前就失败了，要么建行本身失败），
--   所以没有 id 可填。给它编一个（比如用文件名 hash）会让
--   「按 deck 看失败率」的查询把不同文件算成同一份。**null 在这里是事实。**
--
--   于是 `deck_id` 从「恒 NULL」变成「成功的上传填，其余为 null」，
--   那条登记可以收掉了——**但它的理由留着**（见那个文件的 comment）：
--   一个恒 null 的列会让读的人以为可以按它分组。现在它不恒 null 了。
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⓶ 一条新东西：**闭集护栏**（`f1_event_payload_ok` 管不了的那一半）
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `f1_event_payload_ok` 只回答「这个键在不在」。它挡不住**值写错**：
-- 一个拼错的 `unparseable`（多一个 e）键在、非空、类型对，**照样入库**，
-- 然后在分析时变成一个新类别——而它看起来完全正常，只是某一类的计数莫名其妙少了。
--
-- 这正是 F36 那笔交易（一张表 + jsonb，换掉五份 RLS）的代价还没付完的部分：
-- 「每个 event_type 的必需字段必须有会红的护栏」已经做了，
-- **而「闭集字段的取值域」也需要一条**。
--
-- ⚠️ **为什么另立一个函数 + 另一条 CHECK，不改 `f1_event_payload_ok`**：
-- 那个函数被现存的 `style_events_payload_check` 用着，且它的语义是「必需字段」。
-- 把取值域塞进去会让一条 CHECK 同时管两件事，失败时 23514 的错误信息
-- 说不出是哪一件——而两件的处置不同（补字段 vs 改拼写）。
create or replace function public.f3_deck_upload_failure_kinds()
returns text[]
language sql
immutable
as $$
  -- **上传失败分类的闭集。** 与 `api/api/upload_failures.py` 的 `FAILURE_KINDS`
  -- 是同一份清单的两处落地；`api/tests/test_upload_events.py` 从本文件原文
  -- 解析出它再逐项比对（口径 F33：两份存在 ≠ 两份一致）。
  --
  -- 每一类为什么单独存在，见那个 Python 文件里那张「用户该做什么」的表。
  -- 最容易被合掉的一对是 `pptx_not_implemented`（我们没做，会随交付消失）
  -- 与 `unparsable`（这份文件读不了，不会消失）——合成一个数之后，
  -- §3.2 的「课件支持率」变化说不清是哪一边动了。
  select array[
    'unsupported_format',
    'pptx_not_implemented',
    'empty_file',
    'too_large',
    'duplicate_in_course',
    'unparsable',
    'db_error',
    'unknown'
  ];
$$;

comment on function public.f3_deck_upload_failure_kinds() is
  'deck_upload_failed.payload.failure_kind 的闭集（唯一出处之一，另一处是 '
  'api/api/upload_failures.py 的 FAILURE_KINDS，有一条从 SQL 原文解析回来的比对）。';

-- 闭集字段的护栏。**只管取值域，不管必需字段**（那是 f1_event_payload_ok 的活）。
-- 两条 CHECK 分开，失败时 23514 说得出是哪一件。
create or replace function public.f3_event_enums_ok(event_type text, payload jsonb)
returns boolean
language plpgsql
immutable
as $$
begin
  -- 不涉及闭集的事件类型一律放行。**这里不重复做取值域校验**——
  -- event_type 自己的取值域住在 f1_event_required_keys（口径：一处出处）。
  if event_type <> 'deck_upload_failed' then
    return true;
  end if;

  -- 键缺失/为 null 不在这里判 —— 那是 f1_event_payload_ok 的活。
  -- 在这里再判一次会让同一个失败被两条 CHECK 拦，而先报哪一条不确定。
  if payload -> 'failure_kind' is null then
    return true;
  end if;

  return (payload ->> 'failure_kind') = any (public.f3_deck_upload_failure_kinds());
end;
$$;

comment on function public.f3_event_enums_ok(text, jsonb) is
  'payload 里闭集字段的取值域护栏。与 f1_event_payload_ok 分工：'
  '那个管「键在不在」，这个管「值对不对」。'
  '分成两条 CHECK 是为了 23514 说得出是哪一件——补字段与改拼写的处置不同。';

alter table public.style_events drop constraint if exists style_events_enum_check;
alter table public.style_events
  add constraint style_events_enum_check
  check (public.f3_event_enums_ok(event_type, payload));

-- ═══════════════════════════════════════════════════════════════════════════
-- ⓷ 两个事件的必需字段
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 依据 PRD §13.1「必要埋点」那张表的第一行：
--   「课件上传 | **文件类型、页数、处理耗时、成功/失败** | 支持率优化」
--
-- 「成功/失败」在这里由 **event_type 本身**承担（两个类型），不另立一个布尔字段：
-- 立了就有两处出处，而它们漂开的表现是「一条 deck_uploaded 里写着 ok=false」。
--
-- ⚠️ 仍然守 F50：**null 有含义的字段不进清单**。
--   · `deck_uploaded.restricted_pages_excluded` —— 恒 0（`RESTRICTED_PAGES` 按测试集
--     文件名写死，对真实用户恒不触发）。它是 0 不是 null，所以**可以**必需。
--   · `deck_upload_failed.page_count` —— 失败时往往**根本没解析出来**，是 null。
--     **不进清单。** 进了的话，「文件格式不对」这类失败会被 CHECK 拒掉，
--     而那正是最该被采集到的一类（用户第一次上传就撞上它）。
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

    -- ---- 阶段 6 第一批：上传页的两个（PRD §13.1 第一行）----
    --
    -- `source_format` = 文件类型；`page_count` / `usable_page_count` = 页数；
    -- `duration_ms` = 处理耗时；成功/失败由 event_type 承担。
    -- `slice_count` / `embedded_count` 两个都要：不等就意味着有切片没拿到向量，
    -- 而那种切片在检索里等于不存在（摄入那边有同形的断言）。
    -- `verdict` 是 parse 侧的 usable/unusable —— 一份 usable 的文档仍可能
    -- 有跳过页，两者不是一回事。
    when 'deck_uploaded'
      then array['filename', 'source_format', 'page_count', 'usable_page_count',
                 'verdict', 'slice_count', 'embedded_count', 'skipped_pages',
                 'restricted_pages_excluded', 'duration_ms']

    -- ⚠️ **只有三个必需**，刻意的。失败可能发生在任何一步之前：
    -- 格式不对时连文件都没读，`page_count` / `source_format` 全是 null。
    -- 把它们列成必需 = 最该被采集的那一类失败进不了库（F50 的同一条）。
    -- `bytes` 可以必需：那一步之前一定拿到了字节数（0 也是一个值）。
    when 'deck_upload_failed'
      then array['failure_kind', 'filename', 'bytes']
  end;
$$;

comment on function public.f1_event_required_keys(text) is
  'F1/F3 埋点：每个 event_type 的必需 payload 字段（口径 F36）。'
  '返回 NULL 表示未知 event_type —— 事件类型的取值域与本表同一处出处，不另立 CHECK。'
  '⚠️ null 有含义的字段不在清单里（口径 F50）：all_activated_passed / '
  'first_token_latency_ms / gap_verdict / deck_upload_failed.page_count。'
  'f1_event_payload_ok 把 JSON null 判成「没有」，进了清单会让一条完全正确的'
  '事件被拒掉，而被拒的事件是静默消失的。';

-- ---------------------------------------------------------------- schema_version
--
-- payload 口径又变了（九个类型 → 十一个，且多了一条闭集护栏），版本号跟着走。
-- 不抬的话 `f1-events-2` 会同时盖住两份口径（§13.0：没有版本字段就无法区分它们，
-- 合并会得到一个看起来正常的错数）。
alter table public.style_events alter column schema_version set default 'f1-events-3';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：____________　执行结果：____________
--
-- ⚠️ **部署顺序：本迁移先于那个构建**（仓库根 CLAUDE.md 那条）。
-- 反了的话上传的两个事件会因为 `f1_event_required_keys` 还不认识它们而
-- **23514 全部被拒**（未知 event_type → 返回 NULL → payload_ok 判 false），
-- 而**上传本身照常成功返回 201** —— 埋点失败不打断上传（口径 F42）。
-- 这个错不会以「上传坏了」的样子出现。
--
-- 本文件**在本地全部可验**（不碰 pgvector）：新增断言见 `verify_rls_local.mjs`
-- 的「style_events · 上传页两个事件（阶段 6 第一批）」一节。
