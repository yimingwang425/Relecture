-- F3 · `deck_id` 的按类型不变量 + 字段落在哪的自述
-- 日期：2026-09-13　口径见 f1_samples/README.md 的 F36 / F50 / F53
--
-- ═══════════════════════════════════════════════════════════════════════════
-- 为什么有这个文件：一次人工验收里，「deck_id 是不是 null」**问不出答案**
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 2026-09-13 的 `/courses` 人工验收报了两条红：
--   ① `deck_uploaded` 的 `deck_id` 是 null；
--   ② `deck_upload_failed` 的 `reason` 是 null（失败分类没落库）。
--
-- 两条都指向同一件事：**查的地方不对**。
--   · `deck_id` 是**表的一列**，不在 `payload` 里 —— `payload->>'deck_id'` 必为 null。
--   · `reason` **不是本项目任何事件的字段**。失败分类叫 `failure_kind`。
--
-- ⚠️ **但「查错了」不是结论，是症状。** 真正的缺陷有两个，本文件修第一个：
--
-- **缺陷 A：`deck_id` 没有按事件类型的 NOT NULL 不变量。**
--   `f1_event_payload_ok` 只管 `payload` 里的必需键，而 `deck_id` **压根不在
--   payload 里**——所以那条 CHECK 对它一句话也说不上。于是：
--   「`deck_uploaded` 的 `deck_id` 真的是 null」这件事**没有任何东西会红**，
--   而它正是上一批交付说明里写着「已经解掉」的那条。
--
--   验收的人问「它是不是 null」，而系统答不出来 —— 不是答错，是**没有能回答
--   这个问题的机制**。补上之后，这个问题变成不可能发生：null 的行会被拒，
--   于是**行存在本身就是 deck_id 非 null 的证据**。
--
--   （缺陷 B 是「上传的埋点结果只进服务端日志、不进响应」，
--     所以外部无法自证。修在 `api/api/main.py`，见那里的说明。）
--
-- ## 这一对 CHECK 是穷尽的，两边都要
--
-- 只加「成功必须有 deck_id」的话，**失败事件填了一个编出来的 deck_id 仍然通过**，
-- 而那会让「按 deck 看失败率」把不同文件算成同一份。
-- 所以另一半也钉死：失败时 `deck_id` **必须是 null**（那时没有 document 行，
-- null 是事实，不是缺数据）。
--
-- 两条写成**一条 CHECK 里的两个分支**，而不是两条独立 CHECK：它们回答的是
-- 同一个问题（「这一行的 deck_id 与它的事件类型相称吗」），
-- 而 23514 指向同一个约束名时排查路径也是同一条。
alter table public.style_events drop constraint if exists style_events_deck_id_matches_type;
alter table public.style_events
  add constraint style_events_deck_id_matches_type check (
    case event_type
      -- 成功上传一定有 document 行，id 拿得到。null 在这里是 bug，不是事实。
      when 'deck_uploaded'      then deck_id is not null
      -- 失败时**没有** document 行。编一个会让按 deck 的分组把不同文件算成同一份。
      when 'deck_upload_failed' then deck_id is null
      -- 其余类型不约束：讲解那四个的 deck_id 恒 null（一次讲解可能命中多份讲义，
      -- 压进一列要么丢信息要么撒谎），F1 那五个与课件无关。
      else true
    end
  );

comment on constraint style_events_deck_id_matches_type on public.style_events is
  'deck_id 与 event_type 必须相称：deck_uploaded 非 null、deck_upload_failed 为 null。'
  '⚠️ 它补的是 f1_event_payload_ok 管不到的地方 —— 那条只看 payload 里的键，'
  '而 deck_id 是表的一列。没有这一条，「deck_uploaded 的 deck_id 是 null」'
  '不会被任何东西拦住，而那正是 2026-09-13 人工验收问不出答案的那个问题。';

-- ═══════════════════════════════════════════════════════════════════════════
-- 字段落在哪：**让表自己说得出来**
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 那次验收查 `payload->>'deck_id'` 与 `payload->>'reason'`，两个都 null，
-- 于是读成「功能没做」。**猜字段名这件事本身是可以被消除的** —— 表注释里写清。
-- ⚠️ **用普通的相邻 `'...'` 拼接，不用 `E'...'`。** PGlite 的解析器不接受
-- 相邻的 E 字符串（实测两次：`syntax error at or near "E'...'"`），
-- 而本地验证跑的就是这份原文（不是复制品），所以它必须在两边都能执行。
-- 代价是注释里没有换行，用 `·` 分段——比「本地验不了这份迁移」便宜得多。
comment on table public.style_events is
  'F1/F3 埋点事件，一行一次事件（口径 F36：一张表 + event_type 列 + payload jsonb）。'
  'user_id 是真 uuid，随账号注销级联删除（口径 F35 / PRD §12.1）；hash 只在对外导出时。'
  '客户端权限：只能 insert 自己的事件，不能 select / update / delete'
  '—— 读事件是分析行为，走 service_role。'
  '　⚠️ **哪些字段是「列」、哪些在 payload 里**（查错地方会得到一片 null，'
  '而那读起来像「功能没做」）：'
  '　· **列**：event_type / arm / session_id / created_at / schema_version'
  ' / course_id / deck_id。'
  '　· **payload 里**：其余全部。失败分类叫 **failure_kind**（不叫 reason）；'
  '重复上传是哪一层拦下的叫 **caught_by**（pre_check / unique_index）。'
  '　· 每个 event_type 的必需 payload 键：select public.f1_event_required_keys(t)。'
  '　· 失败分类的闭集：select public.f3_deck_upload_failure_kinds()。';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：____________　执行结果：____________
--
-- ⚠️ **这一条迁移会拒掉已有的坏行吗？** 不会 ——
-- `alter table ... add constraint ... check` **默认校验存量**，
-- 所以如果生产里真有一条 `deck_uploaded` 且 `deck_id is null`，
-- **这条迁移会执行失败并回滚**。那不是麻烦，那正是想要的：
-- 它把「到底有没有那种行」从一个查不出答案的问题变成一次明确的执行结果。
--
--   · 迁移成功 → 存量里没有那种行，且以后也不会有。
--   · 迁移失败（23514）→ 存量里**真的有**，那时上一批的交付说明确实说错了，
--     先跑下面这句把它们看清，再决定是改数据还是改代码：
--
--       select event_id, created_at, course_id, deck_id, payload->>'filename'
--       from public.style_events
--       where event_type = 'deck_uploaded' and deck_id is null;
--
-- 本文件**在本地全部可验**：新增断言见 `verify_rls_local.mjs` 的
-- 「deck_id 与 event_type 必须相称」一节（含反向控制 R16）。
