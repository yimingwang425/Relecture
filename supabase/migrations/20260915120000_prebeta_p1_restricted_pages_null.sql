-- 内测前-P1 · `deck_uploaded.restricted_pages_excluded` 停止说谎 + GapChecker 接进生产
-- 日期：2026-09-15　口径见 f1_samples/README.md 的 F68–F72
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⓵ `restricted_pages_excluded` 移出必需清单
-- ═══════════════════════════════════════════════════════════════════════════
--
-- ---------------------------------------------------------------- 这个字段现在是什么意思（写死）
--
--   · **`null` = 未检查。这是 v1 的既定状态。**
--   · 整数 **只可能**出现在将来真的做了受限页排除机制之后。在那之前出现整数就是 bug。
--
-- 用户上传的课件**不做受限页排除**（口径 F68）：我们无从判断用户 PDF 里哪几页
-- 是第三方保留权利内容，责任写进使用条款。`retrieval.corpus.RESTRICTED_PAGES`
-- 是按**测试集文件名**硬编码的清单，与用户上传无关；`/upload` 也从来没把它
-- 传给 `ingest_document`。
--
-- 所以此前每次上传写进来的 `0`，说的是「传进去的清单是空的」，**不是**
-- 「检查过，没有受限页」——而表里分不出这两者（口径 F69）。
-- 这比一条空守卫更坏：空守卫是不作为，这个是**主动产生假证据**。
-- 这个字段没有数据源可补，护栏无从补起，处置只能是让它停止产生假证据。
--
-- ---------------------------------------------------------------- 为什么移出必需清单
--
-- 这正是口径 F50 的适用情形：**null 有含义的字段不进必需清单。**
-- `f1_event_payload_ok` 把 JSON null 判成「没有」。它留在清单里的话，
-- 一条完全正确的 `deck_uploaded`（字段值 null）会被判成缺字段 → 23514，
-- 而被拒的事件是**静默消失的**：上传接口照常 201，事件表里什么都没有。
--
-- 代价如实登记（同 F50）：这个键从此**没有数据库护栏**——真漏了也不会红。
-- 它的护栏在 Python 侧：`api/tests/test_upload_events.py` 断言
-- 「`/upload` 成功后 payload 里这个键在、且值是 null」。
--
-- ---------------------------------------------------------------- ⚠️ 存量不回填
--
-- 生产里现有的 `deck_uploaded` 行，这个字段**全是 0**，而且**全是验收数据**
-- （内测尚未开始），随内测前的验收数据清理一起删，**不在这里回填成 null**。
--
-- ⚠️ **清理之前，这些 0 不可读作「没有受限页」。** 它们的真实含义与 null 相同：
-- 未检查。按 `schema_version` 区分：`f1-events-5` 之前的行里这个 0 不携带任何信息。
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⓶ GapChecker 接进生产：`explain_turn` 的 payload 多了 `gap_*` 几个键
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 本迁移**不改 `explain_turn` 的必需清单**。新增的键
-- （`gap_verdict` / `gap_cost_usd` / `gap_latency_ms` / `gap_implementation` /
-- `gap_model` / `gap_error` / `gap_skipped_hint_pages`）全部**不进清单**：
--   · `gap_verdict` 在判断无效或失败时是 null，有含义（F50，这条早就登记过）；
--   · `gap_cost_usd` 在判断失败时是 null（请求可能已计费，金额未知——写 0 是假证据）；
--   · 部署顺序是本迁移先于新构建。进了清单的话，两者之间那段窗口里
--     **旧构建发出的 `explain_turn` 没有这些键**，会被 23514 全部静默拒掉。
-- payload 口径变了，所以 `schema_version` 跟着抬（见文件末）。
--
-- ---------------------------------------------------------------- 必需字段：生效定义
--
-- 除 `deck_uploaded` 少一个键外，**其余十一个类型逐字不变**（照抄
-- `20260913150000_f4_confusion_marks.sql` 的那份）。`create or replace` 漏一个
-- `when` 分支，那个事件就当场变成「未知类型」全部被拒——
-- `api/tests/test_marks.py::TestMirrorsTheSql` 读的是**按文件名排序的最后一个定义**，
-- 类型数不是 12 就红。
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

    -- ---- F3 接线阶段 6 的四个讲解事件（原样；gap_* 刻意不进，见文件头 ⓶）----
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
    -- 内测前-P1：`deck_uploaded` 去掉 `restricted_pages_excluded`（null = 未检查，F69）。
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
  'deck_uploaded.restricted_pages_excluded（null = 未检查，F69）。'
  'f1_event_payload_ok 把 JSON null 判成「没有」，进了清单会让一条完全正确的'
  '事件被拒掉，而被拒的事件是静默消失的。'
  '⚠️ 「键在」不等于「有内容」：空数组通过 f1_event_payload_ok。'
  'slice_ids 的非空由 f4_event_slice_ids_ok 单独管。';

-- ---------------------------------------------------------------- schema_version
--
-- payload 口径变了两处：`deck_uploaded.restricted_pages_excluded` 由 0 改 null 且移出清单；
-- `explain_turn` 多了 `gap_*`。不抬的话 `f1-events-4` 会同时盖住两份口径——
-- 尤其是 `restricted_pages_excluded`：`-4` 里的 0 与 `-5` 里的 null 含义相同（未检查），
-- 而**只有版本号能告诉读数的人这一点**。
alter table public.style_events alter column schema_version set default 'f1-events-5';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：2026-09-15　执行结果：Success. No rows returned（SQL Editor 原文）
--
--     ⚠️ 这句只证明 DDL 执行成功（F34）。「清单里确实没有这个键」的真库确认
--     （`select public.f1_event_required_keys('deck_uploaded');`）未单独执行，记为未观测。
--
-- ⚠️ **部署顺序：本迁移先于那个构建**（仓库根 CLAUDE.md）。
-- 反了的话，新构建写的 `restricted_pages_excluded: null` 会被**旧的**必需清单
-- 判成缺字段 → `deck_uploaded` **23514 全部被拒**，而上传接口**照常返回 201**
-- （埋点失败不打断主链路，口径 F42）—— 静默失败。
-- 顺序对了则两边都安全：迁移跑完、新构建上线之前，旧构建仍写 0，
-- 0 不在新清单里但多一个键不会被拒。
--
-- 本迁移**不校验存量、不会回滚**：只替换一个函数、改一个列默认值，
-- 不新增约束（已有的 CHECK 不会因为函数被替换而重新校验存量行）。
--
-- ⚠️ 口径 F34：DDL 执行成功只证明它能执行。「null 的事件真的写得进去」
-- 「把键加回清单后 null 的事件真的被拒」是另外的证据：
--   · 本地机制（含反向控制）—— `supabase/verify_rls_local.mjs` 上传节 + R20；
--   · 真库 —— 部署后真传一份课件，读 `/courses/{id}/documents` 响应里的
--     `events.emitted == 1`（事件表从外面读不到，见 `_record_upload_event`）。
