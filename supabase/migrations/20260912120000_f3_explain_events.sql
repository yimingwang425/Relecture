-- F1→F3 接线阶段 6 · §13.0 的四个讲解事件 + 补上 course_id / deck_id 两列
-- 日期：2026-09-12　口径见 f1_samples/README.md 的 F36 / F35 / PRD §13.0
--
-- 沿用 20260910140000_f1_style_events.sql 的写法：一张表 + `event_type` 列 +
-- `payload jsonb`（口径 F36），每个类型的必需字段配一条会红的护栏。
--
-- ═══════════════════════════════════════════════════════════════════════════
-- ⓵ `course_id` / `deck_id`：**触发条件已满足，现在补上**
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 阶段 4 建表时刻意没建这两列，理由与触发条件原样登记在
-- `20260910140000_f1_style_events.sql` 里：
--
--   > F1 的五个事件全部发生在 onboarding 与设置页，**与任何课件无关**
--   > （样例是自写合成课件，口径 F02）。一个恒 null 的列和没有这个列，
--   > 在数据里等价；差别是恒 null 的列会让读的人以为「可以按课分组」，
--   > 然后得到一张全是空值的表。**触发条件**：事件开始涉及具体课件时（F2）。
--
-- **本阶段触发了它**：`explain_turn` / `retrieval_executed` 这两个事件
-- 就发生在一门具体的课、一份具体的讲义上，而 PRD §13.0 的通用字段表写着
-- 「多课程是 v1 范围（§11.1），**从第一天起就要能按课分组**」。
--
-- ⚠️ 两列**刻意不加外键**。这不是忘了，三条路各有各的坏处：
--
--   · `on delete cascade` —— 用户删掉一门课，那门课的**历史事件跟着没了**。
--     事件是既成事实（本表连 update / delete 策略都没有，理由正是「改事件等于
--     改历史」）。删课是一次整理，不该悄悄改掉上个月的北极星指标。
--   · `on delete set null` —— 更糟：行还在，但**再也不知道它属于哪门课**。
--     表现是「历史分组数据慢慢变少」，而没有任何东西会红。
--   · **不加**（现行）—— uuid 原样留着，删课之后仍然分得开组。
--
--   「注销即清空」不受影响：它走的是 `user_id` 那条外键的 cascade（口径 F35 /
--   PRD §12.1），本表建表时就有，与这两列无关。
--
--   代价说清楚：没有外键就没有引用完整性，理论上能写进一个不存在的 course_id。
--   **实际的约束在写入侧**：这四个事件由服务端写，而服务端写的 course_id
--   是它刚刚以用户身份从 `courses` 读回来的那一个（`/explain` 读不到就 404）。
--   客户端写不了——它对本表只有 insert 权限，而 F1 那五个事件不带这两列。
--
alter table public.style_events add column if not exists course_id uuid;
alter table public.style_events add column if not exists deck_id   uuid;

comment on column public.style_events.course_id is
  'F2 课程 id（口径 F47：检索的作用域）。**故意不加外键**——加了 cascade 会让删课'
  '改掉历史指标，加 set null 会让历史分组静默消失。注销清空走 user_id 那条 cascade。'
  'F1 的五个 onboarding 事件此列为 null，那是它们的真实情况，不是缺数据。';

comment on column public.style_events.deck_id is
  'F2 文档 id（= documents.id，PRD §13.0 的 deck_id）。一次讲解可能命中多份讲义，'
  '**此列只在事件天然只涉及一份时填**；`retrieval_executed` 命中的全部文档在 '
  'payload.slice_ids 里带 document_id，不压进这一列——压进去要么丢信息要么撒谎。'
  '⚠️ **本阶段它恒为 NULL**：四个讲解事件没有一个天然只涉及一份讲义。'
  '这与阶段 4 拒绝建这两列的理由（「恒 null 的列和没有这个列在数据里等价，'
  '差别是恒 null 的列会让读的人以为可以按它分组」）**是同一条，如实登记**：'
  '第一个真正填它的事件是上传侧的（deck_upload_failed / deck_uploaded），'
  '而那要等上传页。**触发条件就是上传页**——在那之前，'
  '任何按 deck_id 分组的查询都会得到一张全是空值的表。';

-- ⚠️ 上面那条 comment 里的登记值得再说一遍，因为它是一处**刻意接受的不一致**：
--
--   `course_id` 现在就被真的写进去了（`/explain` 每一轮都填），所以它建得其所。
--   `deck_id` **现在恒为 NULL**，按阶段 4 自己的理由它本该等到上传页再建。
--   两列一起建的理由是 PRD §13.0 的通用字段表把它们列在一起，且一次 alter
--   比两次少一条迁移；**代价是「可以按 deck 分组」这个错觉提前存在了一轮**。
--   挡它的不是列的存在与否，而是这条登记 + 上面那条 comment（`\d+` 看得到）。
--   **如果内测期间真有人按 deck_id 分组并得到空表，那就是这个决定的账。**

-- 按课分组是这两列的**唯一用途**，那就给它一个索引。
create index if not exists style_events_course_created_idx
  on public.style_events (course_id, created_at)
  where course_id is not null;

