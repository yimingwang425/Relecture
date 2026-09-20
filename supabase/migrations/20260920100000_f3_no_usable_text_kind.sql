-- 上传失败分类加一类：`no_usable_text`（2026-09-20）
--
-- ## 为什么要加这一类
--
-- 用户实测报上来的形态：传一份扫描版讲义，界面显示
-- 「已入库：0 个切片 · 0 条向量 · 19 页读不出文字 · 整份判为不可用（可能是扫描件）」——
-- **「已入库」与「不可用」同时为真**。那份文档会一直挂在课里，检索一片都取不到；
-- 更糟的是唯一索引把它记住了，同一份文件再传一次会被判 `duplicate_in_course`，
-- 于是连重试都做不到。
--
-- 产品决策改成**不入库**（`api/api/ingest.py::UnusableDocument`）。
-- 判断落在调用方而不是 parse-service 是刻意的：`parse-service/README.md` 的 P08
-- 写的是「如实报告哪些页不可用，**放行并告知，不替用户拒绝**」——拒不拒收是产品决策。
--
-- ## 为什么不并进 `unparsable`
--
-- 与 `pptx_not_implemented` 当初单独分出来**完全同一个理由**：
--   · 用户侧：这一类该去找一份文字版，`unparsable` 那一类该换一个没坏的文件；
--   · 分析侧（PRD §3.2 课件支持率）：**我们做了 OCR 之后这一类会消失，`unparsable` 不会**。
--     合成一个数之后，支持率的变化说不清是哪一边动了。
--
-- ## 迁移先于构建
--
-- 这个函数是 `deck_upload_failed` 那条 CHECK 的取值域。**先跑这个迁移再部署**：
-- 反了的话新构建发出的 `failure_kind: "no_usable_text"` 会被 23514 拒掉，
-- 而埋点失败不打断主链路（口径 F42）——用户那边看到的仍是一次正常的失败提示，
-- 只有那条事件悄悄少了。

create or replace function public.f3_deck_upload_failure_kinds()
returns text[]
language sql
immutable
as $$
  -- **上传失败分类的闭集。** 与 `api/api/upload_failures.py` 的 `FAILURE_KINDS`
  -- 是同一份清单的两处落地；`api/tests/test_upload_events.py` 从本文件原文
  -- 解析出它再逐项比对（口径 F33：两份存在 ≠ 两份一致）。
  --
  -- ⚠️ 那条比对读的是 **migrations/ 下按文件名排序的最后一个定义**，不是某一个
  -- 固定文件（同 `test_marks._effective_required_keys` 的做法）。所以这一份
  -- `create or replace` 一落地，比对就跟着走到这里，旧文件原样留着不回改。
  --
  -- 每一类为什么单独存在，见那个 Python 文件里那张「用户该做什么」的表。
  -- 最容易被合掉的两对：
  --   · `pptx_not_implemented`（我们没做，会随交付消失）vs `unparsable`（文件读不了，不会消失）
  --   · `no_usable_text`（文件好好的，但一页都读不出文字）vs `unparsable`（文件打不开）
  select array[
    'unsupported_format',
    'pptx_not_implemented',
    'empty_file',
    'too_large',
    'duplicate_in_course',
    'unparsable',
    'no_usable_text',
    'db_error',
    'unknown'
  ];
$$;

comment on function public.f3_deck_upload_failure_kinds() is
  'deck_upload_failed.payload.failure_kind 的闭集（唯一出处之一，另一处是 '
  'api/api/upload_failures.py 的 FAILURE_KINDS，有一条从 SQL 原文解析回来的比对）。'
  '2026-09-20 加入 no_usable_text：整份读不出文字的课件不再入库。';
