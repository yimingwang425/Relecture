-- F1 · 事件的 arm 改成对照 style_config.f1_arm，不再对照 f1_assign_arm()
-- 日期：2026-10-07　口径见 f1_samples/README.md 的 F102
--
-- ═══════════════════════════════════════════════════════════════════════════
-- 🐞 **这是一个从 2026-09-20 起就在生产上的静默 bug。**
--
-- `style_events_arm_matches_assignment` 的 CHECK 是 `arm = f1_assign_arm(user_id)`。
-- 迁移 20260920120000（F85，AB 对照结束）把 `f1_assign_arm` 改成**恒返回 'taste'**，
-- 而 F10 规定已有行的 `style_config.f1_arm` **不回改**——09-20 之前注册的用户
-- 那一列仍是 'pairwise' / 'text'。
--
-- 写事件的两端（`api/api/events.py`、`web/lib/style/events.ts`）都传 `style_config.f1_arm`。
-- 于是对这批老用户：arm = 'pairwise' ≠ f1_assign_arm() = 'taste' → **每一条事件都 23514**。
-- 讲解照常返回 200（埋点失败不打断主链路，F42），事件静静消失。
--
-- 发现于 2026-10-07 的 `verify_retrieval_prod.py` 第 4 节：验收账号是 09-12 建的，
-- `explain_turn` / `style_review_result` / `retrieval_executed` 三条全部被
-- `style_events_arm_matches_assignment` 拒掉。
--
-- 本地为什么一直是绿的：`verify_rls_local.mjs` 的每个用户都是在**全部迁移跑完之后**
-- 才插进 `auth.users` 的，他们的 `f1_arm` 由触发器按新函数写成 'taste'，
-- 于是 `arm = f1_assign_arm()` 对他们恒成立——**本地从来没有一个 09-20 之前的用户**。
-- 这正是 F85 那条警告说的「恒定值没有分辨力」，只是这次咬在了 CHECK 上，而不是分析上。
-- ═══════════════════════════════════════════════════════════════════════════
--
-- ## 修法：对照「这一行配置是怎么来的」，而不是「现在新用户会被分到哪」
--
-- CHECK 的本意（20260910140000 原注释）是「**用户改不了自己在事件里的臂**」。
-- F85 之后 `f1_arm` 的含义是「这行配置是怎么来的」，而它由 `style_config_before_update`
-- 锁死、任何人改不了——所以「事件里的 arm 必须等于他那一行的 f1_arm」守的仍是同一件事，
-- 而且对新老用户都成立。CHECK 不能子查询，所以换成 before insert 触发器。
--
-- **错误码仍是 23514、约束名写进错误**：既有的「伪造 arm 被拒」断言断的是 23514，
-- 换层之后它照样成立，而且能从约束名看出拦它的是哪一层。
--
-- ⚠️ **09-20 至今丢掉的事件补不回来**（被拒的 insert 不留痕迹）。
-- 能确定的只有范围：这段时间里所有 `f1_arm <> 'taste'` 用户的全部事件。
--
-- 部署：**只改数据库，不改应用代码**。在 SQL Editor 跑完即生效，与构建先后无关。

alter table public.style_events drop constraint if exists style_events_arm_matches_assignment;

create or replace function public.f1_event_arm_matches_config()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  cfg_arm text;
begin
  select f1_arm into cfg_arm from public.style_config where user_id = new.user_id;
  if cfg_arm is null then
    raise exception '用户 % 没有 style_config 行（注册触发器没跑到，口径 F09）', new.user_id
      using errcode = '23514', constraint = 'style_events_arm_matches_config';
  end if;
  if new.arm is distinct from cfg_arm then
    raise exception 'style_events.arm (%) 与该用户 style_config.f1_arm (%) 不一致', new.arm, cfg_arm
      using errcode = '23514', constraint = 'style_events_arm_matches_config';
  end if;
  return new;
end;
$$;

drop trigger if exists style_events_arm_matches_config on public.style_events;
create trigger style_events_arm_matches_config
  before insert on public.style_events
  for each row execute function public.f1_event_arm_matches_config();

comment on function public.f1_event_arm_matches_config() is
  '事件的 arm 必须等于该用户 style_config.f1_arm（口径 F102）。替代 20260910140000 的 '
  'style_events_arm_matches_assignment：那条对照 f1_assign_arm()，而它自 F85 起恒为 taste，'
  '把 09-20 之前注册用户的全部事件都拒掉了。';

-- ---------------------------------------------------------------- 执行记录
--
--     生产执行日期：2026-10-07　执行结果：成功
--
--     **真库观测**（verify_retrieval_prod.py 第 4 节，构建 0799e49）：09-12 建的验收账号
--     （f1_arm = pairwise）「事件真的落库」从 emitted 0 / failed 3（23514）变成 emitted 3 / failed 0。
--
-- 真库上要观测的一件事：用 09-20 之前注册的账号（如 f1-rls-a）重跑
-- `verify_retrieval_prod.py`，第 4 节「事件真的落库（期望 3 条）」变绿。