-- ═══════════════════════════════════════════════════════════════════════════
-- ⓶ 四个讲解事件的必需字段
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 依据是 PRD §13.0「本批四个事件」那四张表，逐字对照。
--
-- ⚠️ **一条必须说清的相互作用：`f1_event_payload_ok` 把 JSON null 判成「没有」。**
--
-- 那是阶段 4 刻意定的（前端拿到 undefined 序列化成 null 是最常见的漏字段形态）。
-- 但 §13.0 里有几个字段**null 是有含义的**，不是漏了：
--
--   · `all_activated_passed` —— 无维度可判时为 `null`，**不是 `true`**（口径 D42）。
--   · `first_token_latency_ms` —— 不流式，测不了。日志里如实写 null，
--     **不塞假数**（PRD §9.2 在本轮无数据）。
--   · `gap_verdict` —— F3b 未实现，恒 null。
--
-- 这几个**一律不进必需字段清单**。进了的话，一条完全正确的事件会被 CHECK 拒掉，
-- 而拒掉的表现是「这条事件没了」——漏采集是最安静的一类 bug（阶段 4 的原话）。
--
-- 反过来说，它们因此**没有护栏**：字段真的漏了也不会红。这一条如实登记，
-- 不假装两全。**触发条件**：内测第 1 周数据落盘后，若发现这几个字段有缺失，
-- 要的是一个「键存在但值可为 null」的判据（`payload ? 'k'`），而不是把它们塞进
-- 现在这个清单——那会把「有意义的 null」和「漏了」再次压成同一件事。
--
-- `question` 这个字段登记一处张力：F1 的对照臂刻意**不把描述原文写进事件**
-- （`textMapped()` 压根不接受那个参数，口径 F32 + PRD §12.2）。
-- 而 §13.0 的 `explain_turn` **明确列了 `question`**，且北极星指标要按提问分析。
-- 现按 PRD 执行（它是这件事的出处，且是显式的），**但这是一个决定不是疏忽**：
-- 触发条件——对外开放前的隐私政策逐条对齐时复核一次，届时两条先例摆在一起看。
create or replace function public.f1_event_required_keys(event_type text)
returns text[]
language sql
immutable
as $$
  select case event_type
    -- ---- F1 阶段 4 的五个（原样，一个字没动）----
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

    -- ---- F3 接线阶段 6 的四个（PRD §13.0「本批四个事件」）----
    --
    -- `usage` 四类分列（未命中输入 / 缓存读 / 缓存写 / 输出）的形状由
    -- `explain.llm.Usage` 决定，这里只要求这个键在；三类输入互不重叠这件事
    -- 在 Python 侧有它自己的断言。
    -- `pricing_rates` / `pricing_intro_until` 是 §13.0 的原话：
    -- 「价目带到期日，否则事后无法重算」。
    -- ⚠️ `first_token_latency_ms` 恒 null，**不在清单里**（见上）。
    when 'explain_turn'
      then array['turn_index', 'question', 'usage', 'cost_usd', 'pricing_rates',
                 'latency_ms', 'accepted', 'regenerations',
                 'style_config_fingerprint', 'metric_set_id']

    -- §8.1 已定「不做单一混合分」。**按维度分列存**，只存合并结论的话
    -- §3.2 的「各激活维度均需达标」事后就算不出来。
    -- ⚠️ `all_activated_passed` 可以是 null（口径 D42），**不在清单里**。
    when 'style_review_result'
      then array['dimensions', 'counted_dimensions']

    -- 触顶降级时 `final_accepted` 为 false —— 它是 false 不是 null，所以可以必需。
    when 'regeneration_triggered'
      then array['attempt_index', 'failed_dimensions', 'final_accepted']

    -- §10.1 已实测检索分数判不了 no_answer，但**分布本身仍要长期监控**：
    -- 它是「检索质量有没有随语料变化而退化」的唯一信号。
    -- `slice_ids` 里每项带 document_id —— slice_id 是文档内局部 id，
    -- 一门课两份讲义都有 's001'（2026-09-12 真库观测坐实）。
    when 'retrieval_executed'
      then array['top_k_scores', 'slice_ids', 'pages', 'latency_ms',
                 'embed_model', 'dimensions', 'ruleset_versions', 'backend']
  end;
$$;

comment on function public.f1_event_required_keys(text) is
  'F1/F3 埋点：每个 event_type 的必需 payload 字段（口径 F36）。'
  '返回 NULL 表示未知 event_type —— 事件类型的取值域与本表同一处出处，不另立 CHECK。'
  '⚠️ null 有含义的字段（all_activated_passed / first_token_latency_ms / gap_verdict）'
  '**不在清单里**：f1_event_payload_ok 把 JSON null 判成「没有」，'
  '进了清单会让一条完全正确的事件被拒掉，而被拒的事件是静默消失的。';

-- ---------------------------------------------------------------- schema_version
--
-- payload 的口径变了（多了四个类型），**版本号必须跟着走**。
-- 阶段 4 那个文件写着：「改 `f1_event_required_keys` 的那次迁移顺手
-- `alter column ... set default` 到下一版；已有的行保留它们当时那个值，
-- 这正是要的。」——就是这一次。
--
-- 不改的话，`f1-events-1` 会同时盖住「只有五个类型」和「有九个类型」两份口径，
-- 而那正是 §13.0 说的「没有版本字段就无法区分它们，合并会得到一个看起来正常的错数」。
alter table public.style_events alter column schema_version set default 'f1-events-2';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：____________　执行结果：____________
--
-- ⚠️ 按口径 F34，DDL 执行成功只证明**它能执行**。
-- 四个事件真的落库、护栏真的拦过一次真请求，是另外的证据，各记各的。
--
-- 本文件**在本地全部可验**（不碰 pgvector）：`verify_rls_local.mjs` 跑
-- migrations/ 下的原文，新增断言见该文件的「style_events · 四个讲解事件（阶段 6）」一节。
