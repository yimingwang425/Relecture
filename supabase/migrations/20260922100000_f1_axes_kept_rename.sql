-- `style_text_mapped` 的 `axes_from_default` 改名为 `axes_kept`（2026-09-22）
--
-- ## 这个名字已经在说谎
--
-- 这个字段列的是「用户这段描述里**没有提到**的那几条轴」。原来的处置是
-- 「没提到 → 回落默认档」，名字与行为对得上。
--
-- 2026-09-20 风格矫正搬进讲解之后，处置变了：**没提到 → 保持用户当前的值**
-- （口径 F84，那是为了不让一句「再短一点」把另外三条轴悄悄重置）。
-- 于是 `axes_from_default` 说的是「用了默认值」，而事实是「一个字没动」——
-- 一个读起来完全正常、但含义已经反了的字段名。
--
-- ⚠️ **历史行用的还是旧名**（F10：不回改已有数据）。分析时要把两个名字并起来读：
--
--     coalesce(payload->'axes_kept', payload->'axes_from_default')
--
-- 这是改名的真实代价，写在这里，不掩饰。不改的代价更大：一个说谎的字段名
-- 会让每一个后来读它的人得出相反的结论，而没有任何东西会提醒他。
--
-- ## 其余 13 个类型**逐字不动**
--
-- 本文件是从当前生效定义（`20260919120000_f1_taste_boards.sql`）整段复制来的，
-- 只改了那一个键。`create or replace` 会整体替换，漏抄一个类型的后果是
-- 那个类型的必需字段校验当场消失，而它不会报错。
-- `api/tests/test_upload_events.py` 与 `test_marks.py` 都按**按文件名排序的
-- 最后一份**解析回来比对，所以它们从这一刻起比的就是这一份。

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
      then array['call', 'axes_from_text', 'axes_kept', 'ok', 'prompt_template_version']
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
  'f1 事件 payload 的必需字段（唯一出处）。'
  '2026-09-22：style_text_mapped 的 axes_from_default 改名 axes_kept —— '
  '该字段的含义从「回落默认档」变成了「保持原值」（口径 F84）。'
  '历史行仍用旧名，分析时并起来读。';
