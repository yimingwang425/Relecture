-- 去掉 `style_config.field`（2026-09-19，同日加同日删）。
--
-- ## 为什么加了又删
--
-- 上一份迁移（20260919120000）跟着「你学什么」那一块标签加了这一列，用途是让
-- 成对比较的样例用用户所在领域的题目。**那一块标签当天就被否掉了**，理由是：
--
-- - 课件讲什么**由老师定**，不由学生的专业定——学生自己掌控不了；
-- - 专业与内容类型不是一回事：理工科里有纯文字课程，文科里有数学与逻辑学。
--
-- 于是这一列**没有任何东西会写入**。留着它比删掉更糟：一个恒为 null 的列看起来
-- 像「还没采集到」，而真相是「我们决定不采集」——下一个人读到它会以为是缺口
-- （同 F69 那类「说谎的字段」：一个看起来是测量结果的值，实际从没测过）。
--
-- 无数据可迁：这一列从加上到删除之间没有任何一次写入（前端那一版还没部署）。
alter table public.style_config drop column if exists field;

-- 函数注释里那句「style_taste_chosen.field（null = 没选领域）」随之作废：
-- 事件 payload 里也不再有 field。
comment on function public.f1_event_required_keys(text) is
  'F1/F3/F4 埋点：每个 event_type 的必需 payload 字段（口径 F36）。'
  '返回 NULL 表示未知 event_type —— 事件类型的取值域与本表同一处出处，不另立 CHECK。'
  '⚠️ null 有含义的字段不在清单里（口径 F50）：all_activated_passed / '
  'first_token_latency_ms / gap_verdict / gap_cost_usd / deck_upload_failed.page_count / '
  'deck_uploaded.restricted_pages_excluded（null = 未检查，F69）。'
  'f1_event_payload_ok 把 JSON null 判成「没有」，进了清单会让一条完全正确的'
  '事件被拒掉，而被拒的事件是静默消失的。'
  '⚠️ 「键在」不等于「有内容」：空数组通过 f1_event_payload_ok。';
